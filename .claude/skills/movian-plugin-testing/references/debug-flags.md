# Movian Debug And CLI Flags

Use this reference when choosing launch flags for Movian plugin tests. Most
of this is available through `mdev run` (see `movian-run`); flags not yet
exposed by `mdev` need a fallback direct launch.

## Default Plugin Smoke Launch

```bash
mdev run --name <test> -p "$PLUGIN" "$START_URL"
```

Equivalent to a direct launch of:

```bash
./build.debug/movian \
  -d \
  --disable-upgrades \
  --persistent "$ART/persistent" \
  --cache "$ART/cache" \
  -p "$PLUGIN" \
  "$START_URL"
```

- `-d` is maximum trace in this tree. It prints normal debug plus
  navigation/tuning trace (`|N|` and `|T|`) to stdout.
- `--disable-upgrades` removes plugin-repository/upgrade noise from route
  smoke logs.
- `--persistent` and `--cache` keep tests isolated (`mdev` manages these
  under `/tmp/mdev/<name>/`).
- `-p` loads a development plugin directory.

## Playback / HLS / Probe Runs

Add `--libav-log` only when the test needs FFmpeg/probe/HLS diagnostics
(`mdev run --libav-log ...`):

```bash
./build.debug/movian -d --libav-log --disable-upgrades ...
```

Leave it off for ordinary route and `.view` smoke runs because it can make
logs larger without improving the pass/fail signal.

## Core Crash / GDB Builds

For plugin route/UI testing use the normal `build.debug` binary. For C
crashes, hard-to-read stacks, or memory lifetime bugs, build a separate
development profile instead of replacing the normal debug package:

```bash
LIBAV_COMMON_FLAGS="--disable-inline-asm" ./configure.linux \
  --build=debug-gdb \
  --disable-vdpau \
  --disable-avahi \
  --disable-webkit \
  --enable-polarssl \
  --cc=gcc-9 \
  --optlevel=0 \
  --extra-cflags=-fno-omit-frame-pointer \
  --enable-bughunt

make BUILD=debug-gdb -j$(nproc)
gdb --args ./build.debug-gdb/movian -d --disable-upgrades
```

Use `--sanitize=address` only in a separate `build.asan` style tree when the
goal is memory corruption or use-after-free diagnosis. ASan is valuable, but
it changes runtime behavior and is too noisy for routine plugin smoke tests.

## GLW Debug

`--debug-glw` enables global GLW event/focus tracing via `GLW_TRACE()` in
the debug build. It is useful for:

- focus issues;
- action routing;
- event-map problems;
- keyboard/navigation tests driven through `/api/input/action/<Action>`.

Runtime-verified log examples:

```text
GLW |D| Focus set to .//glwskins/flat/pages/home.view:111 by Init
GLW |D| Event 'Down' route start at widget 'Universe'
GLW |D| Event 'Down' intercepted by widget 'array @ .//glwskins/flat/pages/home.view:86'
GLW |D| Event 'Activate' intercepted by event-map 'activate' at .//glwskins/flat/pages/home.view:115
```

Important distinction:

- `--debug-glw` does not mark every widget with layout boxes.
- `--debug-glw` does not force the visual list cursor to appear. In the flat
  skin, list row highlight uses `isNavFocused()`, and Movian core returns
  true only when the widget is focused and GLW keyboard mode is on. HTTP
  `/api/input/action/<Action>` events are not marked as `EVENT_KEYPRESS`, so
  they can move focus without enabling keyboard mode. Use real X11 keypresses
  (`support/devtools/mdevlib/x11_keypress.py`) when a screenshot must show
  the cursor.
- For layout boxes, texture size logs, text layout logs, and prop
  subscription debug on a specific widget, temporarily add `.view` attribute
  `debug: true` to that widget (`src/ui/glw/glw_view_attrib.c:1382`, sets
  `GLW2_DEBUG`). Remove it before release unless the task explicitly asks to
  keep debug markup. See `movian-view-design` for the full workflow.

Useful source anchors:

- `src/ui/glw/glw.h`: `GLW_TRACE()` is gated by `gconf.debug_glw`.
- `src/ui/glw/glw.c`: event routing/focus traces use `GLW_TRACE()`.
- `src/ui/glw/glw_view_attrib.c:1382`: `.view` attribute `debug` sets
  `GLW2_DEBUG`.
- `src/ui/glw/glw_view_eval.c`: `GLW2_DEBUG` enables `PROP_SUB_DEBUG`.
- `src/ui/glw/glw_image.c`, `src/ui/glw/glw_text_bitmap.c`,
  `src/ui/glw/glw_container.c`: widget-local debug prints layout/texture/text
  details.

## Pointer / Touch Testing

Use `--pointer-is-touch` only when reproducing touch-specific behavior with
a mouse/pointer environment. It converts left press/release/motion into
touch start/end/move in GLW pointer handling.

For input logging, prefer developer setting `inputevents` and, if relevant,
`touchevents`. These are stored under the `dev` settings store and can be
enabled through settings UI, STPP, or `mdev run --dev-flags`.

## Usage Event Tracing

