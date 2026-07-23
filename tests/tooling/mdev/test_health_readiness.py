#!/usr/bin/env python3
"""Behavior tests for mdev's pre-screenshot UI readiness barrier."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from support.devtools.mdevlib import smoke  # noqa: E402


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeInstance:
    def __init__(self, directory: Path, pids: list[int | None] | None = None):
        self.dir = directory
        self._pids = iter(pids) if pids is not None else None

    def base_url(self) -> str:
        return "http://127.0.0.1:42000"

    def live_pid(self) -> int | None:
        if self._pids is None:
            return 1234
        return next(self._pids, None)


class HealthReadinessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.inst = FakeInstance(Path(self.temporary.name))
        self.clock = FakeClock()
        self.time_patches = (
            mock.patch.object(smoke.time, "monotonic", self.clock.monotonic),
            mock.patch.object(smoke.time, "sleep", self.clock.sleep),
        )
        for patcher in self.time_patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_waits_for_delayed_framerate(self) -> None:
        values = iter((None, "(void)", "59.94"))
        with (
            mock.patch.object(
                smoke.harness, "read_log",
                return_value="navigator [INFO]: Opening page:home",
            ),
            mock.patch.object(
                smoke.harness, "prop_value",
                side_effect=lambda _base, _path: next(values),
            ) as prop_value,
        ):
            ready, detail = smoke._wait_for_ui_ready(self.inst, 2.0)

        self.assertTrue(ready)
        self.assertEqual(prop_value.call_count, 3)
        self.assertEqual(self.clock.now, 0.6)
        self.assertIn("UI framerate present", detail)

    def test_missing_framerate_timeout_names_condition(self) -> None:
        with (
            mock.patch.object(
                smoke.harness, "read_log",
                return_value="navigator [INFO]: Opening page:home",
            ),
            mock.patch.object(smoke.harness, "prop_value", return_value="(void)"),
        ):
            ready, detail = smoke._wait_for_ui_ready(self.inst, 0.6)

        self.assertFalse(ready)
        self.assertIn("within 0.6s", detail)
        self.assertIn(smoke.UI_FRAMERATE, detail)
        self.assertIn("usable value", detail)
        self.assertNotIn("missing navigator Opening trace", detail)

    def test_dead_process_fails_without_screenshot(self) -> None:
        inst = FakeInstance(Path(self.temporary.name), pids=[None])
        with (
            mock.patch.object(smoke.harness, "read_log") as read_log,
            mock.patch.object(smoke.harness, "prop_value") as prop_value,
            mock.patch.object(smoke, "_take_png") as take_png,
        ):
            with self.assertRaisesRegex(
                smoke.StepFailure, "instance process is not alive"
            ):
                smoke._health_step(inst, 1.0)

        read_log.assert_not_called()
        prop_value.assert_not_called()
        take_png.assert_not_called()

    def test_timeout_detail_names_both_missing_conditions(self) -> None:
        with (
            mock.patch.object(smoke.harness, "read_log", return_value=""),
            mock.patch.object(smoke.harness, "prop_value", return_value=None),
        ):
            ready, detail = smoke._wait_for_ui_ready(self.inst, 0.3)

        self.assertFalse(ready)
        self.assertIn("navigator Opening trace", detail)
        self.assertIn(smoke.UI_FRAMERATE, detail)

    def test_ready_probe_takes_one_screenshot_with_existing_timeout(self) -> None:
        events = []

        def prop_value(_base: str, path: str) -> str:
            self.assertEqual(path, smoke.UI_FRAMERATE)
            events.append("ready")
            return "60"

        def take_png(_inst, _path, timeout=15.0):
            events.append(("shot", timeout))
            return Path(self.temporary.name) / "shot.png", "abc123"

        with (
            mock.patch.object(
                smoke.harness, "read_log",
                return_value="navigator [INFO]: Opening page:home",
            ),
            mock.patch.object(smoke.harness, "prop_value", side_effect=prop_value),
            mock.patch.object(smoke, "_take_png", side_effect=take_png),
        ):
            healthy, detail = smoke._probe_health(self.inst, 1.0)

        self.assertTrue(healthy)
        self.assertEqual(events, ["ready", ("shot", 7.0)])
        self.assertIn("UI framerate", detail)
        self.assertIn("screenshot PNG present", detail)


if __name__ == "__main__":
    unittest.main()
