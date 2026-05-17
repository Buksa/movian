# Movian M7 Flatpak

This is a local/sideload Flatpak manifest for GLW-only Movian M7 testing.
It is not a Flathub-ready manifest yet.

The manifest intentionally configures Movian with:

```sh
--disable-gu --disable-webkit --disable-dvd --disable-librtmp
```

That keeps the package out of the legacy GTK/GU path and avoids GTK2 runtime
dependencies. The Flatpak SDK OpenSSL package is used instead of bundled
PolarSSL. RTMP is disabled for this first Flatpak MVP because bundled rtmpdump
needs OpenSSL 1.x internals that are opaque in OpenSSL 3.
The public WSL GLX compatibility path is runtime-detected and does not need a
SteamOS-specific configure flag.

Bundled libav is also configured with `--disable-inline-asm` and
`--disable-hwaccels` so the old bundled libav stays portable in the Flatpak SDK.
The manifest adds `LIBAV_CFLAGS=-Wno-error=incompatible-pointer-types` for the
old libav snapshot under the newer Freedesktop SDK compiler.

The Flatpak build installs `$PWD/build.flatpak/movian.bundle` as
`/app/bin/showtime`. That binary carries the Movian resource bundle and does
not need a separate `make install` target. The manifest removes any copied
`build.flatpak` directory first, because local `type: dir` sources can otherwise
carry stale absolute build paths into the sandbox.

The sandbox persists Movian's legacy home-relative state directories:

```text
~/.hts/showtime
~/.cache/movian
```

This is important for installed plugins, settings, metadata, logs and image
cache. Installed plugin ZIPs are stored under
`~/.hts/showtime/installedplugins` inside Movian.

## Build

From the repository root:

```sh
support/flatpak/build-local.sh
```

Expected artifact:

```text
build.flatpak/dev.uzver.MovianM7.flatpak
```

## Run

```sh
flatpak install --user --reinstall --bundle build.flatpak/dev.uzver.MovianM7.flatpak
flatpak run dev.uzver.MovianM7
```

For Steam Deck Gaming Mode, prefer the fullscreen desktop entry or copy
`support/flatpak/steam-deck-gamemode-launcher.sh` to the Deck and add that
script as a Non-Steam Game. It writes diagnostics to
`~/movian-m7-gamemode.log`.
