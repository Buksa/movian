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

PORT_RE = re.compile(r"http-server: Listening on port (\d+)")

# GLW view load/parse errors as emitted by glw_view_seterr()
# (src/ui/glw/glw_view_support.c): "Error <file>:<line>: <message>"
VIEW_ERROR_RE = re.compile(r"GLW\s+\[ERROR\]:\s*Error (.+?):(\d+): (.*)$")

# Error-signal set ported from movian_agent.py SIGNALS["errors"],
# extended with the GLW view error shape.
ERROR_SIGNALS = re.compile(
    r"TypeError|ReferenceError|Cannot read property|Unable to load image|"
    r"Unknown format|\|E\||CRASH|assert|Segmentation fault",
    re.IGNORECASE,
)

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
        if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
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
        """Pid from state.json if it is alive and still a movian process."""
        state = self.load_state()
        if not state:
            return None
        pid = state.get("pid")
        if isinstance(pid, int) and pid_is_movian(pid):
            return pid
        return None

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

def movian_pids() -> list[int]:
    """All live pids whose command line invokes a movian binary.

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
    pids = []
    for line in out.splitlines():
        try:
            pid_str, cmdline = line.split(" ", 1)
        except ValueError:
            continue
        for token in cmdline.split():
            if os.path.basename(token) == "movian":
                pids.append(int(pid_str))
                break
    return [p for p in pids if p != os.getpid()]


def pid_is_movian(pid: int) -> bool:
    try:
        comm = Path("/proc/%d/comm" % pid).read_text().strip()
    except OSError:
        return False
    return comm == "movian"


def kill_owned_pid(pid: int, timeout: float = 5.0) -> None:
    """Terminate a pid we own per state.json.  Refuses to signal anything
    that is not a movian process (stale-pid safety)."""
    if not pid_is_movian(pid):
        return  # already gone (or pid recycled by another program: hands off)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_is_movian(pid):
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
               libav_log: bool, start_url: str | None) -> list[str]:
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
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            tail = "\n".join(read_log(inst).splitlines()[-10:])
            raise MdevError(
                "movian exited with code %s before the HTTP server came up;"
                " log tail:\n%s" % (proc.returncode, tail)
            )
        match = PORT_RE.search(read_log(inst))
        if match:
            port = int(match.group(1))
            break
        time.sleep(0.2)

    if port is None:
        kill_owned_pid(proc.pid)
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
    ]


def view_error_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if VIEW_ERROR_RE.search(line)]


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
