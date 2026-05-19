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

## Validate

```sh
desktop-file-validate \
  support/flatpak/dev.uzver.Movian.desktop \
  support/flatpak/dev.uzver.Movian.GameMode.desktop

appstreamcli validate --no-net \
  build.flatpak-builder/files/share/metainfo/dev.uzver.Movian.metainfo.xml

flatpak build build.flatpak-builder /app/bin/showtime --help

flatpak build build.flatpak-builder ldd /app/bin/showtime | \
  grep -Ei 'gtk|gdk|webkit|rtmp|dvd|vaapi|vdpau|libva|nvidia' || true
```

The dependency grep should be empty for this MVP profile.

## Install

```sh
flatpak install --user --reinstall --bundle \
  build.flatpak/dev.uzver.Movian.flatpak

flatpak run --user dev.uzver.Movian
flatpak info --user dev.uzver.Movian
flatpak info --user --show-permissions dev.uzver.Movian
```

## Manifest Notes

The package persists Movian's legacy state and cache:

```text
--persist=.hts
--persist=.cache/movian
```

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
