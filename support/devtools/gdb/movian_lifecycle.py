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
import shlex
import subprocess
import sys
import time
import threading
import tempfile
import uuid

# ---------------------------------------------------------------------------
# Are we running inside GDB?  CPython test discovery can expose an unrelated
# namespace package named ``gdb``; require the native API, not import success.
# ---------------------------------------------------------------------------
try:
    import gdb  # type: ignore
    _HAVE_GDB = all(hasattr(gdb, name)
                    for name in ("Breakpoint", "Command", "execute"))
    if not _HAVE_GDB:
        gdb = None  # type: ignore
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

WEDGE_PROTOCOL = "movian-lifecycle-wedge-v1"
WEDGE_EVENT = "wedge-capture"


def validate_wedge_event(event):
    """Return deterministic schema errors for one same-session wedge event."""
    errors = []
    if event.get("category") != "wedge":
        errors.append("category must be wedge")
    if event.get("event") != WEDGE_EVENT:
        errors.append("event must be %s" % WEDGE_EVENT)
    for key in ("trigger", "classification", "classificationDetail"):
        if not isinstance(event.get(key), str) or not event[key]:
            errors.append("%s must be a non-empty string" % key)

    correlation = event.get("correlation")
    if not isinstance(correlation, dict):
        errors.append("correlation must be an object")
    else:
        for key in ("subsystem", "resource"):
            if not isinstance(correlation.get(key), str) or \
                    not correlation[key]:
                errors.append("correlation.%s must be a non-empty string" %
                              key)

    eject = event.get("emergencyEject")
    if not isinstance(eject, dict):
        errors.append("emergencyEject must be an object")
    else:
        eject_state = eject.get("state")
        if not isinstance(eject_state, str) or \
                eject_state not in EMERGENCY_EJECT_STATES:
            errors.append("emergencyEject.state must be one of %s" %
                          ", ".join(EMERGENCY_EJECT_STATES))
        for key in ("observed", "requested", "armed", "fired"):
            if not isinstance(eject.get(key), bool):
                errors.append("emergencyEject.%s must be boolean" % key)
        if isinstance(eject_state, str) and \
                eject_state in EMERGENCY_EJECT_STATES:
            expected = {
                "unobserved": (False, False, False, False),
                "not-requested": (True, False, False, False),
                "requested": (True, True, False, False),
                "armed": (True, True, True, False),
                "fired": (True, True, True, True),
            }[eject_state]
            for key, exp in zip(("observed", "requested", "armed", "fired"),
                                expected):
                if eject.get(key) != exp:
                    errors.append(
                        "emergencyEject.%s=%s inconsistent with state %s"
                        % (key, eject.get(key), eject_state))

    session = event.get("session")
    if not isinstance(session, dict):
        errors.append("session must be an object")
    else:
        if not isinstance(session.get("id"), str) or not session["id"]:
            errors.append("session.id must be a non-empty string")
        for key in ("gdbPid", "inferiorPid"):
            if not isinstance(session.get(key), int) or session[key] <= 0:
                errors.append("session.%s must be a positive integer" % key)
        if session.get("attachedAtLaunch") is not True:
            errors.append("session.attachedAtLaunch must be true")

    threads = event.get("remainingThreads")
    if not isinstance(threads, list):
        errors.append("remainingThreads must be an array")
        threads = []
    else:
        for index, thread in enumerate(threads):
            if not isinstance(thread, dict):
                errors.append("remainingThreads[%d] must be an object" % index)
                continue
            for key in ("gdbId", "name", "osTid"):
                if key not in thread:
                    errors.append("remainingThreads[%d].%s missing" %
                                  (index, key))

    capture = event.get("capture")
    if not isinstance(capture, dict):
        errors.append("capture must be an object")
    else:
        if capture.get("status") not in ("success", "error", "timeout"):
            errors.append("capture.status is invalid")
        if not isinstance(capture.get("dumpPath"), str) or \
                not capture["dumpPath"]:
            errors.append("capture.dumpPath must be a non-empty string")
        for key in ("threadCount", "frameCount"):
            if not isinstance(capture.get(key), int) or capture[key] < 0:
                errors.append("capture.%s must be a non-negative integer" %
                              key)
        if not isinstance(capture.get("movianFramePresent"), bool):
            errors.append("capture.movianFramePresent must be boolean")
        if isinstance(threads, list) and \
                isinstance(capture.get("threadCount"), int) and \
                capture["threadCount"] != len(threads):
            errors.append("capture.threadCount does not match remainingThreads")
    return errors


EMERGENCY_EJECT_STATES = ("unobserved", "not-requested", "requested",
                          "armed", "fired")
# Mandatory symbols whose presence in the armed set determines whether the
# emergency-eject tracker observes the inferior lifecycle.  All three must
# be selected and bound before observe() is called.
EMERGENCY_EJECT_MANDATORY = frozenset((
    "app_shutdown", "shutdown_eject", "arch_exit",
))


def is_eject_mandatory(symbol):
    """True if *symbol* is one of the mandatory eject probes."""
    return symbol in EMERGENCY_EJECT_MANDATORY


def all_eject_mandatory_bound(armed_bps):
    """True if every mandatory eject symbol is bound in *armed_bps*.

    *armed_bps* is an iterable of objects with ``.symbol`` and ``.bound``
    attributes (e.g. LifecycleBP instances).  Used to decide whether
    observe() fires after the inventory loop -- state stays
    ``"unobserved"`` otherwise.
    """
    armed_syms = {bp.symbol for bp in armed_bps if bp.bound}
    return EMERGENCY_EJECT_MANDATORY <= armed_syms


