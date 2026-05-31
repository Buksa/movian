# Linux and Flatpak Smoke Checklist

Use this checklist for Linux, WSL, Flatpak, SteamOS, media, image, screenshot,
and packaging changes. Keep the completed commands in the pull request body so
reviewers can reproduce the same path.

## Baseline Linux Build

Run these for ordinary Linux build, C code, configure, module, plugin runtime,
image, screenshot, and media changes:

```sh
git diff --check
./support/configure-linux-debug.sh
make BUILD=debug -j$(nproc)
./build.debug/movian --help
```

Expected result:

- `git diff --check` prints nothing.
- configure completes with the current bundled FFmpeg/libav submodule.
- `make` produces `build.debug/movian`.
- `--help` prints the Movian version and option list.

For release-policy changes that touch the GLW recorder, also verify the
recorder defaults:

```sh
grep -n "ENABLE_GLW_REC" build.debug/config.h

SKIP_SUBMODULE_UPDATE=1 ./configure.linux \
  --build=release-smoke \
  --release \
  --disable-vdpau \
  --enable-polarssl
grep -n "ENABLE_GLW_REC" build.release-smoke/config.h

SKIP_SUBMODULE_UPDATE=1 ./configure.linux \
  --build=release-smoke-rec \
  --release \
  --enable-glw-rec \
  --disable-vdpau \
  --enable-polarssl
grep -n "ENABLE_GLW_REC" build.release-smoke-rec/config.h
```

Expected result:

- debug build: `ENABLE_GLW_REC 1`
- release build: `ENABLE_GLW_REC 0`
- explicit release override: `ENABLE_GLW_REC 1`

## Flatpak Build

Run these for Flatpak manifest, SteamOS, packaging, sandbox, AppStream, icon,
runtime dependency, or release-profile changes:

```sh
support/flatpak/build-local.sh

flatpak build build.flatpak-builder /app/bin/showtime --help

desktop-file-validate \
  support/flatpak/dev.uzver.Movian.desktop \
  support/flatpak/dev.uzver.Movian.GameMode.desktop

appstreamcli validate --no-net \
  build.flatpak-builder/files/share/metainfo/dev.uzver.Movian.metainfo.xml

flatpak build build.flatpak-builder ldd /app/bin/showtime | \
  grep -Ei 'gtk|gdk|webkit|rtmp|dvd|vaapi|vdpau|libva|nvidia|cuda' || true

find build.flatpak/state/build \
  -path '*/build.flatpak/config.h' \
  -exec grep -n "ENABLE_GLW_REC" {} \;

find build.flatpak/state/build \
  -path '*/build.flatpak/src/ui/glw/glw_rec.o' \
  -print

sha256sum build.flatpak/dev.uzver.Movian.flatpak
```

Expected result:

- Flatpak bundle exists at `build.flatpak/dev.uzver.Movian.flatpak`.
- `/app/bin/showtime --help` prints the same version family as the build.
- desktop-file validation succeeds.
- AppStream validation succeeds.
- dependency grep is empty for the current minimal Flatpak profile.
- Flatpak config shows `ENABLE_GLW_REC 0`.
- `glw_rec.o` is absent from the Flatpak build object list.
- SHA256 is recorded in the PR when a bundle is used for manual smoke.

## Runtime Smoke Matrix

Choose the smallest runtime smoke that matches the files changed.

| Change type | Minimum runtime smoke |
| --- | --- |
| Docs only | No runtime smoke required; run `git diff --check`. |
| Linux build/configure/C code | Baseline Linux build and `./build.debug/movian --help`. |
| Flatpak manifest, AppStream, icon, sandbox, release profile | Flatpak build checks plus manual Steam Deck smoke when behavior is user-visible. |
| WSL/X11/GLW UI behavior | Launch GLW on the target X11/WSL environment and capture logs. |
| Screenshot API or UI capture | Verify `/api/screenshot/raw` returns a PNG. |
| WebP or image pipeline | Verify direct WebP URL and `/api/image` content type. |
| FFmpeg, HLS, demux, decode, audio, or media playback | Play a small local file or HLS URL and capture logs. Screenshots alone are not enough. |
| GLW recorder, muxing, encoder, or packet timestamps | Use a debug build with recorder enabled and verify the output container with `ffprobe`. |
| Plugin route or plugin API behavior | Use `support/plugin-smoke/run-plugin-smoke.sh` with an isolated profile. |

## HTTP Screenshot Smoke

