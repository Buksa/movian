"""Core plumbing for the mdev CLI: instance state, process guard,
launch/stop, HTTP helpers, prop access, log scanning and screenshots.

Python 3 stdlib only.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from . import movian_diag_snapshot as diag

STATE_ROOT = Path("/tmp/mdev")

# repo root: this file is <root>/support/devtools/mdevlib/harness.py
REPO_ROOT = Path(__file__).resolve().parents[3]

MOVIAN_BINARY = "./build.debug/movian"

# The viewpreview dev plugin (issue #87): `mdev preview` auto-starts an
# instance with just this plugin loaded if none is running yet.
VIEWPREVIEW_DIR = REPO_ROOT / "support" / "devtools" / "viewpreview"

PORT_RE = re.compile(r"http-server: Listening on port (\d+)")

# GLW view load/parse errors as emitted by glw_view_seterr()
# (src/ui/glw/glw_view_support.c): "Error <file>:<line>: <message>"
VIEW_ERROR_RE = re.compile(r"GLW\s+\[ERROR\]:\s*Error (.+?):(\d+): (.*)$")

# viewpreview.js's fail() logs "viewpreview: ERROR: <msg>" (see
# support/devtools/viewpreview/viewpreview.js); its non-error status line
# ("viewpreview: showing ...") deliberately does not match this.
VIEWPREVIEW_ERROR_RE = re.compile(r"viewpreview:\s*ERROR:")

# `mdev reload --js` (issue #93): action ReloadData -> plugins_reload_dev_plugin()
# (src/plugins.c:1453) logs one of these two lines per `-p` dev plugin.
RELOAD_JS_OK_RE = re.compile(r"Reloaded dev plugin (\S+)")
RELOAD_JS_FAIL_RE = re.compile(r"Unable to reload development plugin: (\S+)")

# Compile-error fallback (issue #93 spike finding): plugin_load()
# (src/plugins.c:611) unconditionally returns 0 for an "ecmascript" plugin
# even when ecmascript_plugin_load() fails to compile the JS -- so
# "Reloaded dev plugin <path>" can appear ALONGSIDE this compile-error
# trace for the very same failed reload. Treat this line as authoritative
# over a same-tick "Reloaded" line for the same plugin.
RELOAD_JS_COMPILE_ERROR_RE = re.compile(r"Unable to compile (\S+) -- (.*)$")

# Error-signal set; the single source of truth for what a generic "error
# line" is (movian_agent.py's SIGNALS["errors"] imports this). GLW view
# errors and viewpreview failures have their own shapes above and are
# matched alongside this in error_lines().
ERROR_SIGNALS = re.compile(
    r"TypeError|ReferenceError|Cannot read property|Unable to load image|"
    r"Unknown format|\|E\||CRASH|assert|Segmentation fault",
    re.IGNORECASE,
)

# nav_open0() (src/navigator.c:763) TRACEs this for every processed open
# event -- the only deterministic signal that a queued /api/open actually
# ran (the prop tree alone can't distinguish "old page still showing" from
# "same URL re-opened").
NAV_OPENING_RE = re.compile(r"navigator.*Opening (\S+)")

IMAGE_MAGIC = [
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"BM", "bmp"),
]

PAGE_URL = "global/navigators/current/currentpage/url"
PAGE_LOADING = "global/navigators/current/currentpage/model/loading"
PAGE_TITLE = "global/navigators/current/currentpage/model/metadata/title"
PAGE_TYPE = "global/navigators/current/currentpage/model/type"
PAGE_NODES = "global/navigators/current/currentpage/model/nodes"


class MdevError(Exception):
    """Failure with a one-line reason; carries the process exit code."""

    def __init__(self, message: str, exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code


class Instance:
    """One named mdev-managed Movian instance under /tmp/mdev/<name>/."""

    def __init__(self, name: str):
        # Must contain at least one non-dot char: "." / ".." would resolve
        # the state dir outside /tmp/mdev/.
        if not re.fullmatch(r"[A-Za-z0-9._-]+", name) or set(name) == {"."}:
            raise MdevError("invalid instance name: %r" % name)
        self.name = name
        self.dir = STATE_ROOT / name
        self.state_path = self.dir / "state.json"
        self.log_path = self.dir / "movian.log"
        self.persistent = self.dir / "persistent"
        self.cache = self.dir / "cache"
        self.shots = self.dir / "shots"

    def ensure_dirs(self) -> None:
        for d in (self.dir, self.persistent, self.cache, self.shots):
            d.mkdir(parents=True, exist_ok=True)

    def load_state(self) -> dict[str, Any] | None:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def save_state(self, state: dict[str, Any]) -> None:
        self.ensure_dirs()
        self.state_path.write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def live_pid(self) -> int | None:
        """Pid from state.json if it is alive and still THIS instance's
        movian process (comm + cmdline check, see owns_pid())."""
        state = self.load_state()
        if not state:
            return None
        pid = state.get("pid")
        if isinstance(pid, int) and self.owns_pid(pid):
            return pid
        return None

    def owns_pid(self, pid: int) -> bool:
        """True only when `pid` is a movian process launched against this
        instance's own --persistent dir. The comm check alone is not
        enough: a stale state.json pid recycled by an UNRELATED movian
        would pass it, and stop/--force would then signal a foreign
        process."""
        if not pid_is_movian(pid):
            return False
        try:
            cmdline = Path("/proc/%d/cmdline" % pid).read_bytes()
        except OSError:
            return False
        return str(self.persistent).encode() in cmdline.split(b"\0")

    def base_url(self) -> str:
        state = self.load_state()
        if not state or not state.get("port"):
            raise MdevError(
                "instance %r is not running (no port in state.json); "
                "start it with: mdev run --name %s" % (self.name, self.name)
            )
        pid = self.live_pid()
        if pid is None:
            raise MdevError(
                "instance %r is not running (pid from state.json is dead)"
                % self.name
            )
        return "http://127.0.0.1:%d" % state["port"]


# ---------------------------------------------------------------------------
# Process guard
# ---------------------------------------------------------------------------

def movian_procs() -> list[tuple[int, str]]:
    """All live (pid, cmdline) pairs whose command line invokes a movian
    binary. `cmdline` is the raw `pgrep -fa` argv string (space-joined).

    Uses `pgrep -fa movian` and keeps only processes where some argv token's
    basename is exactly "movian" (avoids matching unrelated processes whose
    command line merely contains the substring).
    """
    try:
        out = subprocess.run(
            ["pgrep", "-fa", "movian"],
            capture_output=True, text=True, check=False,
        ).stdout
    except OSError as error:
        raise MdevError("pgrep failed: %s" % error)
    procs = []
    for line in out.splitlines():
        try:
            pid_str, cmdline = line.split(" ", 1)
        except ValueError:
            continue
        for token in cmdline.split():
            if os.path.basename(token) == "movian":
                procs.append((int(pid_str), cmdline))
                break
    return [(p, c) for p, c in procs if p != os.getpid()]


def classify_foreign(
    inst: "Instance", own_pid: int | None
) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """Split live movian pids (excluding `own_pid`) into (coexistable
    foreign, same-dir collisions) (issue #94).

    A "collision" is a live movian pid whose cmdline references this
    instance's own `--persistent` path -- i.e. something is running against
    our state dir that `state.json` did not confirm as `own_pid` (stale/
    corrupted state.json, or a race). That case still refuses (exit 2):
    coexistence is only safe for genuinely separate instances/profiles.
    Every other live movian pid is a "foreign" instance -- isolated profile,
    dynamic port, no state.json overlap -- safe to warn-and-coexist with.
    """
    persistent_str = str(inst.persistent)
    foreign: list[tuple[int, str]] = []
    collisions: list[tuple[int, str]] = []
    for pid, cmdline in movian_procs():
        if pid == own_pid:
            continue
        if persistent_str in cmdline:
            collisions.append((pid, cmdline))
        else:
            foreign.append((pid, cmdline))
    return foreign, collisions


def coexist_warning(foreign: list[tuple[int, str]]) -> str:
    """One-line coexistence warning naming each foreign pid + cmdline
    (issue #94 contract)."""
    return "coexisting with foreign movian: " + "; ".join(
        "pid %d (%s)" % (pid, cmdline) for pid, cmdline in foreign
    )


def collision_refusal(inst: "Instance",
                      collisions: list[tuple[int, str]]) -> MdevError:
    """The exit-2 refusal for a same-dir collision (issue #94): a live
    movian pid uses this instance's own --persistent path but state.json
    can't confirm it as ours (stale/corrupted state, or a race). Shared
    by `mdev run` and ensure_running() so the message can't drift."""
    return MdevError(
        "refusing to start: live movian pid(s) using %s are not "
        "confirmed as instance %r's own process by state.json: %s -- "
        "this instance's state may be corrupted; investigate before "
        "retrying (don't --force blindly)." % (
            inst.persistent, inst.name,
            ", ".join("%d (%s)" % (p, c) for p, c in collisions),
        ),
        exit_code=2,
    )


def pid_is_movian(pid: int) -> bool:
    try:
        comm = Path("/proc/%d/comm" % pid).read_text().strip()
    except OSError:
        return False
    return comm == "movian"


def kill_owned_pid(inst: "Instance", pid: int, timeout: float = 5.0) -> None:
    """Terminate a pid this instance owns per state.json.  Refuses to
    signal anything whose comm+cmdline do not prove it is this instance's
    own movian (stale-pid safety: a recycled pid -- even one recycled by
    another movian -- is hands-off)."""
    if not inst.owns_pid(pid):
        return  # already gone or pid recycled: hands off
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not inst.owns_pid(pid):
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    time.sleep(0.2)


# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------

def parse_dev_flags(spec: str) -> dict[str, Any]:
    """Parse "smbdebug=1,ecmascriptdebug=1" into an htsmsg-JSON dict."""
    flags: dict[str, Any] = {}
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise MdevError("--dev-flags expects k=v pairs, got %r" % item)
        key, value = item.split("=", 1)
        if not key:
            raise MdevError("--dev-flags: empty key in %r" % item)
        flags[key] = int(value) if re.fullmatch(r"-?\d+", value) else value
    if not flags:
        raise MdevError("--dev-flags: no flags parsed from %r" % spec)
    return flags


def build_argv(inst: Instance, plugins: list[str], skin: str | None,
               libav_log: bool, start_url: str | None,
               extra_flags: list[str] | None = None) -> list[str]:
    argv = [
        "stdbuf", "-oL", "-eL",
        MOVIAN_BINARY, "-d",
        "--disable-upgrades",
        "--persistent", str(inst.persistent),
        "--cache", str(inst.cache),
    ]
    for plugin in plugins:
        argv += ["-p", os.path.abspath(plugin)]
    if skin:
        argv += ["--skin", os.path.abspath(skin)]
    if libav_log:
        argv.append("--libav-log")
    if extra_flags:
        argv += extra_flags
    if start_url:
        argv.append(start_url)
    return argv


def launch(inst: Instance, argv: list[str], timeout: float = 30.0) -> dict[str, Any]:
    """Start Movian from the repo root and wait for the HTTP port line."""
    binary = REPO_ROOT / MOVIAN_BINARY
    if not binary.is_file():
        raise MdevError("movian binary not found: %s" % binary)

    inst.ensure_dirs()
    log_fd = open(inst.log_path, "wb", buffering=0)
    log_fd.truncate(0)
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(REPO_ROOT),           # dataroot:// resolves against CWD
            stdout=log_fd,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,       # survive mdev exiting
        )
    finally:
        log_fd.close()

    port = None
    deadline = time.monotonic() + timeout
    scanned = ""
    offset = 0
    while time.monotonic() < deadline:
        # Incremental scan: only read what movian appended since the last
        # tick (stdbuf -oL keeps the log line-buffered, so the port line
        # never lands split across reads of a growing file).
        size = log_size(inst)
        if size > offset:
            scanned += read_log_delta(inst, offset)
            offset = size
        if proc.poll() is not None:
            tail = "\n".join(scanned.splitlines()[-10:])
            raise MdevError(
                "movian exited with code %s before the HTTP server came up;"
                " log tail:\n%s" % (proc.returncode, tail)
            )
        match = PORT_RE.search(scanned)
        if match:
            port = int(match.group(1))
            break
        time.sleep(0.2)

    if port is None:
        # The child is ours by construction (we hold the Popen handle);
        # kill_owned_pid()'s comm check could miss it if stdbuf has not
        # exec'd into movian yet, so signal the handle directly.
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
        raise MdevError(
            "timed out (%.0fs) waiting for 'Listening on port' in %s"
            % (timeout, inst.log_path)
        )

    state = {
        "name": inst.name,
        "pid": proc.pid,
        "port": port,
        "log": str(inst.log_path),
        "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "argv": argv,
    }
    inst.save_state(state)
    return state


def ensure_running(name: str, plugins: list[str]) -> Instance:
    """Return a live Instance named `name`, launching one with `plugins`
    if it is not already up. Reuses an already-running instance as-is
    (its existing plugin/skin selection wins -- this does not restart
    it even if `plugins` differs). Same coexistence guard as `mdev run`
    (issue #94): warns and proceeds next to a foreign movian instance;
    never touches a movian process this state dir doesn't own, and still
    refuses (exit 2) on a same-dir collision (see `classify_foreign()`).

    Used by `mdev preview` (issue #87) to auto-start the viewpreview
    instance on first use. Passes the existing core CLI flag
    `--bypass-ecmascript-acl` (src/main.c, gconf.bypass_ecmascript_acl):
    the ecmascript file ACL (src/ecmascript/es_fs.c:filename_is_allowed)
    otherwise restricts a plugin's own `fs`/`native/fs` reads to its own
    directory, but viewpreview.js needs to read fixture JSON and check
    view-file paths anywhere in the repo (or a sibling checkout) -- this
    is a pre-existing core flag, not a new C change.
    """
    inst = Instance(name)
    if inst.live_pid() is not None:
        return inst

    foreign, collisions = classify_foreign(inst, None)
    if collisions:
        raise collision_refusal(inst, collisions)
    if foreign:
        print(coexist_warning(foreign), file=sys.stderr)

    inst.ensure_dirs()
    argv = build_argv(inst, plugins, None, False, None,
                      extra_flags=["--bypass-ecmascript-acl"])
    launch(inst, argv)
    return inst


# ---------------------------------------------------------------------------
# HTTP / prop helpers
# ---------------------------------------------------------------------------

def http_request(base_url: str, path: str, timeout: float = 5.0,
                 method: str = "GET",
                 form: dict[str, str] | None = None) -> dict[str, Any]:
    data = None
    headers = {}
    if form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(
        base_url.rstrip("/") + path, data=data, headers=headers, method=method
    )
    try:
        response = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    except (urllib.error.URLError, OSError) as error:
        return {"ok": False, "error": str(error), "path": path}
    with response:
        try:
            body = response.read()
        except OSError as error:
            return {"ok": False, "error": str(error), "path": path}
        return {
            "ok": 200 <= response.status < 400,
            "status": response.status,
            "content_type": response.headers.get("Content-Type"),
            "body": body,
            "path": path,
        }


def get_prop(base_url: str, path: str, timeout: float = 5.0) -> dict[str, Any] | None:
    """Fetch and parse one /api/prop node; None if the prop does not exist."""
    encoded = urllib.parse.quote(path, safe="/*")
    result = http_request(base_url, "/api/prop/" + encoded, timeout)
    if not result.get("ok"):
        return None
    return diag.parse_prop(result["body"].decode("utf-8", "replace"))


def prop_value(base_url: str, path: str, timeout: float = 5.0) -> str | None:
    parsed = get_prop(base_url, path, timeout)
    if parsed is None:
        return None
    return parsed.get("value")


def prop_has_value(value: str | None) -> bool:
    return value not in (None, "", "(void)", "(zombie)")


def node_count(base_url: str, path: str = PAGE_NODES) -> int:
    parsed = get_prop(base_url, path)
    if parsed is None or parsed.get("value") != "directory":
        return 0
    return len(parsed.get("children", []))


def open_and_wait(inst: Instance, url: str, timeout: float = 20.0) -> dict[str, Any]:
    """GET /api/open for `url` and wait for page-ready; return
    {"url", "title", "type", "nodes"}. Shared by `mdev open` and
    `mdev preview`.
    """
    base = inst.base_url()
    before_url = prop_value(base, PAGE_URL)
    offset = log_size(inst)

    result = http_request(
        base, "/api/open?" + urllib.parse.urlencode({"url": url}),
        timeout=5.0)
    if not result.get("ok"):
        raise MdevError("GET /api/open failed: %s"
                        % (result.get("error") or result.get("status")))

    deadline = time.monotonic() + timeout
    cur_url = title = None
    ready = nav_seen = False
    while time.monotonic() < deadline:
        # /api/open only QUEUES a nav event. Before trusting the prop
        # tree, require nav_open0()'s per-open "Opening <url>" trace in
        # the log delta -- when `url` is already the open page, the props
        # (same url, loading=0, title set) look "ready" immediately and
        # would otherwise report the OLD page's state as the result.
        if not nav_seen:
            delta = read_log_delta(inst, offset)
            nav_seen = any(m.group(1) == url
                           for m in NAV_OPENING_RE.finditer(delta))
            if nav_seen:
                # Grace tick: the trace fires just before the currentpage
                # prop swap becomes visible over HTTP.
                time.sleep(0.3)
            else:
                time.sleep(0.2)
            continue
        cur_url = prop_value(base, PAGE_URL)
        loading = prop_value(base, PAGE_LOADING)
        title = prop_value(base, PAGE_TITLE)
        # Ready when loading is 0 -- or void/absent: routes like
        # page:settings never create the loading prop at all.
        if loading in ("0", "(void)", None) and prop_has_value(title):
            # Verify navigation actually targeted our URL: either the page
            # url now equals the requested one, or it at least changed away
            # from what was open before (redirecting backends may rewrite
            # the page url).
            if cur_url == url or cur_url != before_url:
                ready = True
                break
        time.sleep(0.2)

    if not ready:
        raise MdevError(
            "page not ready after %.0fs: nav_event_seen=%r url=%r "
            "loading=%r title=%r"
            % (timeout, nav_seen, cur_url,
               prop_value(base, PAGE_LOADING), title)
        )

    ptype = prop_value(base, PAGE_TYPE)
    nodes = node_count(base)
    return {"url": cur_url, "title": title, "type": ptype, "nodes": nodes}


# ---------------------------------------------------------------------------
# Log access
# ---------------------------------------------------------------------------

def read_log(inst: Instance) -> str:
    try:
        return inst.log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def log_size(inst: Instance) -> int:
    try:
        return inst.log_path.stat().st_size
    except OSError:
        return 0


def read_log_delta(inst: Instance, offset: int) -> str:
    try:
        with open(inst.log_path, "rb") as f:
            f.seek(offset)
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def error_lines(text: str) -> list[str]:
    return [
        line for line in text.splitlines()
        if ERROR_SIGNALS.search(line) or VIEW_ERROR_RE.search(line)
        or VIEWPREVIEW_ERROR_RE.search(line)
    ]


def view_error_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if VIEW_ERROR_RE.search(line)]


