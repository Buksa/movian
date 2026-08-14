#!/usr/bin/env python3
"""Pure collector tests that do not require an inferior or GDB session."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "support" / "devtools" / "gdb"))

from inventory import EVENTS  # noqa: E402
from movian_lifecycle import (  # noqa: E402
    build_movian_argv,
    classify_run,
    validate_events,
)


def event(seq: int, category: str = "glw", symbol: str = "glw_init",
          kind: str = "enter") -> dict:
    return {
        "seq": seq,
        "monotonicNs": seq * 100,
        "category": category,
        "event": kind,
        "symbol": symbol,
        "observation": "call-entry",
        "thread": {"gdbId": 1, "osTid": 2, "name": "main"},
        "inferior": {"number": 1, "pid": 1234},
        "arguments": {},
        "objects": {},
        "stack": [],
    }


    def test_valid_jsonl_is_ordered_and_contextual(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            rows = [
                event(1),
                {**event(2, "collector", None, "inferior-exited"),
                 "exitCode": 0},
            ]
            path.write_text("".join(json.dumps(item) + "\n" for item in rows),
                            encoding="utf-8")
            result = validate_events(str(path))
            self.assertEqual(result["lines"], 2)
            self.assertEqual(result["bad"], [])
            self.assertEqual(result["inferiorExitCodes"], [0])

    def test_truncated_and_malformed_lines_are_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            path.write_text(json.dumps(event(1)) + "\n{" + "\n",
                            encoding="utf-8")
            result = validate_events(str(path))
            self.assertEqual(result["lines"], 2)
            self.assertEqual(len(result["bad"]), 1)

    def test_event_kinds_are_limited_to_contract_values(self):
        self.assertEqual(EVENTS, {"enter", "create", "destroy"})


class CollectorSelection(unittest.TestCase):
    def test_profile_arguments_are_explicitly_owned(self):
        argv = build_movian_argv(
            "/tmp/movian", "/tmp/profile/persistent", "/tmp/profile/cache",
            "page:home", ["-p", "/tmp/plugin"],
        )
        self.assertEqual(argv[:7], [
            "/tmp/movian", "-d", "--disable-upgrades",
            "--persistent", "/tmp/profile/persistent",
            "--cache", "/tmp/profile/cache",
        ])
        self.assertEqual(argv[-3:], ["-p", "/tmp/plugin", "page:home"])

    def test_empty_collector_artifact_is_not_success(self):
        summary = {
            "port": 42000,
            "inferiorPid": 1234,
            "ownership": {"ownsPid": True},
            "httpReady": True,
            "stopOutcome": "stopped-clean",
            "finalOwnedRemains": False,
            "gdbForceKilled": False,
            "collectorControlReady": True,
            "jsonl": {"lines": 0, "bad": []},
        }
        ok, reasons = classify_run(summary, "gdb-collector", False)
        self.assertFalse(ok)
        self.assertIn("empty-jsonl", reasons)


if __name__ == "__main__":
    unittest.main()
