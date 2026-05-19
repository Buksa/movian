# Movian Public Fork

Movian is a media player for plugins, streams, and local files. This repository
is a clean public fork based on `andoma/movian:movian6`.

The goal of this fork is to keep changes small, reviewable, and based on public
upstream source. Changes are developed as a stacked set of branches on top of
the upstream `movian6` branch.

## Current Branch Stack

- `movian6` - clean upstream baseline from `andoma/movian:movian6`.
- `public/gcc-modernize` - modern GCC and Ubuntu build fixes.
- `public/linux-build-cleanup` - reproducible Linux debug configure helper and
  build notes.
- `public/screenshot-api` - raw screenshot HTTP API plus WSL2 GLX runtime
  compatibility.
- `public/webp-image-support` - WebP probing, loading, decoding, and
  `/api/image` content type support.
- `public/flatpak-steamos` - local SteamOS/Steam Deck Flatpak packaging and
  small Linux runtime fixes used by that package.

## Linux Debug Build

Install the usual build tools and development headers. Package names vary by
distribution; on Ubuntu the useful starting point is:

```sh
sudo apt-get update
sudo apt-get install -y \
  build-essential \
  pkg-config \
  git \
  curl \
  yasm \
  python-is-python3 \
  libsqlite3-dev \
  libfreetype6-dev \
  libfontconfig1-dev \
  libx11-dev \
  libxext-dev \
  libgl1-mesa-dev \
  libpulse-dev \
  libssl-dev \
  libavahi-client-dev \
  libxss-dev \
  libxxf86vm-dev
```

Configure and build:

```sh
./support/configure-linux-debug.sh
make BUILD=debug -j$(nproc)
./build.debug/movian --help
```

The helper runs:

```sh
./configure.linux --build=debug --disable-vdpau --enable-polarssl
```

Extra configure flags can be appended. For example, if your distribution no
longer packages legacy WebKitGTK development headers:

```sh
./support/configure-linux-debug.sh --disable-webkit
```

The debug binary is written to:

```text
build.debug/movian
```

Movian stores legacy settings under:

```text
~/.hts/showtime
```

## Runtime Features In This Stack

- WSL2 GLX handling is detected at runtime; there is no WSL configure option.
- `/api/screenshot/raw` returns a PNG directly.
- `/api/screenshot?raw=1` and `/api/screenshot?raw=true` use the same raw PNG
  path.
- Existing `/api/screenshot` upload behavior is preserved.
- WebP images are recognized by RIFF/WEBP magic, decoded through libav, and
  served from `/api/image` as `image/webp`.
- Steam launches avoid the X11 fullscreen path that can bounce back to the
  Steam loading screen.

## Flatpak / SteamOS

The Flatpak work is a local sideload package for SteamOS and Desktop Linux
testing. It is not a Flathub-ready manifest.

Build from the repository root:

```sh
support/flatpak/build-local.sh
```

The bundle is written to:

```text
build.flatpak/dev.uzver.Movian.flatpak
```

Install and run:

```sh
flatpak install --user --reinstall --bundle \
  build.flatpak/dev.uzver.Movian.flatpak

flatpak run --user dev.uzver.Movian
flatpak run --user --command=showtime dev.uzver.Movian --help
flatpak info --user dev.uzver.Movian
```

The package installs the bundled Movian binary as `/app/bin/showtime`, persists
legacy state with `--persist=.hts` and `--persist=.cache/movian`, generates
AppStream metadata from the current git version, and installs the PNG icon from
`res/showtime/showtime.png`.

More details:

- `docs/README.md`
- `docs/Guides/FLATPAK_STEAMOS_GUIDE.md`
- `support/flatpak/README.md`

## Project Scope

This fork is developed from public upstream source. New behavior should be
implemented as small patches on top of that public base.

Out of scope for the current stack:

- Flathub-ready pinned source archives and hashes.
- VAAPI or other hardware decode paths.
- Native `/dev/input/event*` controller input in the Flatpak sandbox.
- DVD and RTMP support in the Flatpak profile.
- Broad filesystem access beyond common read-only XDG media folders.

## Upstream

Original project:

```text
https://github.com/andoma/movian
```

Upstream branch used here:

```text
https://github.com/andoma/movian/tree/movian6
```

## License

Movian is distributed under the GNU General Public License version 3 or later.
See `LICENSE` for the full license text.