def rate_limit_should_disable(bp, cat):
    """Whether *bp* is disabled when category *cat* reaches its event cap.

    Mandatory eject probes are immune: rate-limiting must never suppress a
    shutdown transition, so the emergency-eject chain stays observable even
    after an ordinary category is capped.  An unbound probe is never armed so
    cannot fire, and only breakpoints in the rate-limited category qualify.

    Pure (no GDB dependency) so the immunity contract is unit-testable from
    CPython.  *bp* needs ``.category``, ``.bound`` and ``.symbol`` attributes.
    """
    if bp.category != cat or not bp.bound:
        return False
    return not is_eject_mandatory(bp.symbol)


_EMERGENCY_EJECT_TRANSITIONS = {
    "unobserved": "not-requested",
    "not-requested": "requested",
    "requested": "armed",
    "armed": "fired",
}


class EmergencyEjectTracker:
    """Pure state machine for the emergency-eject lifecycle.

    The collector observes this from the launch-attached inferior; the host
    never supplies a constant.  Allowed transitions form a strict linear
    chain: unobserved -> not-requested -> requested -> armed -> fired.
    """

    __slots__ = ("_state", "_requested", "_armed", "_fired",
                 "_eject_tid")

    def __init__(self):
        self._state = "unobserved"
        self._requested = False
        self._armed = False
        self._fired = False
        self._eject_tid = None

    @property
    def state(self):
        return self._state

    def observe(self):
        """Called when core-init probes are bound; marks as not-requested."""
        self._advance("not-requested")

    def on_request(self):
        """app_shutdown entered."""
        self._advance("requested")

    def on_arm(self, tid):
        """shutdown_eject entered; record the OS TID of the eject thread."""
        if self._advance("armed"):
            self._eject_tid = tid

    def on_exit(self, tid):
        """arch_exit entered; fires only if called from the eject thread."""
        if self._state == "armed" and self._eject_tid is not None \
                and tid == self._eject_tid:
            self._advance("fired")

    def _advance(self, target):
        expected = _EMERGENCY_EJECT_TRANSITIONS.get(self._state)
        if expected != target:
            return False
        self._state = target
        self._requested = self._state in ("requested", "armed", "fired")
        self._armed = self._state in ("armed", "fired")
        self._fired = self._state == "fired"
        return True

    def snapshot(self):
        """Return the public schema dict."""
        observed = self._state != "unobserved"
        return {
            "state": self._state,
            "observed": observed,
            "requested": self._requested,
            "armed": self._armed,
            "fired": self._fired,
        }


_COLLECTOR = None  # type: ignore[assignment]


