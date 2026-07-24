#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""movian_lifecycle.py -- GDB Python lifecycle event collector (Buksa/movian #144).

Dual-role, single file:

  * **Sourced inside GDB** (``source support/devtools/gdb/movian_lifecycle.py``)
    it defines the collector core and the ``movian-lifecycle-start`` command,
    which arms *internal* breakpoints from an inventory category filter.  On a
    hit it writes one JSONL event and auto-continues (``stop()`` returns
    ``False``).  Ordinary lifecycle probes never stop the target and never run a
    full all-thread backtrace; only 3-6 frames are collected per event.

  * **Run as a plain interpreter**
    (``python3 support/devtools/gdb/movian_lifecycle.py launch ...``) it is the
    **host-launch orchestrator**: it starts the inferior *under GDB from exec*
    so the category breakpoints are armed before the inferior reaches
    ``main_init``; it uses an isolated mdev-style persistent/cache profile,
    records the real Movian inferior PID + parsed HTTP port into the standard
    ``/tmp/mdev/<name>/state.json``, and proves ownership with the existing
    ``comm == movian`` plus exact ``--persistent``-path rule -- which is exactly
    what makes the instance safe for ``mdev stop``.

Tooling only: no production C/runtime changes, no global ptrace/sysctl change.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
import tempfile

# ---------------------------------------------------------------------------
# Are we running inside GDB?  ``import gdb`` only succeeds when this file is
# sourced by GDB; the host-launch orchestrator runs under CPython where it
# raises ImportError.
# ---------------------------------------------------------------------------
try:
    import gdb  # type: ignore
    _HAVE_GDB = True
except Exception:  # pragma: no cover - exercised by the orchestrator path
    gdb = None  # type: ignore
    _HAVE_GDB = False


# Category -> default per-category event cap.  High-volume probes are bounded so
# total collector overhead stays small regardless of how hot a function is; once
# the cap is reached the category's breakpoints are disabled (zero further
# traps) and one "rate-limited" summary event is emitted.
HIGH_VOLUME_CAPS = {
    "prop-subscribe": 64,
    "callout": 64,
    "es-resource": 80,
    "thread-create": 80,
    "glw": 120,
    "service": 80,
}
DEFAULT_CAP = 400

# Categories permitted to STOP the target and capture a full all-thread
# backtrace.  None of the ordinary lifecycle categories are stopping
# categories; a real crash/wedge surfaces as a signal/exit reported via the gdb
# event hooks, not by arming a stopping probe.
STOPPING_CATEGORIES = set()

_COLLECTOR = None  # type: ignore[assignment]


