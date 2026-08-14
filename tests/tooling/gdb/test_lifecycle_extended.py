#!/usr/bin/env python3
"""Pure tests for extended lifecycle scenario evidence handling."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "support" / "devtools" / "gdb"))

from run_lifecycle_extended import (  # noqa: E402
    _count_symbol,
    _response_evidence,
    scenarios,
)


class ExtendedScenarioEvidence(unittest.TestCase):
    def test_response_evidence_drops_binary_body(self):
        result = _response_evidence({
            "ok": True,
            "status": 200,
            "body": b"ok",
            "headers": {"content-type": "text/plain"},
        })
        self.assertEqual(result["bodyLength"], 2)
        self.assertNotIn("body", result)
        self.assertIn("headers", result)

    def test_count_symbol_reads_jsonl_symbols(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            path.write_text("\n".join([
                json.dumps({"symbol": "glw_view_create"}),
                json.dumps({"symbol": "main_init"}),
                json.dumps({"symbol": "glw_view_create"}),
            ]) + "\n", encoding="utf-8")
            self.assertEqual(_count_symbol(path, "glw_view_create"), 2)

    def test_scenarios_have_explicit_action_contracts(self):
        configured = scenarios()
        self.assertEqual(set(configured), {
            "ui-reload", "plugin-reload-unload", "repeated-reload",
            "safe-forced-error",
        })
        self.assertTrue(all(callable(action) for _plugin, action in configured.values()))


if __name__ == "__main__":
    unittest.main()
