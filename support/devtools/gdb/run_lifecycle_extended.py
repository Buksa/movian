#!/usr/bin/env python3
"""Run real GDB lifecycle scenarios beyond the S3/S4 pair.

Every scenario uses the launch-attached collector, a disposable mdev profile,
and an HTTP action.  Action evidence is recorded separately from the final
Movian result: the selected debug binary currently crashes during its ordinary
GLW shutdown path, so a successful reload action must not be promoted to an
overall PASS when the inferior later exits abnormally.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

SCRIPT_DIR = Path(__file__).resolve().parent
REPO = SCRIPT_DIR.parents[2]
STATE_ROOT = Path("/tmp/mdev")
ARCHIVE_ROOT = Path("/tmp/movian-lifecycle")
COLLECTOR = SCRIPT_DIR / "movian_lifecycle.py"
BINARY = os.environ.get("MOVIAN_LIFECYCLE_BINARY",
                       str(REPO / "build.debug/movian"))
PLUGIN = os.environ.get("MOVIAN_LIFECYCLE_PLUGIN",
                       str(SCRIPT_DIR / "lifecycle_test_plugin"))
INVENTORY = os.environ.get("MOVIAN_LIFECYCLE_INVENTORY",
                           str(SCRIPT_DIR / "inventory.json"))
SCENARIO_CATEGORIES = (
    "core-init,shutdown-hook,init-system,init-helper,thread-create,"
    "plugin,es-plugin,es-context,es-resource,navigator,glw,backend,"
    "service,cache"
)

sys.path.insert(0, str(SCRIPT_DIR))
from run_lifecycle_scenarios import (  # noqa: E402
    Instance,
    event_evidence,
    find_port_from_log,
    http_get,
    parse_summary,
    reload_plugin,
    stop_collector,
    wait_startup_settled,
)
from mdevlib.harness import http_request  # noqa: E402


def _state_dir(name: str) -> Path:
    return STATE_ROOT / name


def _prepare_state(name: str) -> tuple[Path, Path, Path]:
    state = _state_dir(name)
    cache = state / "cache"
    log_dir = cache / "log"
    state.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    for path in (log_dir / "movian-0.log", state / "events.jsonl"):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    return state, cache, state / "events.jsonl"


def _launch(name: str, plugin: bool) -> subprocess.Popen[str]:
    command = [
        sys.executable, str(COLLECTOR), "launch",
        "--name", name,
        "--mode", "gdb-collector",
        "--duration", "60",
        "--expect-natural-exit",
        "--categories", SCENARIO_CATEGORIES,
        "--binary", BINARY,
        "--inventory", INVENTORY,
        "--display", os.environ.get("DISPLAY", ":0"),
        "--wayland", os.environ.get("WAYLAND_DISPLAY", "wayland-0"),
    ]
    if plugin:
        command += ["--plugin", PLUGIN]
    env = dict(os.environ)
    env["DISPLAY"] = os.environ.get("DISPLAY", ":0")
    env["WAYLAND_DISPLAY"] = os.environ.get("WAYLAND_DISPLAY", "wayland-0")
    return subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, cwd=REPO, env=env)


def _count_symbol(events_path: Path, symbol: str) -> int:
    if not events_path.exists():
        return 0
    count = 0
    with events_path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("symbol") == symbol:
                count += 1
    return count


def _wait_http(name: str, cache: Path, events: Path) -> tuple[Instance, int]:
    port = find_port_from_log(str(cache), timeout=40)
    if port is None:
        raise RuntimeError("no HTTP port")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if http_get(port, "/api/prop/global"):
            break
        time.sleep(0.2)
    else:
        raise RuntimeError("HTTP control did not become ready")
    if not wait_startup_settled(str(events), timeout=30):
        raise RuntimeError("navigator/GLW startup did not settle")
    return Instance(name), port

def _response_evidence(response: dict) -> dict:
    result = {}
    for key, value in response.items():
        if key == "body":
            result["bodyLength"] = len(value) if isinstance(value, bytes) else 0
        elif isinstance(value, (str, int, float, bool)) or value is None:
            result[key] = value
        else:
            result[key] = repr(value)
    return result


def _quit(instance: Instance) -> dict:
    return _response_evidence(http_request(
        instance.base_url(), "/api/input/action/Quit", timeout=5, method="GET"))


def _run(name: str, label: str, plugin: bool, action) -> dict:
    _state, cache, events = _prepare_state(name)
    proc = _launch(name, plugin)
    action_result: dict = {"ok": False, "steps": []}
    stdout = ""
    started = False
    try:
        try:
            instance, port = _wait_http(name, cache, events)
            started = True
            action_result = action(instance, port, events)
        except Exception as exc:
            action_result = {"ok": False, "error": repr(exc), "steps": []}
        if started and action_result.get("sendQuit", True):
            action_result["quit"] = _quit(Instance(name))
        try:
            stdout, _ = proc.communicate(timeout=120)
        except subprocess.TimeoutExpired:
            stdout = stop_collector(proc, name)
            action_result["collectorTimeout"] = True
    finally:
        if proc.poll() is None:
            stdout = stop_collector(proc, name)
    summary = parse_summary(stdout)
    if not summary:
        summary = {"status": "INFRA_ERROR", "ok": False,
                   "failureReasons": ["collector-produced-no-summary"]}
    try:
        symbols, exit_codes = event_evidence(str(events))
    except (OSError, ValueError) as exc:
        symbols, exit_codes = set(), []
        summary.setdefault("failureReasons", []).append(
            "event-evidence:%s" % exc)
    required = set(action_result.get("requiredSymbols", []))
    missing = sorted(required - symbols)
    reasons = list(summary.get("failureReasons") or [])
    if missing:
        reasons.append("missing-events:%s" % ",".join(missing))
    summary["failureReasons"] = reasons
    summary["scenario"] = label
    summary["scenarioAction"] = action_result
    summary["scenarioEvidence"] = {
        "requiredSymbols": sorted(required),
        "missingSymbols": missing,
        "observedSymbolCount": len(symbols),
        "inferiorExitCodes": exit_codes,
    }
    summary["actionStatus"] = "PASS" if action_result.get("ok") and not missing else "FAIL"
    summary["ok"] = not reasons
    summary["status"] = "PASS" if summary["ok"] else summary.get("status", "FAIL")
    archive = ARCHIVE_ROOT / "extended" / label
    archive.mkdir(parents=True, exist_ok=True)
    if events.exists():
        shutil.copy2(events, archive / "events.jsonl")
    (archive / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def _ui_reload(instance: Instance, port: int, events: Path) -> dict:
    before = _count_symbol(events, "glw_view_create")
    response = http_request(instance.base_url(), "/api/input/action/ReloadUI",
                            timeout=5, method="POST")
    alive = http_get(port, "/api/prop/global")
    after = _count_symbol(events, "glw_view_create")
    return {
        "ok": bool(response.get("ok") and alive),
        "response": _response_evidence(response),
        "httpAliveAfterReload": alive,
        "viewCreateBefore": before,
        "viewCreateAfter": after,
        "requiredSymbols": ["glw_init2", "glw_view_create"],
    }


def _plugin_reload(instance: Instance, _port: int, events: Path) -> dict:
    ok, details, delta = reload_plugin(instance, PLUGIN, settle=15)
    marker = "lscen145: plugin loaded, resources created: 5" in delta
    return {
        "ok": bool(ok and marker),
        "reload": details,
        "resourceMarker": marker,
        "requiredSymbols": ["plugins_reload_dev_plugin",
                            "ecmascript_plugin_load",
                            "ecmascript_plugin_unload"],
    }


def _repeat_reload(instance: Instance, _port: int, events: Path) -> dict:
    steps = []
    for cycle in range(1, 4):
        ok, details, delta = reload_plugin(instance, PLUGIN, settle=15)
        steps.append({
            "cycle": cycle,
            "ok": ok,
            "resourceMarker": "lscen145: plugin loaded, resources created: 5" in delta,
            "details": details,
        })
        if not ok:
            break
    return {
        "ok": len(steps) == 3 and all(step["ok"] and step["resourceMarker"]
                                      for step in steps),
        "cycles": steps,
        "reloadEventCount": _count_symbol(events, "plugins_reload_dev_plugin"),
        "requiredSymbols": ["plugins_reload_dev_plugin",
                            "ecmascript_plugin_load",
                            "ecmascript_plugin_unload"],
    }


def _safe_error(instance: Instance, port: int, _events: Path) -> dict:
    response = http_request(
        instance.base_url(), "/api/lifecycle/__missing_endpoint__",
        timeout=5, method="GET")
    alive = http_get(port, "/api/prop/global")
    return {
        "ok": bool(not response.get("ok") and alive),
        "expectedError": not response.get("ok"),
        "response": _response_evidence(response),
        "httpAliveAfterError": alive,
        "requiredSymbols": ["main_init"],
    }


def scenarios() -> dict[str, tuple[bool, object]]:
    return {
        "ui-reload": (False, _ui_reload),
        "plugin-reload-unload": (True, _plugin_reload),
        "repeated-reload": (True, _repeat_reload),
        "safe-forced-error": (False, _safe_error),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=sorted(list(scenarios()) + ["all"]))
    args = parser.parse_args(argv)
    selected = list(scenarios()) if args.scenario == "all" else [args.scenario]
    results = {}
    for label in selected:
        plugin, action = scenarios()[label]
        name = "lifecycle-extended-" + label.replace("-", "_")
        results[label] = _run(name, label, plugin, action)
        print(json.dumps({label: results[label]}, sort_keys=True))
    out = ARCHIVE_ROOT / "extended" / "latest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0 if all(result.get("ok") for result in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