# ###########################################################################
#  Collector core (GDB only)
# ###########################################################################
if _HAVE_GDB:

    def _monotonic_ns():
        return time.monotonic_ns()

    def _thread_info():
        info = {"gdbId": None, "name": None, "osTid": None}
        try:
            thr = gdb.selected_thread()
            if thr is not None:
                info["gdbId"] = thr.global_num
                ptid = tuple(thr.ptid)
                # ptid is (pid, lwpid, tid); the OS thread id (gettid) is the
                # LWP id on Linux.
                os_tid = 0
                if len(ptid) > 1 and ptid[1]:
                    os_tid = ptid[1]
                elif len(ptid) > 2 and ptid[2]:
                    os_tid = ptid[2]
                elif ptid:
                    os_tid = ptid[0]
                info["osTid"] = os_tid or None
                name = getattr(thr, "name", None)
                if not name and os_tid:
                    try:
                        with open("/proc/%d/comm" % os_tid) as f:
                            name = f.read().strip()
                    except OSError:
                        name = None
                info["name"] = name
        except Exception:
            pass
        return info

    def _summarize_value(val, maxlen=120):
        try:
            if getattr(val, "is_optimized_out", False):
                return "<optimized-out>"
        except Exception:
            pass
        try:
            t = val.type.strip_typedefs()
            code = t.code
            if code == gdb.TYPE_CODE_INT:
                try:
                    return int(val)
                except Exception:
                    return val.format_string()
            if code == gdb.TYPE_CODE_PTR:
                tgt = t.target().strip_typedefs()
                # char * -> best-effort bounded C string
                if tgt.code == gdb.TYPE_CODE_INT and tgt.sizeof == 1:
                    try:
                        return val.string(length=256)[:maxlen]
                    except Exception:
                        pass
                try:
                    return "0x%x" % int(val)
                except Exception:
                    return str(val)[:maxlen]
        except Exception:
            pass
        try:
            return str(val)[:maxlen]
        except Exception:
            return "<unreadable>"

    def _capture_arguments(frame, limit=6):
        args = {}
        try:
            block = frame.block()
            # Walk up to the function's own block where the parameters live.
            while block is not None and block.function is None:
                block = block.superblock
            if block is None:
                return args
            count = 0
            for sym in block:
                if not getattr(sym, "is_argument", False):
                    continue
                name = sym.name if sym.name else ("arg%d" % count)
                try:
                    v = frame.read_var(sym)
                    if getattr(v, "is_optimized_out", False):
                        args[name] = "<optimized-out>"
                    else:
                        args[name] = _summarize_value(v)
                except Exception:
                    args[name] = "<unavailable>"
                count += 1
                if count >= limit:
                    break
        except Exception:
            pass
        return args

    def _capture_stack(limit=6):
        frames = []
        try:
            fr = gdb.newest_frame()
        except Exception:
            return frames
        i = 0
        while fr is not None and i < limit:
            entry = {"function": None, "file": None, "line": None,
                     "address": None}
            try:
                entry["function"] = fr.name()
            except Exception:
                entry["function"] = "?"
            try:
                sal = fr.find_sal()
                if sal is not None and sal.symtab is not None:
                    entry["file"] = sal.symtab.filename
                    entry["line"] = sal.line
            except Exception:
                pass
            try:
                entry["address"] = int(fr.pc())
            except Exception:
                pass
            frames.append(entry)
            try:
                fr = fr.older()
            except Exception:
                break
            i += 1
        return frames

    class LifecycleBP(gdb.Breakpoint):  # type: ignore[misc]
        """Internal, auto-continuing breakpoint on one lifecycle probe."""

        def __init__(self, spec, category, symbol, arg_exprs=None):
            super().__init__(spec, internal=True)
            self.silent = True
            self.category = category
            self.symbol = symbol
            self.arg_exprs = arg_exprs or []
            self.bound = True

        def stop(self):
            # Every ordinary lifecycle hit auto-continues; we never return a
            # truthy value, so the target is never stopped by these probes.
            try:
                if _COLLECTOR is not None:
                    _COLLECTOR.on_hit(self)
            except Exception as exc:  # never let a probe fault the session
                if _COLLECTOR is not None:
                    _COLLECTOR.note_error(exc)
            return False

    class Collector(object):
        def __init__(self, events_path, default_cap, hv_overrides,
                     pidfile=None):
            self.events_path = events_path
            self.pidfile = pidfile
            self._default_cap = default_cap
            self._caps = dict(hv_overrides)  # explicit per-category overrides
            self._fh = open(events_path, "w", encoding="utf-8")
            self._seq = 0
            self._counts = {}
            self._suppressed = {}
            self._summarized = set()
            self._armed = []
            self._unbound = []
            self._pid_written = False
            self._errors = []
            self._install_exit_hook()

        def _cap_for(self, cat):
            if cat in self._caps:
                return self._caps[cat]
            if cat in HIGH_VOLUME_CAPS:
                return HIGH_VOLUME_CAPS[cat]
            return self._default_cap

        # -- event emission --------------------------------------------------
        def emit(self, event):
            self._seq += 1
            event["seq"] = self._seq
            event.setdefault("monotonicNs", _monotonic_ns())
            self._fh.write(json.dumps(event, separators=(",", ":")) + "\n")
            self._fh.flush()

        def note_error(self, exc):
            self._errors.append(repr(exc))
            self.emit({"category": "collector", "event": "probe-error",
                       "symbol": None, "error": repr(exc),
                       "thread": _thread_info(), "arguments": {},
                       "objects": {}, "stack": []})

        # -- breakpoint hit --------------------------------------------------
        def on_hit(self, bp):
            cat = bp.category
            n = self._counts.get(cat, 0) + 1
            self._counts[cat] = n
            cap = self._cap_for(cat)
            if cap and n > cap:
                # Disable every probe in this category (zero further traps) and
                # emit a single rate-limit summary, once.
                self._disable_category(cat)
                self._suppressed[cat] = self._suppressed.get(cat, 0) + 1
                if cat not in self._summarized:
                    self._summarized.add(cat)
                    self.emit({"category": cat, "event": "rate-limited",
                               "symbol": bp.symbol, "cap": cap,
                               "emitted": cap,
                               "thread": _thread_info(), "arguments": {},
                               "objects": {}, "stack": []})
                return
            self._write_pidfile_once()
            try:
                frame = gdb.newest_frame()
                arguments = _capture_arguments(frame)
            except Exception:
                arguments = {}
            # Optional extra argument expressions (used by the falsification
            # probe): evaluated in try/except so a missing/optimized-out value
            # is recorded rather than crashing the session.
            for expr in bp.arg_exprs:
                key = expr if len(expr) <= 48 else ("expr:%s" % expr[:32])
                try:
                    v = gdb.parse_and_eval(expr)
                    if getattr(v, "is_optimized_out", False):
                        arguments[key] = "<optimized-out>"
                    else:
                        arguments[key] = _summarize_value(v)
                except Exception:
                    arguments[key] = "<unavailable>"
            objects = {k: v for k, v in arguments.items()
                       if isinstance(v, str) and v and not v.startswith("<")
                       and not v.startswith("0x")}
            self.emit({
                "category": cat,
                "event": "enter",
                "symbol": bp.symbol,
                "thread": _thread_info(),
                "arguments": arguments,
                "objects": objects,
                "stack": _capture_stack(6),
            })

        def _disable_category(self, cat):
            for bp in self._armed:
                if bp.category == cat and bp.bound:
                    try:
                        bp.enabled = False
                    except Exception:
                        pass

        def _write_pidfile_once(self):
            if self._pid_written or not self.pidfile:
                return
            try:
                pid = gdb.selected_inferior().pid
                if pid:
                    with open(self.pidfile, "w") as f:
                        f.write(str(pid))
                    self._pid_written = True
            except Exception:
                pass

        def armed_summary(self):
            return {
                "armed": sorted({b.symbol for b in self._armed if b.bound}),
                "unbound": sorted(self._unbound),
            }

        def _install_exit_hook(self):
            def _on_exit(event):
                code = getattr(event, "exit_code", None)
                self.emit({"category": "collector", "event": "inferior-exited",
                           "symbol": None, "exitCode": code,
                           "thread": _thread_info(), "arguments": {},
                           "objects": {}, "stack": []})
                try:
                    self._fh.flush()
                except Exception:
                    pass
            try:
                gdb.events.exited.connect(_on_exit)
            except Exception:
                pass

        def arm_from_inventory(self, inventory, categories, arg_exprs_by_symbol):
            sel = set(categories) if categories else None
            for entry in inventory.get("entries", []):
                cat = entry["category"]
                if sel is not None and cat not in sel:
                    continue
                if cat in STOPPING_CATEGORIES:
                    continue
                spec = entry["symbol"]
                arg_exprs = arg_exprs_by_symbol.get(entry["symbol"])
                try:
                    bp = LifecycleBP(spec, cat, entry["symbol"], arg_exprs)
                    self._armed.append(bp)
                except Exception as exc:
                    self._unbound.append("%s (%s)" % (spec, exc))

        def add_adhoc(self, sym, cat, exprs):
            try:
                self._armed.append(LifecycleBP(sym, cat, sym, exprs))
            except Exception as exc:
                self._unbound.append("%s (%s)" % (sym, exc))

        def close(self):
            try:
                self.emit({"category": "collector",
                           "event": "collector-final",
                           "symbol": None,
                           "counts": self._counts,
                           "suppressed": self._suppressed,
                           "errors": self._errors,
                           "thread": _thread_info(), "arguments": {},
                           "objects": {}, "stack": []})
            except Exception:
                pass
            try:
                self._fh.flush()
                self._fh.close()
            except Exception:
                pass

    # -- GDB command wiring --------------------------------------------------
    def _parse_opts(arg):
        opts = {}
        toks = arg.replace("\\,", "\x00").split()
        i = 0
        while i < len(toks):
            t = toks[i].replace("\x00", ",")
            if t.startswith("--"):
                key = t[2:]
                if i + 1 < len(toks) and not toks[i + 1].startswith("--"):
                    opts[key] = toks[i + 1].replace("\x00", ",")
                    i += 2
                else:
                    opts[key] = True
                    i += 1
            else:
                i += 1
        return opts

    class MovianLifecycleStart(gdb.Command):  # type: ignore[misc]
        """movian-lifecycle-start --events FILE [--inventory FILE]
        [--categories a,b,...] [--cap N] [--hv-cap N] [--pidfile FILE]
        [--probe SYM:CAT:EXPR;EXPR ...]

        Arm internal breakpoints for the given categories (default: all
        inventory categories) and begin emitting JSONL events.  Must be run
        before ``run`` so probes are pending and bind at exec, ahead of
        main_init."""

        def __init__(self):
            super().__init__("movian-lifecycle-start", gdb.COMMAND_DATA)

        def invoke(self, arg, from_tty):
            global _COLLECTOR
            opts = _parse_opts(arg)
            events_path = opts.get("events")
            if not events_path:
                print("movian-lifecycle-start: --events FILE is required")
                return
            inventory = {"entries": []}
            inv_path = opts.get("inventory")
            if inv_path:
                try:
                    with open(inv_path) as f:
                        inventory = json.load(f)
                except OSError as exc:
                    print("movian-lifecycle-start: cannot read inventory %s: %s"
                          % (inv_path, exc))
            try:
                hv = int(opts.get("hv-cap", 0))
            except ValueError:
                hv = 0
            hv_overrides = {}
            if hv > 0:
                hv_overrides = {c: hv for c in HIGH_VOLUME_CAPS}
            try:
                default = int(opts.get("cap", 0))
            except ValueError:
                default = 0
            default = default if default > 0 else DEFAULT_CAP

            categories = None
            if opts.get("categories"):
                categories = [c.strip() for c in
                              opts["categories"].split(",") if c.strip()]
            # ad-hoc probes: "symbol:category:expr;expr", several joined by "|"
            arg_exprs_by_symbol = {}
            adhoc = []
            for spec in (opts.get("probe") or "").split("|"):
                if not spec:
                    continue
                parts = spec.split(":", 2)
                sym = parts[0]
                cat = parts[1] if len(parts) > 1 and parts[1] else "adhoc"
                exprs = [e for e in (parts[2].split(";")
                                     if len(parts) > 2 else []) if e]
                adhoc.append((sym, cat, exprs))
                if exprs:
                    arg_exprs_by_symbol[sym] = exprs

            _COLLECTOR = Collector(events_path, default, hv_overrides,
                                   pidfile=opts.get("pidfile"))
            _COLLECTOR.arm_from_inventory(inventory, categories,
                                          arg_exprs_by_symbol)
            for sym, cat, exprs in adhoc:
                _COLLECTOR.add_adhoc(sym, cat, exprs)

            armed = _COLLECTOR.armed_summary()
            _COLLECTOR.emit({
                "category": "collector",
                "event": "collector-installed",
                "symbol": None,
                "armedCount": len(armed["armed"]),
                "armed": armed["armed"][:120],
                "unbound": armed["unbound"][:60],
                "categories": categories or "all",
                "defaultCap": default,
                "hvOverrides": hv_overrides,
                "thread": _thread_info(), "arguments": {}, "objects": {},
                "stack": [],
            })
            print("movian-lifecycle-start: armed %d probes (%d unbound), "
                  "events -> %s" % (len(armed["armed"]), len(armed["unbound"]),
                                    events_path))

    MovianLifecycleStart()

    def _at_gdb_exit(event):
        if _COLLECTOR is not None:
            _COLLECTOR.close()
    try:
        gdb.events.before_prompt.connect(_at_gdb_exit)
    except Exception:
        pass


