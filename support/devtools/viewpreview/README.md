# viewpreview (issue #87)

Dev-only JS plugin that renders **one arbitrary `.view` file in isolation**
with fixture data from a JSON file, so an agent or human can iterate on a
single GLW component without navigating the full app. This is the
rendering backend for the mockup-to-view design loop (`mdev preview`).

Lives under `support/devtools/`; never packaged/shipped (nothing outside
`support/` references it, and packaging only globs `src/`, `res/`, `glwskins/`
etc, never `support/`).

## Quick start

```
mdev preview glwskins/flat/pages/about.view
mdev preview support/devtools/viewpreview/views/demo-list.view \
    --fixture support/devtools/viewpreview/fixtures/directory-nodes.json --shot
```

`mdev preview` auto-starts a `preview`-named instance with
`-p support/devtools/viewpreview` if none is running yet (same
process-guard rules as `mdev run`: it never touches a movian process it
doesn't own). Run `mdev preview --help` for the full flag list.

## How it works (mechanism, no C/skin changes)

A plugin can point a page directly at a view file: setting
`page.type = "raw"` plus `page.metadata.glwview = <path>` makes the flat
skin's `glwskins/flat/pages/raw.view` render *only* that view -- no
navbar, no sidebar, no directory chrome:

```
loader({
  noInitialTransform: true;
  effect: blend;
  time: 0.2;
  source: $self.model.metadata.glwview;
});
```

(`glwskins/flat/pages/raw.view:1-6`)

`viewpreview.js` registers one route, `viewpreview:show:<config>`, that:
1. decodes the route config,
2. optionally loads+applies a fixture JSON onto the page model,
3. sets `page.metadata.glwview` to the target view and `page.type = "raw"`.

## Route encoding

`viewpreview:show:<base64(JSON)>`, where the JSON is:

```json
{ "view": "<absolute path to .view>",
  "fixture": "<absolute path to fixture .json>",
  "type": "raw" }
```

`fixture` and `type` are optional (`type` defaults to `"raw"`).
`<base64(JSON)>` is standard (non-URL-safe) Base64 of the UTF-8-encoded
JSON string, decoded plugin-side with Duktape's built-in
`Duktape.dec('base64', ...)`. Base64 was chosen over raw query args
because the config contains `:` and `/` (paths, `viewpreview:` scheme
strings inside fixture URLs) that would otherwise need a second,
easy-to-get-wrong layer of percent-encoding on top of `mdev`'s own
`/api/open?url=...` encoding. `mdev preview` builds this route for you;
you never need to hand-encode it.

## Fixture JSON schema v1

```json
{
  "metadata": { "title": "...", "subtitle": "...", "...": "..." },
  "args": { "...": "..." },
  "nodes": [
    { "type": "item", "url": "...", "metadata": { "title": "", "subtitle": "", "icon": "" }, "...": "..." }
  ]
}
```

- All three top-level keys are optional; an empty `{}` fixture is valid.
- `metadata` is copied onto `page.metadata.*` (so `$self.model.metadata.*`
  in the target view).
- `args` is copied onto `page.model.args` verbatim (`$self.args.*`).
- Each `nodes[]` entry becomes one `page.appendItem(url, type, metadata)`
  call, i.e. one child of `$self.model.nodes`. `type`/`url`/`metadata` are
  the recognized fields; any other key on a node is copied onto that
  node's own prop root verbatim.
- Any fixture top-level key besides `metadata`/`args`/`nodes` is copied
  onto `page.model.*` verbatim (e.g. a fixture could set `contents` or
  `safeui` this way).
- Unknown/extra keys anywhere are **never rejected** -- they just become
  props, per "everything optional, unknown keys pass through verbatim".

See `fixtures/minimal.json` (metadata only) and
`fixtures/directory-nodes.json` (12 items with title/icon) for worked
examples, and `views/demo-list.view` for a target view that consumes
both.

## How the target view sees the page model (spike finding, Q1)

`raw.view`'s `loader` widget sets neither `args:` nor an explicit `self:`
target, so per `src/ui/glw/glw_view_loader.c:200`
(`vl->scope = glw_scope_retain(w->glw_scope);`), the loaded view
**inherits the exact same scope** as `raw.view` -- no rebinding happens
(that only occurs via the `GLW_ATTRIB_ARGS` / `GLW_ATTRIB_PROP_SELF`
handlers at `glw_view_loader.c:358-370`, neither of which fires here).
Since pages are instantiated via
`cloner($nav.pages, container_z, { ... source: "skin://pages/" + $self.model.type + ".view"; })`
(`glwskins/flat/universe.view:70-75`), `$self` inside `raw.view` (and
therefore inside the target view too) is the **page prop node itself**.

So target views must bind `$self.model.metadata.*` / `$self.model.nodes`
/ `$self.args.*` directly -- not `$parent.*`, not `$args.*` alone, and
**not** `$page.*` (a convention seen in some third-party plugin views,
e.g. `~/movian-plugin-tmdb/views/posters.view`, but there is no `page`
scope root in this core -- see `src/ui/glw/glw_scope.c:54-61` and
`src/ui/glw/glw.h:290-297`: the only roots are
`self/parent/view/args/clone/core/parentview` plus the explicitly
registered `nav` root). `views/demo-list.view` and
`views/error.view` in this directory both use `$self.model.*` for
exactly this reason.

This is also demonstrated by an existing, unmodified in-repo example:
`plugin_examples/listx_cloner/listx_cloner.js` sets
`page.type = 'raw'; page.metadata.glwview = Plugin.path + 'listx_cloner.view';`,
and `plugin_examples/listx_cloner/listx_cloner.view` renders with
`cloner($self.model.nodes, container_y, { ... caption: $self.metadata.title; ... })`.

## Path scheme findings (spike, Q3)

Plain **absolute filesystem paths work directly** in `page.metadata.glwview`
with no scheme prefix, symlink, or wrapper needed. `src/fileaccess/fileaccess.c:99-113`
(`fa_resolve_proto`): when a URL has no `scheme://` prefix, "assume a
plain file" -- a string starting with `/` resolves via the native
filesystem backend directly. This was confirmed empirically during the
spike (see issue #87 spike comment): a throwaway plugin set `glwview` to
the absolute path of `plugin_examples/listx_cloner/listx_cloner.view`
-- a view outside the plugin's own directory -- and it rendered
correctly. `mdev preview` always resolves the view/fixture paths you
give it to absolute paths before building the route, so any of these
work: a path inside this plugin dir, a path elsewhere in the repo (e.g.
`glwskins/flat/pages/about.view`), or a path under a sibling checkout
(e.g. `~/movian-plugin-tmdb/views/posters.view`).

## Error surfacing

A broken preview is never a silent black screen:

- **Missing view file / missing fixture file / malformed fixture
  JSON / any exception while applying the fixture**: `viewpreview.js`
  catches it, keeps `page.type = "raw"`, and points `glwview` at its own
  `views/error.view` instead, with the message in
  `page.metadata.viewpreviewError` (visible on screen) --
  and logs `viewpreview: ERROR: <message>` via `console.log` (visible in
  `mdev log`).
- **Broken target view (a GLW parse/syntax error in the `.view` file
  itself)**: this is not something the plugin can catch in JS -- GLW
  reports it as `GLW [ERROR]: Error <file>:<line>: <message>` in the log
  (`src/ui/glw/glw_view_support.c`). `mdev preview` greps the log delta
  for both this pattern and the `viewpreview: ERROR:` pattern above and
  exits 1 if either is present.

## `mdev preview`

```
mdev preview <view-path> [--fixture <json>] [--name preview] [--shot]
```

- Ensures a `--name` instance (default `preview`) is running with
  `-p support/devtools/viewpreview`, starting one if needed.
- Resolves `<view-path>` and `--fixture` to absolute paths (relative to
  the repo root, like `mdev watch --dir`), builds the
  `viewpreview:show:<config>` route, opens it, and waits for page-ready.
- Exits 1 if the log (from just before the route was opened) contains a
  `viewpreview: ERROR:` line or a GLW view-parse error line; the offending
  line(s) are printed.
- `--shot` takes a screenshot (via `mdev shot`'s machinery) after a clean
  render.

### Known limitation

If an instance named `--name` (default `preview`) is already running but
was started *without* `-p support/devtools/viewpreview` (e.g. hand-started
via `mdev run --name preview`), `mdev preview` reuses it as-is and does not
verify/re-add the plugin -- the `viewpreview:show:...` route will then
fail to resolve. Use a fresh/dedicated `--name` for `mdev preview`, or
`mdev stop --name preview` first, if in doubt.

### Blank render on a clean parse

A raw preview page gives the target view the full screen with no
constraining parent. A root-level `container_y` whose children rely on
default alignment/sizing can render completely blank while parsing
cleanly (exit 0). If a preview comes up empty, set `align` explicitly on
the root container (`align: top;` / `align: center;`) before suspecting
the fixture bindings. See the gotcha note in
`.claude/skills/movian-view-design/references/glw-patterns.md`
(empirically verified during #88 verification).