# ###########################################################################
#  Collector core (GDB only)
# ###########################################################################
if _HAVE_GDB:

    def _monotonic_ns():
        return time.monotonic_ns()

    def _thread_info(thread=None):
        info = {"gdbId": None, "name": None, "osTid": None}
        try:
            thr = thread if thread is not None else gdb.selected_thread()
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

    def _inferior_info():
        info = {"number": None, "pid": None}
        try:
            inferior = gdb.selected_inferior()
            if inferior is not None:
                info["number"] = getattr(inferior, "num", None)
                pid = getattr(inferior, "pid", None)
                info["pid"] = int(pid) if pid else None
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

        def __init__(self, spec, category, symbol, arg_exprs=None,
                     event="enter"):
            super().__init__(spec, internal=True)
            self.silent = True
            self.category = category
            self.symbol = symbol
            self.event = event if event in ("enter", "create", "destroy") \
                else "enter"
            self.observation = "call-entry"
            self.arg_exprs = arg_exprs or []
            try:
                self.bound = bool(self.locations)
            except Exception:
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
            self._closed = False
            self._inferior_exited = False
            self._exit_hook = None
            self._thread_exit_hook = None
            self._install_exit_hook()
            self._install_thread_exit_hook()
            self._eject_tracker = EmergencyEjectTracker()

        def _cap_for(self, cat):
            if cat in self._caps:
                return self._caps[cat]
            if cat in HIGH_VOLUME_CAPS:
                return HIGH_VOLUME_CAPS[cat]
            return self._default_cap

        def _all_eject_mandatory_bound(self):
            """True only if every mandatory eject symbol is in the armed set
            and bound.  Delegates to the module-level pure predicate."""
            return all_eject_mandatory_bound(self._armed)

        def _is_eject_mandatory(self, symbol):
            """True if *symbol* is one of the mandatory eject probes."""
            return is_eject_mandatory(symbol)

        def _update_eject_tracker(self, bp):
            """Advance the emergency-eject tracker for a mandatory symbol hit.

            Called before the rate-limit cap check so mandatory transitions
            are never suppressed by category-level disabling.
            """
            sym = bp.symbol
            if sym == "app_shutdown":
                self._eject_tracker.on_request()
            elif sym == "shutdown_eject":
                try:
                    thr = gdb.selected_thread()
                    tid = thr.ptid[1] if thr is not None else None
                    # Zero is not a valid Linux TID; treat as unavailable.
                    if tid == 0:
                        tid = None
                except Exception:
                    tid = None
                self._eject_tracker.on_arm(tid)
            elif sym == "arch_exit":
                try:
                    thr = gdb.selected_thread()
                    tid = thr.ptid[1] if thr is not None else None
                    # Zero is not a valid Linux TID; treat as unavailable.
                    if tid == 0:
                        tid = None
                except Exception:
                    tid = None
                self._eject_tracker.on_exit(tid)

        # -- event emission --------------------------------------------------
        def emit(self, event):
            if getattr(self, "_closed", False):
                return
            self._seq += 1
            event["seq"] = self._seq
            event.setdefault("monotonicNs", _monotonic_ns())
            try:
                self._fh.write(json.dumps(event, separators=(",", ":")) + "\n")
                self._fh.flush()
            except Exception:
                pass

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

            # Update the emergency-eject tracker BEFORE the rate-limit cap
            # check so mandatory eject transitions are never lost to
            # category-level suppression.
            self._update_eject_tracker(bp)

            cap = self._cap_for(cat)
            if cap and n > cap:
                # Disable non-mandatory probes in this category (zero
                # further traps) and emit a single rate-limit summary,
                # once.  Mandatory eject probes stay enabled so shutdown
                # transitions are always observed.
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
                       if isinstance(v, str) and v.startswith("0x")
                       and v != "0x0"}
            event_data = {
                "category": cat,
                "event": bp.event,
                "symbol": bp.symbol,
                "observation": bp.observation,
                "thread": _thread_info(),
                "inferior": _inferior_info(),
                "arguments": arguments,
                "objects": objects,
                "stack": _capture_stack(6),
            }
            if is_eject_mandatory(bp.symbol):
                event_data["emergencyEject"] = self._eject_tracker.snapshot()
            self.emit(event_data)

        def _disable_category(self, cat):
            for bp in self._armed:
                # Mandatory eject probes are immune (see
                # rate_limit_should_disable) so shutdown transitions stay
                # observable after an ordinary category is capped.
                if not rate_limit_should_disable(bp, cat):
                    continue
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
                self._inferior_exited = True
                code = getattr(event, "exit_code", None)
                self.emit({"category": "collector", "event": "inferior-exited",
                           "symbol": None, "exitCode": code,
                           "thread": _thread_info(),
                           "inferior": _inferior_info(),
                           "arguments": {}, "objects": {}, "stack": []})
                self.close()
            self._exit_hook = _on_exit
            try:
                gdb.events.exited.connect(_on_exit)
            except Exception:
                self._exit_hook = None

        def _install_thread_exit_hook(self):
            def _on_thread_exit(event):
                thread = getattr(event, "inferior_thread", None)
                self.emit({
                    "category": "thread",
                    "event": "thread-exit",
                    "symbol": None,
                    "observation": "gdb-thread-event",
                    "thread": _thread_info(thread),
                    "inferior": _inferior_info(),
                    "arguments": {},
                    "objects": {},
                    "stack": [],
                })
            self._thread_exit_hook = _on_thread_exit
            try:
                gdb.events.thread_exited.connect(_on_thread_exit)
            except Exception:
                self._thread_exit_hook = None

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
                    bp = LifecycleBP(
                        spec, cat, entry["symbol"], arg_exprs,
                        entry.get("event", "enter"))
                    if bp.bound:
                        self._armed.append(bp)
                    else:
                        self._unbound.append("%s (unresolved in loaded binary)" %
                                             spec)
                        bp.delete()
                except Exception as exc:
                    self._unbound.append("%s (%s)" % (spec, exc))
            # Decide observation only after the full inventory loop: state
            # remains unobserved unless ALL three mandatory eject symbols
            # are selected and bound.
            if self._all_eject_mandatory_bound():
                self._eject_tracker.observe()

        def add_adhoc(self, sym, cat, exprs):
            try:
                bp = LifecycleBP(sym, cat, sym, exprs)
                if bp.bound:
                    self._armed.append(bp)
                else:
                    self._unbound.append(
                        "%s (unresolved in loaded binary)" % sym)
                    bp.delete()
            except Exception as exc:
                self._unbound.append("%s (%s)" % (sym, exc))

        def close(self):
            if getattr(self, "_closed", False):
                return
            try:
                self.emit({"category": "collector",
                           "event": "collector-final",
                           "symbol": None,
                           "counts": self._counts,
                           "suppressed": self._suppressed,
                           "errors": self._errors,
                           "emergencyEject": self._eject_tracker.snapshot(),
                           "thread": _thread_info(),
                           "inferior": _inferior_info(),
                           "arguments": {},
                           "objects": {},
                           "stack": []})
            except Exception:
                pass
            self._closed = True
            if self._exit_hook is not None:
                try:
                    gdb.events.exited.disconnect(self._exit_hook)
                except Exception:
                    pass
                self._exit_hook = None
            if self._thread_exit_hook is not None:
                try:
                    gdb.events.thread_exited.disconnect(self._thread_exit_hook)
                except Exception:
                    pass
                self._thread_exit_hook = None
            if not self._inferior_exited:
                for bp in self._armed:
                    try:
                        bp.delete()
                    except Exception:
                        pass
            self._armed = []
            try:
                self._fh.flush()
                self._fh.close()
            except Exception:
                pass

    def _all_thread_info():
        result = []
        try:
            threads = list(gdb.selected_inferior().threads())
        except Exception:
            return result
        for thr in sorted(threads,
                          key=lambda item: getattr(item, "global_num", 0)):
            info = {"gdbId": getattr(thr, "global_num", None),
                    "name": getattr(thr, "name", None), "osTid": None}
            try:
                ptid = tuple(thr.ptid)
                if len(ptid) > 1 and ptid[1]:
                    info["osTid"] = ptid[1]
                elif len(ptid) > 2 and ptid[2]:
                    info["osTid"] = ptid[2]
                elif ptid:
                    info["osTid"] = ptid[0] or None
            except Exception:
                pass
            if not info["name"] and info["osTid"]:
                try:
                    with open("/proc/%d/comm" % info["osTid"]) as f:
                        info["name"] = f.read().strip()
                except OSError:
                    pass
            result.append(info)
        return result

    def _atomic_json(path, payload):
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        temporary = "%s.tmp.%d" % (path, os.getpid())
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(temporary, path)

    def _load_json(path):
        try:
            with open(path, encoding="utf-8") as f:
                value = json.load(f)
            return value if isinstance(value, dict) else None
        except (OSError, ValueError):
            return None

    def _capture_wedge_request(request_path, response_path, session_id,
                               request):
        gdb_pid = os.getpid()
        try:
            inferior_pid = int(gdb.selected_inferior().pid)
        except Exception:
            inferior_pid = 0
        request_id = request.get("requestId")
        response = {
            "protocol": WEDGE_PROTOCOL,
            "sessionId": session_id,
            "requestId": request_id,
            "gdbPid": gdb_pid,
            "inferiorPid": inferior_pid,
            "status": "error",
            "dumpPath": request.get("dumpPath"),
            "threadCount": 0,
            "frameCount": 0,
            "movianFramePresent": False,
            "completedMonotonicNs": _monotonic_ns(),
        }
        errors = []
        if request.get("protocol") != WEDGE_PROTOCOL:
            errors.append("protocol mismatch")
        if request.get("sessionId") != session_id:
            errors.append("session mismatch")
        if request.get("gdbPid") != gdb_pid:
            errors.append("gdb pid mismatch")
        if request.get("inferiorPid") != inferior_pid or inferior_pid <= 0:
            errors.append("inferior pid mismatch")
        if not isinstance(request_id, str) or not request_id:
            errors.append("missing request id")
        for key in ("trigger", "classification", "classificationDetail",
                    "subsystem", "resource"):
            if not isinstance(request.get(key), str) or not request[key]:
                errors.append("missing %s" % key)
        dump_path = request.get("dumpPath")
        state_dir = os.path.realpath(os.path.dirname(request_path))
        if not isinstance(dump_path, str) or not dump_path:
            errors.append("missing dump path")
        else:
            resolved_dump = os.path.realpath(dump_path)
            try:
                confined = os.path.commonpath(
                    (state_dir, resolved_dump)) == state_dir
            except ValueError:
                confined = False
            if not confined:
                errors.append("dump path escapes instance state")

        if errors:
            response["detail"] = "; ".join(errors)
            try:
                _atomic_json(response_path, response)
            except Exception:
                pass
            return response

        # Snapshot the emergency-eject tracker once; used consistently in
        # the dump and the JSONL event (built by the GDB collector, not
        # copied from a host constant).
        if _COLLECTOR is not None:
            eject_snapshot = _COLLECTOR._eject_tracker.snapshot()
        else:
            eject_snapshot = EmergencyEjectTracker().snapshot()
        response["emergencyEject"] = eject_snapshot
        threads = _all_thread_info()
        output = ""
        detail = ""
        try:
            output = gdb.execute("thread apply all bt", to_string=True)
            frame_count = len(re.findall(r"(?m)^#\d+\s", output))
            movian_frame = bool(re.search(
                r"(?m)^#\d+.*(?:/src/|\b(?:main_|glw_|hts_|hc_)\w*)",
                output))
            thread_count = len(threads)
            if thread_count < 2:
                detail = "only %d thread(s) remained" % thread_count
            elif frame_count < 10:
                detail = "captured only %d stack frame(s)" % frame_count
            elif not movian_frame:
                detail = "capture contained no Movian function/source frame"
            else:
                response["status"] = "success"
                detail = "captured %d frames across %d threads" % (
                    frame_count, thread_count)
            response.update({
                "threadCount": thread_count,
                "frameCount": frame_count,
                "movianFramePresent": movian_frame,
            })
        except Exception as exc:
            detail = "thread apply all bt failed: %s: %s" % (
                type(exc).__name__, exc)
            response.update({
                "threadCount": len(threads),
                "frameCount": 0,
                "movianFramePresent": False,
            })

        response["detail"] = detail
        response["completedMonotonicNs"] = _monotonic_ns()
        lines = [
            "capture-status: %s" % response["status"],
            "capture-source: launch-attached-gdb",
            "session-id: %s" % session_id,
            "gdb-pid: %d" % gdb_pid,
            "inferior-pid: %d" % inferior_pid,
            "thread-count: %d" % response["threadCount"],
            "frame-count: %d" % response["frameCount"],
            "movian-frame-present: %s" % str(
                response["movianFramePresent"]).lower(),
            "trigger: %s" % request["trigger"],
            "classification: %s" % request["classification"],
            "subsystem: %s" % request["subsystem"],
            "resource: %s" % request["resource"],
            "emergency-eject-state: %s" % eject_snapshot["state"],
            "detail: %s" % detail,
        ]
        if output:
            lines.extend(("", "gdb-output:", output.rstrip()))
        try:
            with open(dump_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception as exc:
            response["status"] = "error"
            response["detail"] = "could not write dump: %s" % exc
            response["completedMonotonicNs"] = _monotonic_ns()

        event = {
            "category": "wedge",
            "event": WEDGE_EVENT,
            "symbol": None,
            "trigger": request["trigger"],
            "classification": request["classification"],
            "classificationDetail": request["classificationDetail"],
            "classifiedMonotonicNs": request.get("classifiedMonotonicNs"),
            "correlation": {
                "subsystem": request["subsystem"],
                "resource": request["resource"],
            },
            "emergencyEject": eject_snapshot,
            "session": {
                "id": session_id,
                "gdbPid": gdb_pid,
                "inferiorPid": inferior_pid,
                "attachedAtLaunch": True,
            },
            "remainingThreads": threads,
            "capture": {
                "status": response["status"],
                "dumpPath": dump_path,
                "threadCount": response["threadCount"],
                "frameCount": response["frameCount"],
                "movianFramePresent": response["movianFramePresent"],
                "detail": response["detail"],
            },
            "thread": _thread_info(),
            "arguments": {},
            "objects": {},
            "stack": _capture_stack(6),
        }
        schema_errors = validate_wedge_event(event)
        if schema_errors:
            schema_note = "wedge event schema: %s" % \
                "; ".join(schema_errors)
            response["status"] = "error"
            if response.get("detail"):
                response["detail"] = "%s (%s)" % (
                    response["detail"], schema_note)
            else:
                response["detail"] = schema_note
            event["capture"]["status"] = "error"
            event["capture"]["detail"] = response["detail"]
        if _COLLECTOR is not None:
            _COLLECTOR.emit(event)
        try:
            _atomic_json(response_path, response)
        except Exception:
            pass
        return response
    # -- GDB command wiring --------------------------------------------------
    def _parse_opts(arg):
        opts = {}
        toks = shlex.split(arg)
        i = 0
        while i < len(toks):
            token = toks[i]
            if not token.startswith("--"):
                raise ValueError("unexpected argument: %s" % token)
            if i + 1 >= len(toks) or toks[i + 1].startswith("--"):
                raise ValueError("%s requires a value" % token)
            opts[token[2:]] = toks[i + 1]
            i += 2
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
            try:
                opts = _parse_opts(arg)
            except ValueError as exc:
                raise gdb.GdbError("movian-lifecycle-start: %s" % exc)
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

            if _COLLECTOR is not None:
                _COLLECTOR.close()
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

    # -- Wedge-control stop discrimination (#146) ---------------------------
    # The post-run control loop may suppress (resume with `signal 0`) ONLY a
    # SIGSTOP paired with an atomically published, current request whose
    # protocol / session / GDB pid / inferior pid / request id all match THIS
    # session.  Any other stop signal (SIGSEGV / SIGABRT / ...) is delivered
    # with its original signal so the inferior terminates truthfully; no
    # request is consumed for such a stop and no signal-resume tight loop can
    # occur.  `gdb.events.stop` fires for every stop, including the one that
    # makes `run` return, so the recorded signal is current when invoke runs.
    _last_stop_signal = None

    def _record_stop_signal(event):
        global _last_stop_signal
        _last_stop_signal = getattr(event, "stop_signal", None)

    def _current_stop_signal():
        return _last_stop_signal

    def _request_matches_current(request, session_id):
        """True only if `request` is a fully-identified wedge request for this
        exact GDB session and inferior, making it safe to capture and resume."""
        if not isinstance(request, dict):
            return False
        if request.get("protocol") != WEDGE_PROTOCOL:
            return False
        if request.get("sessionId") != session_id:
            return False
        if request.get("gdbPid") != os.getpid():
            return False
        try:
            inferior_pid = int(gdb.selected_inferior().pid)
        except Exception:
            return False
        if request.get("inferiorPid") != inferior_pid or inferior_pid <= 0:
            return False
        request_id = request.get("requestId")
        if not isinstance(request_id, str) or not request_id:
            return False
        return True

    class MovianLifecycleWedgeControl(gdb.Command):  # type: ignore[misc]
        """Capture a pending host request whenever the inferior stops."""

        def __init__(self):
            super().__init__("movian-lifecycle-wedge-control",
                             gdb.COMMAND_DATA)

        def invoke(self, arg, from_tty):
            opts = _parse_opts(arg)
            request_path = opts.get("request")
            response_path = opts.get("response")
            session_id = opts.get("session")
            if not request_path or not response_path or not session_id:
                raise gdb.GdbError(
                    "movian-lifecycle-wedge-control requires request, "
                    "response, and session")
            processed = set()
            while True:
                try:
                    if not gdb.selected_inferior().pid:
                        return
                except Exception:
                    return
                stop_signal = _current_stop_signal()
                request = _load_json(request_path)
                # Only a SIGSTOP paired with a current, fully-identified
                # request may be captured and resumed with `signal 0`.  A
                # stale or mismatched request is never consumed here.
                if stop_signal == "SIGSTOP" and \
                        _request_matches_current(request, session_id):
                    request_id = request.get("requestId")
                    if request_id not in processed:
                        processed.add(request_id)
                        _capture_wedge_request(
                            request_path, response_path, session_id, request)
                        try:
                            if not gdb.selected_inferior().pid:
                                return
                            gdb.execute("signal 0")
                        except Exception:
                            return
                        continue
                    # A later SIGSTOP with the already-consumed request must
                    # not be suppressed: leave the inferior stopped for GDB
                    # exit so the stop signal is preserved.
                    return
                # Any non-SIGSTOP stop (SIGSEGV / SIGABRT / ...) is delivered
                # with its exact original signal so the inferior terminates
                # truthfully; the loop then stops (no resume tight loop, no
                # request consumed for that stop).
                if stop_signal is not None and stop_signal != "SIGSTOP":
                    try:
                        gdb.execute("signal " + stop_signal)
                    except Exception:
                        pass
                    return
                # SIGSTOP without a paired current identity, or an unrecorded
                # stop reason: never suppress without a proven identity and
                # never busy-loop.  Leave the inferior stopped for GDB exit.
                return

    MovianLifecycleWedgeControl()

    def _close_on_gdb_exit(event):
        if _COLLECTOR is not None:
            _COLLECTOR.close()
    try:
        gdb.events.gdb_exiting.connect(_close_on_gdb_exit)
    except Exception:
        pass
    try:
        gdb.events.stop.connect(_record_stop_signal)
    except Exception:
        pass



# ###########################################################################
#  Host-launch orchestrator (CPython, no gdb import)
# ###########################################################################
STATE_ROOT = "/tmp/mdev"
PORT_RE = re.compile(r"http-server: Listening on port (\d+)")
LOG_TS_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})\.(\d{3}): ")



