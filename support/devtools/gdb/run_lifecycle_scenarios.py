#!/usr/bin/env python3
"""run_lifecycle_scenarios.py -- run lifecycle scenarios s3 (reload) and
s4 (normal shutdown) using the GDB collector + HTTP action injection."""

from __future__ import annotations
import json
import os
import subprocess
import sys
import time
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(SCRIPT_DIR, "../../.."))
COLLECTOR = os.path.join(SCRIPT_DIR, "movian_lifecycle.py")
BINARY = os.path.join(REPO, "build.debug/movian")
PLUGIN = os.path.join(SCRIPT_DIR, "lifecycle_test_plugin")
STATE_ROOT = "/tmp/mdev"
ARCHIVE_ROOT = "/tmp/movian-lifecycle"
SCENARIO_CATEGORIES = (
    "core-init,shutdown-hook,init-system,init-helper,thread-create,"
    "plugin,es-plugin,es-context,es-resource,navigator,glw,backend,"
    "service,cache"
)

sys.path.insert(0, os.path.abspath(os.path.join(SCRIPT_DIR, "..")))
from mdevlib.harness import (  # noqa: E402
    Instance,
    MdevError,
    http_request,
    kill_owned_pid,
    log_size,
    read_log_delta,
    reload_js_fail_lines,
    reload_js_ok_lines,
    reload_line_matches_dir,
)


def find_port_from_log(cache_dir: str, timeout: float = 40.0) -> int | None:
    """Poll movian log for HTTP port line. Matches 'http-server: Listening on port'."""
    log_path = os.path.join(cache_dir, "log", "movian-0.log")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(log_path):
            try:
                with open(log_path, "r") as f:
                    for line in f:
                        if "http-server: Listening on port " in line:
                            return int(line.split("http-server: Listening on port ")[1].split()[0])
            except (OSError, ValueError):
                pass
        time.sleep(0.2)
    return None


def http_get(port: int, path: str, timeout: float = 3.0) -> bool:
    try:
        req = urllib.request.urlopen(
            f"http://127.0.0.1:{port}{path}", timeout=timeout
        )
        return req.status == 200
    except Exception:
        return False


def reload_plugin(instance: Instance, plugin_dir: str,
                  settle: float = 15.0) -> tuple[bool, list[dict], str]:
    """Send ReloadData and apply mdev's authoritative reload log checks."""
    offset = log_size(instance)
    response = http_request(
        instance.base_url(), "/api/input/action/ReloadData",
        timeout=5.0, method="GET")
    if not response.get("ok"):
        return False, [{
            "plugin": plugin_dir,
            "ok": False,
            "detail": response.get("error") or response.get("status"),
        }], ""

    deadline = time.monotonic() + settle
    delta = ""
    while time.monotonic() < deadline:
        delta = read_log_delta(instance, offset)
        failures = [
            line for line in reload_js_fail_lines(delta)
            if reload_line_matches_dir(line, plugin_dir)
        ]
        successes = [
            line for line in reload_js_ok_lines(delta)
            if reload_line_matches_dir(line, plugin_dir)
        ]
        if failures:
            return False, [{
                "plugin": plugin_dir, "ok": False, "detail": failures[0],
            }], delta
        if successes:
            return True, [{
                "plugin": plugin_dir, "ok": True, "detail": successes[-1],
            }], delta
        time.sleep(0.15)
    return False, [{
        "plugin": plugin_dir, "ok": False, "detail": "no reload result seen",
    }], delta


def run_collector(name: str, extra_args: list[str], duration: float = 1.0,
                  env_override: dict | None = None) -> dict:
    """Run the GDB collector and parse its summary."""
    env = dict(os.environ)
    if env_override:
        env.update(env_override)
    env["DISPLAY"] = env.get("DISPLAY", ":0")
    env["WAYLAND_DISPLAY"] = env.get("WAYLAND_DISPLAY", "wayland-0")
    cmd = [
        sys.executable, COLLECTOR, "launch",
        "--name", name,
        "--mode", "gdb-collector",
        "--start-url", "page:home",
        "--binary", BINARY,
        "--duration", str(duration),
        "--display", env["DISPLAY"],
        "--wayland", env["WAYLAND_DISPLAY"],
    ] + extra_args
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=300, env=env, cwd=REPO
    )
    # Parse MOVIAN_LIFECYCLE_SUMMARY from stdout
    for line in (result.stdout + result.stderr).splitlines():
        if line.startswith("MOVIAN_LIFECYCLE_SUMMARY "):
            import json
            return json.loads(line[len("MOVIAN_LIFECYCLE_SUMMARY "):])
    return {"error": "no summary", "stdout": result.stdout[-2000:],
            "stderr": result.stderr[-2000:]}


