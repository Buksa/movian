# GLW view patterns

Worked recipes mined from this repo's own skin (`glwskins/flat/**/*.view`,
98 files) and, where marked, third-party plugin checkouts. Every snippet
below is copied **verbatim** (or trivially trimmed) from a real file with
a `path:line` citation so it's checkable. Corpus files illustrate idiom
only — the underlying mechanism for each pattern is in `glw-view-language.md`
(language mechanics) / `glw-widget-catalog.md` (per-class attributes),
which this file cross-references rather than re-deriving.

## Page skeleton + fixture binding (renders under `mdev preview`)

A target view bound to `$self.model.*` plus a fixture JSON that
`mdev preview` can render it with. Both files **already exist in this
repo** (built and render-verified for issue #87's own DoD), so this pair
is known-good, not new content:

```view
// support/devtools/viewpreview/views/demo-list.view (lines 9-51)
widget(container_y, {
  padding: 1em;

  widget(label, {
    caption: $self.model.metadata.title ?? "(untitled)";
    size: 1.6em;
  });

  widget(list_y, {
    spacing: 0.15em;

    cloner($self.model.nodes, container_x, {
      spacing: 0.5em;
      height: 1.6em;

      widget(icon, {
        hidden: !($self.metadata.icon);
        size: 1.2em;
        source: "skin://" + $self.metadata.icon;
      });

      widget(label, {
        caption: $self.metadata.title;
        size: 1.1em;
      });
    });
  });
});
```

```json
// support/devtools/viewpreview/fixtures/directory-nodes.json (trimmed to 2 nodes)
{
  "metadata": { "title": "Directory nodes fixture" },
  "nodes": [
    { "type": "item", "url": "viewpreview:demo:1",
      "metadata": { "title": "Alpha", "icon": "icons/ic_folder_48px.svg" } },
    { "type": "item", "url": "viewpreview:demo:2",
      "metadata": { "title": "Bravo", "icon": "icons/ic_folder_48px.svg" } }
  ]
}
```

```
mdev preview support/devtools/viewpreview/views/demo-list.view \
    --fixture support/devtools/viewpreview/fixtures/directory-nodes.json --shot
```

Why `$self.model.*` and not `$page.*`/`$args.*`: the `loader` that
`viewpreview.js` renders through inherits its scope unchanged (no `args:`/
`self:` rebinding), and pages are cloned with `$self` bound to the page
prop itself — see `glw-view-language.md` §2's scope-root table and
`support/devtools/viewpreview/README.md` "How the target view sees the
page model" (anchored to `glw_view_loader.c:200` and
`glwskins/flat/universe.view:70-75`). Each `nodes[]` fixture entry becomes
one child of `$self.model.nodes`, i.e. one `$self` inside the `cloner()`
block.

## Full page skeleton (list + detail split, from the shipped skin)

`glwskins/flat/pages/list.view:1-76` is the real, in-use page for
directory listings. Trimmed to the skeleton shape:

```view
#import "skin://theme.view"
#import "skin://styles/style_list.view"

widget(container_z, {
  widget(container_x, {
    widget(container_z, {
      filterConstraintX: true;
      widget(list_y, {
        id: "scrollable";
        navWrap: true;
        chaseFocus: true;
        cloner($self.model.nodes, loader, {
          selectOnFocus: true;
          time: 0.1;
          effect: blend;
          alt: "skin://items/list/default.view";
          source: "skin://items/list/" + $self.type + ".view";
        });
      });
      ScrollBar("scrollable", 3em, $ui.universeBottomHeight);
    });
  });
  widget(container_y, {
    align: top;
    PageHeader($self.model.metadata.title);
  });
});
```

(`glwskins/flat/pages/list.view:1-44,72-75`; `ScrollBar`/`PageHeader` are
`#define` macros from `glwskins/flat/theme.view:94,154`). Note the
**loader + `alt` + per-item `.view` dispatch** idiom: each clone loads
`"skin://items/list/" + $self.type + ".view"`, falling back to
`items/list/default.view` when the typed file doesn't exist (`alt:`, see
`glw-widget-catalog.md`'s `loader` row) — one list handles many
differently-shaped item types this way (`action`, `bool`, `video`,
`settings`, ...; `glwskins/flat/items/list/` has 20+ such files).

## Poster / tile grid

`glwskins/flat/pages/grid.view:1-44` (full file, minus the trailing
`ScrollBar`/header identical to the list page):

```view
#import "skin://theme.view"
#import "skin://styles/style_grid.view"

widget(container_z, {
  widget(array, {
    id: "scrollable";
    margin: [1em, 0, 1em, 0];
    Xspacing: 0.5em;
    Yspacing: 0.5em;
    childTilesX: select($ui.aspect > 1, 5, 2);
    childTilesY: 4;
    chaseFocus: true;
    navWrap: true;

    cloner($self.model.nodes, loader, {
      time: 0.1;
      effect: blend;
      alt: "skin://items/rect/default.view";
      source: "skin://items/rect/" + $self.type + ".view";
    });
  });
});
```