class LaunchInterrupted(Exception):
    """Raised by the host launcher so signal-driven aborts still run cleanup."""

    def __init__(self, signum):
        super().__init__("received signal %d" % signum)
        self.signum = signum

def _e(s):
    return s.encode(errors="replace") if isinstance(s, str) else s


def pid_is_movian(pid):
    try:
        with open("/proc/%d/comm" % pid) as f:
            return f.read().strip() == "movian"
    except OSError:
        return False


def process_start_ticks(pid):
    """Return Linux /proc start ticks, preventing recycled debugger PID use."""
    try:
        with open("/proc/%d/stat" % pid) as f:
            value = f.read()
        fields = value[value.rfind(")") + 2:].split()
        return int(fields[19])
    except (OSError, ValueError, IndexError):
        return None


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
                 probes, mode, extra=None, control=None):
    """Build a temp gdb command file.  For gdb-collector mode it sources this
    module and arms the collector before `run` so probes bind at exec."""
    movian_args = build_movian_argv(binary, persistent, cache, start_url, extra)
    pidfile = os.path.join(state_dir, "inferior.pid")
    lines = [
        "set pagination off",
        "set confirm off",
        "set print pretty off",
        "file " + os.path.abspath(binary),
        "set breakpoint pending on",
        # Cleanup signals flow to Movian. The controller sends SIGSTOP only
        # after exact ownership + TracerPid proof; GDB's default stop is then
        # resumed with `signal 0`, so SIGSTOP never reaches the inferior.
        "handle SIGTERM nostop noprint pass",
        "handle SIGINT nostop noprint pass",
        "handle SIGPIPE nostop noprint pass",
        "handle SIGHUP nostop noprint pass",
    ]
    if mode == "gdb-collector":
        lines.append("source " + self_path)
        cmd = [
            "movian-lifecycle-start",
            "--events", shlex.quote(events_path),
            "--inventory", shlex.quote(inventory_path),
            "--pidfile", shlex.quote(pidfile),
        ]
        if categories:
            cmd += ["--categories", shlex.quote(categories)]
        if cap:
            cmd += ["--cap", str(cap)]
        if hv_cap:
            cmd += ["--hv-cap", str(hv_cap)]
        if probes:
            cmd += ["--probe", shlex.quote(probes)]
        lines.append(" ".join(cmd))
    lines.append("set args " + " ".join(shlex.quote(a)
                                        for a in movian_args[1:]))
    lines.append("run")
    if mode == "gdb-collector" and control:
        command = [
            "movian-lifecycle-wedge-control",
            "--request", shlex.quote(control["requestPath"]),
            "--response", shlex.quote(control["responsePath"]),
            "--session", shlex.quote(control["sessionId"]),
        ]
        lines.append(" ".join(command))
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
    inferior_exit_codes = []
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
            if obj.get("category") == "wedge" and \
                    obj.get("event") == WEDGE_EVENT:
                for error in validate_wedge_event(obj):
                    bad.append({"line": ln, "error": "wedge: %s" % error})
            cats[obj.get("category")] = cats.get(obj.get("category"), 0) + 1
            if obj.get("event") == "inferior-exited":
                inferior_exit_codes.append(obj.get("exitCode"))
            if sample is None:
                sample = obj
    return {"lines": n, "bad": bad[:20], "categories": cats,
            "sample": sample, "inferiorExitCodes": inferior_exit_codes}


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

