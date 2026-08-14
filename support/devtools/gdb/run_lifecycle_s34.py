#!/usr/bin/env python3
"""Run issue #145 scenarios S3 (dev-plugin reload) and S4 (clean shutdown).

The collector owns GDB and Movian.  A runner thread waits for the recorded
mdev instance, sends the HTTP action, and requires lifecycle-event evidence
from the current run before reporting success.
"""

from __future__ import annotations
import os
import subprocess
import sys
import tempfile
import time
import json
import shutil

sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..")))
from mdevlib.harness import (  # noqa: E402
    Instance,
    MdevError,
    http_request,
    log_size,
    read_log_delta,
    reload_js_fail_lines,
    reload_js_ok_lines,
    reload_line_matches_dir,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(SCRIPT_DIR, "../../.."))
COLLECTOR = os.path.join(SCRIPT_DIR, "movian_lifecycle.py")
BINARY = os.environ.get("MOVIAN_LIFECYCLE_BINARY",
                         os.path.join(REPO, "build.debug/movian"))
PLUGIN = os.environ.get("MOVIAN_LIFECYCLE_PLUGIN",
                        os.path.join(SCRIPT_DIR, "lifecycle_test_plugin"))
INVENTORY = os.environ.get("MOVIAN_LIFECYCLE_INVENTORY",
                           os.path.join(SCRIPT_DIR, "inventory.json"))
ARCHIVE_ROOT = "/tmp/movian-lifecycle"
SCENARIO_CATEGORIES = (
    "core-init,shutdown-hook,init-system,init-helper,thread-create,"
    "plugin,es-plugin,es-context,es-resource,navigator,glw,backend,"
    "service,cache"
)


def find_port_from_log(cache_dir: str, timeout: float = 40.0) -> int | None:
    """Poll movian log for HTTP port line."""
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
    import urllib.request
    try:
        req = urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout)
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


def build_gdb_cmdfile(name: str, action_url: str, extra_movian_args: list[str]) -> str:
    """Build a GDB command file that uses Python threading to interact with
    the running inferior.  A daemon thread polls the movian log for HTTP-ready,
    sends the action via HTTP, then waits for natural shutdown.  Breakpoints
    on fini/shutdown symbols fire normally because GDB remains attached.
    """
    state_dir = os.path.join(STATE_ROOT, name)
    cache = os.path.join(state_dir, "cache")
    persistent = os.path.join(state_dir, "persistent")
    events_path = os.path.join(state_dir, "events.jsonl")
    pidfile = os.path.join(state_dir, "inferior.pid")
    interact_py = os.path.join(SCRIPT_DIR, "scenario_interact.py")

    movian_args = [BINARY, "-d", "--disable-upgrades",
                   "--persistent", persistent,
                   "--cache", cache]
    movian_args += extra_movian_args
    movian_args += ["page:home"]

    lines = [
        "set pagination off",
        "set confirm off",
        "set print pretty off",
        "file " + os.path.abspath(BINARY),
        "set breakpoint pending on",
        "handle SIGTERM nostop noprint pass",
        "handle SIGINT nostop noprint pass",
        "handle SIGPIPE nostop noprint pass",
        "handle SIGHUP nostop noprint pass",
        "source " + os.path.abspath(COLLECTOR),
        "movian-lifecycle-start --events " + events_path +
        " --inventory " + INVENTORY + " --pidfile " + pidfile,
        "set args " + " ".join('"' + a + '"' for a in movian_args[1:]),
        "run",
    ]

    fd, path = tempfile.mkstemp(prefix="lifecycle_s34_" + name + "_",
                                suffix=".gdb")
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    return path


def _forward_launch_args(extra_movian_args: list[str]) -> list[str]:
    """Translate Movian argv into options supported by collector launch."""
    forwarded = []
    index = 0
    while index < len(extra_movian_args):
        token = extra_movian_args[index]
        if token in ("-p", "--plugin"):
            if index + 1 >= len(extra_movian_args):
                raise ValueError("%s requires a plugin directory" % token)
            forwarded.extend(["--plugin", extra_movian_args[index + 1]])
            index += 2
            continue
        forwarded.append("--extra-arg=" + token)
        index += 1
    return forwarded


def _event_evidence(events_path: str) -> tuple[set[str], list[int | None]]:
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
            if event.get("symbol"):
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
            symbols, _ = _event_evidence(events_path)
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


