#!/usr/bin/env python3
"""A popup parks the route, and `mdev open` called the parked page ready
(movian#242).

`native/popup.message` is synchronous. `es_message` calls `message_popup()`
and switches on its return value (`src/ecmascript/es_misc.c:154-181`), and
`message_popup` builds the popup prop then blocks in `popup_display()`
(`src/notifications.c:223-262`). A handler that calls it never reaches
`page.loading = false`, so the `loading` prop is never created at all.

That absence is the trap. `open_and_wait` read an absent `loading` as the
static `page:*` case, waited `ABSENT_LOADING_SETTLE` for the state to hold,
and declared the page ready. Measured on the stand against a route that
raises a popup from inside its own handler:

    $ mdev open --name popup242 popuptest:blocking
    url:   popuptest:blocking
    title: (void)
    exit=0                      <-- reported READY

    global/popups/ (directory, 1 children)
      *0/  type = message  message = issue #242 probe: dismiss me  ok = 1
    18:51:21.753: popuptest242: blocking route entered; raising popup
                                       (no "resumed" line)

So the defect is a false GREEN, not the timeout the issue first described.

Three cases, pinned apart because they fail apart:

* absent `loading` + a popup pending -> NOT ready. The measured defect.
* definite `loading == "0"` + a popup pending -> still ready. A page that
  finished and then asked something has published a finished state, and
  refusing it would break every such page.
* absent `loading` + no popup -> still ready. Every static `page:*` route
  reaches ready only through this settle window, so a regression here times
  out all of them.

The fakes keep `loading` ABSENT rather than `"0"` wherever the case is about
the settle window; a control that published `"0"` would exercise the branch
above and leave the guarded path uncovered.
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


class Navigator:
    """A navigator whose route may park on a popup.

    `popups` is what `global/popups` would report. It goes to 1 only after
    the navigation lands -- the route is what creates it.
    """

    def __init__(self, *, raises_popup: bool, publishes_loading: bool = False,
                 popups_before: int = 0, probe_readable: bool = True,
                 url: str = "popuptest:blocking"):
        self.url = url
        self.raises_popup = raises_popup
        # A route that publishes `loading = 0` outright, as against one that
        # never creates the prop at all.
        self.publishes_loading = publishes_loading
        # Something unrelated already up before this navigation -- a ConnMan
        # credential request, a file picker.
        self.popups = popups_before
        self.popups_before = popups_before
        # `get_prop` returning None: the request timed out or was refused.
        self.probe_readable = probe_readable
        self.landed = False

    def http_request(self, base, path, timeout=5.0, method="GET", form=None):
        if path.startswith("/api/open"):
            self.landed = True
            if self.raises_popup:
                self.popups = self.popups_before + 1
        return {"ok": True, "body": b""}

    def read_log_delta(self, inst, offset):
        return ("navigator [INFO ]: Opening %s\n" % self.url
                if self.landed else "")

    def prop_value(self, base, path, timeout=5.0):
        if not self.landed:
            return None
        if path == harness.PAGE_URL:
            return self.url
        if path == harness.PAGE_LOADING:
            return "0" if self.publishes_loading else None
        return None

    def node_count(self, base, path=harness.PAGE_NODES):
        return 0

    def pending_popups(self, base):
        return self.popups if self.probe_readable else None


class Clock:
    """Time that only moves when the code sleeps, so the test is not a real
    wait and the settle window is measured rather than raced."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def drive(nav: Navigator, *, timeout: float = 6.0):
    saved = (harness.http_request, harness.prop_value,
             harness.read_log_delta, harness.log_size,
             harness.node_count, harness.time, harness.pending_popups)
    harness.http_request = nav.http_request
    harness.read_log_delta = nav.read_log_delta
    harness.prop_value = nav.prop_value
    harness.log_size = lambda inst: 0
    harness.node_count = nav.node_count
    harness.pending_popups = nav.pending_popups
    harness.time = Clock()
    try:
        try:
            return harness.open_and_wait(FakeInstance(), nav.url,
                                         timeout=timeout)
        except harness.MdevError as error:
            return error
    finally:
        (harness.http_request, harness.prop_value,
         harness.read_log_delta, harness.log_size,
         harness.node_count, harness.time, harness.pending_popups) = saved