def classify_run(summary, mode, leave_running):
    """Pure success contract for a launch summary -> (ok, failureReasons).

    All modes require: a parsed HTTP port, an inferior PID, exact ownership
    (comm==movian + exact persistent path), HTTP-ready, and a valid cleanup
    outcome (stopped-clean when not leave-running; left-running only with
    proven ownership). gdb-collector additionally requires non-empty JSONL
    with zero validation errors. Deterministic: same summary -> same reasons.
    """
    reasons = []
    own = summary.get("ownership")
    own = own if isinstance(own, dict) else {}
    if summary.get("port") is None:
        reasons.append("no-http-port")
    if summary.get("inferiorPid") is None:
        reasons.append("no-inferior-pid")
    if not own.get("ownsPid"):
        reasons.append("ownership-not-proven")
    if summary.get("httpReady") is not True:
        reasons.append("http-not-ready")
    early_exit = summary.get("inferiorExitedBeforeDuration") is True
    expect_natural_exit = summary.get("expectNaturalExit") is True
    exit_codes = (summary.get("jsonl") or {}).get("inferiorExitCodes") or []
    clean_natural_exit = (
        early_exit
        and expect_natural_exit
        and summary.get("gdbReturnCode") == 0
        and bool(exit_codes)
        and all(code == 0 for code in exit_codes)
    )
    if early_exit and not expect_natural_exit:
        reasons.append("unexpected-inferior-exit")
    if expect_natural_exit and not early_exit:
        reasons.append("expected-natural-exit-not-observed")
    if early_exit and expect_natural_exit and not clean_natural_exit:
        reasons.append("natural-exit-not-clean")
    if leave_running:
        if not (own.get("ownsPid") and summary.get("inferiorPid") is not None):
            reasons.append("leave-running-without-ownership")
        if summary.get("finalOwnedRemains") is not True:
            reasons.append("leave-running-inferior-not-live")
    else:
        if summary.get("finalOwnedRemains"):
            reasons.append("orphan-inferior-after-cleanup")
        if (summary.get("inferiorPid") is not None
                and summary.get("stopOutcome") != "stopped-clean"
                and not clean_natural_exit):
            reasons.append("cleanup-not-clean:%s" % summary.get("stopOutcome"))
        if summary.get("gdbForceKilled"):
            reasons.append("gdb-force-killed")
    if mode == "gdb-collector":
        if summary.get("collectorControlReady") is not True:
            reasons.append("collector-control-not-ready")
        j = summary.get("jsonl") or {}
        if not j.get("lines"):
            reasons.append("empty-jsonl")
        if j.get("bad"):
            reasons.append("jsonl-validation-errors:%d" % len(j["bad"]))
    if not reasons:
        summary["status"] = "PASS"
    elif summary.get("wedge") or summary.get("wedgeClassification"):
        summary["status"] = "WEDGE"
    elif summary.get("timedOut") or any("timeout" in reason for reason in reasons):
        summary["status"] = "TIMEOUT"
    elif any(
            reason.startswith(("jsonl-", "collector-"))
            or reason in ("empty-jsonl", "collector-control-not-ready")
            for reason in reasons):
        summary["status"] = "COLLECTOR_ERROR"
    elif any(reason in {"no-http-port", "http-not-ready", "no-inferior-pid"}
             for reason in reasons):
        summary["status"] = "INFRA_ERROR"
    else:
        summary["status"] = "FAIL"
    return (len(reasons) == 0), reasons


