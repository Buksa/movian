# M7 Flatpak / SteamOS Packaging Research

Date: 2026-05-17

Goal: keep the first public Flatpak/SteamOS branch narrow enough for review and
local sideload testing.

## Scope

The MVP targets:

- GLW/X11/OpenGL frontend;
- PulseAudio-compatible audio through the Flatpak socket;
- network, plugins, and local media from common XDG folders;
- software decoding through bundled libav;
- Steam Input keyboard mapping for Steam Deck controller navigation.

This is not a Flathub-ready manifest. The manifest uses a local `type: dir`
source and expects submodules to be populated before `flatpak-builder` runs.

## Sandbox

The manifest keeps permissions intentionally small:

- `--share=network`
- `--share=ipc`
- `--socket=x11`
- `--socket=wayland`
- `--socket=pulseaudio`
- `--device=dri`
- read-only `xdg-download`, `xdg-pictures`, `xdg-videos`, and `xdg-music`
- `--persist=.hts`
- `--persist=.cache/movian`

The `--persist` entries preserve Movian's legacy `$HOME/.hts/showtime` state
and `$HOME/.cache/movian` cache inside the Flatpak app home.

## Build Profile

The Flatpak configure profile disables legacy or deferred pieces:

- `--disable-gu`
- `--disable-webkit`
- `--disable-dvd`
- `--disable-vdpau`
- `--disable-avahi`
- `--disable-libxss`
- `--disable-libxxf86vm`
- `--disable-librtmp`

Bundled libav is configured with only the conservative disables supported by the
old movian6 libav snapshot:

- `--disable-inline-asm`
- `--disable-hwaccels`

The Flatpak manifest also sets
`LIBAV_CFLAGS=-Wno-error=incompatible-pointer-types` for the newer Freedesktop
SDK compiler.

The public WSL GLX compatibility path is runtime-detected. It does not need a
SteamOS configure switch.

The package installs `$PWD/build.flatpak/movian.bundle` as `/app/bin/showtime`.
Using the bundled binary avoids relying on a Linux `make install` target and
keeps GLW skin/resources embedded for the sideload package.

Because the manifest uses local `type: dir` sources, it removes any copied
`build.flatpak` directory before configure. Otherwise a previous host-side
build can leave absolute paths in generated libav Makefiles inside the sandbox.

## Gaming Mode

Two X11 fixes are included for Steam Deck Gaming Mode:

- avoid `override_redirect` fullscreen windows when launched under Steam
  (`SteamGameId` or `SteamAppId`);
- update `is_fullscreen` after the no-window-manager fullscreen window is
  recreated.

The keymap also maps `XK_Menu` to `ACTION_MENU` so Steam Input can expose
Movian's menu action without raw input permissions.

## Deferred

Keep these for later branches:

- VAAPI / hardware decoding;
- `/run/media:ro` access for SD card or USB media;
- native `/dev/input/event*` controller input;
- DVD and RTMP support;
- Flathub-ready pinned sources and metadata polish.
