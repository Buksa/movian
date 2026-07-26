#!/usr/bin/env python3
"""Focused regression tests for lifecycle_analyze reload correlation."""

from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "support" / "devtools" / "gdb"))
from lifecycle_analyze import derive_resource_balance, load_events  # noqa: E402
import run_lifecycle_s34 as scenario_runner  # noqa: E402


ARMED = [
    "es_context_create", "es_context_begin", "es_context_end",
    "es_resource_link", "es_resource_unlink",
    "service_create", "service_destroy",
    "ecmascript_plugin_load", "ecmascript_plugin_unload",
    "plugin_load", "plugin_unload",
]


def event(seq, symbol=None, category="collector", *, ns=None, args=None,
          stack=None, kind="enter", **extra):
    value = {
        "seq": seq,
        "monotonicNs": seq * 10 if ns is None else ns,
        "category": category,
        "event": kind,
        "symbol": symbol,
        "thread": {"gdbId": 1, "name": "movian", "osTid": 100},
        "arguments": args or {},
        "objects": {
            key: value for key, value in (args or {}).items()
            if isinstance(value, str) and value.startswith("0x")
        },
        "stack": [{"function": name} for name in (stack or [])],
    }
    value.update(extra)
    return value


def manifest(plugin_id):
    return '{"type":"ecmascript","id":"%s"}' % plugin_id


def balanced_reload_events(armed=None):
    events = [
        event(1, kind="collector-installed", armed=armed or ARMED),
        # Initial load establishes the old context -> plugin-id mapping and
        # the resource -> context mapping used by es_resource_unlink.
        event(2, "ecmascript_plugin_load", "es-plugin", ns=20,
              args={"manifest": manifest("target")}),
        event(3, "es_context_begin", "es-context", ns=30,
              args={"ec": "0xold"}),
        event(4, "es_resource_link", "es-resource", ns=40,
              args={"er": "0xoldres", "ec": "0xold"}),
        event(10, "plugins_reload_dev_plugin", "plugin", ns=1000),
        event(11, "plugin_load", "plugin", ns=1010,
              stack=["plugin_load", "plugins_reload_dev_plugin"]),
        event(12, "plugin_unload", "plugin", ns=1020,
              stack=["plugin_unload", "plugin_load"]),
        event(13, "ecmascript_plugin_unload", "es-plugin", ns=1030,
              args={"id": "target"},
              stack=["ecmascript_plugin_unload", "plugin_unload"]),
        event(14, "es_context_begin", "es-context", ns=1040,
              args={"ec": "0xold"}, stack=["ecmascript_plugin_unload"]),
        event(15, "es_resource_unlink", "es-resource", ns=1050,
              args={"er": "0xoldres"},
              stack=["es_resource_unlink", "ecmascript_plugin_unload"]),
        event(16, "service_destroy", "service", ns=1060,
              stack=["service_destroy", "ecmascript_plugin_unload"]),
        event(17, "prop_unsubscribe", "prop-subscribe", ns=1070,
              stack=["prop_unsubscribe", "ecmascript_plugin_unload"]),
        event(18, "es_context_end", "es-context", ns=1080,
              args={"ec": "0xold"}, stack=["ecmascript_plugin_unload"]),
        event(19, "ecmascript_plugin_load", "es-plugin", ns=1090,
              args={"manifest": manifest("target")},
              stack=["ecmascript_plugin_load", "plugin_load"]),
        event(20, "es_context_create", "es-context", ns=1100,
              stack=["es_context_create", "ecmascript_plugin_load"]),
        event(21, "es_context_begin", "es-context", ns=1110,
              args={"ec": "0xnew"}, stack=["ecmascript_plugin_load"]),
        event(22, "es_resource_link", "es-resource", ns=1120,
              args={"er": "0xnewres", "ec": "0xnew"},
              stack=["es_resource_link", "ecmascript_plugin_load"]),
        # GDB may resolve one source function to multiple breakpoint locations.
        event(22, "es_resource_link", "es-resource", ns=1121,
              args={"er": "0xnewres", "ec": "0xnew"},
              stack=["es_resource_link", "ecmascript_plugin_load"]),
        event(23, "service_create", "service", ns=1130,
              stack=["service_create", "ecmascript_plugin_load"]),
        event(24, "prop_subscribe", "prop-subscribe", ns=1140,
              stack=["prop_subscribe", "ecmascript_plugin_load"]),
        event(25, "es_context_end", "es-context", ns=1150,
              args={"ec": "0xnew"}, stack=["ecmascript_plugin_load"]),
        # Unrelated activity in the same six-second wall-clock window must not
        # affect the target plugin's service balance.
        event(26, "service_create", "service", ns=1160,
              stack=["unrelated_runtime_work"]),
    ]
    return events


