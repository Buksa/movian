#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Focused deterministic test for movian_lifecycle.classify_run (issue #144 rework).

The host launch success/failure contract is a pure function: a launch summary ->
(ok, failureReasons).  This asserts it deterministically across every required
prerequisite (port, inferior PID, exact ownership, HTTP-ready, valid cleanup,
and -- for gdb-collector -- non-empty error-free JSONL) WITHOUT mocking GDB or
spawning an inferior.  Run with:

    python3 -m unittest tests.tooling.gdb.test_movian_lifecycle
    # or
    python3 tests/tooling/gdb/test_movian_lifecycle.py
"""

from pathlib import Path
import os
import shlex
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "support" / "devtools" / "gdb"))
from movian_lifecycle import _gdb_cmdfile, classify_run  # noqa: E402


def own(ok=True):
    return {"pid": 1234, "comm": "movian", "commIsMovian": True,
            "persistentInCmdline": True, "ownsPid": ok, "cmdline": "x"}


def base(**over):
    s = {"port": 42000, "inferiorPid": 1234, "ownership": own(True),
         "httpReady": True, "stopOutcome": "stopped-clean",
         "finalOwnedRemains": False, "gdbForceKilled": False,
         "jsonl": {"lines": 5, "bad": []}}
    s.update(over)
    return s


class Success(unittest.TestCase):
    def test_collector_success(self):
        ok, r = classify_run(base(), "gdb-collector", False)
        self.assertTrue(ok)
        self.assertEqual(r, [])

    def test_plain_success(self):
        ok, r = classify_run(base(jsonl=None), "plain", False)
        self.assertTrue(ok)
        self.assertEqual(r, [])

    def test_leave_running_owned(self):
        ok, r = classify_run(
            base(stopOutcome="left-running", finalOwnedRemains=True),
            "gdb-collector", True)
        self.assertTrue(ok)
        self.assertEqual(r, [])


class Failures(unittest.TestCase):
    def test_no_port(self):
        ok, r = classify_run(base(port=None), "gdb-collector", False)
        self.assertFalse(ok)
        self.assertIn("no-http-port", r)

    def test_no_inferior_pid(self):
        ok, r = classify_run(base(inferiorPid=None), "gdb-collector", False)
        self.assertFalse(ok)
        self.assertIn("no-inferior-pid", r)

    def test_ownership_not_proven(self):
        ok, r = classify_run(base(ownership=own(False)), "gdb-collector", False)
        self.assertFalse(ok)
        self.assertIn("ownership-not-proven", r)

    def test_http_not_ready(self):
        ok, r = classify_run(base(httpReady=False), "gdb-collector", False)
        self.assertFalse(ok)
        self.assertIn("http-not-ready", r)

    def test_orphan_after_cleanup(self):
        ok, r = classify_run(base(finalOwnedRemains=True),
                             "gdb-collector", False)
        self.assertFalse(ok)
        self.assertIn("orphan-inferior-after-cleanup", r)

    def test_cleanup_not_clean(self):
        ok, r = classify_run(base(stopOutcome="killed-after-timeout"),
                             "gdb-collector", False)
        self.assertFalse(ok)
        self.assertTrue(any(x.startswith("cleanup-not-clean") for x in r))

    def test_empty_jsonl(self):
        ok, r = classify_run(base(jsonl={"lines": 0, "bad": []}),
                             "gdb-collector", False)
        self.assertFalse(ok)
        self.assertIn("empty-jsonl", r)

    def test_jsonl_validation_errors(self):
        ok, r = classify_run(base(jsonl={"lines": 3, "bad": [{"line": 1}]}),
                             "gdb-collector", False)
        self.assertFalse(ok)
        self.assertTrue(any(x.startswith("jsonl-validation-errors") for x in r))

    def test_leave_running_unowned(self):
        s = {"port": 42000, "inferiorPid": None, "ownership": own(False),
             "httpReady": True, "stopOutcome": "left-running"}
        ok, r = classify_run(s, "gdb-collector", True)
        self.assertFalse(ok)
        self.assertIn("leave-running-without-ownership", r)

    def test_leave_running_not_live(self):
        ok, r = classify_run(base(stopOutcome="left-running"),
                             "gdb-collector", True)
        self.assertFalse(ok)
        self.assertIn("leave-running-inferior-not-live", r)

    def test_gdb_force_killed(self):
        ok, r = classify_run(base(gdbForceKilled=True),
                             "gdb-collector", False)
        self.assertFalse(ok)
        self.assertIn("gdb-force-killed", r)


class CommandFile(unittest.TestCase):
    def test_probe_with_spaces_round_trips(self):
        probe = "es_resource_create:es-resource:st->flags == 1"
        path, _ = _gdb_cmdfile(
            "/tmp/collector path.py", "/tmp/events path.jsonl",
            "/tmp/inventory path.json", "/tmp/state", "/tmp/persistent",
            "/tmp/cache", "/tmp/movian", "page:home", "es-resource",
            0, 0, probe, "gdb-collector")
        try:
            lines = Path(path).read_text().splitlines()
        finally:
            os.unlink(path)
        source = next(line for line in lines if line.startswith("source "))
        self.assertEqual(source, "source /tmp/collector path.py")
        command = next(line for line in lines
                       if line.startswith("movian-lifecycle-start "))
        tokens = shlex.split(command)
        self.assertEqual(tokens[tokens.index("--probe") + 1], probe)
        self.assertEqual(tokens[tokens.index("--events") + 1],
                         "/tmp/events path.jsonl")


class Determinism(unittest.TestCase):
    def test_stable_order(self):
        s = base(port=None, inferiorPid=None, ownership=own(False),
                 httpReady=False, finalOwnedRemains=True,
                 jsonl={"lines": 0, "bad": [{}]})
        _, r1 = classify_run(s, "gdb-collector", False)
        _, r2 = classify_run(s, "gdb-collector", False)
        self.assertEqual(r1, r2)
        self.assertEqual(r1[0], "no-http-port")


if __name__ == "__main__":
    unittest.main()