# ###########################################################################
#  Host-launch orchestrator (CPython, no gdb import)
# ###########################################################################
STATE_ROOT = "/tmp/mdev"
PORT_RE = re.compile(r"http-server: Listening on port (\d+)")
LOG_TS_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})\.(\d{3}): ")


def _e(s):
    return s.encode(errors="replace") if isinstance(s, str) else s


def pid_is_movian(pid):
    try:
        with open("/proc/%d/comm" % pid) as f:
            return f.read().strip() == "movian"
    except OSError:
        return False


def owns_pid(pid, persistent):
    if not pid_is_movian(pid):
        return False
    try:
        cmdline = open("/proc/%d/cmdline" % pid, "rb").read()
    except OSError:
        return False
    return _e(str(persistent)) in cmdline.split(b"\x00")


def find_inferior_pid(persistent):
    """Return the live movian pid launched against `persistent`."""
    try:
        pids = [int(n) for n in os.listdir("/proc") if n.isdigit()]
    except OSError:
        return None
    for pid in pids:
        if owns_pid(pid, persistent):
            return pid
    return None


def kill_owned(pid, persistent, timeout=8.0):
    """mdev's kill_owned_pid algorithm: SIGTERM, wait, SIGKILL.  Returns one of
    stopped-clean / killed-after-timeout / not-owned / still-alive."""
    if not owns_pid(pid, persistent):
        return "not-owned"
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return "stopped-clean"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not owns_pid(pid, persistent):
            return "stopped-clean"
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return "killed-after-timeout"
    time.sleep(0.3)
    return "killed-after-timeout" if not owns_pid(pid, persistent) \
        else "still-alive"


