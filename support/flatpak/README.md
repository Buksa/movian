# Movian Flatpak

This directory contains the local/sideload Flatpak packaging for Movian.
The manifest builds the GLW/X11/OpenGL UI and installs the bundled binary as
`/app/bin/showtime`.

## Files

- `dev.uzver.Movian.yml` - Flatpak manifest.
- `dev.uzver.Movian.desktop` - normal launcher.
- `dev.uzver.Movian.GameMode.desktop` - fullscreen launcher.
- `dev.uzver.Movian.metainfo.xml.in` - AppStream template.
- `build-local.sh` - local builder wrapper.
- `steam-deck-gamemode-launcher.sh` - optional host-side diagnostic launcher.
- `../../res/showtime/showtime.png` - installed app icon source.

## Build

From the repository root:

```sh
support/flatpak/build-local.sh
```

The bundle is written to:

```text
build.flatpak/dev.uzver.Movian.flatpak
```

The build log is written to:

```text
build.flatpak/flatpak-build.log
```

`build-local.sh` removes old `build.flatpak/state/build` contents by default
so repeated local builds do not grow without bound. Set
`FLATPAK_KEEP_BUILD_DIRS=1` when debugging a failed Flatpak module build and
you need to inspect the preserved builder work directories.

## Validate

```sh
desktop-file-validate \
  support/flatpak/dev.uzver.Movian.desktop \
  support/flatpak/dev.uzver.Movian.GameMode.desktop

appstreamcli validate --no-net \
  build.flatpak-builder/files/share/metainfo/dev.uzver.Movian.metainfo.xml

flatpak build build.flatpak-builder /app/bin/showtime --help

flatpak build build.flatpak-builder ldd /app/bin/showtime | \
  grep -Ei 'gtk|gdk|webkit|librtmp|dvd|vaapi|vdpau|libva|nvidia|gmp|gnutls' || true
```

The dependency grep should not show GTK/WebKit, DVD, VAAPI, VDPAU, Nvidia,
or external `librtmp` dependencies. `libgmp` and `libgnutls` are expected
because they enable FFmpeg's native RTMP-family protocol support.

The full smoke checklist for Linux, Flatpak, runtime, and Steam Deck checks is:

```text
docs/Guides/LINUX_FLATPAK_SMOKE_CHECKLIST.md
```

The SSH-assisted Steam Deck copy/install/log workflow is:

```text
docs/Guides/STEAM_DECK_REMOTE_TESTING.md
```

## Install

```sh
flatpak install --user --reinstall --bundle \
  build.flatpak/dev.uzver.Movian.flatpak

flatpak run --user dev.uzver.Movian
flatpak info --user dev.uzver.Movian
flatpak info --user --show-permissions dev.uzver.Movian
```

## Manifest Notes

The manifest disables the hidden GLW recorder:

```text
--disable-glw-rec
```

That keeps the release-oriented Flatpak from writing large debug
`capture.mkv` files. The `Alt+F12` recorder hotkey is a no-op in this build.

The manifest enables bundled FFmpeg protocol helpers:

```text
--enable-version3
--enable-gmp
--enable-gnutls
```

`gmp` enables FFmpeg's RTMPE crypt helper, while `gnutls` enables FFmpeg TLS
and HTTPS protocols. `--enable-version3` is required by FFmpeg when `gmp` is
enabled. Together these flags allow native FFmpeg support for `rtmpe://`,
`rtmps://`, `rtmpte://`, and `rtmpts://` without enabling the old external
`librtmp` backend.

The package persists Movian's legacy state and cache:

```text
--persist=.hts
--persist=.cache/movian
```

Avahi service discovery uses the host daemon over the system D-Bus:

```text
--system-talk-name=org.freedesktop.Avahi
```

This allows `_smb._tcp` servers to appear under Local network without
granting broader system bus access.

Flatpak 1.14 does not accept Linux capability finish args such as
`--cap-add=NET_BIND_SERVICE`; `flatpak build-finish --cap-add=...` fails with
`Unknown option`. Do not use Docker/Podman capability syntax in this manifest.
For SMB2 server Windows acceptance, keep TCP `445` exposure as a separate
packaging or host-forwarding problem from high-port request-handler smokes.

AppStream metadata is generated from `dev.uzver.Movian.metainfo.xml.in`
during the build. The version comes from:

```sh
git describe --dirty --abbrev=5 | sed -e 's/-/./g'
```

That keeps Discover, `flatpak info`, and Movian's About/log version aligned.

The app icon is installed as:

```text
/app/share/icons/hicolor/256x256/apps/dev.uzver.Movian.png
```
