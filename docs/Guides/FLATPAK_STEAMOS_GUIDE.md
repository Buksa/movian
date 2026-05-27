# SteamOS Flatpak Guide

This guide covers the local Movian Flatpak used for SteamOS and Steam Deck
smoke testing. It is a sideload package built from this checkout, not a
Flathub-ready manifest.

## Files

- Manifest: `support/flatpak/dev.uzver.Movian.yml`
- Desktop entry: `support/flatpak/dev.uzver.Movian.desktop`
- Fullscreen desktop entry: `support/flatpak/dev.uzver.Movian.GameMode.desktop`
- AppStream template: `support/flatpak/dev.uzver.Movian.metainfo.xml.in`
- Build wrapper: `support/flatpak/build-local.sh`
- Optional diagnostic launcher: `support/flatpak/steam-deck-gamemode-launcher.sh`
- Icon source: `res/showtime/showtime.png`

## Build Profile

The Flatpak profile builds the GLW/X11/OpenGL frontend and disables older or
deferred subsystems:

```sh
--disable-gu
--disable-webkit
--disable-dvd
--disable-vdpau
--disable-avahi
--disable-libxss
--disable-libxxf86vm
--disable-librtmp
--disable-glw-rec
```

The manifest also sets:

```text
LIBAV_CFLAGS=-Wno-error=incompatible-pointer-types
LIBAV_COMMON_FLAGS=--disable-x86asm --disable-inline-asm --disable-hwaccels
                   --disable-vaapi --disable-vdpau --disable-cuda
                   --disable-cuda-llvm --disable-cuvid --disable-ffnvcodec
                   --disable-nvdec --disable-v4l2-m2m
SKIP_SUBMODULE_UPDATE=1
```

`SKIP_SUBMODULE_UPDATE=1` keeps Flatpak builds from reaching out to git during
packaging, so the submodules must already be populated in the checkout.

The build installs `build.flatpak/movian.bundle` as `/app/bin/showtime`.
AppStream metadata is generated during the build from `git describe`, which
keeps Discover's version aligned with Movian's About/log output.
The app icon is installed from `res/showtime/showtime.png` as a hicolor
`256x256` PNG under the Flatpak app id.

The hidden GLW recorder is disabled in this release-oriented profile. It is a
developer/debug aid that writes large `capture.mkv` files in builds where it is
enabled. Use `/api/screenshot/raw` for Flatpak smoke captures.

## Sandbox

The manifest keeps permissions narrow:

```text
shared=network;ipc;
sockets=x11;wayland;pulseaudio;
devices=dri;
filesystems=xdg-download:ro;xdg-pictures:ro;xdg-videos:ro;xdg-music:ro;
persistent=.hts;.cache/movian;
```

The persisted directories preserve Movian's legacy settings, plugin state, and
image cache inside the Flatpak app home.

## Host Setup

On Ubuntu or WSL Ubuntu:

```sh
sudo apt update
sudo apt install -y \
  flatpak \
  flatpak-builder \
  desktop-file-utils \
  appstream \
  dbus-user-session
```

Add Flathub and install the runtime:

```sh
flatpak remote-add --user --if-not-exists flathub \
  https://dl.flathub.org/repo/flathub.flatpakrepo

flatpak install --user -y flathub \
  org.freedesktop.Platform//25.08 \
  org.freedesktop.Sdk//25.08
```

## Build

From the repository root:

```sh
support/flatpak/build-local.sh
```

Expected artifact:

```text
build.flatpak/dev.uzver.Movian.flatpak
```

Useful local checks after a build:

```sh
flatpak build build.flatpak-builder /app/bin/showtime --help
appstreamcli validate --no-net \
  build.flatpak-builder/files/share/metainfo/dev.uzver.Movian.metainfo.xml
flatpak build build.flatpak-builder ldd /app/bin/showtime | \
  grep -Ei 'gtk|gdk|webkit|rtmp|dvd|vaapi|vdpau|libva|nvidia' || true
sha256sum build.flatpak/dev.uzver.Movian.flatpak
```

The dependency grep should print nothing for the current MVP profile.

For the full Linux, Flatpak, runtime, and manual Steam Deck checklist, see:

```text
docs/Guides/LINUX_FLATPAK_SMOKE_CHECKLIST.md
```

## Install And Run

Install the generated bundle:

```sh
flatpak install --user --reinstall --bundle \
  build.flatpak/dev.uzver.Movian.flatpak
```

Run from CLI:

```sh
flatpak run --user dev.uzver.Movian
flatpak run --user --command=showtime dev.uzver.Movian --help
flatpak info --user dev.uzver.Movian
flatpak info --user --show-permissions dev.uzver.Movian
```

`flatpak info` should show the same version that Movian prints in About/log.

## Steam Deck Smoke

Build on Ubuntu/WSL/VM, copy the bundle to the Deck, then install it in Desktop
Mode through Discover or CLI:

```sh
flatpak install --user --reinstall --bundle \
  ~/Downloads/dev.uzver.Movian.flatpak
```

Minimum smoke checklist:

- Discover shows the same version as `flatpak info` and Movian About/log.
- Desktop Mode launch opens the GLW UI.
- Gaming Mode launch opens through `Movian (GameMode)` or a Non-Steam Game.
- Fullscreen does not loop or bounce back to the Steam loading screen.
- Basic navigation works through a Steam Input keyboard-style layout.
- Installed plugins and settings survive a restart.
- A direct WebP URL opens as an image.
- `/api/screenshot/raw` returns a PNG when the HTTP API is enabled.
- Pressing `Alt+F12` does not start recording or crash the Flatpak build.

## Steam Input

Movian's Linux GLW UI consumes keyboard and pointer events. For Steam Deck
Gaming Mode, map the controller to keyboard actions:

```text
D-pad / Left Stick: Arrow Up / Down / Left / Right
A: Enter
B: Escape
X: Backspace
Y or Menu: Menu key
L1 / R1: Page Up / Page Down
```

If arrows and Enter move focus inside Movian, input is reaching the app. Native
raw controller input is out of scope for this Flatpak branch.

## Diagnostic Launcher

If Gaming Mode does not show a window, copy
`support/flatpak/steam-deck-gamemode-launcher.sh` to the Deck and add it as a
Non-Steam Game. It runs:

```sh
flatpak run dev.uzver.Movian --fullscreen -d
```

and writes environment and launch diagnostics to:

```text
~/movian-gamemode.log
```

## Known Limits

- The manifest uses local `type: dir` sources; Flathub needs pinned source
  archives and hashes.
- Hardware acceleration is disabled for this first package profile.
- DVD, RTMP, GU/WebKit, Avahi, VDPAU, libXss, and libXxf86vm are disabled.
- The sandbox exposes common XDG media folders read-only, not every removable
  media path.
- Native `/dev/input/event*` controller access is not requested; use Steam
  Input keyboard mapping.