`array`'s `childTilesX`/`childTilesY` (see `glw-widget-catalog.md`'s
`array` row, `glw_array.c:529-553`) fix the visible grid shape;
`childTilesX: select($ui.aspect > 1, 5, 2)` is the "wide vs. tall screen"
idiom used throughout the flat skin.

## List with focus highlight (`isNavFocused()` + keyboard-mode caveat)

`glwskins/flat/theme.view:53-60`, the shared highlight macro used by every
list row:

```view
#define ListItemHighlight() {
  widget(quad, {
    fhpSpill: true;
    additive: true;
    alpha: 0.1 * isHovered() + 0.2 * isNavFocused();
  });
}
```

Used in a real row, `glwskins/flat/items/list/settings.view:1-8`:

```view
#import "skin://theme.view"

widget(container_z, {
  height: 2em;
  ListItemBevel();
  ListItemHighlight();
  focusable: true;
  onEvent(activate, navOpen($self.url));
```

**Keyboard-mode caveat** (`glw-view-language.md`'s `isFocused`/
`isNavFocused` entry, anchored `glw_view_eval.c:5008` and
`glw.c:2500-2501`): `isNavFocused()` only reads true when the widget has
real GLW keyboard focus **and** the UI is in keyboard-navigation mode,
which only an actual `EVENT_KEYPRESS`-flagged input event turns on.
Driving the UI via `/api/input/action/<Action>` (what `mdev` does) will
not visibly highlight the row — those are synthetic action deliveries,
not keypresses. A screenshot that must show the highlight needs a real
X11 keypress (`support/devtools/mdevlib/x11_keypress.py`), per this
skill's own `--debug-glw` section.

## Loader / include composition

Two distinct mechanisms, easy to conflate — see `glw-view-language.md` §5
for the source-level distinction:

- **`#import` / `#include`** (preprocessor, parse-time token splice):
  `#import "skin://theme.view"` at the top of nearly every skin file
  (e.g. `glwskins/flat/pages/list.view:1-2`) pulls in shared macros —
  deduplicated, so ten files importing `theme.view` splice it once per
  top-level parse (`glw_view_preproc.c:316-346`).
