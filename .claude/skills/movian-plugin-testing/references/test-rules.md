# Movian Plugin Test Rules

## Plan Before Running

State assumptions and success criteria before touching code or launching
Movian. Keep the criteria narrow:

- target route or user flow;
- expected page title/type/loading state;
- expected metadata fields;
- expected node count or important row types;
- expected visual/artwork state;
- disallowed log patterns.

Use the smallest flow that can prove the change.

## Launch

Use `mdev run [start_url] -p <plugin-dir>` (see the `movian-run` skill) for
ordinary plugin smoke runs. It launches with `-d --disable-upgrades
--persistent ... --cache ...` from the repo root and parses the real HTTP
port out of the log for you.

`-d` is maximum trace output in this tree: stdout includes normal debug lines
plus navigation/tuning trace (`|N|` and `|T|`). Add `--libav-log` only for
playback, HLS, probe, or FFmpeg-specific failures — it adds noise without
improving the pass/fail signal for ordinary route/`.view` smokes.

Use `--debug-glw` (raw `movian` flag; not yet wired into `mdev run` — add it
via a fallback launch if needed) for focus/action/event-map diagnosis. For
layout boxes or widget-local texture/text diagnostics, temporarily add `.view`
attribute `debug: true` to the specific widget instead — see
`debug-flags.md` and `movian-view-design`.

Do not use `--showtime-shell-fd` in normal plugin tests. It is a launcher/
appliance hook for sending SSH server on/off commands through an inherited
file descriptor, not a plugin runtime diagnostic.

After Movian CLI/trace/parser changes, verify `./build.debug/movian --help`
and run a short flag smoke. At minimum prove `-d`, `-s`, `-j`, `--persistent`,
`--cache`, and `-p` are consumed as options and do not appear as
`navigator |N| Opening -<flag>`.

## Smoke Failure Escalation

Do not keep repeating the same failing smoke run. If Movian exits nonzero,
segfaults, aborts, hangs before the HTTP server is ready, or the log points to
a native crash without a useful stack:

- save the exact command, isolated profile/cache path (`mdev` already gives
  you this under `/tmp/mdev/<name>/`), log tail, screenshot if available, and
  exit status;
- build/use a separate `build.debug-gdb` profile from `debug-flags.md`;
- rerun under gdb and capture `bt full`, `info threads`, and `thread apply all
  bt full`;
- use `build.asan` only when the evidence suggests memory corruption,
  use-after-free, or heap overwrite.

If the smoke fails with a JavaScript `TypeError`, bad route, missing
metadata, or an expected GLW image error, stay in the normal `build.debug`
loop and fix the plugin or `.view` first.

## Standard Pass Criteria

A route smoke passes when:

- `/api/open` reaches the expected plugin route (`mdev open` verifies this);
- `global/navigators/current/currentpage/model/loading` is `0` **or**
  void/absent — some routes (e.g. `page:settings`) never create this prop;
  see `movian-plugin-testing/SKILL.md`'s page-ready section;
- `global/navigators/current/currentpage/model/metadata/title` is expected;
- required metadata exists under `currentpage/model/metadata`;
- important rows under `currentpage/model/nodes/*N` have expected `type`,
  `url`, title, and icon;
- screenshot is not black/blank and matches the expected UI state;
- log has no new `TypeError`, `ReferenceError`, `Cannot read property`,
  `Unable to load image`, `Unknown format`, or plugin-specific error trace.

Repository plugin update checks may show network errors for dead
`movian.tv`; treat those as noise unless the task is repo/update behavior.

## Media Playback Pass Criteria

A media smoke passes only when playback reaches the layer being tested. Keep
these states separate (full detail in `media-playback-smoke.md`):

- `routed`: Movian accepts the URL and dispatches it to the expected backend.
- `probed`: the demuxer reports container and stream metadata.
- `decoded`: audio/video codecs are created and packets continue after start.
- `rendered`: `/api/screenshot/raw` (`mdev shot`) shows the expected moving
  video frame, or the media props prove an audio-only stream is active.

For video playback, logs alone are not enough — capture a screenshot and
inspect it. When the protocol has a standalone command-line probe, run that
before Movian to separate server/input failures from Movian failures.

Store media artifacts under `/tmp/movian-*-smoke/` (or the `mdev` instance's
own `/tmp/mdev/<name>/` state, when using `mdev`): exact command/summary,
Movian log, server/publisher/probe logs, prop snapshot, screenshot.

## Prop API Rules

Read page state from:

```text
global/navigators/current/currentpage/model/metadata/title
global/navigators/current/currentpage/model/loading
global/navigators/current/currentpage/model/nodes/*N/...
```

(`mdev props global/navigators/current/currentpage --depth 2` for a quick
pretty-printed subtree.)

Open normal rows by reading `nodes/*N/url` and calling
`GET /api/open?url=<url>` (`mdev open <url>`).

Activate action rows and popups with POST body parameters:

```bash
curl -X POST -d action=Activate \
  "$BASE/api/prop/global/navigators/current/currentpage/model/nodes/*N/eventSink"

curl -X POST -d action=Ok \
  "$BASE/api/prop/global/popups/*0/eventSink"
```

