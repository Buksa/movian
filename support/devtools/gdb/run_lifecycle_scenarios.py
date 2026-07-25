#!/usr/bin/env python3
"""run_lifecycle_scenarios.py -- run lifecycle scenarios s3 (reload) and
s4 (normal shutdown) using the GDB collector + HTTP action injection."""

from __future__ import annotations
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


def http_post_action(port: int, action: str, timeout: float = 10.0) -> str:
    """Send action via GET /api/input/action/<ActionName>."""
    url = f"http://127.0.0.1:{port}/api/input/action/{action}"
    try:
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return f"error: {e}"


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


def scenario_3_reload():
    """Scenario 3: Plugin reload.
    1. Launch with plugin under GDB collector (duration20)
    2. Poll for HTTP-ready
    3. Wait for page to load (nav_open0)
    4. Send ReloadData action (triggers nav_reload_current -> plugins_reload_dev_plugin)
    5. Wait for collector to finish
    """
    name = "lscen145-s3"
    state_dir = os.path.join(STATE_ROOT, name)
    cache_dir = os.path.join(state_dir, "cache")
    events_path = os.path.join(state_dir, "events.jsonl")
    log_dir = os.path.join(cache_dir, "log")

    # Clean state
    import shutil
    for p in [state_dir, cache_dir, log_dir]:
        os.makedirs(p, exist_ok=True)
    log_file = os.path.join(log_dir, "movian-0.log")
    if os.path.exists(log_file):
        os.unlink(log_file)

    print(f"[s3] Launching collector with plugin (duration20s)...")
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
        "--duration", "20",
        "--display", ":0",
        "--wayland", "wayland-0",
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env=env, cwd=REPO, text=True
    )

    # Poll for HTTP port in the log
    print("[s3] Waiting for HTTP-ready...")
    port = find_port_from_log(cache_dir, timeout=40)
    if port is None:
        proc.kill()
        proc.wait()
        print("[s3] FAILED: no HTTP port")
        return None
    print(f"[s3] HTTP port: {port}")

    # Poll until HTTP actually responds
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if http_get(port, "/api/prop/global"):
            break
        time.sleep(0.3)
    else:
        proc.kill()
        proc.wait()
        print("[s3] FAILED: HTTP not ready")
        return None

    # Wait for page to be fully loaded (nav_open0 completes, GLW ready)
    print("[s3] HTTP ready. Waiting 5s for page load...")
    time.sleep(5.0)

    print("[s3] Sending ReloadData action...")
    resp = http_post_action(port, "ReloadData")
    print(f"[s3] ReloadData: {resp[:200]}")

    # Wait for reload to complete and events to settle
    time.sleep(3.0)

    print("[s3] Waiting for collector to finish...")
    stdout, _ = proc.communicate(timeout=120)

    # Parse summary
    summary = {}
    for line in stdout.splitlines():
        if line.startswith("MOVIAN_LIFECYCLE_SUMMARY "):
            import json
            summary = json.loads(line[len("MOVIAN_LIFECYCLE_SUMMARY "):])
            break

    print(f"[s3] Summary: ok={summary.get('ok')} exitCode={summary.get('exitCode')}")
    print(f"[s3] Events: {summary.get('jsonl', {}).get('lines', 0)} lines")
    return summary


def scenario_4_shutdown():
    """Scenario 4: Normal shutdown.
    1. Launch under GDB collector (duration20)
    2. Poll for HTTP-ready
    3. Send /api/restart (triggers app_shutdown -> main_fini -> fini callbacks)
    4. Wait for clean shutdown
    """
    name = "lscen145-s4"
    state_dir = os.path.join(STATE_ROOT, name)
    cache_dir = os.path.join(state_dir, "cache")
    log_dir = os.path.join(cache_dir, "log")

    import shutil
    for p in [state_dir, cache_dir, log_dir]:
        os.makedirs(p, exist_ok=True)
    log_file = os.path.join(log_dir, "movian-0.log")
    if os.path.exists(log_file):
        os.unlink(log_file)

    print(f"[s4] Launching collector (duration20s)...")
    env = dict(os.environ)
    env["DISPLAY"] = ":0"
    env["WAYLAND_DISPLAY"] = "wayland-0"
    cmd = [
        sys.executable, COLLECTOR, "launch",
        "--name", name,
        "--mode", "gdb-collector",
        "--start-url", "page:home",
        "--binary", BINARY,
        "--duration", "20",
        "--display", ":0",
        "--wayland", "wayland-0",
        "--extra-arg", "--with-restart",
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env=env, cwd=REPO, text=True
    )

    # Poll for HTTP port
    print("[s4] Waiting for HTTP-ready...")
    port = find_port_from_log(cache_dir, timeout=40)
    if port is None:
        proc.kill()
        proc.wait()
        print("[s4] FAILED: no HTTP port")
        return None
    print(f"[s4] HTTP port: {port}")

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if http_get(port, "/api/prop/global"):
            break
        time.sleep(0.3)
    else:
        proc.kill()
        proc.wait()
        print("[s4] FAILED: HTTP not ready")
        return None

    print("[s4] HTTP ready. Sending /api/restart (app_shutdown)...")
    resp = http_get(port, "/api/restart")
    print(f"[s4] /api/restart: {resp}")

    print("[s4] Waiting for collector to finish...")
    stdout, _ = proc.communicate(timeout=120)

    summary = {}
    for line in stdout.splitlines():
        if line.startswith("MOVIAN_LIFECYCLE_SUMMARY "):
            import json
            summary = json.loads(line[len("MOVIAN_LIFECYCLE_SUMMARY "):])
            break

    print(f"[s4] Summary: ok={summary.get('ok')} exitCode={summary.get('exitCode')}")
    print(f"[s4] Events: {summary.get('jsonl', {}).get('lines', 0)} lines")
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

    import json
    print("\n=== RESULTS ===")
    print(json.dumps(results, indent=2, default=str))