`--show-usage-events` prints internal `usage_event()` calls to stdout on
Linux. It can help classify broad flows such as open page, plugin
install/upgrade, play audio/video, SMB/FTP, and metadata queries. It is not a
replacement for route/page prop assertions.

## Remote UDP Log

`-L <ip[:port]>` sends colored trace lines to a UDP log receiver. Default
port is `4000` if omitted. This is optional for local tests but useful when
stdout is hard to capture or when another process should collect logs.

```bash
nc -u -l -p 4001 > "$ART/netlog.txt" &
./build.debug/movian -d -L 127.0.0.1:4001 ...
```

## Shell FD

`--showtime-shell-fd <fd>` is not a plugin-testing flag. It is a launcher/
appliance integration hook:

- stores an inherited file descriptor in `gconf.shell_fd`;
- if `shell_fd > 0`, runcontrol creates a network setting named
  `Enable SSH server`;
- toggling that setting writes one byte to the fd: `1` for on, `2` for off;
- on Raspberry Pi startup, any non-default shell fd also kills the
  framebuffer.

Do not use it in ordinary plugin smoke tests. It is only worth testing if
the task explicitly covers runcontrol, appliance shell integration, or SSH
server control.

## Developer Debug Settings

Many subsystem debug switches are settings, not CLI flags. They are useful in
targeted tests, but too noisy to enable all at once.

High-value plugin testing toggles:

- `httpdebug`: HTTP requests/responses.
- `ecmascriptdebug`: module loading and JS runtime diagnostics.
- `hlsdebug`: HLS playback/debug.
- `imagedebug`: image decode/texture loading path.
- `metadatadebug`: TMDB/metadata diagnostics.
- `settingsdebug`: settings store reads/writes.
- `threadsdebug`: thread lifecycle.
- `inputevents`: input action diagnostics.
- `touchevents`: touch event diagnostics.
- `nohttpreuse`: disable HTTP connection reuse for network-flake isolation.

Torrent and SMB toggles are useful only for those flows:

- `bt`, `bttracker`, `btpeercon`, `btpeerdl`, `btpeerul`, `btdiskio`;
- `smbdebug`;
- `ftpdebug`, `ftpserverdebug`.

For automated runs, set only the toggles that match the failure being
investigated and include them in the artifact summary.

## CLI Sanity After Core Changes

After changing CLI parsing, run `movian --help` and a short flag smoke
proving that advertised options are consumed as options. Specifically check
that logs do not contain:

```text
navigator |N| Opening -j
navigator |N| Opening -s
navigator |N| Opening --some-option
```

Minimum flags to test: `-d`, `-s`, `-j`, `--persistent`, `--cache`, `-p`, and
`--disable-upgrades`.

## Seeding `dev` flags for an isolated profile

`dev` flags (`smbdebug`, `ecmascriptdebug`, `nohttpreuse`, ...) are not a CLI
switch, and the HTTP `/api/prop` path `settings/dev/nodes/<flag>` returns 404
in this build (settings sit under `global/settings/apps/nodes`, which STPP
reaches over its WebSocket but the plain HTTP prop API does not). The
reliable, profile-scoped way to enable them is `mdev run --dev-flags
smbdebug=1,ecmascriptdebug=1`, which writes the htsmsg JSON store file before
launch (`persistent_file.c` builds the path as `<persistent>/settings/<group>`
and `htsmsg_store.c` reads it as JSON). Toggling at runtime over STPP
(`settings.dev.nodes.<flag>.value`, string `"1"`/`"0"`) works only for an
instance already wired to an STPP client; prefer the JSON-store seed for
scripted smokes.

## Line-buffered output for background runs

`movian -d > log 2>&1 &` runs fully buffered: trace lines sit in the stdio
buffer and are lost if the process is killed (e.g. a smoke's cleanup trap) or
while it stays idle. `mdev` already wraps the binary with `stdbuf -oL -eL`
(`support/devtools/mdevlib/harness.py: build_argv()`); if you launch directly
instead, do the same:

```sh
stdbuf -oL -eL ./build.debug/movian -d ... >"$LOG" 2>&1 &
```

This matters for assertions that wait on asynchronous events emitted only
after a quiet period — e.g. the SMB2 keepalive `Keepalive ... echo rc=0` line
that appears ~30 s after a pooled session goes idle.

## libsmb2 `smb2_echo` socket-validity check (historical, now fixed)

`smb2_echo()` in `ext/libsmb2/lib/sync.c` once had an inverted
socket-validity check (`SMB2_VALID_SOCKET` instead of `!SMB2_VALID_SOCKET`)
in an earlier `libsmb2-6.2`-based revision, so it returned `-ENOMEM` ("Not
Connected") on every live session. The current gitlink
(`ext/libsmb2` at `libsmb2-6.2-208-gc443352` as of this writing) has the
fix — `grep '!SMB2_VALID_SOCKET(smb2->fd)' ext/libsmb2/lib/sync.c` matches at
line 83, 861, and 961. If `smbdebug` ever shows `Keepalive ... echo rc=-12`
after a submodule bump, re-check that grep first; it means the fix regressed
out of the pinned commit, not that Movian's client logic is broken.
