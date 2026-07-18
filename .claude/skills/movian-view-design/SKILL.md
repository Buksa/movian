---
name: movian-view-design
description: Iterating on GLW `.view` files — live reload, isolated single-view preview, wirebox/prop-sub tracing, focus/event tracing, and skin overrides. Use when asked to edit a `.view` file, debug GLW layout/focus/rendering, iterate on a skin, or preview a view outside the full app.
---

# Movian View Design

The durable procedural knowledge for the `.view` edit loop, plus the GLW
DSL reference set (issue #88) under `references/`:

- `references/glw-view-language.md` — the `.view` language: lexical
  elements, all 5 assignment operators (`=`, `?=`, `:=`, `<-`, `_=_`),
  the complete expression-function table (`funcvec[]`, 90 entries), prop
  scopes (`$self`/`$args`/`$parent`/`$view`/`$clone`/`$ui`/`$core`/`$nav`),
  event maps, and the preprocessor (`#define`/`#import`/`#include`).
  **Read this before writing a `.view` from scratch.**
- `references/glw-widget-catalog.md` — all 51 registered widget classes
  (name, flags, layout behavior, class-specific attributes) and the
  116-entry global attribute table.
- `references/glw-patterns.md` — worked recipes from the skin corpus:
  page skeleton + `mdev preview` fixture pair, list/grid pages, focus
  highlight, popups, settings rows, plugin data binding, debug moves.

`mdev viewdoc --check` diffs those reference docs against the C source
tables (`attribtab[]` / `funcvec[]`) and exits nonzero on drift — run it
after touching either side. A richer `viewpreview` fixture-authoring
guide is still a tracked follow-up; this file itself covers the
edit/reload loop and the core mechanisms it depends on, each with its
source anchor.

For editor diagnostics, hover, and `#include` definitions, see
`docs/Guides/language-tooling.md`; from the repository root, build
`movian-analyze` and run `./support/devtools/mdev lsp doctor` before
configuring an editor.

## The edit/reload loop

For a view already reachable in a running app (e.g. anything under
`glwskins/flat/`):

```
mdev run                              # once, if not already running
mdev reload [--shot]                  # after each edit
```

or, to auto-reload on every save:

```
mdev watch [--dir glwskins/flat] [--shot]
```

`mdev reload`/`mdev watch` send the `ReloadUI` action
(`/api/input/action/ReloadUI`) and grep the log delta for GLW view-parse
errors. `ReloadUI` is handled at `src/ui/glw/glw.c:2522`:

```c
if(event_is_action(e, ACTION_RELOAD_UI)) {
  glw_load_universe(gr);
  return;
```

`glw_load_universe()` (`src/ui/glw/glw.c:404-423`) is a **full view-cache
flush**: it calls `glw_unload_universe()` (which calls
`glw_view_cache_flush()` and destroys the whole widget tree), then rebuilds
`universe.view` from scratch with a fresh scope. Every `.view` file under the
active skin gets re-parsed and re-evaluated on a reload — not just the one
you edited. This is what makes `mdev reload`/`watch` a full-fidelity replay
of a fresh launch's view-loading, but it also means an unrelated `.view`
file with a pre-existing error will surface on every reload, not just when
you touch it.

### Known blind spot (tracked as #92)

`mdev reload`/`mdev preview` only grep for GLW **parser/preprocessor**
errors (`GLW [ERROR]: Error <file>:<line>: <message>`, emitted by
`glw_view_seterr()` in `src/ui/glw/glw_view_support.c`) and, for `preview`,
the plugin's own `viewpreview: ERROR:` line. A `.view` **lexer** error (e.g.
an unterminated string literal) or a failure to even open the target file
does not match either pattern — these commands will exit 0 and report a
clean reload even though the view did not load. Until #92 lands:

