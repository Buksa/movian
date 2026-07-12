# GLW Pointer And Touch Smoke

Use this workflow for horizontal rows, nested scrolling, sliders, hover,
touch, or kinetic-scroll changes.

## Fixture Shape

Prefer a deterministic page with at least three independently scrollable
sections. Give cards visible prefixes such as `U01`, `N01`, and `T01` so row
offsets can be read directly from screenshots.

For cloned sections, keep the row and its optional scrollbar in the same
clone:

```view
widget(list_x, {
  id: "section-row";
  ...
});

widget(slider_x, {
  bind("section-row");
  focusable: canScroll();
});
```

A repeated local ID is valid here: binding resolves the nearest row in each
cloned section, so rows retain independent positions.

Keep optional UI disabled in the tracked fixture when that is the intended
default. For scrollbar smoke, copy the fixture to `/tmp`, enable the
scrollbar only in that copy, and load the temporary plugin with `mdev run -p
<copy-dir>`.

## Input Matrix

Launch Movian with an isolated profile and `--pointer-is-touch` for touch
gestures (fallback direct launch; not yet an `mdev run` flag). Use
`support/devtools/mdevlib/x11_pointer.py` for pointer input and
`support/devtools/mdevlib/x11_keypress.py` for visible keyboard focus.

Prove each behavior independently:

- horizontal drag changes only the selected `list_x`;
- a second row keeps its offset when the first row moves;
- vertical swipe over a card scrolls the enclosing `list_y`;
- ordinary vertical wheel input over a horizontal row bubbles to `list_y`;
- D-pad left/right stays within a row and up/down crosses sections;
- pointer hover and keyboard focus produce their intended visual states;
- each enabled local scrollbar moves only its bound row.

## Kinetic And Grab Checks

Capture screenshots before release, immediately after release, and 200-800
ms later (`mdev shot` for each). Kinetic scrolling passes when the selected
row continues moving after release while other rows remain stable.

Repeat the drag with the release point outside the original row. It must
still finalize kinetic scrolling. After motion settles, send an ordinary
pointer move without pressing a button and capture another screenshot. The
row must not move; otherwise a scroll grab may still be active.

`TOUCH_CANCEL` is usually a source-level check unless the environment has a
reliable native touch injector. Confirm that cancellation is consumed only
by the current scroll owner and can reach a parent after vertical handoff.

## Evidence

Store artifacts under `/tmp/movian-*-smoke` (or alongside the `mdev`
instance's own state):

- exact launch and input commands;
- Movian log (`mdev log`);
- page title and `loading=0`-or-void (`mdev props`);
- before/release/delayed screenshots (`mdev shot`);
- per-row or per-scrollbar screenshots;
- log grep for JS, GLW, assertion, signal, and crash failures (`mdev log
  --errors`).

Do not infer motion from logs alone. Read the visible card labels or
compare images. Always check that unrelated rows and the outer vertical
position remain stable when they are expected to.

For plugin archive discovery, build a ZIP only under `/tmp`, place
`plugin.json` at its root, and require a manifest `title`. Confirm that
Movian shows that title, then remove the archive.

## Review Gate

Before merge, inspect inline review threads as well as the review summary.
Filter comments to the current head SHA and confirm that no unresolved,
non-outdated actionable thread remains. Automated review summaries may be
clean while inline comments were added separately.