def run_scenario(name: str, action_url: str, extra_movian_args: list[str],
                 scenario_label: str) -> dict | None:
    """Launch the collector, perform one action, and validate its evidence."""
    import threading
    state_dir = os.path.join(STATE_ROOT, name)
    cache = os.path.join(state_dir, "cache")
    log_dir = os.path.join(cache, "log")
    events_path = os.path.join(state_dir, "events.jsonl")

    for path in (state_dir, cache, log_dir):
        os.makedirs(path, exist_ok=True)
    for stale_path in (os.path.join(log_dir, "movian-0.log"), events_path):
        try:
            os.unlink(stale_path)
        except FileNotFoundError:
            pass

    action_state = {"done": False, "ok": False, "detail": None}

    def bg_send_action():
        try:
            port = find_port_from_log(cache, timeout=40.0)
            if port is None:
                raise RuntimeError("no HTTP port found")
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if http_get(port, "/api/prop/global"):
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError("HTTP control did not become ready")
            if not wait_startup_settled(events_path):
                raise RuntimeError(
                    "navigator/GLW startup did not settle before action")

            instance = Instance(name)
            if action_url.endswith("/ReloadData"):
                reload_ok, details, delta = reload_plugin(
                    instance, PLUGIN, settle=15.0)
                if not reload_ok:
                    raise RuntimeError(
                        "ReloadData failed: %s" %
                        json.dumps(details, sort_keys=True))
                if "lscen145: plugin loaded, resources created: 5" not in delta:
                    raise RuntimeError(
                        "reload lacked the plugin resource-count marker")
                action_state["detail"] = details
                shutdown = http_request(
                    instance.base_url(), "/api/input/action/Quit",
                    timeout=5.0, method="GET")
                if not shutdown.get("ok"):
                    raise RuntimeError(
                        "Quit after reload failed: %s" % shutdown)
            else:
                response = http_request(
                    instance.base_url(), action_url,
                    timeout=5.0, method="GET")
                if not response.get("ok"):
                    raise RuntimeError(
                        "scenario action failed: %s" % response)
                action_state["detail"] = response
            action_state["ok"] = True
        except (MdevError, OSError, RuntimeError, ValueError) as exc:
            action_state["detail"] = str(exc)
        finally:
            action_state["done"] = True

    sender = threading.Thread(target=bg_send_action, daemon=True)
    sender.start()

    launch_args = [
        sys.executable, os.path.join(SCRIPT_DIR, "movian_lifecycle.py"),
        "launch", "--name", name, "--duration", "60",
        "--expect-natural-exit", "--categories", SCENARIO_CATEGORIES,
        "--binary", BINARY, "--inventory", INVENTORY,
    ]

    env = dict(os.environ)
    env["DISPLAY"] = ":0"
    env["WAYLAND_DISPLAY"] = "wayland-0"

    print(f"[{scenario_label}] Launching via movian_lifecycle.py")
    try:
        result = subprocess.run(
            launch_args, capture_output=True, text=True, timeout=120,
            env=env, cwd=REPO)
    except subprocess.TimeoutExpired:
        print(f"[{scenario_label}] Launch timed out")
        return None
    sender.join(timeout=5)

    summary = {}
    for line in (result.stdout + result.stderr).splitlines():
        if line.startswith("MOVIAN_LIFECYCLE_SUMMARY "):
            summary = json.loads(line[len("MOVIAN_LIFECYCLE_SUMMARY "):])
            break
    if not summary:
        print(f"[{scenario_label}] Collector produced no summary")
        return None

    reasons = list(summary.get("failureReasons") or [])
    if result.returncode != 0:
        reasons.append("collector-exit:%d" % result.returncode)
    if not action_state["done"] or not action_state["ok"]:
        reasons.append("scenario-action:%s" % action_state["detail"])
    try:
        symbols, exit_codes = _event_evidence(events_path)
    except (OSError, ValueError) as exc:
        symbols, exit_codes = set(), []
        reasons.append("event-evidence:%s" % exc)
    required = (
        {"plugins_reload_dev_plugin", "ecmascript_plugin_unload",
         "ecmascript_plugin_load"}
        if scenario_label == "s3"
        else {"app_shutdown", "main_fini"}
    )
    missing = sorted(required - symbols)
    if missing:
        reasons.append("missing-events:%s" % ",".join(missing))
    if exit_codes != [0]:
        reasons.append("inferior-exit-codes:%s" % exit_codes)
    summary["failureReasons"] = reasons
    summary["scenarioAction"] = action_state
    summary["scenarioEvidence"] = {
        "requiredSymbols": sorted(required),
        "missingSymbols": missing,
        "inferiorExitCodes": exit_codes,
    }
    summary["ok"] = not reasons

    event_count = (summary.get("jsonl") or {}).get("lines", 0)
    print(f"[{scenario_label}] events={event_count} "
          f"action_ok={action_state['ok']} ok={summary['ok']}")
    return summary

def scenario_3_reload():
    """Scenario 3: Plugin reload via HTTP ReloadData + Quit."""
    return run_scenario(
        name="lscen145-s3",
        action_url="/api/input/action/ReloadData",
        extra_movian_args=["-p", PLUGIN],
        scenario_label="s3"
    )


def scenario_4_shutdown():
    """Scenario 4: Normal shutdown via HTTP Quit action."""
    return run_scenario(
        name="lscen145-s4",
        action_url="/api/input/action/Quit",
        extra_movian_args=[],
        scenario_label="s4"
    )


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Run lifecycle scenarios s3/s4 via GDB")
    p.add_argument("scenario", choices=["s3", "s4", "both"])
    args = p.parse_args()

    results = {}
    if args.scenario in ("s3", "both"):
        results["s3"] = scenario_3_reload()
    if args.scenario in ("s4", "both"):
        results["s4"] = scenario_4_shutdown()

    print(json.dumps(results, indent=2, default=str))
    if any(not result or not result.get("ok")
           for result in results.values()):
        sys.exit(1)
