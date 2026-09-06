#!/usr/bin/env python3
"""`/api/open` is accepted and dropped, so issuing it once is not enough.

Measured on the stand (movian#233): an open issued 1s after `mdev run`
returns answers 302 and never reaches the navigator -- no "Opening <url>" in
the log, then or later. `page:settings`, which needs no plugin and no route
registration, is dropped the same way, so it is the navigator and not the
backend. The same request at ~3s works.

Waiting longer cannot recover a dropped event, which is why raising
`--timeout` never helped. The GET is idempotent, so `open_and_wait` issues it
again while the "Opening <url>" trace has not appeared.

What is pinned here is the pair: it must re-issue while nothing has landed,
and it must NOT re-issue once the navigation is seen -- a second open of a
page already opening is a real navigation the caller did not ask for.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from support.devtools.mdevlib import harness  # noqa: E402


class FakeInstance:
    def base_url(self):
        return "http://127.0.0.1:1"


class Reissue(unittest.TestCase):
    def drive(self, *, lands_after: int | None, timeout: float = 6.0):
        """Run `open_and_wait` against a navigator that swallows the first
        `lands_after` opens. Returns (result-or-error, opens issued)."""
        state = {"opens": 0, "landed": False}
        saved = (harness.http_request, harness.prop_value,
                 harness.read_log_delta, harness.log_size,
                 harness.node_count, harness.time)

        def http_request(base, path, timeout=5.0):
            if path.startswith("/api/open"):
                state["opens"] += 1
                if lands_after is not None and state["opens"] > lands_after:
                    state["landed"] = True
            return {"ok": True, "body": b""}

        def read_log_delta(inst, offset):
            return ("navigator [INFO ]: Opening test:url\n"
                    if state["landed"] else "")

        def prop_value(base, path, timeout=5.0):
            if not state["landed"]:
                return None
            if path == harness.PAGE_URL:
                return "test:url"
            if path == harness.PAGE_LOADING:
                return "0"
            return None

        class Clock:
            """Time that only moves when the code sleeps, so the test is not
            a real wait -- and so `NAV_REISSUE_AFTER` is measured, not raced."""

            def __init__(self):
                self.now = 0.0

            def monotonic(self):
                return self.now

            def sleep(self, seconds):
                self.now += seconds

        clock = Clock()
        harness.http_request = http_request
        harness.read_log_delta = read_log_delta
        harness.prop_value = prop_value
        harness.log_size = lambda inst: 0
        harness.node_count = lambda base, path=harness.PAGE_NODES: 0
        harness.time = clock
        try:
            try:
                return harness.open_and_wait(
                    FakeInstance(), "test:url", timeout=timeout), state["opens"]
            except harness.MdevError as error:
                return error, state["opens"]
        finally:
            (harness.http_request, harness.prop_value,
             harness.read_log_delta, harness.log_size,
             harness.node_count, harness.time) = saved

    def test_a_dropped_open_is_issued_again_and_succeeds(self) -> None:
        result, opens = self.drive(lands_after=1)
        self.assertIsInstance(result, dict)
        self.assertEqual(result["url"], "test:url")
        self.assertEqual(opens, 2)

    def test_an_open_that_lands_immediately_is_issued_once(self) -> None:
        """The other half. A re-issue is a real navigation nobody asked for,
        so it must not happen when the first one worked."""
        result, opens = self.drive(lands_after=0)
        self.assertIsInstance(result, dict)
        self.assertEqual(opens, 1)

    def test_re_issuing_is_bounded_and_the_count_is_reported(self) -> None:
        """A URL that never lands must still fail, and say how hard it
        tried -- `nav_event_seen=False` alone read as "mdev open never
        navigates" and sent the diagnosis to the wrong place."""
        error, opens = self.drive(lands_after=None, timeout=20.0)
        self.assertIsInstance(error, harness.MdevError)
        self.assertEqual(opens, harness.NAV_REISSUE_LIMIT)
        self.assertIn("open issued %d times" % harness.NAV_REISSUE_LIMIT,
                      str(error))


if __name__ == "__main__":
    unittest.main(verbosity=2)
