# Movian Public Branch Notes

This directory documents the public `movian6` branch in this checkout. The
notes are based on code, scripts, and packaging files present in this
repository.

## Current Public Baseline

The current branch includes:

- modern GCC and Ubuntu build fixes;
- reproducible Linux debug configure helper and README build notes;
- raw screenshot HTTP API on top of the existing screenshot code;
- WebP probing, image loading, libav decode mapping, and `/api/image` WebP
  content type;
- bundled FFmpeg 4.4.7 as the media backend, while keeping existing `libav`
  option names and internal build variables for compatibility;
- local SteamOS/Steam Deck Flatpak packaging and small Linux runtime fixes used
  by that package;
- a plugin debug workflow for route smoke tests against `build.debug/movian`.

## Linux Build

For current Ubuntu or WSL environments, use the helper added by the build
cleanup branch:

```sh
./support/configure-linux-debug.sh
make BUILD=debug -j$(nproc)
./build.debug/movian --help
```

The helper runs:

```sh
./configure.linux --build=debug --disable-vdpau --enable-polarssl
```

Extra configure flags can be appended to the helper command.

## Plugin Debug Workflow

Use the direct debug binary for plugin development and route smoke tests:

```sh
PLUGIN_PATH=/path/to/plugin \
START_URL=plugin:start \
EXPECTED_TITLE="Plugin Title" \
support/plugin-smoke/run-plugin-smoke.sh
```

The main guides are:

- `Guides/PLUGIN_DEBUG_WORKFLOW.md`
- `Guides/PLUGIN_DEVELOPMENT_NOTES.md`
- `Guides/PLUGIN_API_REFERENCE.md`

The reusable runner lives under:

- `support/plugin-smoke/`

## Runtime Changes

The public stack currently includes these user-visible changes:

- WSL2 GLX handling is detected at runtime in `src/ui/glw/glw_x11.c`; there is
  no WSL configure flag.
- `/api/screenshot/raw` returns a PNG directly. `/api/screenshot?raw=1` and
  `/api/screenshot?raw=true` use the same path. The old `/api/screenshot`
  upload flow remains.
- WebP files are recognized by RIFF/WEBP magic in file probing and image
  loading. The libav image decoder maps WebP, and `/api/image` returns
  `image/webp` for WebP payloads.
- The bundled media backend is FFmpeg 4.4.7. Existing command-line and helper
  names such as `--libav-log` remain unchanged.
- Steam launches avoid the X11 fullscreen `override_redirect` path when
  `SteamGameId` or `SteamAppId` is set. `XK_Menu` maps to Movian's menu action
  for Steam Input keyboard layouts.
- The hidden GLW recorder is kept as a developer/debug aid. Linux debug builds
  enable it by default; release and Flatpak builds disable it unless
  `--enable-glw-rec` is passed explicitly.

## Flatpak / SteamOS

The Flatpak work is a local sideload package, not a Flathub recipe. The main
guide is:

- `Guides/FLATPAK_STEAMOS_GUIDE.md`

The source-of-truth packaging files live under:

- `support/flatpak/`

The package installs the bundled Movian binary as `/app/bin/showtime`, keeps
state through `--persist=.hts` and `--persist=.cache/movian`, and generates
AppStream metadata from the current `git describe` version so Discover matches
Movian's About/log output.

The Flatpak package disables the GLW recorder hotkey. Use
`/api/screenshot/raw` for smoke/debug captures in that profile.

## Out Of Scope

These are intentionally left for later branches:

- Flathub-ready pinned source archives and hashes.
- VAAPI or other hardware decode paths.
- Native `/dev/input/event*` controller input in the Flatpak sandbox.
- DVD and RTMP support in the Flatpak profile.
- Broader filesystem permissions such as removable media paths.
