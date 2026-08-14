#!/usr/bin/env python3
"""Deterministic wedge, timeout, and emergency-eject contracts."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "support" / "devtools" / "gdb"))

from movian_lifecycle import (  # noqa: E402
    EMERGENCY_EJECT_STATES,
    EmergencyEjectTracker,
    all_eject_mandatory_bound,
    classify_run,
    is_eject_mandatory,
    rate_limit_should_disable,
    validate_wedge_event,
)


class EjectStateTest(unittest.TestCase):
    def test_transition_chain_is_strict(self):
        tracker = EmergencyEjectTracker()
        self.assertEqual(tracker.snapshot()["state"], "unobserved")
        tracker.observe()
        tracker.on_request()
        tracker.on_arm(77)
        tracker.on_exit(77)
        self.assertEqual(tracker.snapshot()["state"], "fired")
        tracker.on_exit(77)
        self.assertEqual(tracker.snapshot()["state"], "fired")

    def test_mandatory_probes_are_rate_limit_immune(self):
        self.assertTrue(is_eject_mandatory("app_shutdown"))
        bound = [SimpleNamespace(symbol=name, bound=True)
                 for name in ("app_shutdown", "shutdown_eject", "arch_exit")]
        self.assertTrue(all_eject_mandatory_bound(bound))
        mandatory = SimpleNamespace(category="core-init", bound=True,
                                    symbol="app_shutdown")
        ordinary = SimpleNamespace(category="core-init", bound=True,
                                   symbol="main_init")
        self.assertFalse(rate_limit_should_disable(mandatory, "core-init"))
        self.assertTrue(rate_limit_should_disable(ordinary, "core-init"))


class WedgeSchemaTest(unittest.TestCase):
    def test_invalid_wedge_event_fails_closed(self):
        event = {"category": "wedge", "event": "wedge-capture"}
        errors = validate_wedge_event(event)
        self.assertTrue(errors)

    def test_all_states_are_explicit(self):
        self.assertEqual(set(EMERGENCY_EJECT_STATES), {
            "unobserved", "not-requested", "requested", "armed", "fired",
        })


class ClassificationTest(unittest.TestCase):
    def test_timeout_is_not_pass(self):
        summary = {
            "port": 1, "inferiorPid": 2,
            "ownership": {"ownsPid": True}, "httpReady": True,
            "expectNaturalExit": False, "inferiorExitedBeforeDuration": False,
            "stopOutcome": "timeout", "finalOwnedRemains": True,
            "gdbForceKilled": True, "collectorControlReady": True,
            "jsonl": {"lines": 1, "bad": []}, "timedOut": True,
        }
        ok, reasons = classify_run(summary, "gdb-collector", False)
        self.assertFalse(ok)
        self.assertEqual(summary["status"], "TIMEOUT")
        self.assertIn("orphan-inferior-after-cleanup", reasons)

    def test_missing_collector_artifact_is_collector_error(self):
        summary = {
            "port": 1, "inferiorPid": 2,
            "ownership": {"ownsPid": True}, "httpReady": True,
            "stopOutcome": "stopped-clean", "finalOwnedRemains": False,
            "gdbForceKilled": False, "collectorControlReady": True,
            "jsonl": {"lines": 0, "bad": []},
        }
        ok, _ = classify_run(summary, "gdb-collector", False)
        self.assertFalse(ok)
        self.assertEqual(summary["status"], "COLLECTOR_ERROR")


if __name__ == "__main__":
    unittest.main()