def archive_scenario(run_id: str, scenario_name: str, state_dir: str):
    """Copy JSONL + derived artifacts under ARCHIVE_ROOT/<run_id>/."""
    archive_dir = os.path.join(ARCHIVE_ROOT, run_id)
    os.makedirs(archive_dir, exist_ok=True)
    import shutil
    events = os.path.join(state_dir, "events.jsonl")
    if os.path.exists(events):
        shutil.copy2(events, os.path.join(archive_dir, f"{scenario_name}-events.jsonl"))
    # Copy any derived artifacts from analyze step
    for fname in os.listdir(archive_dir):
        pass  # already there
    return archive_dir


def stop_collector(proc: subprocess.Popen, name: str) -> str:
    """Ask the launcher to abort so its ownership-checked cleanup runs."""
    if proc.poll() is None:
        proc.terminate()
    try:
        stdout, _ = proc.communicate(timeout=45)
        return stdout
    except subprocess.TimeoutExpired:
        instance = Instance(name)
        state = instance.load_state() or {}
        inferior = state.get("pid")
        if isinstance(inferior, int):
            kill_owned_pid(instance, inferior, timeout=8.0)
        try:
            stdout, _ = proc.communicate(timeout=15)
            return stdout
        except subprocess.TimeoutExpired:
            # The owned inferior is already gone, so force-killing only this
            # launcher cannot orphan Movian.
            proc.kill()
            stdout, _ = proc.communicate(timeout=5)
            return stdout


def parse_summary(stdout: str) -> dict:
    for line in stdout.splitlines():
        if line.startswith("MOVIAN_LIFECYCLE_SUMMARY "):
            import json
            return json.loads(line[len("MOVIAN_LIFECYCLE_SUMMARY "):])
    return {}


def event_evidence(events_path: str) -> tuple[set[str], list[int | None]]:
    import json
    symbols = set()
    exit_codes = []
    with open(events_path) as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError as exc:
                raise ValueError(
                    "%s:%d: malformed JSON: %s" %
                    (events_path, line_number, exc))
            if event.get("event") == "enter" and event.get("symbol"):
                symbols.add(event["symbol"])
            if event.get("event") == "inferior-exited":
                exit_codes.append(event.get("exitCode"))
    return symbols, exit_codes


def wait_startup_settled(events_path: str, timeout: float = 30.0) -> bool:
    """Wait for navigator/GLW startup and one quiet second of event output."""
    required = {"nav_open0", "glw_init2", "glw_view_create"}
    deadline = time.monotonic() + timeout
    last_size = None
    quiet_since = None
    while time.monotonic() < deadline:
        try:
            size = os.path.getsize(events_path)
            symbols, _ = event_evidence(events_path)
        except (OSError, ValueError):
            time.sleep(0.2)
            continue
        now = time.monotonic()
        if size != last_size:
            last_size = size
            quiet_since = now
        elif (required <= symbols and quiet_since is not None
              and now - quiet_since >= 1.0):
            return True
        time.sleep(0.2)
    return False


def finish_summary(summary: dict, events_path: str,
                   required_symbols: set[str]) -> dict:
    reasons = list(summary.get("failureReasons") or [])
    if not summary.get("ok") and not reasons:
        reasons.append("collector-summary-not-ok")
    try:
        symbols, exit_codes = event_evidence(events_path)
    except (OSError, ValueError) as exc:
        symbols, exit_codes = set(), []
        reasons.append("event-evidence:%s" % exc)
    missing = sorted(required_symbols - symbols)
    if missing:
        reasons.append("missing-events:%s" % ",".join(missing))
    if exit_codes != [0]:
        reasons.append("inferior-exit-codes:%s" % exit_codes)
    summary["scenarioEvidence"] = {
        "requiredSymbols": sorted(required_symbols),
        "missingSymbols": missing,
        "inferiorExitCodes": exit_codes,
    }
    summary["failureReasons"] = reasons
    summary["ok"] = not reasons
    return summary


