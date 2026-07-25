#!/usr/bin/env python3
"""GDB Python helper for lifecycle scenarios that need to interact with
the running inferior (S3 reload, S4 shutdown).

Usage in GDB command file:
    source support/devtools/gdb/movian_lifecycle.py
    movian-lifecycle-start --events <path> --inventory <path> --pidfile <path>
    set args ...
    python
    import scenario_interact
    scenario_interact.start(action_url="<url>", wait_s=12, settle_s=8)
    end
    run

The background thread polls the movian log for HTTP-ready, sends the action,
then waits for natural exit.  Breakpoints on fini/shutdown symbols fire
normally during the shutdown path because GDB remains attached.
"""
import gdb
import threading
import time
import os
import urllib.request

_thread = None

def _find_port(cache_dir, timeout=40.0):
    log_dir = os.path.join(cache_dir, "log")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.isdir(log_dir):
            for fn in sorted(os.listdir(log_dir)):
                try:
                    with open(os.path.join(log_dir, fn)) as f:
                        for line in f:
                            if "http-server: Listening on port " in line:
                                return int(line.split("port ")[1].split()[0])
                except (OSError, ValueError, IndexError):
                    pass
        time.sleep(0.5)
    return None

def _worker(cache_dir, action_url, wait_s, settle_s):
    port = _find_port(cache_dir)
    if port is None:
        return
    time.sleep(wait_s)
    try:
        urllib.request.urlopen(
            "http://127.0.0.1:%d%s" % (port, action_url), timeout=5)
    except Exception:
        pass
    time.sleep(settle_s)

def start(action_url, cache_dir=None, wait_s=3.0, settle_s=8.0):
    """Start a daemon thread that sends an HTTP action after HTTP-ready."""
    global _thread
    if cache_dir is None:
        cache_dir = os.environ.get("LIFECYCLE_CACHE_DIR", "/tmp/mdev/dev/cache")
    _thread = threading.Thread(
        target=_worker,
        args=(cache_dir, action_url, wait_s, settle_s),
        daemon=True)
    _thread.start()
