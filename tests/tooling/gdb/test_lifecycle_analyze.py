#!/usr/bin/env python3
"""Ordering, balance, malformed-stream, and UNKNOWN analyzer tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "support" / "devtools" / "gdb"))

from lifecycle_analyze import (  # noqa: E402
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_UNKNOWN,
    analyze_events,
    derive_fini_order,
    derive_init_order,
    derive_resource_balance,
    derive_thread_lifecycle,
    parse_jsonl,
)


def inventory():
    return {
        "entries": [
            {"symbol": "init_a", "category": "init-helper", "phase": "startup",
             "event": "enter", "contractOrder": 1},
            {"symbol": "init_b", "category": "init-helper", "phase": "startup",
             "event": "enter", "contractOrder": 2},
            {"symbol": "fini_a", "category": "init-helper", "phase": "shutdown",
             "event": "enter", "contractOrder": 3},
            {"symbol": "res_create", "category": "es-resource", "phase": "runtime",
             "event": "create", "pairedWith": "res_destroy", "contractOrder": 4},
            {"symbol": "res_destroy", "category": "es-resource", "phase": "runtime",
             "event": "destroy", "pairedWith": "res_create", "contractOrder": 5},
            {"symbol": "thread_create", "category": "thread-create", "phase": "runtime",
             "event": "create", "contractOrder": 6},
        ],
        "missingCandidates": [],
    }


def event(seq, symbol, category="init-helper", kind="enter", tid=7):
    return {
        "seq": seq,
        "monotonicNs": seq,
        "category": category,
        "event": kind,
        "symbol": symbol,
        "thread": {"gdbId": 1, "osTid": tid},
        "arguments": {},
        "objects": {},
        "stack": [],
    }


class JsonlParsing(unittest.TestCase):
    def test_valid_and_truncated_streams(self):
        valid = parse_jsonl(json.dumps(event(1, "init_a")) + "\n")
        self.assertEqual(valid["errors"], [])
        malformed = parse_jsonl(json.dumps(event(1, "init_a")) + "\n{")
        self.assertEqual(len(malformed["errors"]), 1)
        self.assertTrue(malformed["truncated"])

    def test_nonmonotonic_sequence_is_collector_error(self):
        result = parse_jsonl("\n".join([
            json.dumps(event(2, "init_a")),
            json.dumps(event(1, "init_b")),
        ]))
        self.assertTrue(any(error["kind"] == "sequence-order"
                            for error in result["errors"]))


class Ordering(unittest.TestCase):
    def test_init_order_passes_and_reversal_fails(self):
        self.assertEqual(
            derive_init_order(inventory(), [event(1, "init_a"), event(2, "init_b")])["status"],
            STATUS_PASS)
        self.assertEqual(
            derive_init_order(inventory(), [event(1, "init_b"), event(2, "init_a")])["status"],
            STATUS_FAIL)

    def test_fini_order_missing_observation_is_unknown(self):
        result = derive_fini_order(inventory(), [])
        self.assertEqual(result["status"], STATUS_UNKNOWN)
        self.assertEqual(result["evidence"], "NOT_REACHED")


class Balance(unittest.TestCase):
    def test_resource_balance_requires_shutdown_evidence(self):
        balanced = [
            event(1, "res_create", "es-resource", "create"),
            event(2, "res_destroy", "es-resource", "destroy"),
            event(3, None, "collector", "inferior-exited"),
        ]
        self.assertEqual(derive_resource_balance(inventory(), balanced)["status"],
                         STATUS_PASS)

        leaked = [
            event(1, "res_create", "es-resource", "create"),
            event(2, None, "collector", "inferior-exited"),
        ]
        self.assertEqual(derive_resource_balance(inventory(), leaked)["status"],
                         STATUS_FAIL)

    def test_thread_balance_uses_observed_thread_ids(self):
        events = [
            event(1, "thread_create", "thread-create", "create", tid=11),
            event(2, None, "thread", "thread-exit", tid=11),
            event(3, None, "collector", "inferior-exited"),
        ]
        self.assertEqual(derive_thread_lifecycle(inventory(), events)["status"],
                         STATUS_PASS)

    def test_no_observation_is_unknown_not_pass(self):
        result = analyze_events(inventory(), "")
        self.assertEqual(result["status"], STATUS_UNKNOWN)
        self.assertTrue(all(item["status"] == STATUS_UNKNOWN
                            for item in result["results"]))


if __name__ == "__main__":
    unittest.main()