Do not assume a route is ready until the target prop exists and
`loading` is `0` or void (see the page-ready caveat above).

## First-Launch Popup Rules

On a fresh profile, dismiss first-launch popups such as plugin TOS before the
first `/api/open`, unless the popup itself is the behavior under test. Send
`action=Ok` to the HTTP alias `global/popups/*0/eventSink`; an undisposed
popup can leave the navigator at `page:home`, with `(void)` model props or no
usable nodes even after `/api/open` returns success.

Use HTTP `*0` only for popup inspection/actions. For popup text fields, STPP
must subscribe to `popups`, recover the child propref, and set fields
relative to that propref; paths such as `popups.*0.username` are not valid
STPP paths.

## Prop Timing Rules

`prop.subscribeValue()` gets an initial callback unless `noInitialUpdate` is
used through the lower-level subscribe options. Treat the first value as
state, not necessarily as a user action.

For page tests, wait for the concrete prop that proves the flow:

- title plus `loading=0`-or-void for a route;
- node `url` and node title for a row;
- popup node before sending `Ok`;
- playinfo props before asserting resume state.

For playback/resume tests:

- cache `videoparams:` from `currentpage.url` or `currentpage.source` before
  playback starts;
- use `global/media/current/url` as the start/stop signal;
- title, icon, and duration can arrive after URL start;
- after stop, wait about 150 ms before using `native/metadata.bindPlayInfo()`
  on a temp prop and reading `restartpos`;
- if duration matters, inspect both
  `global/media/current/metadata/duration` and
  `global/navigators/current/currentpage/media/metadata/duration`.

## Capability Surface

For broad automation QA, verify the surfaces that matter for plugin testing:

- launch + HTTP port discovery from Movian logs (`mdev` does this for you);
- `/api/diag` for basic HTTP diagnostics;
- `/api/open` for route navigation (GET with `?url=`, not a POST body — see
  `movian-run` and `httpcontrol-stpp.md`);
- `/api/openparameterized/<scheme>` for JSON-parameterized dispatch;
- `/api/prop` GET for model state;
- `/api/prop/global/popups/*0` for inspecting the active popup and its
  fields;
- `/api/prop` POST `debug=on/off` as a flag mutation only — verify the
  target branch has a `PROP_DEBUG_THIS` consumer before expecting logs (see
  `prop-debugging.md`: in this tree it does not);
- `/api/ecmascript/stats` for context memory, rooted/native objects, and
  permanent resources;
- `/api/ecmascript/gc` only when a global diagnostic GC is explicitly
  intended;
- `prop.print`, `Page.dump()`, and `Item.dump()` for narrow stderr tree
  dumps;
- `/api/prop` POST `action=Activate` or `action=Ok` to `eventSink`;
- `/api/input/action/Back` and other action names from `src/event.c`;
- `/api/input/utf8?str=<text>` when a text field is focused;
- `/api/image?url=<image-url>` for artwork;
- `/api/screenshot/raw` and `/api/screenshot?raw=1` (`mdev shot`);
- `/api/stpp` JSON subscriptions for live prop updates.

STPP JSON paths are dot-separated and relative to propref `0`. Use:

```json
[1, 1, 0, "navigators.current.currentpage.model.metadata.title"]
```

Do not use slash paths for STPP JSON. Do not prefix root paths with
`global.` — both returned `null` in runtime verification.

Plugin settings are also reachable through dot paths, e.g.:

```json
[3, 0, "settings.apps.nodes.<plugin-id>.nodes.<settingId>.value", "<value>"]
```

The corresponding slash path (`/api/prop/global/settings/apps/...`) does not
update the setting over plain HTTP.

For popup fields, HTTP paths like `/api/prop/global/popups/*0/username` are
inspection paths. STPP cannot set `popups.*0.username` directly because `*0`
is an HTTP alias for an unnamed child. Subscribe to `popups`, read the
exported child propref, then set `"username"`, `"password"`, or `"domain"`
relative to that propref. If that does not match visible UI behavior, use
real X11 keypresses as the fallback input path
(`support/devtools/mdevlib/x11_keypress.py`).

## Visual Tests

For `.view` changes:

- capture before/after screenshots (`mdev shot`, or `mdev reload --shot` /
  `mdev preview --shot` — see `movian-view-design`);
- inspect the image, not just logs;
- check that text and images do not overlap;
- check that artwork sources are concrete Movian-supported images;
- treat `webp`, literal `null`, and JSON `imageset:[...]` strings as suspect
  unless the current build has been proven to load them;
- remember the reload false-green blind spot (SKILL.md) — a clean exit does
  not prove the view has no lexer/file error.

For pointer/touch scrolling, capture a short screenshot sequence rather than
only the final state — see `glw-pointer-touch-smoke.md` for the full matrix.

## Human Handoff

If waiting exceeds 120 seconds:

1. Save current URL, page type, title, loading, node count, popups.
2. Save a screenshot (`mdev shot`).
3. Save last 120-200 log lines (`mdev log --tail 200`).
4. Ask the user to perform one exact action.
5. Keep observing with the same artifact capture.

Do not spin indefinitely.
