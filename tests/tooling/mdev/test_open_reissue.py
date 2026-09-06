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


class NavTraceMatching(unittest.TestCase):
    """The trace carries the whole URL, spaces included.

    `NAV_OPENING_RE` used `\\S+`, so `Opening canonproof:search:red lipstick`
    captured `canonproof:search:red`, the equality failed, and `nav_seen`
    stayed false for a navigation that HAD happened. That is movian#182's
    "any URL containing a space" -- which this file's comments had put down
    to `openerror` alone. Both causes were real; only one was fixed.

    Found by Codex on the re-issue PR, where it was about to become four
    spurious navigations per spaced URL instead of one wasted timeout.
    """

    def seen(self, line: str, url: str) -> bool:
        return any(m.group(1).rstrip() == url
                   for m in harness.NAV_OPENING_RE.finditer(line))

    def test_a_url_with_spaces_is_recognised(self) -> None:
        self.assertTrue(self.seen(
            "09:41:05.725: navigator [INFO ]: Opening "
            "canonproof:search:red lipstick",
            "canonproof:search:red lipstick"))

    def test_the_ordinary_urls_still_match(self) -> None:
        for url in ("page:home", "settings:", "introspect:page",
                    "page:settings"):
            with self.subTest(url):
                self.assertTrue(self.seen(
                    "09:41:05.725: navigator [INFO ]: Opening " + url, url))

    def test_a_different_url_is_not_matched(self) -> None:
        """The other half: to-end-of-line must not turn the check into
        "something was opened"."""
        self.assertFalse(self.seen(
            "09:41:05.725: navigator [INFO ]: Opening page:home",
            "page:settings"))
        self.assertFalse(self.seen(
            "09:41:05.725: navigator [INFO ]: Opening a b", "a"))


class Reissue(unittest.TestCase):
    def drive(self, *, lands_after: int | None, timeout: float = 6.0,
              url: str = "test:url"):
        """Run `open_and_wait` against a navigator that swallows the first
        `lands_after` opens. Returns (result-or-error, opens issued)."""
        state = {"opens": 0, "landed": False, "url": url}
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
            return ("navigator [INFO ]: Opening %s\n" % state["url"]
                    if state["landed"] else "")

        def prop_value(base, path, timeout=5.0):
            if not state["landed"]:
                return None
            if path == harness.PAGE_URL:
                return state["url"]
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
                    FakeInstance(), url, timeout=timeout), state["opens"]
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

    def test_a_spaced_url_that_lands_is_not_re_issued(self) -> None:
        """The regression the re-issue would have caused. Before the trace
        regex was fixed, a spaced URL never matched, so this path fired four
        times -- four real navigations for one request."""
        result, opens = self.drive(
            lands_after=0, url="canonproof:search:red lipstick")
        self.assertIsInstance(result, dict)
        self.assertEqual(opens, 1)

    def test_an_open_that_lands_immediately_is_issued_once(self) -> None:
        """The other half. A re-issue is a real navigation nobody asked for,
        so it must not happen when the first one worked."""
        result, opens = self.drive(lands_after=0)
        self.assertIsInstance(result, dict)
        self.assertEqual(opens, 1)

    def test_another_page_opening_is_not_this_one(self) -> None:
        """The equality, exercised through `open_and_wait` and not just
        against the regex.

        `nav_seen` exists to stop the PREVIOUS page being reported as this
        one: when the requested url is already open, the props look ready
        immediately. A gate that accepts any `Opening` line at all puts that
        failure straight back, and it survived a mutation until this test
        drove it.
        """
        state = {"opens": 0}
        saved = (harness.http_request, harness.prop_value,
                 harness.read_log_delta, harness.log_size,
                 harness.node_count, harness.time)

        class Clock:
            def __init__(self):
                self.now = 0.0

            def monotonic(self):
                return self.now

            def sleep(self, seconds):
                self.now += seconds

        def http_request(base, path, timeout=5.0):
            if path.startswith("/api/open"):
                state["opens"] += 1
            return {"ok": True, "body": b""}

        harness.http_request = http_request
        # Somebody ELSE'S navigation is in the log, and the props are those
        # of a page that is fully loaded -- exactly the shape that would be
        # misreported as ours.
        harness.read_log_delta = \
            lambda inst, offset: "navigator [INFO ]: Opening other:page\n"
        harness.prop_value = lambda base, path, timeout=5.0: {
            harness.PAGE_URL: "other:page",
            harness.PAGE_LOADING: "0"}.get(path)
        harness.log_size = lambda inst: 0
        harness.node_count = lambda base, path=harness.PAGE_NODES: 0
        harness.time = Clock()
        try:
            with self.assertRaises(harness.MdevError) as caught:
                harness.open_and_wait(FakeInstance(), "test:url", timeout=8.0)
        finally:
            (harness.http_request, harness.prop_value,
             harness.read_log_delta, harness.log_size,
             harness.node_count, harness.time) = saved
        self.assertIn("nav_event_seen=False", str(caught.exception))

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