def cache_log_path(cache_dir):
    return os.path.join(cache_dir, "log", "movian-0.log")


def parse_port_and_startup(cache_dir, timeout=40.0):
    """Poll <cache>/log/movian-0.log for the HTTP port line; return
    (port, startup_ms) where startup_ms is the inferior's own
    first-log-line -> port-line duration (exec-to-HTTP-ready, gdb-independent).
    The log is written by movian via unbuffered write(), so the line appears as
    soon as the http server binds."""
    path = cache_log_path(cache_dir)
    deadline = time.monotonic() + timeout
    first_ts_ms = None
    port = None
    port_ts_ms = None
    pos = 0
    while time.monotonic() < deadline:
        try:
            with open(path, errors="replace") as f:
                f.seek(pos)
                chunk = f.read()
                pos = f.tell()
        except OSError:
            time.sleep(0.1)
            continue
        for line in chunk.splitlines():
            m = LOG_TS_RE.match(line)
            if m:
                ts = ((int(m.group(1)) * 3600 + int(m.group(2)) * 60 +
                       int(m.group(3))) * 1000 + int(m.group(4)))
                if first_ts_ms is None:
                    first_ts_ms = ts
                pm = PORT_RE.search(line)
                if pm:
                    port = int(pm.group(1))
                    port_ts_ms = ts
        if port is not None:
            base = first_ts_ms if first_ts_ms is not None else port_ts_ms
            return port, (port_ts_ms - base)
        time.sleep(0.1)
    return None, None


