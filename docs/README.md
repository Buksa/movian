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
- a bundled read-only SMB2/SMB3 backend for Linux and Flatpak, exposed through
  temporary `smb2://` URLs alongside the existing `smb://` backend;
- local SteamOS/Steam Deck Flatpak packaging and small Linux runtime fixes used
  by that package;
- a plugin debug workflow for route smoke tests against `build.debug/movian`;
- repeatable public-branch patterns for build, packaging, media, and smoke
  work.

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
./configure.linux --build=debug --disable-vdpau --enable-polarssl --disable-librtmp
```

Before running the helper on a fresh Ubuntu/WSL install, make sure FFmpeg's
RTMP-family TLS/crypto dependencies are available:

```sh
sudo apt install libgmp-dev libgnutls28-dev
```

The helper exports the same FFmpeg RTMP-family feature flags used by the
Flatpak profile:

```text
--enable-version3 --enable-gmp --enable-gnutls
```

It also uses the Flatpak-compatible portable FFmpeg switches that disable
assembly and hardware acceleration autodetect for this debug profile.

Extra configure flags can be appended to the helper command. The old external
`librtmp` backend can still be re-enabled explicitly for comparison builds with
`--enable-librtmp`.

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

## Smoke Checklist

Use the Linux/Flatpak smoke checklist when preparing public branches that touch
build, packaging, media, image, screenshot, plugin runtime, WSL, or Flatpak
behavior:

- `Guides/LINUX_FLATPAK_SMOKE_CHECKLIST.md`

For branch structure, compatibility, feature gating, Flatpak validation, and
runtime smoke patterns, use:

- `Guides/PUBLIC_WORK_PATTERNS.md`
- `Guides/CODEX_PROJECT_KNOWLEDGE_WORKFLOW.md`

## Bundled Modules

Use the bundled module update plan before changing submodule pointers or
vendored third-party code:

- `Guides/BUNDLED_MODULE_UPDATE_PLAN.md`
- `support/check-submodules.sh`

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
- Linux debug and Flatpak builds disable the old external `librtmp` backend by
  default and use FFmpeg's RTMP-family protocols with `gmp` and `gnutls`.
- Linux debug and Flatpak builds include pinned static libsmb2 support.
  `smb2://` provides read-only SMB2/SMB3 browsing and playback while the
  existing `smb://` backend remains available for compatibility. See
  `Guides/LINUX_SMB2_BACKEND.md`. Movian also ships a built-in SMB2 *server*
  (`Guides/SMB_SERVER_SPECIFICATION.md`,
  `Guides/SMB_SERVER_LIFECYCLE_ARCHITECTURE.md`). It listens on an
  unprivileged port by default; what it would take to reach it from Windows
  Explorer on TCP `445` is researched in
  `Guides/SMB2_WINDOWS_VISIBILITY_RESEARCH.md`.
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
- DVD support in the Flatpak profile.
- Broader filesystem permissions such as removable media paths.