def cleanup_owned(persistent, proc, pid, leave_running, gdb_timeout=10.0):
    """Stop the owned inferior and the GDB process, then run a final
    exact-persistent-path sweep that kills only an inferior this profile still
    owns. Never signals a foreign process. Returns outcome fields for the
    summary; finalOwnedRemains is the hard no-orphan gate."""
    out = {"stopOutcome": "no-inferior", "finalOwnedRemains": False,
           "gdbReturnCode": None, "gdbForceKilled": False}
    if leave_running:
        out["stopOutcome"] = "left-running"
        out["finalOwnedRemains"] = find_inferior_pid(persistent) is not None
        return out
    if pid is not None:
        out["stopOutcome"] = kill_owned(pid, persistent, timeout=8.0)
    if proc is not None:
        try:
            proc.wait(timeout=gdb_timeout)
        except subprocess.TimeoutExpired:
            out["gdbForceKilled"] = True
            proc.kill()
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        out["gdbReturnCode"] = proc.returncode
    # Final exact-path sweep: if the GDB process had to be force-killed before
    # it forwarded the signal (or anything else slipped), reap only our own.
    leftover = find_inferior_pid(persistent)
    if leftover is not None:
        out["finalScanKill"] = kill_owned(leftover, persistent, timeout=5.0)
    out["finalOwnedRemains"] = find_inferior_pid(persistent) is not None
    return out