def viewpreview_error_lines(text: str) -> list[str]:
    return [line for line in text.splitlines()
            if VIEWPREVIEW_ERROR_RE.search(line)]


def reload_js_ok_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if RELOAD_JS_OK_RE.search(line)]


def reload_js_fail_lines(text: str) -> list[str]:
    """"Unable to reload development plugin" (the errbuf-reported failure
    path) plus the duktape compile-error fallback (see
    RELOAD_JS_COMPILE_ERROR_RE's docstring) -- either is a failure."""
    return [
        line for line in text.splitlines()
        if RELOAD_JS_FAIL_RE.search(line) or RELOAD_JS_COMPILE_ERROR_RE.search(line)
    ]


def reload_line_matches_dir(line: str, plugin_dir: str) -> bool:
    """True when a reload-result log line refers to `plugin_dir`: the path
    token it names is the dir itself ("Reloaded dev plugin <dir>") or a
    file under it (the compile-error line names a .js file). A plain
    substring test would also credit a sibling dir like <dir>-extra's
    lines to <dir>."""
    for pattern in (RELOAD_JS_OK_RE, RELOAD_JS_FAIL_RE,
                    RELOAD_JS_COMPILE_ERROR_RE):
        match = pattern.search(line)
        if match:
            path = match.group(1)
            return (path == plugin_dir
                    or path.startswith(plugin_dir.rstrip("/") + "/"))
    return False