class PendingPopupIsNotReady(unittest.TestCase):
    def test_a_parked_route_is_not_reported_ready(self) -> None:
        """The measured defect. Today this returned a dict and exit 0."""
        nav = Navigator(raises_popup=True)
        result = drive(nav)
        self.assertIsInstance(
            result, harness.MdevError,
            "a route parked on a popup was reported ready: %r" % (result,))
        self.assertIn("popup", str(result).lower())

    def test_the_refusal_says_what_is_pending(self) -> None:
        """A refusal naming the wrong cause is worse than none. The count is
        what tells a reader the route is parked rather than merely slow."""
        result = drive(Navigator(raises_popup=True))
        self.assertIn("1 popup(s) pending", str(result))

    def test_a_page_that_publishes_loading_zero_is_still_ready(self) -> None:
        """The narrowing, stated as a case. A page that finished and then
        asked something has published a finished state; refusing it would
        break every such page, and nothing attributes a popup to a route."""
        nav = Navigator(raises_popup=True, publishes_loading=True)
        nav.popups = 1
        self.assertIsInstance(drive(nav), dict)

    def test_a_static_route_with_no_popup_still_settles(self) -> None:
        """The control, and it has to be the ABSENT-loading one.

        Every static `page:*` route never creates the `loading` prop, so it
        reaches ready only through the settle window this guard sits in
        front of. A control that published `loading == "0"` would exercise
        the branch above and leave this one uncovered -- a regression here
        times out every static route.
        """
        nav = Navigator(raises_popup=False)
        self.assertIsNone(nav.prop_value(None, harness.PAGE_LOADING),
                          "the fake must keep `loading` absent, or this test "
                          "is pinning the wrong branch")
        self.assertIsInstance(drive(nav), dict)


class OnlyThisNavigationsPopups(unittest.TestCase):
    """A popup that predates the open cannot have parked this route.

    `global/popups` is global. A ConnMan credential request
    (networking/connman.c:341) or a file picker (fa_filepicker.c:296) can be
    pending for reasons unrelated to the navigation, and blocking on one
    would hang every static `page:*` open until the deadline and blame the
    route for it.
    """

    def test_a_pre_existing_popup_does_not_block_a_static_route(self) -> None:
        nav = Navigator(raises_popup=False, popups_before=1)
        self.assertIsInstance(drive(nav), dict)

    def test_a_popup_raised_on_top_of_one_still_blocks(self) -> None:
        """The other half: the count has to RISE, not merely be non-zero."""
        nav = Navigator(raises_popup=True, popups_before=1)
        result = drive(nav)
        self.assertIsInstance(result, harness.MdevError, result)
        self.assertIn("2 popup(s) pending (1 before this open)", str(result))


class AnUnreadableProbeFailsClosed(unittest.TestCase):
    """AGENTS.md: a silent instrument is not evidence until the instrument is
    known to be working.

    `node_count` collapsed an unreadable prop to 0, so a timed-out or
    refused read said "no popup" and let a parked page settle -- the false
    green restored by the instrument failing rather than by the bug
    returning.
    """

    def test_an_unreadable_probe_does_not_certify_ready(self) -> None:
        nav = Navigator(raises_popup=True, probe_readable=False)
        result = drive(nav)
        self.assertIsInstance(result, harness.MdevError, result)
        self.assertIn("probe could not be read", str(result))

    def test_it_fails_closed_even_with_no_popup_at_all(self) -> None:
        """The sharp case: nothing is pending, but we cannot see that. A
        wait that trusts an unreadable instrument is guessing."""
        nav = Navigator(raises_popup=False, probe_readable=False)
        self.assertIsInstance(drive(nav), harness.MdevError)


if __name__ == "__main__":
    unittest.main(verbosity=2)