- **`widget(loader, { source: ...; })`** (runtime, re-evaluated on
  `source` change): loads a *separate* `.view` file as a live, swappable
  child — this is how per-item-type dispatch (list/grid patterns above)
  and whole-page swapping (`glwskins/flat/universe.view:45-56`, the
  background and loading-screen loaders) work. Unlike `#import`, it
  re-runs whenever `source`'s value changes, and the loaded file gets its
  own scope (inherited from the loader unless `args:`/`self:` rebind it —
  `glw-widget-catalog.md`'s `loader` row).

## Popup

`glwskins/flat/popups/auth.view:1-20` (a real, working modal popup):

```view
#import "common.view"

onEvent(cancel, deliverEvent($self.eventSink));
onEvent(back,   deliverEvent($self.eventSink, "cancel"));

widget(popup, {
  aspect: 2;
  clickable: true;
  onEvent(click, deliverEvent($self.eventSink, "Cancel"), true, true, true);

  widget(container_z, {
    clickable: true;
    filterConstraintX: true;
    PopupBackdrop();
    widget(container_y, {
      padding: [$ui.xmargin, 1em];
      spacing: 2em;
```

Popups are instantiated the same way pages are — a `cloner()` over a
directory property at the top level of the universe:

```view
cloner($core.popups, loader, {
  source: "popups/" + $self.type + ".view";
});
```

(`glwskins/flat/universe.view:80-82`). `$self.eventSink` (used above) is
how a popup reports its result (Cancel/OK/...) back to whatever created
it — `deliverEvent($self.eventSink, ...)`.

## Settings row (`translate()` idiom + table alignment)

`glwskins/flat/items/list/settings.view:1-40` (trimmed):

```view
#import "skin://theme.view"

widget(container_z, {
  height: 2em;
  ListItemBevel();
  ListItemHighlight();
  focusable: true;
  onEvent(activate, navOpen($self.url));

  widget(container_x, {
    style: "ListItem";
    widget(icon, {
      source: $self.metadata.icon ??
        translate($self.subtype,
                  "skin://icons/ic_settings_48px.svg",
          "about",    "skin://icons/ic_help_48px.svg",
          "sound",    "skin://icons/ic_speaker_48px.svg",
          "video",    "skin://icons/ic_videocam_48px.svg",
          "network",  "skin://icons/ic_settings_ethernet_48px.svg"
                 );
      style: "ListItemIcon";
    });
    widget(label, {
      caption: $self.metadata.title;
      style: ["ListItemLabel", "ListItemLabelContainer"];
    });
  });
});
```

This is the real usage of `translate()` (see `glw-view-language.md`'s
function table): use `$self.metadata.icon` if set (`??`), else pick a
default icon by `$self.subtype` string, else `translate()`'s own
`default` argument. For a row that needs column-aligned siblings across
rows, `table` + `container_x { tableMode: true; }`
(`glwskins/flat/popups/auth.view:22-27`) aligns per-row children into
columns (`glw-widget-catalog.md`'s `table`/`container_x` rows).

## Plugin-view data binding: `$self.model.nodes` and `page.appendItem`

The list/grid page patterns above both do `cloner($self.model.nodes, ...)`.
On the JS/plugin side, each entry of that directory property is created
by one `page.appendItem(url, type, metadata)` call — exactly what the
`viewpreview` fixture's `nodes[]` array does on your behalf (`support/
devtools/viewpreview/README.md` "Fixture JSON schema v1": *"Each `nodes[]`
entry becomes one `page.appendItem(url, type, metadata)` call, i.e. one
child of `$self.model.nodes`"*). A minimal plugin route body:

```js
page.type = "directory";
page.appendItem("myplugin:item:1", "item", { title: "Alpha" });
page.appendItem("myplugin:item:2", "item", { title: "Bravo" });
```

pairs with a view doing `cloner($self.model.nodes, container_x, { ...
caption: $self.metadata.title; ... })` — each clone's `$self` is one
appended item's own prop node, exactly like the fixture pattern above but
populated live by the plugin instead of from a JSON file. An unmodified
in-repo example of the same wiring:
`plugin_examples/listx_cloner/listx_cloner.js` +
`plugin_examples/listx_cloner/listx_cloner.view` (cited in the
viewpreview README).

## Event maps (`onEvent`, action names)

`onEvent(actionOrExpr, targetExpr, enabled?, final?, early?)` — see
`glw-view-language.md`'s function-table entry for full semantics
(`glw_view_eval.c:3361-3400+`). The first argument is usually a bare
action-name word, matched case-insensitively (`action_str2code()` →
`str2val` → `strcasecmp`, `src/event.c:210-214`, `src/misc/strtab.h:39`)
against `src/event.c`'s 71-entry `actionnames[]` (`src/event.c:109-200`).
Common ones:
`Up, Down, Left, Right, Activate, Click, Enter, Cancel, Back, Forward,
Select, Menu, ItemMenu, Copy, Paste, PageUp, PageDown, ReloadUI,
ReloadData, Sysinfo, MediaStats, LogWindow` — read `src/event.c:110-198`
for the full list. Corpus idiom (`glwskins/flat/universe.view:10-25`):

```view
onEvent(sysinfo, {
  toggle($ui.sysinfo);
});

onEvent(back, {
  $ui.logwindow = false;
}, $ui.logwindow);
```

The third argument (`$ui.logwindow` above) is the `enabled` operand — the
mapping only fires while it's truthy, so this "back closes the log
window" map is inert unless the log window is actually open.

## Debug techniques

- **`debug: true`** on one widget — layout-box/texture-size/prop-sub
  tracing scoped to that widget (`glw_view_attrib.c:1382`, `GLW2_DEBUG`);
  see `SKILL.md`'s "Widget-local debug tracing" section (covers this in
  depth — not re-derived here).
- **`_=_` debug assignment** — same value semantics as `=`, differing
  only for a direct prop-to-prop link, which it creates with `debug=1`
  for subscription tracing (`glw-view-language.md` §3's `_=_` row,
  `glw_view_eval.c:970-971`). No hits in `glwskins/flat/**/*.view` —
  reach for `debug: true` on a widget first; it's the idiom actually
  used in this tree.
- **`trace(prefix, value)`** function — `TRACE()`s `value` prefixed by
  `prefix` (`glw-view-language.md`'s function table,
  `glw_view_eval.c:5432-5460`); an inline probe for watching one
  expression's value in `mdev log` without attaching a debugger.

## Coverage & gaps

Documented: page skeleton (viewpreview-fixture and shipped-list-page
forms), poster/tile grid, focus-highlight list row with the
keyboard-mode caveat, loader-vs-`#import` composition, popup, settings
row, plugin data binding, event maps, and debug techniques — 9 recipes,
each with a real corpus citation.

Intentionally **not** covered (out of scope for a recipe doc, or owned
elsewhere):

- **Style-cascade recipes** (`style()`/`newstyle()` beyond the examples
  above) — `glwskins/flat/styles/*.view` are a large, mostly
  self-explanatory corpus; not individually walked through.
- **OSK (on-screen keyboard) composition** (`glwskins/flat/osk.view`) —
  large single-purpose file, not generalizable into a recipe.
- **Video-page layout** (`glwskins/flat/pages/video.view`) — the `video`
  class's attribute surface is in `glw-widget-catalog.md`; the
  playback-control composition is out of scope per issue #88's
  "video/media widget internals" boundary.
- **Screensaver / `detachable` recipes** — feature-specific one-offs in
  this skin, not generalized.
- Full plugin-authoring guidance beyond the one minimal `page.appendItem`
  snippet — that's the `viewpreview` README's and the
  `movian-plugin-testing` skill's territory.