# ---------------------------------------------------------------------------
# Reload / screenshot flows
# ---------------------------------------------------------------------------

def do_reload(inst: Instance, settle: float = 2.0) -> tuple[bool, list[str]]:
    """POST ReloadUI and grep the log delta for GLW view errors.

    Returns (ok, error_lines).
    """
    base = inst.base_url()
    offset = log_size(inst)
    result = http_request(base, "/api/input/action/ReloadUI",
                          timeout=5.0, method="POST")
    if not result.get("ok"):
        raise MdevError(
            "POST /api/input/action/ReloadUI failed: %s"
            % (result.get("error") or result.get("status"))
        )
    deadline = time.monotonic() + settle
    errors: list[str] = []
    while time.monotonic() < deadline:
        errors = view_error_lines(read_log_delta(inst, offset))
        if errors:
            break
        time.sleep(0.2)
    return (not errors, errors)


def plugin_dirs_from_argv(argv: list[str]) -> list[str]:
    """Extract every `-p`/`--plugin` value from a recorded launch argv
    (see `state.json`'s "argv"; `build_argv()` always stores an
    `os.path.abspath()`'d value there)."""
    dirs = []
    i = 0
    while i < len(argv):
        if argv[i] in ("-p", "--plugin") and i + 1 < len(argv):
            dirs.append(argv[i + 1])
            i += 2
        else:
            i += 1
    return dirs


