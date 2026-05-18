# Movian M7 Flatpak

This directory contains the local/sideload Flatpak packaging for Movian M7.
The manifest builds the GLW/X11/OpenGL UI and installs the bundled binary as
`/app/bin/showtime`.

## Files

- `dev.uzver.MovianM7.yml` - Flatpak manifest.
- `dev.uzver.MovianM7.desktop` - normal launcher.
- `dev.uzver.MovianM7.GameMode.desktop` - fullscreen launcher.
- `dev.uzver.MovianM7.metainfo.xml.in` - AppStream template.
- `build-local.sh` - local builder wrapper.
- `steam-deck-gamemode-launcher.sh` - optional host-side diagnostic launcher.

## Build

From the repository root:

```sh
support/flatpak/build-local.sh
```

The bundle is written to:

```text
build.flatpak/dev.uzver.MovianM7.flatpak
```

The build log is written to:

```text
build.flatpak/flatpak-build.log
```

## Validate

```sh
desktop-file-validate \
  support/flatpak/dev.uzver.MovianM7.desktop \
  support/flatpak/dev.uzver.MovianM7.GameMode.desktop

appstreamcli validate --no-net \
  build.flatpak-builder/files/share/metainfo/dev.uzver.MovianM7.metainfo.xml

flatpak build build.flatpak-builder /app/bin/showtime --help

flatpak build build.flatpak-builder ldd /app/bin/showtime | \
  grep -Ei 'gtk|gdk|webkit|rtmp|dvd|vaapi|vdpau|libva|nvidia' || true
```

The dependency grep should be empty for this MVP profile.

## Install

```sh
flatpak install --user --reinstall --bundle \
  build.flatpak/dev.uzver.MovianM7.flatpak

flatpak run --user dev.uzver.MovianM7
flatpak info --user dev.uzver.MovianM7
flatpak info --user --show-permissions dev.uzver.MovianM7
```

## Manifest Notes

The package persists Movian's legacy state and cache:

```text
--persist=.hts
--persist=.cache/movian
```

AppStream metadata is generated from `dev.uzver.MovianM7.metainfo.xml.in`
during the build. The version comes from:

```sh
git describe --dirty --abbrev=5 | sed -e 's/-/./g'
```

That keeps Discover, `flatpak info`, and Movian's About/log version aligned.
