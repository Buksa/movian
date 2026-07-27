#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Focused deterministic coverage for issue #146 emergency-eject rate-limit
immunity (P2-2).

Two production invariants live behind ``if _HAVE_GDB:`` and were therefore
unreachable from CPython tests:

  * ``Collector.on_hit`` advances the emergency-eject tracker *before* the
    per-category cap-suppression ``return``, so a mandatory shutdown probe
    that is itself rate-limited still records its transition.
  * ``Collector._disable_category`` never disables a mandatory eject probe,
    so the shutdown chain stays observable after an ordinary category is
    capped.

This file locks both down.  The immunity contract (which breakpoints a
category-wide disable may touch) is extracted into the pure, GDB-free
``rate_limit_should_disable`` predicate and tested directly.  The ordering
contract is exercised against the *real* ``Collector`` by loading a fresh
copy of ``movian_lifecycle`` with a minimal fake ``gdb`` module (just enough
to satisfy class definition + ``Collector.__init__``); it never runs a real
inferior.  Run with:

    python3 -m unittest tests.tooling.gdb.test_eject_ratelimit
"""

import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
_LIFECYCLE = ROOT / "support" / "devtools" / "gdb" / "movian_lifecycle.py"


# ---------------------------------------------------------------------------
# Minimal fake gdb -- just enough for movian_lifecycle to define its GDB-only
# classes and instantiate the Collector.  NOT a GDB scaffold: no inferior is
# run, no breakpoints are armed against a binary.  Only the few attributes
# referenced at class-definition / Collector-construction time are provided.
# ---------------------------------------------------------------------------
class _EventSlot:
    def connect(self, fn):
        pass

    def disconnect(self, fn):
        pass


class _FakeBreakpoint:
    def __init__(self, spec, internal=False):
        self.locations = []
        self.enabled = True

    def delete(self):
        pass


class _FakeCommand:
    def __init__(self, name, command_class=None):
        pass


class _FakeEvents:
    exited = _EventSlot()
    gdb_exiting = _EventSlot()
    stop = _EventSlot()


class _FakeThread:
    def __init__(self, ptid):
        self.ptid = ptid
        self.global_num = 1
        self.name = "fake-thread"


class _FakeInferior:
    pid = 0


def _make_fake_gdb():
    gdb = types.ModuleType("gdb")
    gdb.Breakpoint = _FakeBreakpoint
    gdb.Command = _FakeCommand
    gdb.GdbError = type("GdbError", (Exception,), {})
    gdb.COMMAND_DATA = 0
    gdb.execute = lambda *a, **k: ""
    gdb.events = _FakeEvents()
    gdb.newest_frame = lambda: None
    gdb.selected_inferior = lambda: _FakeInferior()
    gdb.parse_and_eval = lambda expr: None
    # Tests mutate ``thread_ptid`` to drive the eject-tracker TID logic.
    gdb.thread_ptid = (1, 4242, 0)
    gdb.selected_thread = lambda: _FakeThread(gdb.thread_ptid)
    return gdb


def _load_lifecycle_with_gdb():
    """Import a fresh copy of movian_lifecycle under a fake ``gdb``.

    Loaded under a throwaway module name so it never contaminates the cached
    ``movian_lifecycle`` (which other test files import with
    ``_HAVE_GDB == False``).  ``sys.modules['gdb']`` is restored afterwards;
    the loaded module keeps its own bound ``gdb`` reference regardless.
    """
    fake = _make_fake_gdb()
    saved = sys.modules.pop("gdb", None)
    sys.modules["gdb"] = fake
    try:
        spec = importlib.util.spec_from_file_location(
            "_movian_lifecycle_ratelimit_test", _LIFECYCLE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        if saved is not None:
            sys.modules["gdb"] = saved
        else:
            sys.modules.pop("gdb", None)


LC = _load_lifecycle_with_gdb()


class _Bp:
    """Plain stand-in for a LifecycleBP hit / armed entry."""

    def __init__(self, category, symbol, bound=True):
        self.category = category
        self.symbol = symbol
        self.bound = bound
        self.enabled = True
        self.arg_exprs = []


class RateLimitShouldDisableTest(unittest.TestCase):
    """Pure predicate: which breakpoints a category-wide disable may touch.

    These run with no GDB and would FAIL if mandatory eject probes lost their
    immunity (e.g. the ``is_eject_mandatory`` check were dropped).
    """

    def test_mandatory_probes_are_immune(self):
        for sym in ("app_shutdown", "shutdown_eject", "arch_exit"):
            self.assertFalse(
                LC.rate_limit_should_disable(_Bp("core-init", sym), "core-init"),
                "%s must not be disabled by rate-limiting" % sym)

    def test_non_mandatory_in_category_is_disabled(self):
        self.assertTrue(
            LC.rate_limit_should_disable(_Bp("core-init", "main_init"),
                                         "core-init"))

    def test_unbound_probe_is_not_disabled(self):
        # An unbound probe is never armed, so disabling it is a no-op; the
        # predicate must report False for both mandatory and ordinary symbols.
        self.assertFalse(LC.rate_limit_should_disable(
            _Bp("core-init", "app_shutdown", bound=False), "core-init"))
        self.assertFalse(LC.rate_limit_should_disable(
            _Bp("core-init", "main_init", bound=False), "core-init"))

    def test_other_category_is_not_disabled(self):
        self.assertFalse(LC.rate_limit_should_disable(
            _Bp("prop-subscribe", "main_init"), "core-init"))

    def test_disable_category_skips_mandatory_for_real(self):
        """The real Collector._disable_category must leave mandatory probes
        armed while disabling ordinary ones in the capped category."""
        with tempfile.TemporaryDirectory() as d:
            col = LC.Collector(str(Path(d) / "events.jsonl"),
                               default_cap=1, hv_overrides={})
            self.addCleanup(col.close)
            mandatory = [_Bp("core-init", s)
                         for s in ("app_shutdown", "shutdown_eject",
                                   "arch_exit")]
            ordinary = [_Bp("core-init", "main_init"),
                        _Bp("core-init", "fa_load")]
            col._armed = mandatory + ordinary
            col._disable_category("core-init")
            for bp in mandatory:
                self.assertTrue(bp.enabled,
                                "%s must stay enabled" % bp.symbol)
            for bp in ordinary:
                self.assertFalse(bp.enabled,
                                 "%s must be disabled" % bp.symbol)


class CollectorRateLimitOrderingTest(unittest.TestCase):
    """The real Collector.on_hit must advance the eject tracker BEFORE the
    cap-suppression return, so rate-limited mandatory hits still record their
    transitions.  These FAIL if ``_update_eject_tracker`` is moved below the
    ``if cap and n > cap: ... return`` block (or inside the non-capped arm).
    """

    def _new_collector(self, tmpdir, cap=1):
        events = str(Path(tmpdir) / "events.jsonl")
        col = LC.Collector(events, default_cap=cap, hv_overrides={})
        self.addCleanup(col.close)
        # Precondition reproduced from arm_from_inventory: all three mandatory
        # eject symbols are selected and bound, so observation is active.
        col._eject_tracker.observe()
        return col, events

    def _hit(self, col, category, symbol, ptid=None):
        if ptid is not None:
            LC.gdb.thread_ptid = ptid
        col.on_hit(_Bp(category, symbol))

    def test_tracker_advances_under_rate_limit(self):
        """cap=1: after the first hit saturates the category, every later hit
        is rate-limited (no 'enter' emitted) yet still advances the tracker."""
        with tempfile.TemporaryDirectory() as d:
            col, events = self._new_collector(d, cap=1)
            self._hit(col, "core-init", "main_init", ptid=(1, 1, 0))  # n=1, emitted
            self.assertEqual(col._eject_tracker.state, "not-requested")
            self._hit(col, "core-init", "app_shutdown", ptid=(1, 10, 0))  # n=2, capped
            self.assertEqual(col._eject_tracker.state, "requested")
            self._hit(col, "core-init", "shutdown_eject", ptid=(1, 4242, 0))  # capped
            self.assertEqual(col._eject_tracker.state, "armed")
            self.assertEqual(col._eject_tracker._eject_tid, 4242)

            lines = [json.loads(ln) for ln in
                     Path(events).read_text(encoding="utf-8").splitlines()
                     if ln.strip()]
            enters = [e for e in lines if e.get("event") == "enter"]
            # Only the saturating non-mandatory hit produced an 'enter'; the
            # two mandatory hits were suppressed yet still drove the tracker
            # to 'armed' -- i.e. _update_eject_tracker ran before the return.
            self.assertEqual([e["symbol"] for e in enters], ["main_init"])
            self.assertTrue(any(e.get("event") == "rate-limited"
                                for e in lines))

    def test_fired_reached_under_rate_limit_on_eject_thread(self):
        """A full mandatory chain under rate-limiting reaches 'fired' when
        arch_exit runs on the eject thread."""
        with tempfile.TemporaryDirectory() as d:
            col, _ = self._new_collector(d, cap=1)
            self._hit(col, "core-init", "main_init", ptid=(1, 1, 0))  # saturate
            self._hit(col, "core-init", "app_shutdown", ptid=(1, 10, 0))  # capped
            self._hit(col, "core-init", "shutdown_eject", ptid=(1, 4242, 0))  # capped
            self._hit(col, "core-init", "arch_exit", ptid=(1, 4242, 0))  # capped
            self.assertEqual(col._eject_tracker.state, "fired")

    def test_wrong_thread_arch_exit_stays_armed_under_rate_limit(self):
        """arch_exit on a thread other than the eject thread leaves the
        tracker 'armed' (the eject timer did not actually fire), even though
        every hit is rate-limited."""
        with tempfile.TemporaryDirectory() as d:
            col, _ = self._new_collector(d, cap=1)
            self._hit(col, "core-init", "main_init", ptid=(1, 1, 0))  # saturate
            self._hit(col, "core-init", "app_shutdown", ptid=(1, 10, 0))  # capped
            self._hit(col, "core-init", "shutdown_eject", ptid=(1, 4242, 0))  # capped
            self._hit(col, "core-init", "arch_exit", ptid=(1, 9999, 0))  # capped
            self.assertEqual(col._eject_tracker.state, "armed")
            self.assertFalse(col._eject_tracker.snapshot()["fired"])


if __name__ == "__main__":
    unittest.main()
