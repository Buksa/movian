# Deterministic GLW Focus For Async Plugin Pages

Use this pattern when a plugin page populates a grid from concurrent requests.
Without an explicit policy, initial focus can land on whichever item becomes
focusable first, so request timing changes navigation behavior.

## Stable Design

1. Append a stable focus target before starting asynchronous work. A search row
   is useful because it exists before preview requests finish.
2. Give the route a page-specific `metadata.glwview` based on the skin view it
   already used.
3. Preserve the standard grid contract: `array`, model-node `cloner`,
   `chaseFocus`, `navWrap`, item loaders, `PageHeader`, `ScrollBar`, spacing,
   and clipping.
4. Count every request scheduled for the page with one completion barrier.
5. Arm a one-shot GLW focus only after every request has completed.
6. Cancel pending auto-focus when the user navigates before completion.

Do not drive private focus properties such as `prop_suggest_focus` from plugin
JavaScript. The plugin should publish readiness and interaction state; the GLW
view should own focus.

## Completion Barrier

Every scheduled request must decrement the barrier exactly once for:

- successful data;
- empty data;
- HTTP or parsing errors;
- empty authenticated sections.

When the barrier reaches zero, set a ready property. Use a simple numeric
property for the `onInactivity` timeout, such as `0` while loading and `1` when
focus may run. This proved more predictable than a compound `select(...)`
expression used directly as the timeout.

Example plugin metadata:

```js
page.metadata.pageReady = false;
page.metadata.userNavigated = false;
page.metadata.focusDelay = 0;

function sectionComplete() {
    if (--pending === 0) {
        page.metadata.pageReady = true;
        if (parseInt(page.metadata.userNavigated.toString(), 10) === 0)
            page.metadata.focusDelay = 1;
    }
}
```

Make sure the list helper calls its completion callback on error and empty
paths as well as success.

## Focus Scope

Keep `onInactivity(...)`, the static widget ID, and `focus("id")` in the same
loaded item view:

```view
onInactivity($parent.model.metadata.focusDelay, {
  focus("page-search");
});

widget(container_x, {
  id: "page-search";
  ...
});
```

An ID owned by a separately loaded clone was not reliably resolved when
`focus(...)` ran from the parent grid view. This matches the implementation in
`src/ui/glw/glw_view_eval.c`, where focus lookup starts from the current view
evaluation context.

## Early Non-Final Guards

If input arrives before loading completes, disarm the pending focus without
consuming the input:

```view
onEvent(down, {
  $self.model.metadata.userNavigated = 1;
  $self.model.metadata.focusDelay = 0;
}, true, false, true);
```

Use equivalent guards for `up`, `left`, `right`, and `activate`.

The early phase records intent before a child consumes the event. The non-final
form allows normal search or grid navigation to continue. With `--debug-glw`,
the expected evidence includes:

```text
event-map 'down' ... during descent final=no
... by FocusChild
array @ ... custom-grid.view
```

A final handler can swallow navigation. An ascent-only handler can run too
late.

## Race Test

Run two isolated-profile scenarios:

1. Normal load:
   - wait for all sections;
   - prove the ready property and `FocusMethod` on the stable target;
   - send `Down` and verify `FocusChild` on the first card;
   - verify subsequent arrow events are handled by the grid `array`.
2. Early input:
   - send `Down` immediately after opening the route;
   - wait for loading to finish;
   - prove `userNavigated=1` and `focusDelay=0`;
   - verify no later `FocusMethod` pulls focus back.

Reopen the exact route before collecting focus evidence. Navigator history may
restore focus from a detail or full-list page and produce logs from a different
view.

HTTP input actions are valid for focus-routing tests but may not display the
visual `isNavFocused()` highlight because they do not carry `EVENT_KEYPRESS`.
Use X11 for visual proof only when the GLW log shows the intended arrow event.
Some WSL/X11 setups can translate an injected arrow into `Click, Activate`;
that run is not valid keyboard-navigation evidence.

## Relevant Core Files

- `glwskins/flat/pages/grid.view`
- `src/ui/glw/glw_view_eval.c`
- `src/ui/glw/glw_event.c`
- `src/ui/glw/glw_navigation.c`
- `src/ui/glw/glw_array.c`
- `src/ui/glw/glw_x11.c`
