# Plugin Debug Workflow

This guide describes a repeatable local workflow for developing and testing
Movian plugins against a debug Linux build.

The workflow is intentionally separate from Flatpak, Snap, and distribution
packages. Plugin development is fastest when Movian runs directly from
`build.debug/movian` with an isolated profile.

## Build

From the repository root:

```sh
./support/configure-linux-debug.sh
make BUILD=debug -j$(nproc)
./build.debug/movian --help
```

The helper configures the Linux debug build with:

```sh
./configure.linux --build=debug --disable-vdpau --enable-polarssl
```

Append extra configure flags when needed:

```sh
./support/configure-linux-debug.sh --disable-webkit
```

## Manual Plugin Launch

Use a temporary persistent profile and cache so a smoke run does not modify a
normal user profile:

```sh
ART=/tmp/movian-plugin-debug
rm -rf "$ART"
mkdir -p "$ART"

./build.debug/movian \
  -d \
  --disable-upgrades \
  --persistent "$ART/persistent" \
  --cache "$ART/cache" \
  -p /path/to/plugin \
  plugin:start
```

Useful launch flags:

- `-d` enables high-signal debug trace output.
- `--disable-upgrades` removes plugin repository and upgrade noise from logs.
- `--persistent` and `--cache` isolate the run.
- `-p` loads a development plugin directory.
- `--debug-glw` adds GLW focus and event-routing logs.
- `--libav-log` is useful for playback, probe, HLS, and decoder issues.

Use `--libav-log` only when media probing or playback is part of the test. It
can make ordinary route smoke logs much larger.

## Smoke Runner

The repository includes a small generic runner:

```sh
support/plugin-smoke/run-plugin-smoke.sh
```

Minimal usage:

```sh
PLUGIN_PATH=/path/to/plugin \
START_URL=plugin:start \
EXPECTED_TITLE="Plugin Title" \
support/plugin-smoke/run-plugin-smoke.sh
```

Optional environment variables:

- `MOVIAN_BIN` - Movian binary, default `./build.debug/movian`.
- `ARTIFACTS` - artifact directory, default `/tmp/movian-plugin-smoke`.
- `READY_LOG_PATTERN` - optional extended-regex log pattern that must appear
  before the route is opened.
- `EXPECTED_TYPE` - optional expected `currentpage.model.type`.
- `MIN_NODES` - optional minimum node count under `currentpage.model.nodes`.
- `REQUIRE_SCREENSHOT=1` - fail if `/api/screenshot/raw` cannot be captured.
- `DEBUG_GLW=1` - add `--debug-glw`.
- `LIBAV_LOG=1` - add `--libav-log`.
- `ALLOW_EXISTING_MOVIAN=1` - allow another Movian process to already be
  running.

The runner saves:

- `movian.log`
- `open.html`
- `title.txt`
- `loading.txt`
- `type.txt`
- `nodes.txt`
- `screenshot.png` when available
- `log-signals.txt`
- `movian-tail.txt` on failure

## Pass Criteria

A route smoke should prove the smallest useful flow:

- Movian starts and prints the HTTP control port.
- The development plugin loads.
- `/api/open?url=<START_URL>` reaches the expected route.
- `currentpage.model.metadata.title` contains `EXPECTED_TITLE`.
- `currentpage.model.loading` becomes not-busy.
- `currentpage.model.type` matches `EXPECTED_TYPE` when provided.
- `currentpage.model.nodes` has at least `MIN_NODES` entries when provided.
- Logs do not contain common JavaScript crash signatures.
- `/api/screenshot/raw` returns a PNG when visual proof is required.

The runner treats screenshot capture as optional unless `REQUIRE_SCREENSHOT=1`
is set. This keeps non-visual route checks usable in headless or GL-limited
environments.

## HTTP And Prop Surface

Useful endpoints during manual diagnosis:

```text
/api/open?url=<encoded-url>
/api/prop/global/navigators/current/currentpage/model/metadata/title
/api/prop/global/navigators/current/currentpage/model/loading
/api/prop/global/navigators/current/currentpage/model/type
/api/prop/global/navigators/current/currentpage/model/nodes
/api/screenshot/raw
/api/screenshot?raw=1
/api/image?url=<encoded-image-url>
/api/stpp
```

For action rows and popup buttons, send action events as POST body fields:

```sh
curl -fsS -X POST -d action=Activate \
  "$BASE/api/prop/global/navigators/current/currentpage/model/nodes/*0/eventSink"

curl -fsS -X POST -d action=Ok \
  "$BASE/api/prop/global/popups/*0/eventSink"
```

For normal rows created with `page.appendItem(url, ...)`, prefer reading the
row URL and opening it through `/api/open`. Reserve `eventSink` activation for
action rows, popups, and option-like controls.

## STPP Notes

The WebSocket endpoint is:

```text
ws://127.0.0.1:<port>/api/stpp
```

JSON subscriptions use dot-separated paths relative to root propref `0`, for
example:

```json
[1, 1, 0, "navigators.current.currentpage.model.metadata.title"]
[1, 2, 0, "navigators.current.currentpage.model.loading"]
```

Do not use slash-separated paths for JSON STPP subscriptions.

## Escalation

Stay in the normal `build.debug` workflow for JavaScript route failures,
missing metadata, missing nodes, and view/layout issues.

Use a separate debug profile for native crashes or unclear native stacks:

```sh
LIBAV_COMMON_FLAGS="--disable-inline-asm" ./configure.linux \
  --build=debug-gdb \
  --disable-vdpau \
  --disable-avahi \
  --disable-webkit \
  --enable-polarssl \
  --optlevel=g \
  --extra-cflags=-fno-omit-frame-pointer \
  --enable-bughunt

make BUILD=debug-gdb -j$(nproc)
gdb --args ./build.debug-gdb/movian -d --disable-upgrades
```

Use AddressSanitizer only for memory corruption or use-after-free
investigations, because it changes runtime behavior and can make ordinary
plugin route smoke much noisier.
