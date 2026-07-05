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

Before touching the bundle, the script computes `git describe --dirty
--abbrev=5` on the host and smoke-checks the result: it refuses to run if
that's empty (see "Manifest Notes" below), and after flatpak-builder
finishes it fails loudly if `/app/bin/showtime` is missing, zero-length,
non-executable, doesn't run under `flatpak build ... --help`, or if the
staged metainfo's version is empty/`0.0.0`. A "successful" flatpak-builder
run only means every build-command exited zero -- it does not mean `make`
actually relinked a good binary instead of reusing a stale/corrupt one left
over from an earlier interrupted attempt (see Buksa/movian#64), so
build-local.sh checks the real artifact itself rather than trusting the
exit code alone.

## Validate

The entrypoint and version checks above run automatically at the end of
`build-local.sh`. The following remain manual/optional:

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

AppStream metadata is generated from `dev.uzver.Movian.metainfo.xml.in`
during the build. The version comes from:

```sh
git describe --dirty --abbrev=5 | sed -e 's/-/./g'
```

`git describe --dirty` cannot run inside the sandboxed build itself: the
manifest's `sources: type: dir, path: ../..` copies the whole repo,
including every submodule's `.git` gitlink -- a path relative to *this*
checkout's location. Once copied elsewhere, those relative paths no longer
resolve, and `--dirty`'s "is a submodule modified?" check fails outright
(`fatal: not a git repository: ...`), which quietly collapsed the version to
empty (`support/gitver.mk`'s `VERSION_GIT`) or `0.0.0` (the metainfo
fallback) for every flatpak build. `build-local.sh` now runs `git describe`
on the host, before the sandboxed copy happens, and writes the result to a
git-ignored `.movian-version-override` file at the repo root; both
`support/gitver.mk` and the manifest's metainfo step prefer that file over
calling `git describe` themselves. That keeps Discover, `flatpak info`, and
Movian's About/log version aligned with the host's real `git describe`
output.

The app icon is installed as:

```text
/app/share/icons/hicolor/256x256/apps/dev.uzver.Movian.png
```
