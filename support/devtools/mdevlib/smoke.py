"""Declarative mdev regression-smoke runner (issue #90)."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.parse
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import harness
from .harness import Instance, MdevError

SMOKES_DIR = harness.REPO_ROOT / "support" / "devtools" / "smokes"
CURRENT_PAGE = "global/navigators/current/currentpage"
UI_FRAMERATE = "global/userinterfaces/ui/framerate"
WEDGE_BACKTRACE_FILE = "thread-backtrace.txt"
WEDGE_BACKTRACE_TIMEOUT = 5.0
SMOKE_ORDER = (
    "health",
    "open-home",
    "preview-demo",
    "preview-pilot",
    "reload-clean",
    "keyboard-mode",
    "js-reload",
)
STEP_FIELDS = {
    "health": {"do"},
    "open": {"do", "url"},
    "preview": {"do", "view", "fixture"},
    "action": {"do", "name", "count"},
    "reload": {"do", "js"},
    "assert_prop": {"do", "path", "equals", "one_of", "absent"},
    "assert_log": {"do", "must_match", "must_not_match"},
    "shot": {"do", "tag"},
    "sleep": {"do", "seconds"},
}


class StepFailure(Exception):
    """One smoke-step assertion or operation failed."""

    def __init__(self, detail: str, evidence: Any = None):
        super().__init__(detail)
        self.detail = detail
        self.evidence = evidence


def _validate_definition(path: Path, data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise MdevError("smoke definition must be an object: %s" % path)
    if set(data) != {"name", "describe", "needs", "steps"}:
        raise MdevError("smoke %s must contain name, describe, needs, steps" % path)
    if data["name"] != path.stem:
        raise MdevError("smoke name %r does not match %s" % (data["name"], path.name))
    if not isinstance(data["describe"], str) or not data["describe"].strip():
        raise MdevError("smoke %s has no describe" % data["name"])
    needs = data["needs"]
    if not isinstance(needs, dict) or set(needs) != {"plugin", "binary"}:
        raise MdevError("smoke %s has invalid needs" % data["name"])
    if needs["plugin"] is not None and not isinstance(needs["plugin"], str):
        raise MdevError("smoke %s needs.plugin must be a directory or null" % data["name"])
    if not isinstance(needs["binary"], str):
        raise MdevError("smoke %s needs.binary must be a path" % data["name"])
    if not isinstance(data["steps"], list) or not data["steps"]:
        raise MdevError("smoke %s has no steps" % data["name"])

    for index, step in enumerate(data["steps"], 1):
        if not isinstance(step, dict) or step.get("do") not in STEP_FIELDS:
            raise MdevError("smoke %s step %d has unknown verb" % (data["name"], index))
        verb = step["do"]
        unknown = set(step) - STEP_FIELDS[verb]
        if unknown:
            raise MdevError("smoke %s step %d has unknown fields: %s" % (
                data["name"], index, ", ".join(sorted(unknown))))
        required = {
            "health": set(),
            "open": {"url"},
            "preview": {"view", "fixture"},
            "action": {"name", "count"},
            "reload": {"js"},
            "assert_prop": {"path"},
            "assert_log": set(),
            "shot": {"tag"},
            "sleep": {"seconds"},
        }[verb]
        missing = required - set(step)
        if missing:
            raise MdevError("smoke %s step %d lacks %s" % (
                data["name"], index, ", ".join(sorted(missing))))
        if verb == "assert_prop":
            comparisons = {"equals", "one_of", "absent"} & set(step)
            if len(comparisons) != 1:
                raise MdevError("smoke %s step %d needs exactly one prop comparison" % (
                    data["name"], index))
        elif verb == "assert_log":
            patterns = {"must_match", "must_not_match"} & set(step)
            if not patterns:
                raise MdevError("smoke %s step %d needs a log pattern" % (
                    data["name"], index))
            for key in patterns:
                try:
                    re.compile(step[key], re.MULTILINE)
                except (re.error, TypeError) as error:
                    raise MdevError("smoke %s step %d has invalid %s: %s" % (
                        data["name"], index, key, error))
        elif verb == "sleep":
            seconds = step["seconds"]
            if not isinstance(seconds, (int, float)) or not 0 <= seconds <= 5:
                raise MdevError("smoke %s step %d sleep must be between 0 and 5 seconds" % (
                    data["name"], index))
        elif verb == "action":
            if not isinstance(step["count"], int) or step["count"] < 1:
                raise MdevError("smoke %s step %d action count must be positive" % (
                    data["name"], index))
        elif verb == "reload" and not isinstance(step["js"], bool):
            raise MdevError("smoke %s step %d reload.js must be boolean" % (
                data["name"], index))
    return data


def load_definitions() -> list[dict[str, Any]]:
    definitions = []
    for path in SMOKES_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise MdevError("cannot read smoke definition %s: %s" % (path, error))
        definitions.append(_validate_definition(path, data))
    if not definitions:
        raise MdevError("no smoke definitions found in %s" % SMOKES_DIR)
    names = [definition["name"] for definition in definitions]
    if len(set(names)) != len(names):
        raise MdevError("duplicate smoke name in %s" % SMOKES_DIR)
    order = {name: index for index, name in enumerate(SMOKE_ORDER)}
    return sorted(definitions, key=lambda item: (order.get(item["name"], len(order)),
                                                 item["name"]))


def select_definitions(definitions: list[dict[str, Any]], target: str) -> list[dict[str, Any]]:
    if target == "all":
        selected = definitions
    else:
        selected = [item for item in definitions if item["name"] == target]
        if not selected:
            raise MdevError("unknown smoke %r (use `mdev smoke list`)" % target)
    if target == "all" and (not selected or selected[0]["name"] != "health"):
        raise MdevError("smoke run all requires health to be the first definition")
    return selected


def _resolve_repo_path(path: str) -> str:
    value = Path(path)
    if not value.is_absolute():
        value = harness.REPO_ROOT / value
    return str(value.resolve())


def _plugins_for(definitions: list[dict[str, Any]]) -> list[str]:
    plugins = []
    for definition in definitions:
        binary = definition["needs"]["binary"]
        if binary != harness.MOVIAN_BINARY.removeprefix("./"):
            raise MdevError("smoke %s requests unsupported binary %s" % (
                definition["name"], binary))
        plugin = definition["needs"]["plugin"]
        if plugin is not None:
            resolved = _resolve_repo_path(plugin)
            if resolved not in plugins:
                plugins.append(resolved)
    return plugins


def _coerce_prop(value: Any) -> Any:
    if value in (None, "", "(void)", "(zombie)"):
        return None
    if not isinstance(value, str):
        return value
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?(?:\d+\.\d*|\d*\.\d+)", value):
        return float(value)
    return value


def _prop_actual(parsed: dict[str, Any] | None) -> Any:
    if parsed is None:
        return None
    if parsed.get("value") == "directory":
        return len(parsed.get("children", []))
    return _coerce_prop(parsed.get("value"))


def _prop_matches(step: dict[str, Any], actual: Any) -> bool:
    if "equals" in step:
        return actual == step["equals"]
    if "one_of" in step:
        return actual in step["one_of"]
    return (actual is None) == step["absent"]


def _assert_prop(inst: Instance, step: dict[str, Any]) -> str:
    base = inst.base_url()
    deadline = time.monotonic() + 5.0
    actual = None
    while time.monotonic() < deadline:
        actual = _prop_actual(harness.get_prop(base, step["path"]))
        if _prop_matches(step, actual):
            return "%s = %r" % (step["path"], actual)
        time.sleep(0.1)
    expected = {key: step[key] for key in ("equals", "one_of", "absent") if key in step}
    raise StepFailure("prop %s is %r, expected %s" % (step["path"], actual, expected),
                      {"path": step["path"], "actual": actual, "expected": expected})


def _assert_log(step: dict[str, Any], delta: str) -> str:
    evidence = []
    if "must_match" in step:
        match = re.search(step["must_match"], delta, re.MULTILINE)
        if match is None:
            raise StepFailure("log delta did not match %r" % step["must_match"],
                              {"pattern": step["must_match"], "log_delta": delta})
        evidence.append(match.group(0))
    if "must_not_match" in step:
        matches = re.findall(step["must_not_match"], delta, re.MULTILINE)
        if matches:
            lines = [line for line in delta.splitlines()
                     if re.search(step["must_not_match"], line)]
            raise StepFailure("log delta matched forbidden pattern %r" %
                              step["must_not_match"],
                              {"pattern": step["must_not_match"],
                               "matches": lines or matches, "log_delta": delta})
    return "; ".join(evidence) if evidence else "forbidden pattern absent"


def _take_png(inst: Instance, out: Path, timeout: float = 15.0) -> tuple[Path, str]:
    """Capture a screenshot, validate PNG, return (path, sha256_hex)."""
    path, sha256_hex = harness.take_shot(inst, str(out), timeout=timeout)
    assert path is not None
    try:
        with path.open("rb") as image:
            magic = image.read(8)
    except OSError as error:
        raise MdevError("cannot read screenshot %s: %s" % (path, error))
    if magic != b"\x89PNG\r\n\x1a\n":
        raise MdevError("screenshot is not PNG: magic=%s" % magic.hex())
    return path, sha256_hex


def _execute_step(
    inst: Instance,
    smoke_name: str,
    step: dict[str, Any],
    previous_delta: str,
    route_builder: Callable[[str, str | None], str],
) -> tuple[str, str, str | None, dict[str, int]]:
    """Execute one smoke step.

    Returns ``(detail, log_delta, screenshot_hash, metrics)``. Healthy
    screenshot latency is machine-readable in ``metrics``.
    """
    verb = step["do"]
    offset = harness.log_size(inst)

    if verb == "health":
        detail, health_hash, screenshot_ms = _health_step(inst)
        return (detail, harness.read_log_delta(inst, offset), health_hash,
                {"screenshotLatencyMs": screenshot_ms})
    elif verb == "open":
        result = harness.open_and_wait(inst, step["url"])
        detail = "opened %s title=%s nodes=%d" % (
            result["url"], result["title"], result["nodes"])
    elif verb == "preview":
        base = inst.base_url()
        flush = harness.http_request(base, "/api/input/action/ReloadUI",
                                     timeout=5.0, method="POST")
        if not flush.get("ok"):
            raise StepFailure("view-cache flush failed: %s" %
                              (flush.get("error") or flush.get("status")))
        time.sleep(0.4)
        route = route_builder(step["view"], step["fixture"])
        result = harness.open_and_wait(inst, route)
        time.sleep(1.5)
        detail = "previewed %s title=%s nodes=%d" % (
            step["view"], result["title"], result["nodes"])
    elif verb == "action":
        base = inst.base_url()
        path = "/api/input/action/" + urllib.parse.quote(step["name"], safe="")
        for _ in range(step["count"]):
            result = harness.http_request(base, path, timeout=5.0, method="POST")
            if not result.get("ok"):
                raise StepFailure("POST %s failed: %s" %
                                  (path, result.get("error") or result.get("status")))
        detail = "sent %s x%d" % (step["name"], step["count"])
    elif verb == "reload":
        if step["js"]:
            ok, per_plugin = harness.do_reload_js(inst)
            if not ok:
                raise StepFailure("JS reload failed", per_plugin)
            detail = "JS reload: " + "; ".join(item["detail"] for item in per_plugin)
        else:
            ok, errors = harness.do_reload(inst)
            if not ok:
                raise StepFailure("ReloadUI reported GLW errors", errors)
            detail = "ReloadUI clean"
    elif verb == "assert_prop":
        detail = _assert_prop(inst, step)
    elif verb == "assert_log":
        return _assert_log(step, previous_delta), "", None, {}
    elif verb == "shot":
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        out = inst.shots / ("%s-%s-%s.png" % (smoke_name, step["tag"], stamp))
        path, shot_hash = _take_png(inst, out)
        detail = "shot %s" % path
        return detail, harness.read_log_delta(inst, offset), shot_hash, {}
    elif verb == "sleep":
        time.sleep(step["seconds"])
        detail = "slept %.3gs" % step["seconds"]
    else:  # validation makes this unreachable
        raise StepFailure("unsupported verb %s" % verb)

    return detail, harness.read_log_delta(inst, offset), None, {}


def _wait_for_ui_ready(
    inst: Instance,
    timeout: float,
) -> tuple[bool, str]:
    """Wait until startup navigation and GLW frame dispatch are both active."""
    deadline = time.monotonic() + timeout
    nav_seen = False
    framerate = None
    if inst.live_pid() is None:
        return False, "instance process is not alive"
    try:
        base = inst.base_url()
    except MdevError as error:
        return False, str(error)

    while time.monotonic() < deadline:
        if inst.live_pid() is None:
            return False, "instance process is not alive"
        if not nav_seen:
            nav_seen = harness.NAV_OPENING_RE.search(
                harness.read_log(inst)) is not None
        framerate = harness.prop_value(base, UI_FRAMERATE)
        if nav_seen and harness.prop_has_value(framerate):
            return True, "startup navigator Opening trace and UI framerate present"
        time.sleep(0.3)

    missing = []
    if not nav_seen:
        missing.append("navigator Opening trace")
    if not harness.prop_has_value(framerate):
        missing.append("%s usable value" % UI_FRAMERATE)
    return False, "startup readiness did not complete within %gs: missing %s" % (
        timeout, " and ".join(missing))


def _health_step(inst: Instance, timeout: float = 20.0) -> tuple[str, str, int]:
    """Require UI readiness, then record the healthy screenshot latency."""
    ready, detail = _wait_for_ui_ready(inst, timeout)
    if not ready:
        raise StepFailure(detail)
    probe = inst.dir / ".smoke-health.png"
    started = time.monotonic()
    try:
        _, probe_hash = _take_png(inst, probe)
    except MdevError as error:
        raise StepFailure("screenshot probe failed: %s" % error)
    screenshot_ms = int((time.monotonic() - started) * 1000)
    try:
        probe.unlink()
    except OSError:
        pass
    return (detail + " and screenshot PNG present", probe_hash,
            screenshot_ms)


def _probe_health(inst: Instance, timeout: float = 20.0) -> tuple[bool, str]:
    ready, detail = _wait_for_ui_ready(inst, timeout)
    if not ready:
        return False, detail
    probe = inst.dir / ".smoke-health.png"
    try:
        _take_png(inst, probe, timeout=7.0)
    except MdevError as error:
        return False, str(error)
    try:
        probe.unlink()
    except OSError:
        pass
    return True, detail + " and screenshot PNG present"


def _collect_props(base: str, path: str, depth: int) -> dict[str, Any]:
    parsed = harness.get_prop(base, path)
    if parsed is None:
        return {"path": path, "error": "not found"}
    node: dict[str, Any] = {"path": path, "value": parsed.get("value")}
    if parsed.get("value") == "directory":
        children = parsed.get("children", [])
        node["child_count"] = len(children)
        if depth > 0:
            node["children"] = {}
            for index, name in enumerate(children):
                ref = "*%d" % index if name == "<unnamed>" else name
                node["children"][ref] = _collect_props(
                    base, path + "/" + ref, depth - 1)
    return node


def stop_wedged_instance(inst: Instance) -> str:
    """Stop a wedged instance owned by this state dir.  Returns the stop
    outcome from ``harness.kill_owned_pid`` (``"stopped-clean"``,
    ``"killed-after-timeout"``, or ``"still-alive"``)."""
    pid = inst.live_pid()
    if pid is None:
        return "stopped-clean"
    return harness.kill_owned_pid(inst, pid)


def _write_bundle(
    inst: Instance,
    smoke_name: str,
    transcript: dict[str, Any],
    wedge: bool,
) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    bundle = inst.dir / "smoke-fail" / ("%s-%s" % (smoke_name, stamp))
    bundle.mkdir(parents=True, exist_ok=False)
    (bundle / "steps.json").write_text(
        json.dumps(transcript, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    tail = "\n".join(harness.read_log(inst).splitlines()[-120:])
    (bundle / "log-tail.txt").write_text(tail + ("\n" if tail else ""),
                                          encoding="utf-8")
    try:
        props = _collect_props(inst.base_url(), CURRENT_PAGE, 3)
    except MdevError as error:
        props = {"path": CURRENT_PAGE, "error": str(error)}
    (bundle / "props.json").write_text(
        json.dumps(props, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    if not wedge:
        try:
            _take_png(inst, bundle / "shot.png")
        except MdevError:
            pass
    return bundle


WEDGE_PROTOCOL = "movian-lifecycle-wedge-v1"


def _proc_start_ticks(pid: int) -> int | None:
    try:
        value = Path("/proc/%d/stat" % pid).read_text(encoding="utf-8")
        fields = value[value.rfind(")") + 2:].split()
        return int(fields[19])
    except (OSError, ValueError, IndexError):
        return None


def _proc_tracer_pid(pid: int) -> int | None:
    try:
        status = Path("/proc/%d/status" % pid).read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"(?m)^TracerPid:\s*(\d+)$", status)
    return int(match.group(1)) if match else None


def _gdb_process_matches(control: dict[str, Any]) -> bool:
    pid = control.get("gdbPid")
    start_ticks = control.get("gdbStartTicks")
    if not isinstance(pid, int) or not isinstance(start_ticks, int):
        return False
    try:
        comm = Path("/proc/%d/comm" % pid).read_text(
            encoding="utf-8").strip()
    except OSError:
        return False
    return comm == "gdb" and _proc_start_ticks(pid) == start_ticks


def _attached_collector_control(
    inst: Instance,
    inferior_pid: int,
) -> dict[str, Any] | None:
    state = inst.load_state() or {}
    if state.get("collector") is not True:
        return None
    control = state.get("collectorControl")
    if not isinstance(control, dict):
        raise MdevError("collector state lacks same-session wedge control")
    if control.get("protocol") != WEDGE_PROTOCOL:
        raise MdevError("collector wedge-control protocol mismatch")
    if state.get("pid") != inferior_pid:
        raise MdevError("collector state inferior pid mismatch")
    session_id = control.get("sessionId")
    if not isinstance(session_id, str) or not session_id:
        raise MdevError("collector state lacks session identity")
    if not _gdb_process_matches(control):
        raise MdevError("launch GDB identity proof failed")
    gdb_pid = control["gdbPid"]
    if _proc_tracer_pid(inferior_pid) != gdb_pid:
        raise MdevError("owned inferior is not traced by launch GDB")
    state_dir = inst.dir.resolve()
    for key in ("requestPath", "responsePath"):
        value = control.get(key)
        if not isinstance(value, str) or \
                Path(value).resolve().parent != state_dir:
            raise MdevError("collector %s escapes instance state" % key)
    return control


def _write_capture_result(
    artifact: Path,
    status: str,
    detail: str,
    timeout: float,
    source: str,
    pid: int | None = None,
    output: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = dict(extra or {})
    result.update({
        "status": status,
        "source": source,
        "detail": detail,
        "dumpPath": str(artifact.resolve()),
        "pid": pid,
    })
    lines = [
        "capture-status: %s" % status,
        "capture-source: %s" % source,
        "pid: %s" % (pid if pid is not None else "none"),
        "timeout-seconds: %g" % timeout,
        "detail: %s" % detail,
    ]
    if output:
        lines.extend(("", "gdb-output:", output.rstrip()))
    try:
        artifact.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass
    return result


def _capture_from_attached_gdb(
    artifact: Path,
    control: dict[str, Any],
    inst: Instance,
    inferior_pid: int,
    timeout: float,
    trigger: str,
    classification: str,
    classification_detail: str,
    subsystem: str,
    resource: str,
) -> dict[str, Any]:
    request_path = Path(control["requestPath"])
    response_path = Path(control["responsePath"])
    request_id = uuid.uuid4().hex
    request = {
        "protocol": WEDGE_PROTOCOL,
        "sessionId": control["sessionId"],
        "requestId": request_id,
        "gdbPid": control["gdbPid"],
        "inferiorPid": inferior_pid,
        "trigger": trigger,
        "classification": classification,
        "classificationDetail": classification_detail,
        "subsystem": subsystem,
        "resource": resource,
        "dumpPath": str(artifact.resolve()),
        "classifiedMonotonicNs": time.monotonic_ns(),
    }
    try:
        response_path.unlink()
    except OSError:
        pass
    temporary = request_path.with_suffix(".json.tmp")
    try:
        temporary.write_text(
            json.dumps(request, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        temporary.replace(request_path)
    except OSError as error:
        return _write_capture_result(
            artifact, "error", "could not publish wedge request: %s" % error,
            timeout, "launch-attached-gdb", pid=inferior_pid,
            extra={"sessionId": control["sessionId"],
                   "gdbPid": control["gdbPid"]})
    # Pin the inferior identity with a pidfd BEFORE signaling, then re-prove
    # the launch-GDB session still owns it.  A numeric os.kill would race a
    # recycled PID; the pidfd cannot target the wrong process, and any open /
    # revalidation / send failure fails closed here (no reactive gdb -p).
    try:
        pidfd = os.pidfd_open(inferior_pid)
    except OSError as error:
        return _write_capture_result(
            artifact, "error",
            "could not open pidfd for inferior: %s" % error,
            timeout, "launch-attached-gdb", pid=inferior_pid,
            extra={"sessionId": control["sessionId"],
                   "gdbPid": control["gdbPid"]})
    try:
        if not inst.owns_pid(inferior_pid) or \
                not _gdb_process_matches(control) or \
                _proc_tracer_pid(inferior_pid) != control["gdbPid"]:
            return _write_capture_result(
                artifact, "error",
                "pidfd revalidation failed: launch GDB no longer traces the "
                "pinned inferior", timeout, "launch-attached-gdb",
                pid=inferior_pid,
                extra={"sessionId": control["sessionId"],
                       "gdbPid": control["gdbPid"]})
        try:
            signal.pidfd_send_signal(pidfd, signal.SIGSTOP)
        except OSError as error:
            return _write_capture_result(
                artifact, "error",
                "could not send SIGSTOP via pidfd: %s" % error,
                timeout, "launch-attached-gdb", pid=inferior_pid,
                extra={"sessionId": control["sessionId"],
                       "gdbPid": control["gdbPid"]})
    finally:
        try:
            os.close(pidfd)
        except OSError:
            pass

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = json.loads(response_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            time.sleep(0.05)
            continue
        if not isinstance(response, dict) or \
                response.get("protocol") != WEDGE_PROTOCOL or \
                response.get("sessionId") != control["sessionId"] or \
                response.get("requestId") != request_id:
            time.sleep(0.05)
            continue
        result = {
            "status": response.get("status", "error"),
            "source": "launch-attached-gdb",
            "detail": response.get("detail", ""),
            "dumpPath": str(artifact.resolve()),
            "pid": inferior_pid,
            "sessionId": control["sessionId"],
            "gdbPid": control["gdbPid"],
            "threadCount": response.get("threadCount", 0),
            "frameCount": response.get("frameCount", 0),
            "movianFramePresent": response.get(
                "movianFramePresent", False),
            "emergencyEject": response.get("emergencyEject"),
        }
        if not artifact.is_file() or artifact.stat().st_size == 0:
            return _write_capture_result(
                artifact, "error",
                "launch GDB response arrived without a non-empty dump",
                timeout, "launch-attached-gdb", pid=inferior_pid,
                extra=result)
        return result
    return _write_capture_result(
        artifact, "timeout",
        "launch GDB capture exceeded the %g-second bound" % timeout,
        timeout, "launch-attached-gdb", pid=inferior_pid,
        extra={"sessionId": control["sessionId"],
               "gdbPid": control["gdbPid"]})


def _capture_wedge_backtrace(
    inst: Instance,
    bundle: Path,
    timeout: float = WEDGE_BACKTRACE_TIMEOUT,
    trigger: str = "smoke-health",
    classification: str = "instance-health-wedge",
    classification_detail: str = "mdev health probe failed",
    subsystem: str = "screenshot-health",
    resource: str = "/api/screenshot/raw",
) -> dict[str, Any]:
    """Capture before cleanup, preferring the launch-attached GDB session."""
    artifact = bundle / WEDGE_BACKTRACE_FILE
    try:
        pid = inst.live_pid()
        if pid is None:
            return _write_capture_result(
                artifact, "skipped",
                "no live pid proven owned by this instance", timeout,
                "none")
        if not inst.owns_pid(pid):
            return _write_capture_result(
                artifact, "skipped",
                "pid ownership proof failed before capture", timeout,
                "none", pid=pid)
        try:
            control = _attached_collector_control(inst, pid)
        except MdevError as error:
            # A collector-marked run must never fall back to reactive gdb -p:
            # losing the already-attached session is a hard capture failure.
            return _write_capture_result(
                artifact, "error", str(error), timeout,
                "launch-attached-gdb", pid=pid)
        if control is not None:
            return _capture_from_attached_gdb(
                artifact, control, inst, pid, timeout, trigger, classification,
                classification_detail, subsystem, resource)

        command = [
            "gdb", "--batch", "--nx", "--quiet", "-p", str(pid),
            "-ex", "set pagination off",
            "-ex", "set confirm off",
            "-ex", "thread apply all bt",
            "-ex", "detach",
        ]
        try:
            completed = subprocess.run(
                command, stdin=subprocess.DEVNULL, capture_output=True,
                text=True, check=False, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            stdout = error.stdout or ""
            stderr = error.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            return _write_capture_result(
                artifact, "timeout",
                "gdb exceeded the %g-second bound" % timeout, timeout,
                "reactive-gdb-attach", pid=pid, output=stdout + stderr)
        except OSError as error:
            return _write_capture_result(
                artifact, "error", "could not execute gdb: %s" % error,
                timeout, "reactive-gdb-attach", pid=pid)

        output = completed.stdout + completed.stderr
        frame_count = len(re.findall(r"(?m)^#\d+\s", output))
        if completed.returncode != 0:
            status = "error"
            detail = "gdb exited with status %d" % completed.returncode
        elif frame_count == 0:
            status = "error"
            detail = "gdb returned no stack frames"
        else:
            status = "success"
            detail = "captured %d stack frames" % frame_count
        return _write_capture_result(
            artifact, status, detail, timeout, "reactive-gdb-attach",
            pid=pid, output=output, extra={"frameCount": frame_count})
    except Exception as error:
        return _write_capture_result(
            artifact, "error", "unexpected capture failure: %s: %s" %
            (type(error).__name__, error), timeout, "unknown")


def _cleanup_collector_debugger(
    inst: Instance,
    timeout: float = 3.0,
) -> dict[str, Any]:
    state = inst.load_state() or {}
    if state.get("collector") is not True:
        return {"status": "not-applicable"}
    control = state.get("collectorControl")
    if not isinstance(control, dict):
        return {"status": "identity-refused",
                "detail": "missing collectorControl"}
    gdb_pid = control.get("gdbPid")
    if not _gdb_process_matches(control):
        return {"status": "exited", "gdbPid": gdb_pid}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and _gdb_process_matches(control):
        time.sleep(0.05)
    if not _gdb_process_matches(control):
        return {"status": "exited", "gdbPid": gdb_pid}
    try:
        os.kill(gdb_pid, signal.SIGTERM)
    except ProcessLookupError:
        return {"status": "exited", "gdbPid": gdb_pid}
    deadline = time.monotonic() + min(timeout, 2.0)
    while time.monotonic() < deadline and _gdb_process_matches(control):
        time.sleep(0.05)
    if _gdb_process_matches(control):
        try:
            os.kill(gdb_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        time.sleep(0.1)
        status = "force-killed" if not _gdb_process_matches(control) \
            else "still-alive"
    else:
        status = "terminated"
    return {"status": status, "gdbPid": gdb_pid}


def _record_stop_outcome(
    bundle: Path,
    transcript: dict[str, Any],
    stop_outcome: str,
) -> None:
    transcript["stop_outcome"] = stop_outcome
    (bundle / "steps.json").write_text(
        json.dumps(transcript, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")


def _capture_and_stop_wedge(
    inst: Instance,
    bundle: Path,
    transcript: dict[str, Any],
    trigger: str,
    classification_detail: str,
) -> tuple[str, dict[str, Any]]:
    lower_detail = classification_detail.lower()
    if "screenshot" in lower_detail or "504" in lower_detail:
        subsystem = "screenshot-health"
        resource = "/api/screenshot/raw"
    else:
        subsystem = "startup-readiness"
        resource = UI_FRAMERATE
    transcript["ordering"] = ["classified"]
    capture = _capture_wedge_backtrace(
        inst, bundle, trigger=trigger,
        classification="instance-health-wedge",
        classification_detail=classification_detail,
        subsystem=subsystem, resource=resource)
    transcript["wedge_capture"] = capture
    transcript["ordering"].append("capture:%s" % capture["status"])
    stop_outcome = stop_wedged_instance(inst)
    transcript["ordering"].append("owned-cleanup:%s" % stop_outcome)
    debugger_cleanup = _cleanup_collector_debugger(inst)
    transcript["collector_gdb_cleanup"] = debugger_cleanup
    transcript["ordering"].append(
        "gdb-cleanup:%s" % debugger_cleanup["status"])
    if inst.live_pid() is not None:
        stop_outcome = stop_wedged_instance(inst)
        transcript["ordering"].append("final-owned-cleanup:%s" % stop_outcome)
    _record_stop_outcome(bundle, transcript, stop_outcome)
    return stop_outcome, debugger_cleanup


def _run_one(
    inst: Instance,
    definition: dict[str, Any],
    route_builder: Callable[[str, str | None], str],
) -> tuple[bool, bool, dict[str, Any], Path | None]:
    records: list[dict[str, Any]] = []
    previous_delta = harness.read_log(inst)
    failure: dict[str, Any] | None = None

    for index, step in enumerate(definition["steps"], 1):
        record: dict[str, Any] = {
            "index": index,
            "verb": step["do"],
            "step": step,
        }
        try:
            detail, previous_delta, step_hash, metrics = _execute_step(
                inst, definition["name"], step, previous_delta, route_builder)
            record.update({"verdict": "pass", "detail": detail})
            if step_hash is not None:
                record["hash"] = step_hash
            if metrics:
                record["metrics"] = metrics
            records.append(record)
        except (MdevError, StepFailure) as error:
            record.update({"verdict": "fail", "detail": str(error)})
            evidence = getattr(error, "evidence", None)
            if evidence is not None:
                record["evidence"] = evidence
            records.append(record)
            failure = {
                "step_index": index,
                "verb": step["do"],
                "detail": str(error),
            }
            break

    if failure is None:
        transcript = {
            "smoke": definition["name"],
            "instance": inst.name,
            "verdict": "pass",
            "steps": records,
        }
        return True, False, transcript, None

    healthy, health_detail = _probe_health(inst)
    wedge = definition["name"] == "health" or not healthy
    failure["instance_health"] = health_detail
    transcript = {
        "smoke": definition["name"],
        "instance": inst.name,
        "verdict": "instance-health failure" if wedge else "assertion failure",
        "failure": failure,
        "steps": records,
    }
    bundle = _write_bundle(inst, definition["name"], transcript, wedge)
    stop_outcome = None
    debugger_cleanup: dict[str, Any] | None = None
    if wedge:
        trigger = ("health-step" if failure["verb"] == "health"
                   else "post-failure-health-probe:%s" % failure["verb"])
        classification_detail = (
            failure["detail"] if definition["name"] == "health"
            else health_detail)
        stop_outcome, debugger_cleanup = _capture_and_stop_wedge(
            inst, bundle, transcript, trigger, classification_detail)
        if stop_outcome == "still-alive" or \
                debugger_cleanup["status"] in (
                    "still-alive", "identity-refused"):
            pid = inst.live_pid()
            print("instance-health failure (wedge), stop+relaunch [%s]: %s"
                  % (stop_outcome, health_detail), file=sys.stderr)
            print(str(bundle), file=sys.stderr)
            raise MdevError(
                "owned cleanup incomplete (pid=%d, gdb=%s)"
                % (pid or 0, debugger_cleanup["status"]),
                exit_code=2,
            )
    if wedge:
        print("instance-health failure (wedge), stop+relaunch [%s]: %s"
              % (stop_outcome, health_detail), file=sys.stderr)
    else:
        print("smoke %s failed at step %d (%s): %s" % (
            definition["name"], failure["step_index"], failure["verb"],
            failure["detail"]), file=sys.stderr)
    print(str(bundle), file=sys.stderr)
    return False, wedge, transcript, bundle


def run(
    definitions: list[dict[str, Any]],
    target: str,
    instance_name: str,
    route_builder: Callable[[str, str | None], str],
) -> tuple[int, dict[str, Any], str]:
    selected = select_definitions(definitions, target)
    plugins = _plugins_for(selected)
    inst = harness.ensure_running(instance_name, plugins)

    results = []
    bundles = []
    green = 0

    if target != "all" and selected[0]["name"] != "health":
        healthy, detail = _probe_health(inst)
        if not healthy:
            transcript = {
                "smoke": selected[0]["name"],
                "instance": inst.name,
                "verdict": "instance-health failure",
                "failure": {"step_index": 0, "verb": "health",
                            "detail": detail},
                "steps": [],
            }
            bundle = _write_bundle(inst, selected[0]["name"], transcript, True)
            stop_outcome, debugger_cleanup = _capture_and_stop_wedge(
                inst, bundle, transcript, "pre-smoke-health-probe", detail)
            print("instance-health failure (wedge), stop+relaunch [%s]: %s"
                  % (stop_outcome, detail), file=sys.stderr)
            print(str(bundle), file=sys.stderr)
            if stop_outcome == "still-alive" or \
                    debugger_cleanup["status"] in (
                        "still-alive", "identity-refused"):
                data = {"instance": inst.name, "green": 0, "total": 1,
                        "results": [transcript], "bundles": [str(bundle)],
                        "stop_outcome": stop_outcome,
                        "gdb_cleanup": debugger_cleanup}
                return 2, data, "0/1 green (cleanup incomplete)"
            data = {"instance": inst.name, "green": 0, "total": 1,
                    "results": [transcript], "bundles": [str(bundle)],
                    "stop_outcome": stop_outcome,
                    "gdb_cleanup": debugger_cleanup}
            return 2, data, "0/1 green"

    for definition in selected:
        ok, wedge, transcript, bundle = _run_one(inst, definition, route_builder)
        results.append(transcript)
        if ok:
            green += 1
        if bundle is not None:
            bundles.append(str(bundle))
        if wedge:
            break

    total = len(selected)
    code = 2 if any(result["verdict"] == "instance-health failure"
                    for result in results) else (0 if green == total else 1)
    data = {
        "instance": inst.name,
        "green": green,
        "total": total,
        "results": results,
        "bundles": bundles,
    }
    human_lines = []
    for result in results:
        if result["verdict"] == "pass":
            human_lines.append("PASS %s (%d steps)" %
                               (result["smoke"], len(result["steps"])))
        else:
            human_lines.append("FAIL %s: %s" %
                               (result["smoke"], result["verdict"]))
    human_lines.append("%d/%d green" % (green, total))
    return code, data, "\n".join(human_lines)
