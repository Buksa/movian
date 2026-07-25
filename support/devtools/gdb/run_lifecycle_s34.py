#!/usr/bin/env python3
"""run_lifecycle_s34.py -- Run lifecycle scenarios S3 (reload) and S4 (shutdown)
using a custom GDB script that calls the reload/shutdown function directly.

This avoids the event-queue dispatch issue by calling the C functions directly
from GDB after the process is fully initialized.
"""

from __future__ import annotations
import os
import subprocess
import sys
import tempfile
import time
import json
import shutil

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(SCRIPT_DIR, "../../.."))
COLLECTOR = os.path.join(SCRIPT_DIR, "movian_lifecycle.py")
BINARY = os.path.join(REPO, "build.debug/movian")
PLUGIN = os.path.join(SCRIPT_DIR, "lifecycle_test_plugin")
INVENTORY = os.path.join(SCRIPT_DIR, "inventory.json")
STATE_ROOT = "/tmp/mdev"
ARCHIVE_ROOT = "/tmp/movian-lifecycle"


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


def run_scenario(name: str, action_url: str, extra_movian_args: list[str],
                 scenario_label: str) -> dict | None:
    """Run a scenario using movian_lifecycle.py launch with a background
    HTTP-action sender.  The launch command manages GDB; a background
    thread sends the action after HTTP-ready.
    """
    import threading
    import urllib.request
    state_dir = os.path.join(STATE_ROOT, name)
    cache = os.path.join(state_dir, "cache")
    log_dir = os.path.join(cache, "log")
    events_path = os.path.join(state_dir, "events.jsonl")

    for p in [state_dir, cache, log_dir]:
        os.makedirs(p, exist_ok=True)
    log_file = os.path.join(log_dir, "movian-0.log")
    if os.path.exists(log_file):
        os.unlink(log_file)

    action_sent = {"done": False}

    def bg_send_action():
        port = find_port_from_log(cache, timeout=40.0)
        if port is None:
            print(f"[{scenario_label}] No port found")
            return
        time.sleep(3)
        url = f"http://127.0.0.1:{port}{action_url}"
        print(f"[{scenario_label}] Sending action: {url}")
        try:
            req = urllib.request.urlopen(url, timeout=5)
            print(f"[{scenario_label}] Action response: {req.status}")
        except Exception as exc:
            print(f"[{scenario_label}] Action failed: {exc}")
        action_sent["done"] = True

    t = threading.Thread(target=bg_send_action, daemon=True)
    t.start()

    launch_args = [
        sys.executable, os.path.join(SCRIPT_DIR, "movian_lifecycle.py"),
        "launch", "--name", name, "--duration", "60",
    ]
    if extra_movian_args:
        for arg in extra_movian_args:
            launch_args.extend(["--movian-arg", arg])

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

    summary = {}
    for line in result.stdout.splitlines():
        if line.startswith("MOVIAN_LIFECYCLE_SUMMARY "):
            summary = json.loads(line[len("MOVIAN_LIFECYCLE_SUMMARY "):])
            break

    if not summary and os.path.exists(events_path):
        with open(events_path) as f:
            ev_lines = f.readlines()
        summary = {"eventCount": len(ev_lines), "ok": len(ev_lines) > 10}

    event_count = (summary.get("jsonl", {}).get("lines", 0)
                   if isinstance(summary.get("jsonl"), dict)
                   else summary.get("eventCount", 0))
    print(f"[{scenario_label}] events={event_count} action_sent={action_sent['done']}")
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

    print("\n=== RESULTS ===")
    print(json.dumps(results, indent=2, default=str))