class Loading(unittest.TestCase):
    def test_rejects_malformed_record(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as stream:
            stream.write(json.dumps({"seq": 1}) + "\n")
            stream.write("{truncated\n")
            path = stream.name
        self.addCleanup(Path(path).unlink)
        with self.assertRaises(SystemExit):
            load_events([path])

    def test_rejects_multiple_runs(self):
        with self.assertRaises(SystemExit):
            load_events(["first.jsonl", "second.jsonl"])


class ReloadBalance(unittest.TestCase):
    def test_balances_only_target_plugin_cycle(self):
        result = derive_resource_balance(
            balanced_reload_events(), plugin_id_hint="target")
        window = result["perWindow"][0]
        self.assertEqual(window["status"], "balanced")
        self.assertTrue(window["balanced"])
        self.assertEqual(window["oldContextPtr"], "0xold")
        self.assertEqual(window["newContextPtr"], "0xnew")
        self.assertEqual(window["balance"]["service"]["created"], 1)
        self.assertEqual(window["balance"]["service"]["destroyed"], 1)
        self.assertEqual(window["balance"]["es-resource"]["delta"], 0)

    def test_does_not_merge_another_plugin_reload_cycle(self):
        events = balanced_reload_events()
        events.extend([
            event(30, "plugin_load", "plugin", ns=1200),
            event(31, "plugin_unload", "plugin", ns=1210),
            event(32, "ecmascript_plugin_unload", "es-plugin", ns=1220,
                  args={"id": "other"}),
            event(33, "es_context_begin", "es-context", ns=1230,
                  args={"ec": "0xother-old"}),
            event(34, "ecmascript_plugin_load", "es-plugin", ns=1240,
                  args={"manifest": manifest("other")}),
            event(35, "es_context_begin", "es-context", ns=1250,
                  args={"ec": "0xother-new"}),
            # Intentionally unmatched: it must not affect target's verdict.
            event(36, "service_create", "service", ns=1260),
            event(37, "es_context_end", "es-context", ns=1270,
                  args={"ec": "0xother-new"},
                  stack=["ecmascript_plugin_load"]),
        ])
        result = derive_resource_balance(events, plugin_id_hint="target")
        window = result["perWindow"][0]
        self.assertEqual(window["status"], "balanced")
        self.assertEqual(window["balance"]["service"]["created"], 1)
        self.assertEqual(window["balance"]["service"]["destroyed"], 1)

    def test_missing_probe_is_indeterminate(self):
        armed = [symbol for symbol in ARMED
                 if symbol != "es_resource_unlink"]
        result = derive_resource_balance(
            balanced_reload_events(armed), plugin_id_hint="target")
        window = result["perWindow"][0]
        self.assertEqual(window["status"], "indeterminate")
        reasons = {entry["kind"]: entry["reasons"]
                   for entry in window["indeterminateReasons"]}
        self.assertIn("destroy-probe-not-armed", reasons["es-resource"])

    def test_rate_limited_required_kind_is_indeterminate(self):
        events = balanced_reload_events()
        events.insert(4, event(
            5, "es_resource_link", "es-resource", ns=50,
            kind="rate-limited", cap=4, emitted=4))
        result = derive_resource_balance(events, plugin_id_hint="target")
        window = result["perWindow"][0]
        self.assertEqual(window["status"], "indeterminate")
        reasons = {entry["kind"]: entry["reasons"]
                   for entry in window["indeterminateReasons"]}
        self.assertIn("category-rate-limited", reasons["es-resource"])

    def test_missing_target_cycle_is_indeterminate(self):
        result = derive_resource_balance(
            balanced_reload_events(), plugin_id_hint="other")
        window = result["perWindow"][0]
        self.assertEqual(window["status"], "indeterminate")
        self.assertIsNone(window["balanced"])
        self.assertIn("target-plugin-cycle-not-observed",
                      window["indeterminateReasons"])


class ScenarioLaunchArguments(unittest.TestCase):
    def test_forwards_plugin_with_supported_launch_option(self):
        forwarded = scenario_runner._forward_launch_args(
            ["-p", "/tmp/plugin", "--with-restart"])
        self.assertEqual(
            forwarded,
            ["--plugin", "/tmp/plugin", "--extra-arg=--with-restart"])
        self.assertNotIn("--movian-arg", forwarded)

    def test_compile_error_overrides_reloaded_line(self):
        delta = "\n".join([
            "Unable to compile file:///tmp/plugin/plugin.js -- SyntaxError",
            "Reloaded dev plugin file:///tmp/plugin",
        ])

        class FakeInstance:
            @staticmethod
            def base_url():
                return "http://127.0.0.1:1"

        with mock.patch.object(
                scenario_runner, "log_size", return_value=0), \
             mock.patch.object(
                 scenario_runner, "http_request",
                 return_value={"ok": True}), \
             mock.patch.object(
                 scenario_runner, "read_log_delta", return_value=delta):
            ok, details, _ = scenario_runner.reload_plugin(
                FakeInstance(), "/tmp/plugin")

        self.assertFalse(ok)
        self.assertIn("Unable to compile", details[0]["detail"])



if __name__ == "__main__":
    unittest.main()