def instance_state(name):
    return os.path.join(STATE_ROOT, name)


def write_state(name, persistent, cache, pid, port, log_path, argv, extra=None):
    d = instance_state(name)
    for sub in ("", "persistent", "cache", "shots"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    state = {
        "name": name,
        "pid": pid,
        "port": port,
        "log": log_path,
        "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "argv": argv,
    }
    if extra:
        state.update(extra)
    tmp = os.path.join(d, "state.json.tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, os.path.join(d, "state.json"))
    return state


def build_movian_argv(binary, persistent, cache, start_url, extra=None):
    argv = [binary, "-d", "--disable-upgrades",
            "--persistent", persistent, "--cache", cache]
    if extra:
        argv += list(extra)
    if start_url:
        argv.append(start_url)
    return argv


def _gdb_cmdfile(self_path, events_path, inventory_path, state_dir,
                 persistent, cache, binary, start_url, categories, cap, hv_cap,
                 probes, mode):
    """Build a temp gdb command file.  For gdb-collector mode it sources this
    module and arms the collector before `run` so probes bind at exec."""
    movian_args = build_movian_argv(binary, persistent, cache, start_url)
    pidfile = os.path.join(state_dir, "inferior.pid")
    lines = [
        "set pagination off",
        "set confirm off",
        "set print pretty off",
        "file " + os.path.abspath(binary),
        "set breakpoint pending on",
        # Let shutdown/termination signals flow to movian (no gdb stop).
        "handle SIGTERM nostop noprint pass",
        "handle SIGINT nostop noprint pass",
        "handle SIGPIPE nostop noprint pass",
        "handle SIGHUP nostop noprint pass",
    ]
    if mode == "gdb-collector":
        cmd = ("source %(self)s\n"
               "movian-lifecycle-start --events %(ev)s --inventory %(inv)s "
               "--pidfile %(pf)s" % {
                   "self": self_path, "ev": events_path,
                   "inv": inventory_path, "pf": pidfile})
        if categories:
            cmd += " --categories %s" % categories
        if cap:
            cmd += " --cap %d" % cap
        if hv_cap:
            cmd += " --hv-cap %d" % hv_cap
        if probes:
            cmd += " --probe %s" % probes
        lines.append(cmd)
    lines.append("set args " + " ".join('"%s"' % a.replace('"', '\\"')
                                        for a in movian_args[1:]))
    lines.append("run")
    fd, path = tempfile.mkstemp(prefix="movian_lifecycle_", suffix=".gdb")
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    return path, movian_args


def validate_events(events_path):
    required = {"seq", "monotonicNs", "category", "event", "symbol",
                "thread", "arguments", "objects", "stack"}
    n = 0
    bad = []
    cats = {}
    sample = None
    with open(events_path) as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            n += 1
            try:
                obj = json.loads(line)
            except Exception as exc:
                bad.append({"line": ln, "error": "json: %s" % exc})
                continue
            missing = required - set(obj.keys())
            if missing:
                bad.append({"line": ln, "error": "missing %s" % sorted(missing)})
            th = obj.get("thread") or {}
            for k in ("gdbId", "name", "osTid"):
                if k not in th:
                    bad.append({"line": ln, "error": "thread.%s missing" % k})
            cats[obj.get("category")] = cats.get(obj.get("category"), 0) + 1
            if sample is None:
                sample = obj
    return {"lines": n, "bad": bad[:20], "categories": cats, "sample": sample}


def wait_http_ready(port, timeout=12.0, t0=None):
    """Poll the HTTP control endpoint until it actually serves (200).  The
    'Listening on port' log line means the socket called listen(); this proves
    the server is past accept() and really ready."""
    import urllib.request
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            req = urllib.request.urlopen(
                "http://127.0.0.1:%d/api/prop/global" % port, timeout=1)
            if req.status == 200:
                return True, int((time.monotonic() - (t0 or time.monotonic()))
                                 * 1000)
            last = "status %d" % req.status
        except Exception as exc:
            last = repr(exc)
        time.sleep(0.1)
    return False, last

def run_launch(args):
    name = args.name
    persistent = args.persistent or os.path.join(instance_state(name),
                                                 "persistent")
    cache = args.cache or os.path.join(instance_state(name), "cache")
    state_dir = instance_state(name)
    binary = args.binary
    start_url = args.start_url
    self_path = os.path.abspath(__file__)
    inventory_path = args.inventory or os.path.join(
        os.path.dirname(self_path), "inventory.json")

    # Fresh isolated dirs per run.
    for p in (persistent, cache, os.path.join(cache, "log")):
        os.makedirs(p, exist_ok=True)
    # Pre-flight: stop any stale movian this exact persistent path still owns
    # (safe -- kill_owned only ever signals comm==movian + exact-path matches)
    # and clear the cache log so the port-line we read is provably from THIS
    # run, not a leftover from a same-name rerun.
    stale = find_inferior_pid(persistent)
    if stale is not None:
        kill_owned(stale, persistent, timeout=4.0)
    _log_dir = os.path.join(cache, "log")
    if os.path.isdir(_log_dir):
        for _fn in os.listdir(_log_dir):
            try:
                os.unlink(os.path.join(_log_dir, _fn))
            except OSError:
                pass

    events_path = args.events or os.path.join(state_dir, "events.jsonl")
    log_path = os.path.join(state_dir, "movian.log")

    env = dict(os.environ)
    env["DISPLAY"] = args.display
    env["WAYLAND_DISPLAY"] = args.wayland
    env["MOVIAN_MDEV_ALLOW_GDB"] = "1"

    summary = {"mode": args.mode, "name": name, "events": events_path,
               "log": log_path, "persistent": persistent, "cache": cache}

    t0 = time.monotonic()
    proc = None
    cmdfile = None
    if args.mode == "plain":
        movian_args = build_movian_argv(binary, persistent, cache, start_url)
        log_fd = open(log_path, "wb", buffering=0)
        log_fd.truncate(0)
        proc = subprocess.Popen(movian_args, cwd=os.getcwd(),
                                env=env, stdout=log_fd,
                                stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL,
                                start_new_session=True)
        log_fd.close()
    else:  # gdb-bare or gdb-collector
        cmdfile, movian_args = _gdb_cmdfile(
            self_path, events_path, inventory_path, state_dir, persistent,
            cache, binary, start_url, args.categories, args.cap, args.hv_cap,
            args.probe, args.mode)
        log_fd = open(log_path, "wb", buffering=0)
        log_fd.truncate(0)
        gdb_argv = [args.gdb, "-q", "-batch", "-x", cmdfile]
        proc = subprocess.Popen(gdb_argv, cwd=os.getcwd(), env=env,
                                stdout=log_fd, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL,
                                start_new_session=True)
        log_fd.close()
    summary["argv"] = movian_args

    port, startup_ms = parse_port_and_startup(cache, timeout=args.startup_timeout)
    summary["port"] = port
    summary["startupMsInternal"] = startup_ms
    summary["externalWallMs"] = int((time.monotonic() - t0) * 1000)

    pid = find_inferior_pid(persistent)
    summary["inferiorPid"] = pid

    if port is not None and pid is not None:
        write_state(name, persistent, cache, pid, port, log_path, movian_args,
                    extra={"collector": args.mode == "gdb-collector",
                           "events": events_path})

    http_ok = None
    http_ready_ms = None
    if port is not None:
        http_ok, detail = wait_http_ready(port, timeout=12.0, t0=t0)
        if not http_ok:
            summary["httpProbeError"] = str(detail)
        else:
            http_ready_ms = detail
    summary["httpReady"] = http_ok
    summary["httpReadyMs"] = http_ready_ms

    if pid is not None:
        try:
            comm = open("/proc/%d/comm" % pid).read().strip()
            raw = open("/proc/%d/cmdline" % pid, "rb").read()
            cmdline = raw.replace(b"\x00", b" ").decode(errors="replace").strip()
            pers_in = _e(str(persistent)) in raw.split(b"\x00")
        except OSError:
            comm = cmdline = None
            pers_in = False
        summary["ownership"] = {
            "pid": pid, "comm": comm,
            "commIsMovian": comm == "movian",
            "persistentInCmdline": pers_in,
            "ownsPid": owns_pid(pid, persistent),
            "cmdline": cmdline,
        }

    if args.leave_running:
        summary["stopOutcome"] = "left-running"
    else:
        if args.mode == "gdb-collector" and port is not None and args.duration > 0:
            time.sleep(args.duration)
        stop_outcome = "no-inferior"
        if pid is not None:
            stop_outcome = kill_owned(pid, persistent, timeout=8.0)
        summary["stopOutcome"] = stop_outcome
        if proc is not None:
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
    if cmdfile:
        try:
            os.unlink(cmdfile)
        except OSError:
            pass

    if (not args.leave_running and args.mode == "gdb-collector"
            and os.path.exists(events_path)):
        summary["jsonl"] = validate_events(events_path)

    print("MOVIAN_LIFECYCLE_SUMMARY " + json.dumps(summary, sort_keys=True))
    return 0

def build_argparser():
    import argparse
    p = argparse.ArgumentParser(
        prog="movian_lifecycle.py",
        description="movian GDB lifecycle collector (issue #144)")
    sub = p.add_subparsers(dest="cmd")
    lp = sub.add_parser("launch", help="host-launch the inferior under GDB "
                        "and collect lifecycle events")
    lp.add_argument("--name", default="issue144")
    lp.add_argument("--mode", choices=["plain", "gdb-bare", "gdb-collector"],
                    default="gdb-collector")
    lp.add_argument("--binary", default="./build.debug/movian")
    lp.add_argument("--persistent")
    lp.add_argument("--cache")
    lp.add_argument("--start-url", default="page:home")
    lp.add_argument("--inventory")
    lp.add_argument("--events")
    lp.add_argument("--categories", default=None,
                    help="comma-separated inventory categories to arm "
                         "(default: all)")
    lp.add_argument("--cap", type=int, default=0)
    lp.add_argument("--hv-cap", type=int, default=0)
    lp.add_argument("--probe", default="",
                    help="adhoc probe: symbol:category:expr;expr "
                         "(use | to separate several)")
    lp.add_argument("--duration", type=float, default=1.0,
                    help="extra collection seconds after HTTP-ready "
                         "(collector mode)")
    lp.add_argument("--leave-running", action="store_true",
                    help="record state and return without stopping the "
                         "inferior (lets e.g. `mdev stop` prove ownership)")
    lp.add_argument("--startup-timeout", type=float, default=40.0)
    lp.add_argument("--gdb", default="gdb")
    lp.add_argument("--display", default=os.environ.get("DISPLAY", ":0"))
    lp.add_argument("--wayland",
                    default=os.environ.get("WAYLAND_DISPLAY", "wayland-0"))
    lp.set_defaults(func=run_launch)
    vp = sub.add_parser("validate", help="validate a JSONL event log")
    vp.add_argument("events")
    vp.set_defaults(func=lambda a: (print(json.dumps(
        validate_events(a.events), sort_keys=True)), 0)[1])
    return p


def main(argv=None):
    p = build_argparser()
    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        p.print_help()
        return 2
    return args.func(args) or 0


if __name__ == "__main__" and not _HAVE_GDB:
    sys.exit(main())