def scenario_3_reload():
    """Reload the dev plugin, prove reload success, then request clean exit."""
    name = "lscen145-s3"
    state_dir = os.path.join(STATE_ROOT, name)
    cache_dir = os.path.join(state_dir, "cache")
    events_path = os.path.join(state_dir, "events.jsonl")
    log_dir = os.path.join(cache_dir, "log")
    for path in (state_dir, cache_dir, log_dir):
        os.makedirs(path, exist_ok=True)
    for stale_path in (os.path.join(log_dir, "movian-0.log"), events_path):
        try:
            os.unlink(stale_path)
        except FileNotFoundError:
            pass

    env = dict(os.environ)
    env["DISPLAY"] = ":0"
    env["WAYLAND_DISPLAY"] = "wayland-0"
    cmd = [
        sys.executable, COLLECTOR, "launch",
        "--name", name,
        "--mode", "gdb-collector",
        "--start-url", "page:home",
        "--binary", BINARY,
        "--plugin", PLUGIN,
        "--duration", "60",
        "--expect-natural-exit",
        "--categories", SCENARIO_CATEGORIES,
        "--display", ":0",
        "--wayland", "wayland-0",
    ]
    print("[s3] Launching collector with plugin")
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env=env, cwd=REPO, text=True)

    port = find_port_from_log(cache_dir, timeout=40)
    if port is None:
        stop_collector(proc, name)
        print("[s3] FAILED: no HTTP port")
        return None
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if http_get(port, "/api/prop/global"):
            break
        time.sleep(0.3)
    else:
        stop_collector(proc, name)
        print("[s3] FAILED: HTTP not ready")
        return None
    if not wait_startup_settled(events_path):
        stop_collector(proc, name)
        print("[s3] FAILED: navigator/GLW startup did not settle")
        return None

    instance = Instance(name)
    try:
        reload_ok, details, reload_delta = reload_plugin(
            instance, PLUGIN, settle=15.0)
    except MdevError as exc:
        stop_collector(proc, name)
        print("[s3] FAILED: %s" % exc)
        return None
    if not reload_ok:
        stop_collector(proc, name)
        print("[s3] FAILED: ReloadData: %s" % details)
        return None
    if "lscen145: plugin loaded, resources created: 5" not in reload_delta:
        stop_collector(proc, name)
        print("[s3] FAILED: missing plugin resource-count marker")
        return None

    shutdown = http_request(
        instance.base_url(), "/api/input/action/Quit",
        timeout=5.0, method="GET")
    if not shutdown.get("ok"):
        stop_collector(proc, name)
        print("[s3] FAILED: Quit after reload: %s" % shutdown)
        return None
    try:
        stdout, _ = proc.communicate(timeout=120)
    except subprocess.TimeoutExpired:
        stop_collector(proc, name)
        print("[s3] FAILED: collector did not finish")
        return None

    summary = parse_summary(stdout)
    if not summary:
        print("[s3] FAILED: collector produced no summary")
        return None
    summary = finish_summary(
        summary, events_path,
        {"plugins_reload_dev_plugin", "ecmascript_plugin_unload",
         "ecmascript_plugin_load"})
    summary["reloadDetails"] = details
    print("[s3] Summary: ok=%s exitCode=%s" %
          (summary.get("ok"), summary.get("exitCode")))
    return summary


def scenario_4_shutdown():
    """Request Quit and require the normal shutdown/fini event sequence."""
    name = "lscen145-s4"
    state_dir = os.path.join(STATE_ROOT, name)
    cache_dir = os.path.join(state_dir, "cache")
    events_path = os.path.join(state_dir, "events.jsonl")
    log_dir = os.path.join(cache_dir, "log")
    for path in (state_dir, cache_dir, log_dir):
        os.makedirs(path, exist_ok=True)
    for stale_path in (os.path.join(log_dir, "movian-0.log"), events_path):
        try:
            os.unlink(stale_path)
        except FileNotFoundError:
            pass

    env = dict(os.environ)
    env["DISPLAY"] = ":0"
    env["WAYLAND_DISPLAY"] = "wayland-0"
    cmd = [
        sys.executable, COLLECTOR, "launch",
        "--name", name,
        "--mode", "gdb-collector",
        "--start-url", "page:home",
        "--binary", BINARY,
        "--duration", "60",
        "--expect-natural-exit",
        "--categories", SCENARIO_CATEGORIES,
        "--display", ":0",
        "--wayland", "wayland-0",
    ]
    print("[s4] Launching collector")
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env=env, cwd=REPO, text=True)

    port = find_port_from_log(cache_dir, timeout=40)
    if port is None:
        stop_collector(proc, name)
        print("[s4] FAILED: no HTTP port")
        return None
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if http_get(port, "/api/prop/global"):
            break
        time.sleep(0.3)
    else:
        stop_collector(proc, name)
        print("[s4] FAILED: HTTP not ready")
        return None
    if not wait_startup_settled(events_path):
        stop_collector(proc, name)
        print("[s4] FAILED: navigator/GLW startup did not settle")
        return None

    instance = Instance(name)
    shutdown = http_request(
        instance.base_url(), "/api/input/action/Quit",
        timeout=5.0, method="GET")
    if not shutdown.get("ok"):
        stop_collector(proc, name)
        print("[s4] FAILED: Quit: %s" % shutdown)
        return None
    try:
        stdout, _ = proc.communicate(timeout=120)
    except subprocess.TimeoutExpired:
        stop_collector(proc, name)
        print("[s4] FAILED: collector did not finish")
        return None

    summary = parse_summary(stdout)
    if not summary:
        print("[s4] FAILED: collector produced no summary")
        return None
    summary = finish_summary(
        summary, events_path, {"app_shutdown", "main_fini"})
    print("[s4] Summary: ok=%s exitCode=%s" %
          (summary.get("ok"), summary.get("exitCode")))
    return summary


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Run lifecycle scenarios s3/s4")
    p.add_argument("scenario", choices=["s3", "s4", "both"])
    args = p.parse_args()

    results = {}
    if args.scenario in ("s3", "both"):
        results["s3"] = scenario_3_reload()
    if args.scenario in ("s4", "both"):
        results["s4"] = scenario_4_shutdown()

    print("\n=== RESULTS ===")
    print(json.dumps(results, indent=2, default=str))
    if any(not result or not result.get("ok")
           for result in results.values()):
        sys.exit(1)
