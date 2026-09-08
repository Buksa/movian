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
                 has_cancel: bool = False, popup_type: str = "message",
                 ignores_action: bool = False,
                 publishes_loading: bool = False,
                 url: str = "popuptest:blocking"):
        self.url = url
        self.raises_popup = raises_popup
        self.post_fails = post_fails
        # `message_popup(msg, MESSAGE_POPUP_CANCEL | MESSAGE_POPUP_OK)`
        # publishes both; the ok-only form leaves `cancel` void.
        self.has_cancel = has_cancel
        self.popup_type = popup_type
        # A sink that enqueues the event and does nothing with it --
        # `filepicker_event` ignores both actions outright.
        self.ignores_action = ignores_action
        # A route that publishes `loading = 0` outright, as against one
        # that never creates the prop.
        self.publishes_loading = publishes_loading
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
        if self.ignores_action:
            # 200 from prop_http.c, and the popup is still there.
            return True
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
            # `page.loading = false` while it was parked. A route that
            # never publishes one at all -- every static `page:*` -- keeps
            # it absent forever, which is the settle path, NOT `"0"`.
            if self.publishes_loading or self.resumed:
                return "0"
            return None
        if path == harness.PAGE_TITLE:
            return "popup dismissed" if self.resumed else None
        if path == harness.POPUP_MESSAGE_PROP:
            return self.MESSAGE if self.popups else None
        if path == harness.POPUP_TYPE_PROP:
            return self.popup_type if self.popups else None
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
        nav = Navigator(raises_popup=True, publishes_loading=True)
        nav.popups = 1
        result = drive(nav, dismiss_popups=False)
        self.assertIsInstance(result, dict, result)

    def test_a_static_route_with_no_popup_still_settles(self) -> None:
        """The control that matters, and it has to be the ABSENT-loading one.

        Every static `page:*` route never creates the `loading` prop at all,
        so it reaches ready only through the settle window this change put a
        guard in front of. A control that published `loading == "0"` would
        exercise the branch above instead and leave this one uncovered --
        which is what it did until a review said so: a regression here times
        out every static route and no test would have noticed.
        """
        nav = Navigator(raises_popup=False)
        self.assertIsNone(nav.prop_value(None, harness.PAGE_LOADING),
                          "the fake must keep `loading` absent, or this "
                          "test is pinning the wrong branch")
        result = drive(nav, dismiss_popups=False)
        self.assertIsInstance(result, dict, result)


class TheAnswerIsAlwaysCancel(unittest.TestCase):
    """The safety rule, and why it is one action rather than a choice.

    `Ok` is the dangerous one: every blocking popup the core raises that
    authorises something destructive acts only on OK -- `fileaccess.c:978`
    deletes files, `metadb.c:61` clears the metadata cache -- and
    `global/popups` is the whole queue with no attribution, so a wait cannot
    know whose popup it is answering.

    Choosing the action from what the popup said a moment ago does not help,
    because `*0` is POSITIONAL: the oldest popup closing between the read and
    the POST rebinds it to the next one, and an `Ok` decided for an ok-only
    popup could land on a deletion prompt. One action that is safe for every
    popup removes the question instead of racing it.
    """

    def test_a_popup_offering_cancel_is_declined(self) -> None:
        nav = Navigator(raises_popup=True, has_cancel=True)
        result = drive(nav)
        self.assertIsInstance(result, dict, result)
        self.assertEqual(nav.posts[0][1], {"action": "Cancel"})

    def test_an_ok_only_popup_is_declined_too(self) -> None:
        """The half that makes one action possible at all.

        `message_popup` maps whatever action arrives without checking its own
        flags (notifications.c:264-266), so Cancel answers a popup that never
        offered the button. Measured on the stand against
        `popup.message(msg, true, false)`: `cancel` read `(void)`, and
        `action=Cancel` resumed the route with `answer=false` and emptied the
        queue. Without that, declining would park an ok-only route forever
        and the safe action would not exist.
        """
        nav = Navigator(raises_popup=True, has_cancel=False)
        result = drive(nav)
        self.assertIsInstance(result, dict, result)
        self.assertEqual(nav.posts[0][1], {"action": "Cancel"})

    def test_ok_is_never_sent(self) -> None:
        """The negative, over every shape the fake can take."""
        for kwargs in ({"has_cancel": True}, {"has_cancel": False}):
            with self.subTest(**kwargs):
                nav = Navigator(raises_popup=True, **kwargs)
                drive(nav)
                self.assertNotIn(
                    "Ok", [form["action"] for _, form in nav.posts])

    def test_a_popup_that_is_not_a_message_is_left_alone(self) -> None:
        """A filepicker or an auth prompt wants input, not an action.
        `filepicker_event` ignores both, so posting would loop forever."""
        nav = Navigator(raises_popup=True, popup_type="filepicker")
        result = drive(nav)
        self.assertIsInstance(result, harness.MdevError, result)
        self.assertEqual(nav.posts, [])

    def test_a_sink_that_ignores_the_action_is_not_counted(self) -> None:
        """HTTP 200 means the event was enqueued, not that anything acted
        on it. Counting on the status alone appended a dismissal every tick
        for a popup still sitting there."""
        nav = Navigator(raises_popup=True, ignores_action=True)
        result = drive(nav)
        self.assertIsInstance(result, harness.MdevError, result)
        # Not `assertNotIn("answered")`: the standing refusal already ends
        # "parked until one is answered", so that matches whether or not
        # anything was counted. The evidence clause is what must be absent.
        # The prefix, not a count: `assertNotIn("answered 1 popup")` would
        # pass a bug that appended one per tick and reported "answered 7
        # popup(s)". The neighbouring "parked until one is answered" clause
        # is why the bare word cannot be used.
        self.assertNotIn("-- answered", str(result))
        # Exactly one. An answer stays outstanding until the queue confirms
        # it, so a sink that ignores the action gets asked once and not once
        # per tick -- and the refusal says the answer was posted and never
        # confirmed rather than claiming it worked.
        self.assertEqual(len(nav.posts), 1)
        self.assertIn("posted but unconfirmed", str(result))


class DismissalInsideTheLoop(unittest.TestCase):
    def test_the_popup_is_answered_and_the_page_becomes_ready(self) -> None:
        nav = Navigator(raises_popup=True)
        result = drive(nav)
        self.assertIsInstance(result, dict, result)
        self.assertTrue(nav.resumed, "the handler was never unparked")
        self.assertEqual([path for path, _ in nav.posts],
                         ["global/popups/*0/eventSink"])
        self.assertEqual(nav.posts[0][1], {"action": "Cancel"})

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
        self.assertEqual(result.get("popupsAnswered"),
                         ["%s [Cancel]" % Navigator.MESSAGE])

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
        # And a refusal is not an unconfirmed answer: nothing was accepted,
        # so there is nothing to report as posted.
        self.assertNotIn("posted but unconfirmed", str(result))

    def test_nothing_is_posted_when_no_popup_is_up(self) -> None:
        """The dismissal must be driven by an observed popup, not fired
        blindly every tick at a prop that is not there."""
        nav = Navigator(raises_popup=False)
        result = drive(nav)
        self.assertIsInstance(result, dict, result)
        self.assertEqual(nav.posts, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