def do_reload_js(inst: Instance, settle: float = 2.0) -> tuple[bool, list[dict]]:
    """POST ReloadData (issue #93) and grep the log delta for the
    per-dev-plugin reload result reported by `plugins_reload_dev_plugin()`
    (src/plugins.c:1453).

    Exit criteria: ok only when every `-p` dev plugin recorded for this
    instance reports "Reloaded dev plugin ..." AND no
    "Unable to reload development plugin"/duktape compile-error line
    matches that plugin's path -- see RELOAD_JS_COMPILE_ERROR_RE's
    docstring for why the compile-error line must win over a same-tick
    "Reloaded" line for the same plugin.

    Returns (ok, per_plugin) where per_plugin is a list of
    {"plugin": <dir or None>, "ok": bool, "detail": <matched log line>}
    (one entry per `-p` dir, plus a trailing entry for any failure line
    that couldn't be attributed to a specific plugin dir).
    """
    state = inst.load_state() or {}
    plugin_dirs = plugin_dirs_from_argv(state.get("argv") or [])
    if not plugin_dirs:
        raise MdevError(
            "instance %r has no dev plugins (-p) to reload with --js"
            % inst.name
        )

    base = inst.base_url()
    offset = log_size(inst)
    result = http_request(base, "/api/input/action/ReloadData",
                          timeout=5.0, method="POST")
    if not result.get("ok"):
        raise MdevError(
            "POST /api/input/action/ReloadData failed: %s"
            % (result.get("error") or result.get("status"))
        )

    deadline = time.monotonic() + settle
    ok_lines: list[str] = []
    fail_lines: list[str] = []
    while time.monotonic() < deadline:
        delta = read_log_delta(inst, offset)
        ok_lines = reload_js_ok_lines(delta)
        fail_lines = reload_js_fail_lines(delta)
        accounted = sum(
            1 for d in plugin_dirs
            if any(reload_line_matches_dir(line, d)
                   for line in ok_lines + fail_lines)
        )
        if accounted >= len(plugin_dirs):
            break
        time.sleep(0.15)

    per_plugin: list[dict] = []
    for plugin_dir in plugin_dirs:
        matched_fail = [line for line in fail_lines
                        if reload_line_matches_dir(line, plugin_dir)]
        matched_ok = [line for line in ok_lines
                      if reload_line_matches_dir(line, plugin_dir)]
        ok = bool(matched_ok) and not matched_fail
        per_plugin.append({
            "plugin": plugin_dir,
            "ok": ok,
            "detail": matched_fail[0] if matched_fail else (
                matched_ok[0] if matched_ok else "no reload result seen"
            ),
        })

    # A failure line that names no known plugin dir still fails the
    # overall reload (belt-and-suspenders; observed to always be
    # attributable in practice -- see RELOAD_JS_COMPILE_ERROR_RE).
    unattributed = [
        line for line in fail_lines
        if not any(reload_line_matches_dir(line, d) for d in plugin_dirs)
    ]
    if unattributed:
        per_plugin.append({"plugin": None, "ok": False,
                           "detail": unattributed[0]})

    overall_ok = all(p["ok"] for p in per_plugin)
    return overall_ok, per_plugin


def sniff_image(body: bytes) -> str | None:
    """Return a file extension for known image magic bytes, else None."""
    for magic, ext in IMAGE_MAGIC:
        if body.startswith(magic):
            return ext
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "webp"
    return None


def take_shot(inst: Instance, out: str | None = None,
              timeout: float = 15.0) -> Path:
    base = inst.base_url()
    result = http_request(base, "/api/screenshot/raw", timeout=timeout)
    if not result.get("ok"):
        raise MdevError(
            "GET /api/screenshot/raw failed: %s"
            % (result.get("error") or result.get("status"))
        )
    body = result["body"]
    if not body:
        raise MdevError("screenshot is empty")
    ext = sniff_image(body)
    if ext is None:
        raise MdevError(
            "screenshot has unknown magic bytes: %s" % body[:8].hex()
        )
    if out:
        path = Path(out)
    else:
        inst.ensure_dirs()
        path = inst.shots / (time.strftime("%Y%m%d-%H%M%S") + "." + ext)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path