When a display is available and the HTTP API is enabled, start Movian with an
isolated profile:

```sh
rm -rf /tmp/movian-screenshot-smoke
mkdir -p /tmp/movian-screenshot-smoke

./build.debug/movian \
  -d \
  --disable-upgrades \
  --persistent /tmp/movian-screenshot-smoke/persistent \
  --cache /tmp/movian-screenshot-smoke/cache \
  > /tmp/movian-screenshot-smoke/movian.log 2>&1 &
MOVIAN_PID=$!
```

Wait for the log line containing `http-server: Listening on port`, then fetch a
PNG:

```sh
PORT=$(sed -n 's/.*http-server: Listening on port \([0-9][0-9]*\).*/\1/p' \
  /tmp/movian-screenshot-smoke/movian.log | tail -1)

curl -fsS "http://127.0.0.1:${PORT}/api/screenshot/raw" \
  -o /tmp/movian-screenshot-smoke/screenshot.png

file /tmp/movian-screenshot-smoke/screenshot.png
kill "$MOVIAN_PID"
```

Expected result:

- `file` reports a PNG image.
- Keep `movian.log` and `screenshot.png` if the smoke is used as PR evidence.

If startup or capture takes more than two minutes, stop the run, keep the log,
and treat it as a manual follow-up.

## Media Smoke

For FFmpeg, HLS, demux, decode, audio, Flatpak runtime, or image decoder
changes, record the exact media input used. Prefer a small local sample or a
stable short HLS URL:

```sh
rm -rf /tmp/movian-media-smoke
mkdir -p /tmp/movian-media-smoke

./build.debug/movian \
  -d \
  --disable-upgrades \
  --persistent /tmp/movian-media-smoke/persistent \
  --cache /tmp/movian-media-smoke/cache \
  "file:///absolute/path/to/sample.mp4" \
  > /tmp/movian-media-smoke/movian.log 2>&1
```

Expected result:

- playback starts without crash or decoder fatal errors;
- the log does not contain unexpected media pipeline errors;
- if the change is Flatpak-specific, repeat the same input through the Flatpak
  package when practical.

### Local RTMP Smoke

Use this when RTMP handling or the FFmpeg-backed RTMP fallback changes. The
script starts a local synthetic RTMP stream with `ffmpeg`, opens it through the
HTTP API, and keeps the Movian/server logs as artifacts:

```sh
./support/configure-linux-debug.sh --build=debug-ffrtmp-smoke --disable-librtmp
make BUILD=debug-ffrtmp-smoke -j$(nproc)

MOVIAN_BIN=./build.debug-ffrtmp-smoke/movian \
  support/rtmp-smoke/run-rtmp-smoke.sh
```

Expected result:

- the log contains `Probed as flv`;
- playback starts for the local `rtmp://127.0.0.1:.../live/test` URL;
- the detected streams include H.264 video and AAC audio;
- the current media URL property points at the local RTMP URL.

Use the normal Flatpak build checks as well when RTMP behavior changed in the
Flatpak profile.

## Steam Deck Manual Smoke

Use this when the branch affects Flatpak launch behavior, fullscreen, Steam
Input, sandbox permissions, installed metadata, or anything visible to Deck
users.

Install the generated bundle in Desktop Mode:

```sh
flatpak install --user --reinstall --bundle \
  ~/Downloads/dev.uzver.Movian.flatpak
```

Checklist:

- Discover shows the same version as `flatpak info` and Movian About/log.
- Desktop Mode launch opens the GLW UI.
- Gaming Mode launch opens through `Movian (GameMode)` or a Non-Steam Game.
- Fullscreen does not loop or bounce back to the Steam loading screen.
- Steam Input keyboard mapping can navigate the UI.
- Installed plugins and settings survive restart.
- A direct WebP URL opens as an image.
- `/api/screenshot/raw` returns a PNG when the HTTP API is enabled.
- `Alt+F12` does not record or crash the Flatpak build.

Record the bundle SHA256 and any manual notes in the PR.

## Evidence To Keep

For every PR, keep the evidence proportional to the change:

- branch name and commit SHA;
- exact configure/build commands;
- `./build.debug/movian --help` or Flatpak `/app/bin/showtime --help` version;
- AppStream and desktop validation result for Flatpak changes;
- bundle SHA256 when manually installed;
- runtime log directory for UI/media/plugin smoke;
- screenshot or media output details when the PR touches those paths.

Do not include local-only source trees or personal paths as public evidence. Use
paths from this checkout or temporary smoke directories.
