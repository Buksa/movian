# Movian Public Branch Notes

This directory documents the public branch stack in this checkout. The notes
are based on code, scripts, and packaging files present in this repository.

## Branch Stack

The current public work is organized as small reviewable branches:

- `public/gcc-modernize` - modern GCC and Ubuntu build fixes only.
- `public/linux-build-cleanup` - reproducible Linux debug configure helper and
  README build notes.
- `public/screenshot-api` - raw screenshot HTTP API on top of the existing
  screenshot code.
- `public/webp-image-support` - WebP probing, image loading, libav decode
  mapping, and `/api/image` WebP content type.
- `public/flatpak-steamos` - local SteamOS/Steam Deck Flatpak packaging and
  small Linux runtime fixes needed by that package.

Each branch is intended to stay public-safe, narrow, and easy to review.

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
- Steam launches avoid the X11 fullscreen `override_redirect` path when
  `SteamGameId` or `SteamAppId` is set. `XK_Menu` maps to Movian's menu action
  for Steam Input keyboard layouts.

## Flatpak / SteamOS

The Flatpak work is a local sideload package, not a Flathub recipe. The main
guide is:

- `Guides/M7_FLATPAK_STEAMOS_GUIDE.md`

The source-of-truth packaging files live under:

- `support/flatpak/`

The package installs the bundled Movian binary as `/app/bin/showtime`, keeps
state through `--persist=.hts` and `--persist=.cache/movian`, and generates
AppStream metadata from the current `git describe` version so Discover matches
Movian's About/log output.

## Out Of Scope

These are intentionally left for later branches:

- Flathub-ready pinned source archives and hashes.
- VAAPI or other hardware decode paths.
- Native `/dev/input/event*` controller input in the Flatpak sandbox.
- DVD and RTMP support in the Flatpak profile.
- Broader filesystem permissions such as removable media paths.
