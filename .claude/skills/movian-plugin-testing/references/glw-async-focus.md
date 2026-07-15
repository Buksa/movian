# Deterministic Focus on Async GLW Pages

Use this pattern when a Movian page inserts rows or cards from concurrent
requests and the skin's initial focus depends on whichever item appears
first.

## Stable Design

1. Give the route a page-specific `metadata.glwview` based on the skin view
   it already used. Preserve the original `array`, `navWrap`, `chaseFocus`,
   header, scrollbar, spacing, and item loaders.
2. Append the stable focus target before starting asynchronous work. A
   search row is a good target because it exists before preview requests
   finish.
3. Track every request scheduled for the page with one completion barrier.
   Each request must decrement it exactly once on success, error, and empty
   data. Empty authenticated sections still count as completed work.
4. Arm focus only after the barrier reaches zero. Keep the trigger as a
   simple numeric metadata prop, for example `0` while loading and `1` when
   focus may run. Direct numeric props proved more predictable than a
   compound `select(...)` expression inside `onInactivity`.
5. Put `onInactivity(...)`, the static widget `id`, and `focus("id")` in the
   same loaded item view. A parent page view did not reliably resolve an ID
   owned by a separately loaded clone.
6. Add early, non-final handlers for `up`, `down`, `left`, `right`, and
   `activate` on the page-view root. They should mark that the user
   navigated and disarm the pending focus, while allowing the event to
   continue to the search widget or grid.

Example root guard:

```text
onEvent(down, {
  $self.model.metadata.userNavigated = 1;
  $self.model.metadata.focusDelay = 0;
}, true, false, true);
```

The useful GLW proof is `during descent final=no`, followed by the normal
widget handling. A final handler can swallow navigation; an ascent-only
handler can run too late when a child already consumed the event.

Example focus owner:

```text
onInactivity($parent.model.metadata.focusDelay, {
  focus("page-search");
});

widget(container_x, {
  id: "page-search";
  ...
});
```

Do not drive private focus props such as `prop_suggest_focus` from plugin
JavaScript. Let the GLW view own focus and let the standard grid own
subsequent navigation.

## Race Tests

Run two separate scenarios on isolated profiles (separate `mdev --name`
instances, or the same instance reopened via `mdev open` between runs):

1. Normal load: wait for all requests, then prove the ready prop, the focus
   trigger, and `FocusMethod` on the stable target.
2. Early input: send `Down` immediately after opening the route, before the
   barrier completes. Prove `userNavigated=1`, `focusDelay=0`, and absence of
   a later auto-focus that pulls the user back.

For the normal scenario, prove that `Down` from the search target produces
`FocusChild` on the first card and is then handled by the page's `array`.
Continue with `Right`, `Left`, `Up`, and `Down` to verify ordinary grid
navigation.

Always reopen the target route immediately before a focus test. Navigator
history can restore focus from a detail or full-list page and make logs
from a different skin view look like a failure of the custom page.

## Input Evidence

- `/api/input/action/<Action>` is valid for focus-routing and race behavior.
- It may not produce the visible `isNavFocused()` highlight.
- X11 input (`support/devtools/mdevlib/x11_keypress.py`) is preferable for
  visual cursor proof only when the GLW log shows the intended `Up`, `Down`,
  `Left`, or `Right` event (launch with `--debug-glw`; see `debug-flags.md`).
- If X11 reports `Click, Activate` for an arrow, record the environment
  problem and do not count that run as keyboard-navigation evidence.