- do not treat a green `mdev reload`/`preview` as full proof for a change
  that could produce a lexer-level or file-not-found failure (a stray quote,
  a typo'd path);
- follow up with `mdev log --tail 40` or a screenshot when the change is
  nontrivial, and look for any `GLW` line near the reload, not just the two
  patterns the command itself checks.

## Dev-plugin JS reload (`mdev reload --js` / `mdev watch --js`, issue #93)

`mdev reload`/`mdev watch` as described above are **views-only** — they never
reload a dev plugin's JS, only `.view` files. If you're iterating on a `-p`
dev plugin's `.js` (not its `.view` files), use `--js` instead: `mdev reload
--js` / `mdev watch --js [--dir <plugin-dir>]` send the `ReloadData` action,
which force-reloads every `-p` plugin's ECMAScript AND reloads the current
page as a side effect (page state resets — an open page backed by the
reloaded plugin does not survive, it's torn down and reopened at the same
URL). Full mechanism, the exit-code contract, and a core quirk in how a JS
compile failure is reported are documented in the `movian-run` skill's
"Reload: views vs. dev-plugin JS" section — read that before relying on
`--js`'s exit code as proof of a working reload.

## Isolated single-view preview (`mdev preview`, issue #87)

For iterating on one `.view` file without navigating the full app:

```
mdev preview <path/to/file.view> [--fixture <fixture.json>] [--shot]
```

This renders the view through the `support/devtools/viewpreview/` dev
plugin — a `page.type = "raw"` page whose `metadata.glwview` points straight
at your file, so no navbar/sidebar/directory chrome loads around it. Full
mechanism, route encoding, and the fixture JSON schema (v1: `metadata` /
`args` / `nodes`, all optional) are documented in
`support/devtools/viewpreview/README.md` — read that before authoring a
fixture.

Key facts worth restating here:

- `mdev preview`'s auto-started `preview` instance always launches with
  `--bypass-ecmascript-acl` (a pre-existing core flag,
  `support/devtools/mdevlib/harness.py: ensure_running()`, consumed at
  `src/ecmascript/es_fs.c:91` in `filename_is_allowed()`). Without it, the
  ECMAScript file ACL restricts a plugin's own `fs`/`native/fs` reads to its
  own directory (`ec_storage`/`ec_path` prefix match); `viewpreview.js` needs
  to read fixture JSON and view files anywhere in the repo or a sibling
  checkout, so this flag is required for the preview workflow to work at
  all, not an optional convenience.
- Views can be referenced by a plain absolute filesystem path in
  `page.metadata.glwview` — no scheme prefix or symlink needed
  (`src/fileaccess/fileaccess.c:99-113`, `fa_resolve_proto`: no
  `scheme://` prefix means "assume a plain file"). `mdev preview` always
  resolves the path you give it to an absolute path first.
- Target views must bind through `$self.model.*` / `$self.args.*` — not
  `$page.*`, which some third-party plugin views use by convention but which
  has no scope root in this core (`src/ui/glw/glw_scope.c:54-61`,
  `src/ui/glw/glw.h:290-297`). See the viewpreview README's "How the target
  view sees the page model" section for the full explanation.
- **Known limitation**: reusing an already-running instance under the
  `preview` (or your custom `--name`) that was *not* started with
  `-p support/devtools/viewpreview` will fail to resolve the
  `viewpreview:show:...` route. Use a fresh/dedicated name, or
  `mdev stop --name preview` first, if in doubt.

## The mockup → view loop (issue #89, proven on the pilot page)

Turning a reference image (screenshot/mockup pasted into the session) into
a working `.view`. Proven end-to-end on
`support/devtools/viewpreview/views/pilot-series.view` +
`fixtures/pilot-series.json` (a series-episodes page: navbar, poster+info
column, focused season card, episode list) — 7 rounds to convergence.
Follow this exact sequence:

1. **Fixture first.** Extract every piece of text/data visible in the
   reference into a viewpreview schema-v1 fixture (`metadata` for
   page-level fields, extra top-level keys land on `$self.model.*`,
   `nodes[]` for list rows — per-node extra keys land on the node root,
   e.g. `$self.episode` inside a cloner). Generate placeholder PNGs for
   artwork (never commit third-party images); relative `source:` paths
   resolve against the *view file's directory* (`glw_resolve_path` →
   `fa_absolute_path`, `src/ui/glw/glw_view_attrib.c:36-59`), so
   `"../fixtures/" + $self.metadata.icon` works from `views/`.
2. **Write convergence criteria before the first render**: element
   presence/nesting, column proportions, alignment, focus state = must
   match; fonts/AA, exact colors, placeholder art, icon glyphs = accepted
   deltas. Without this list you will chase pixels forever.
3. **Static gate before every render**:
   `./build.debug/movian-analyze --check <view>` (instant; catches parse
   errors without touching the instance). Macro note: GLW `#define`
   bodies must be `{ ... }` blocks — expression-shaped macros don't parse.
4. **Render + screenshot**:
   `mdev preview <view> --fixture <fixture.json> --shot`, then iterate
   with `mdev reload --name preview` + `mdev shot --name preview` (reload
   keeps the page; a fresh `--shot` via preview re-opens it).
   **Health-check on first launch**: if the page never opens or
   `/api/screenshot/raw` 504s, the instance is wedged — `mdev stop
   --name preview` and relaunch (see CONSTRAINTS.md in
   movian-plugin-testing).
