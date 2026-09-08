#!/usr/bin/env python3
"""A popup parks the route, and `mdev open` calls the parked page ready
(movian#242).

`native/popup.message` is synchronous. `es_message` calls `message_popup()`
and switches on its return value (`src/ecmascript/es_misc.c:154-181`), and
`message_popup` builds the popup prop then blocks in `popup_display()`
(`src/notifications.c:223-262`). A handler that calls it never reaches
`page.loading = false`, so the `loading` prop is never created at all.

That absence is the trap. `open_and_wait` reads an absent `loading` as the
static `page:*` case, waits `ABSENT_LOADING_SETTLE` for the state to hold,
and declares the page ready. Measured on the stand against a route that
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

Two rules come out of that, and they are pinned apart because they fail
apart:

* An absent `loading` must not settle into ready while a popup is pending.
  A definite `loading == "0"` still may -- a page that finished and then
  asked something IS ready, and blocking on that would break it.
* The dismissal must run INSIDE the wait loop. The popup is created by the
  route, so at the moment `/api/open` is issued there is nothing to dismiss;
  the fakes below raise it only after the navigation lands, which is what
  makes a pre-loop dismissal fail this file.

The dismissal itself is `POST action=Ok` to the `*0` indexed child, the
lookup movian#152 restored. Verified against the stand: status 200, the
route logged `resumed, answer=true`, `global/popups` emptied and `loading`
became `0`.
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
    """A navigator whose route raises a popup and parks.

    `popups` counts what `global/popups` would report. It goes to 1 only
    after the navigation lands -- the route is what creates it -- and back to
    0 when something POSTs the eventSink, which is when the parked handler
    resumes and publishes `loading`.
    """

    # The wording a real run produced on the stand, kept verbatim: the probe
    # must carry what the producer writes, and this is what
    # `global/popups/*0/message` held while the route was parked.
    MESSAGE = "issue #242 probe: dismiss me"

    def __init__(self, *, raises_popup: bool, post_fails: bool = False,
                 url: str = "popuptest:blocking"):
        self.url = url
        self.raises_popup = raises_popup
        self.post_fails = post_fails
        self.landed = False
        self.popups = 0
        self.posts: list[tuple[str, dict[str, str]]] = []
        self.resumed = False

    # -- the reads and the one write open_and_wait makes -------------------
    def http_request(self, base, path, timeout=5.0, method="GET", form=None):
        if path.startswith("/api/open"):
            self.landed = True
            if self.raises_popup:
                self.popups = 1
        return {"ok": True, "body": b""}

    def post_prop(self, base, path, form, timeout=5.0):
        self.posts.append((path, form))
        if self.post_fails:
            return False
        # Answering it unparks the handler, which then publishes `loading`
        # -- exactly the sequence observed on the stand.
        self.popups = 0
        self.resumed = True
        return True

    def read_log_delta(self, inst, offset):
        return ("navigator [INFO ]: Opening %s\n" % self.url
                if self.landed else "")

    def prop_value(self, base, path, timeout=5.0):
        if not self.landed:
            return None
        if path == harness.PAGE_URL:
            return self.url
        if path == harness.PAGE_LOADING:
            # Absent until the handler resumes: the route never got to
            # `page.loading = false` while it was parked.
            return "0" if self.resumed or not self.raises_popup else None
        if path == harness.PAGE_TITLE:
            return "popup dismissed" if self.resumed else None
        if path == harness.POPUP_MESSAGE_PROP:
            return self.MESSAGE if self.popups else None
        return None

    def node_count(self, base, path=harness.PAGE_NODES):
        return self.popups if path == harness.POPUPS_PROP else 0


class Clock:
    """Time that only moves when the code sleeps, so the test is not a real
    wait and the settle window is measured rather than raced."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def drive(nav: Navigator, *, timeout: float = 6.0, **kwargs):
    saved = (harness.http_request, harness.prop_value,
             harness.read_log_delta, harness.log_size,
             harness.node_count, harness.time, harness.post_prop)
    harness.http_request = nav.http_request
    harness.read_log_delta = nav.read_log_delta
    harness.prop_value = nav.prop_value
    harness.log_size = lambda inst: 0
    harness.node_count = nav.node_count
    harness.post_prop = nav.post_prop
    harness.time = Clock()
    try:
        try:
            return harness.open_and_wait(
                FakeInstance(), nav.url, timeout=timeout, **kwargs)
        except harness.MdevError as error:
            return error
    finally:
        (harness.http_request, harness.prop_value,
         harness.read_log_delta, harness.log_size,
         harness.node_count, harness.time, harness.post_prop) = saved