def _build_gdb_argv(gdb_path, cmdfile):
    """Build the GDB argument vector for the collector session.

    ``--nx`` suppresses system (``/etc/gdb/gdbinit``) and user
    (``~/.gdbinit``) init files so foreign Python hooks or redefined
    commands cannot interfere with the collector's identity proof,
    wedge-control, and ordering guarantees.
    """
    return [gdb_path, "--nx", "-q", "-batch", "-x", cmdfile]

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

    events_path = args.events or os.path.join(state_dir, "events.jsonl")
    log_path = os.path.join(state_dir, "movian.log")
    summary = {"mode": args.mode, "name": name, "events": events_path,
               "log": log_path, "persistent": persistent, "cache": cache,
               "expectNaturalExit": args.expect_natural_exit}
    control = None
    if args.mode == "gdb-collector":
        control = {
            "protocol": WEDGE_PROTOCOL,
            "sessionId": uuid.uuid4().hex,
            "requestPath": os.path.join(state_dir, "wedge-request.json"),
            "responsePath": os.path.join(state_dir, "wedge-response.json"),
            "gdbBasename": os.path.basename(args.gdb),
        }
        summary["collectorSessionId"] = control["sessionId"]

    for p in (persistent, cache, os.path.join(cache, "log")):
        os.makedirs(p, exist_ok=True)
    # Pre-flight: verify GDB and binary exist before spawning anything.
    # A missing binary makes GDB hang indefinitely waiting for the inferior
    # to exec; a missing GDB is an immediate failure.
    import shutil
    for tool_path, label in ((args.gdb, "gdb"), (binary, "binary")):
        resolved = (shutil.which(tool_path) if "/" not in tool_path
                    else tool_path)
        if resolved is None or not os.path.isfile(resolved) or \
                not os.access(resolved, os.X_OK):
            summary["failureReasons"] = [
                "preflight-%s-not-found:%s" % (label, tool_path)]
            summary["ok"] = False
            summary["exitCode"] = 1
            print("MOVIAN_LIFECYCLE_SUMMARY " + json.dumps(summary,
                                                           sort_keys=True))
            return 1

    # Pre-flight (defect #2): prove any exact-profile stale owner is gone
    # BEFORE spawning. kill_owned only ever signals comm==movian + exact-path
    # matches (never foreign); killed-after-timeout is acceptable only once
    # owns_pid is false. Abort if it cannot be proven gone.
    stale = find_inferior_pid(persistent)
    if stale is not None:
        kill_owned(stale, persistent, timeout=4.0)
        if find_inferior_pid(persistent) is not None:
            summary["failureReasons"] = ["preflight-stale-owner-not-gone"]
            summary["ok"] = False
            summary["exitCode"] = 1
            print("MOVIAN_LIFECYCLE_SUMMARY " + json.dumps(summary,
                                                           sort_keys=True))
            return 1
    # Clear the cache log so the port-line we read is provably from THIS run.
    _log_dir = os.path.join(cache, "log")
    for _fn in (os.listdir(_log_dir) if os.path.isdir(_log_dir) else []):
        try:
            os.unlink(os.path.join(_log_dir, _fn))
        except OSError:
            pass
    if control:
        # Both control paths MUST be absent before the launch GDB Popen so a
        # stale request from a prior session cannot drive a new capture.
        # ENOENT is expected; any other removal failure fails closed here.
        clear_errors = []
        for path in (control["requestPath"], control["responsePath"]):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            except OSError as error:
                clear_errors.append("%s: %s" % (path, error))
        if clear_errors:
            summary["failureReasons"] = (
                ["stale-control-clear-failed"] + clear_errors)
            summary["ok"] = False
            summary["exitCode"] = 1
            print("MOVIAN_LIFECYCLE_SUMMARY " + json.dumps(
                summary, sort_keys=True))
            return 1

    env = dict(os.environ)
    env["DISPLAY"] = args.display
    env["WAYLAND_DISPLAY"] = args.wayland
    env["MOVIAN_MDEV_ALLOW_GDB"] = "1"

    t0 = time.monotonic()
    proc = None
    cmdfile = None
    movian_args = []
    pid = None
    extra = []
    for _p in args.plugins:
        extra += ["-p", _p]
    extra += list(args.extra_args)
    previous_signal_handlers = {}

    def interrupt_launch(signum, _frame):
        raise LaunchInterrupted(signum)

    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_signal_handlers[signum] = signal.signal(
                signum, interrupt_launch)
    try:
        if args.mode == "plain":
            movian_args = build_movian_argv(binary, persistent, cache,
                                            start_url, extra)
            log_fd = open(log_path, "wb", buffering=0)
            log_fd.truncate(0)
            proc = subprocess.Popen(movian_args, cwd=os.getcwd(), env=env,
                                    stdout=log_fd, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL,
                                    start_new_session=True)
            log_fd.close()
        else:  # gdb-bare or gdb-collector
            cmdfile, movian_args = _gdb_cmdfile(
                self_path, events_path, inventory_path, state_dir, persistent,
                cache, binary, start_url, args.categories, args.cap,
                args.hv_cap, args.probe, args.mode, extra, control)
            log_fd = open(log_path, "wb", buffering=0)
            gdb_argv = _build_gdb_argv(args.gdb, cmdfile)
            proc = subprocess.Popen(gdb_argv, cwd=os.getcwd(), env=env,
                                    stdout=log_fd, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL,
                                    start_new_session=True)
            log_fd.close()
            if control:
                control["gdbPid"] = proc.pid
                control["gdbStartTicks"] = process_start_ticks(proc.pid)
                summary["collectorControlReady"] = (
                    control["gdbStartTicks"] is not None)
        summary["argv"] = movian_args

        port, startup_ms = parse_port_and_startup(
            cache, timeout=args.startup_timeout)
        summary["port"] = port
        summary["startupMsInternal"] = startup_ms
        summary["externalWallMs"] = int((time.monotonic() - t0) * 1000)

        pid = find_inferior_pid(persistent)
        summary["inferiorPid"] = pid

        state_extra = {"collector": args.mode == "gdb-collector",
                       "events": events_path}
        if control:
            state_extra["collectorControl"] = control
        if port is not None and pid is not None:
            write_state(name, persistent, cache, pid, port, log_path,
                        movian_args, extra=state_extra)

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
                cmdline = raw.replace(b"\x00", b" ").decode(
                    errors="replace").strip()
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

        if (not args.leave_running and args.mode == "gdb-collector"
                and port is not None and args.duration > 0):
            try:
                proc.wait(timeout=args.duration)
                summary["inferiorExitedBeforeDuration"] = True
            except subprocess.TimeoutExpired:
                pass
    except LaunchInterrupted as exc:
        summary["cancelledBySignal"] = exc.signum
    except Exception as exc:
        summary["exception"] = repr(exc)
    finally:
        # Defect #3: guaranteed cleanup regardless of how the body ended --
        # stop the owned inferior + GDB, then a final exact-persistent-path
        # sweep so no orphan movian/gdb can remain.
        cleanup = cleanup_owned(persistent, proc, pid, args.leave_running)
        summary["stopOutcome"] = cleanup["stopOutcome"]
        summary["gdbReturnCode"] = cleanup["gdbReturnCode"]
        summary["finalOwnedRemains"] = cleanup["finalOwnedRemains"]
        summary["gdbForceKilled"] = cleanup["gdbForceKilled"]
        if "finalScanKill" in cleanup:
            summary["finalScanKill"] = cleanup["finalScanKill"]
        if args.mode == "gdb-collector" and os.path.exists(events_path):
            summary["jsonl"] = validate_events(events_path)
        if cmdfile:
            try:
                os.unlink(cmdfile)
            except OSError:
                pass
        for signum, handler in previous_signal_handlers.items():
            signal.signal(signum, handler)

    ok, reasons = classify_run(summary, args.mode, args.leave_running)
    if summary.get("exception"):
        reasons.append("exception:%s" % summary["exception"])
    if summary.get("cancelledBySignal"):
        reasons.append("cancelled-by-signal:%s" %
                       summary["cancelledBySignal"])
    ok = not reasons
    summary["failureReasons"] = reasons
    summary["ok"] = ok
    summary["exitCode"] = 0 if ok else 1
    print("MOVIAN_LIFECYCLE_SUMMARY " + json.dumps(summary, sort_keys=True))
    return summary["exitCode"]


def run_validate(args):
    result = validate_events(args.events)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["lines"] and not result["bad"] else 1

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
    lp.add_argument("-p", "--plugin", action="append", default=[],
                    dest="plugins",
                    help="dev plugin dir to load (repeatable); forwarded to "
                         "movian as -p (issue #145 plugin scenarios)")
    lp.add_argument("--extra-arg", action="append", default=[],
                    dest="extra_args",
                    help="extra movian argv token (repeatable); use "
                         "--extra-arg=--option for option-like values")
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
    lp.add_argument(
        "--expect-natural-exit", action="store_true",
        help="require a clean exit before --duration expires; intended for "
             "scenarios that explicitly send a shutdown action")
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
    vp.set_defaults(func=run_validate)
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