5. **Compare multimodally** (read the shot next to the reference), fix
   the worst structural delta first, repeat. Layout gotchas that cost
   rounds on the pilot:
   - In a `container_x`, a child column with its own content constraint
     ignores `weight:` — set `filterConstraintX: true;` on the column to
     make weights govern the split.
   - Right-aligning a trailing label in a row: interpose
     `widget(dummy, { });` and make the row's parent column
     `filterConstraintX: true;`.
   - Progress bars: `container_z` of a dim full-width quad + a
     `container_x` of `quad(weight: $self.progress)` /
     `dummy(weight: 1 - $self.progress)`.
   - `backdrop` border-scaling renders only the vertical border bands on
     this stand (top/bottom bands never draw, with skin or custom
     9-slice PNGs alike) — build outline frames from 4 quads instead
     (see `FRAME()` in the pilot view).
6. **Focus states need the #114 fix** (`/api/input/action` events carry
   `EVENT_KEYPRESS` so keyboard mode engages): the *first* arrow sent to
   a fresh instance is consumed by the mouse→keyboard mode switch, and
   initial focus lands by weight at page-load — drive focus to the
   target widget with a few `/api/input/action/up|down` calls before the
   comparison shot, and verify it visually (`isFocused()`-driven
   highlights).
7. **Before the PR**: `mdev viewdoc --check`, `make BUILD=debug
   movian-analyze-corpus`, and a final full-page shot pass against the
   convergence criteria, per-criterion.

## Widget-local debug tracing (`debug: true`)

Add the `.view` attribute `debug: true` to one widget to get layout-box,
texture-size, text-layout, and prop-subscription tracing scoped to that
widget only — much lower-noise than `--debug-glw` for a single-widget
investigation:

```view
widget(container_x, {
  debug: true;
  ...
});
```

Anchor: `src/ui/glw/glw_view_attrib.c:1382` —
`{"debug", mod_flag, GLW2_DEBUG, mod_flags2}`. `GLW2_DEBUG` also enables
`PROP_SUB_DEBUG` in `src/ui/glw/glw_view_eval.c`, and widget-local debug
prints live in `glw_image.c`, `glw_text_bitmap.c`, `glw_container.c`. Remove
the attribute before shipping unless the task explicitly wants it kept.

## `--debug-glw`: focus/event tracing and its limits

`--debug-glw` (not yet an `mdev run` flag — use a fallback direct launch)
turns on global `GLW_TRACE()` event/focus routing logs for the whole UI, not
one widget. See `movian-plugin-testing/references/debug-flags.md` for
runtime-verified log examples and the full explanation of its two
documented limits:

- it does **not** draw layout boxes on every widget (use `debug: true` on a
  specific widget for that instead);
- it does **not** force the visual list cursor to appear — the flat skin's
  row highlight needs real GLW keyboard mode, which
  `/api/input/action/<Action>` does not enable (those events are not
  `EVENT_KEYPRESS`). A screenshot that must show the cursor needs a real
  X11 keypress via `support/devtools/mdevlib/x11_keypress.py`.

## `--skin` and the temp-skin-copy workflow

`--skin <dir>` (`mdev run --skin <dir>`, core flag parsed at
`src/main.c:733-735`: `mystrset(&gconf.skin, argv[1])`) points GLW at an
alternate skin directory instead of the default `glwskins/flat`. Use it to
iterate on a copy of the skin without touching the tracked tree:

```
cp -r glwskins/flat /tmp/skin-experiment
# edit files under /tmp/skin-experiment
mdev run --name skin-test --skin /tmp/skin-experiment
mdev watch --name skin-test --dir /tmp/skin-experiment --shot
```

Remember `mdev watch --dir` (like `mdev preview`'s path resolution) resolves
a relative path against the repo root, not the current shell's cwd — pass an
absolute path when watching a directory outside the repo.

## Corpus caveat: this fork's builtin gap

Third-party plugin views written against a different Movian fork can use
JS builtins this fork does not implement. Observed example: a sibling
checkout's `~/movian-plugin-tmdb/views/posters.view` calls `isReady()`,
which is not implemented here and renders as literal `Unknown function` text
in the UI (not a load failure — the view still loads and reload/preview
report clean). `text.view` in the same plugin works fine. Treat any view
sourced from outside this repo as a candidate for this kind of gap; a
"reload OK" plus a visibly broken screenshot together mean "unsupported
builtin", not "GLW error".

## Follow-ups tracked elsewhere

- ~~A GLW DSL reference (attributes, widgets, event model)~~ — landed
  (issue #88): see `references/glw-view-language.md`,
  `references/glw-widget-catalog.md`, `references/glw-patterns.md` and
  the `mdev viewdoc --check` drift detector, all described at the top of
  this file.
- A richer `viewpreview` fixture-authoring guide beyond the schema already
  in `support/devtools/viewpreview/README.md`.
- Issue #92: teach `mdev reload`/`preview` to also catch lexer errors and
  file-open failures, closing the blind spot documented above.