class PendingPopupIsNotReady(unittest.TestCase):
    def test_a_parked_route_is_not_reported_ready(self) -> None:
        """The false green, with dismissal switched off so nothing rescues
        it. Today this returns a dict and exit 0."""
        nav = Navigator(raises_popup=True)
        result = drive(nav, dismiss_popups=False)
        self.assertIsInstance(
            result, harness.MdevError,
            "a route parked on a popup was reported ready: %r" % (result,))
        self.assertIn("popup", str(result).lower())

    def test_a_page_that_publishes_loading_zero_is_still_ready(self) -> None:
        """The other half, and the reason the rule is about ABSENT loading
        only. A page that finished and then asked something has published a
        finished state; refusing it would break every such page."""
        nav = Navigator(raises_popup=True)
        nav.resumed = True          # loading == "0" while a popup is up
        nav.popups = 1
        result = drive(nav, dismiss_popups=False)
        self.assertIsInstance(result, dict, result)

    def test_no_popup_no_change(self) -> None:
        """The control. Without a popup the absent-loading path must still
        settle into ready, or this fix has broken every static page:* route."""
        nav = Navigator(raises_popup=False)
        nav.resumed = False
        result = drive(nav, dismiss_popups=False)
        self.assertIsInstance(result, dict, result)


class DismissalInsideTheLoop(unittest.TestCase):
    def test_the_popup_is_answered_and_the_page_becomes_ready(self) -> None:
        nav = Navigator(raises_popup=True)
        result = drive(nav)
        self.assertIsInstance(result, dict, result)
        self.assertTrue(nav.resumed, "the handler was never unparked")
        self.assertEqual([path for path, _ in nav.posts],
                         ["global/popups/*0/eventSink"])
        self.assertEqual(nav.posts[0][1], {"action": "Ok"})

    def test_what_was_dismissed_is_reported(self) -> None:
        """DoD 4. A plugin that popups on every open is a finding about the
        plugin; swallowing it turns a diagnosis into a hidden retry."""
        nav = Navigator(raises_popup=True)
        result = drive(nav)
        self.assertIsInstance(result, dict, result)
        self.assertEqual(result.get("popupsDismissed"), 1)
        # A count says one was answered; only the text says WHICH, and the
        # queue is global -- the core raises blocking popups too, so a run
        # that answered somebody else's must be able to show it.
        self.assertEqual(result.get("popupsAnswered"), [Navigator.MESSAGE])

    def test_dismissal_is_opt_out(self) -> None:
        """DoD 3. A test that wants to assert a popup appeared must be able
        to stop mdev answering it."""
        nav = Navigator(raises_popup=True)
        drive(nav, dismiss_popups=False)
        self.assertEqual(nav.posts, [])

    def test_a_failed_dismissal_does_not_end_the_wait(self) -> None:
        """DoD 5. No popup is the normal case and a refused POST is not an
        error the caller should see; the wait carries on and fails, if it
        fails, for the reason that is actually true."""
        nav = Navigator(raises_popup=True, post_fails=True)
        result = drive(nav)
        self.assertIsInstance(
            result, harness.MdevError,
            "a popup that could not be answered must not read as ready")
        self.assertIn("popup", str(result).lower())
        self.assertGreater(len(nav.posts), 1,
                           "one refused POST ended the whole wait")

    def test_nothing_is_posted_when_no_popup_is_up(self) -> None:
        """The dismissal must be driven by an observed popup, not fired
        blindly every tick at a prop that is not there."""
        nav = Navigator(raises_popup=False)
        result = drive(nav)
        self.assertIsInstance(result, dict, result)
        self.assertEqual(nav.posts, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
