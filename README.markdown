# Movian Public Fork

Movian is a media player for plugins, streams, and local files. This repository
is a clean public fork based on `andoma/movian:movian6`.

The goal of this fork is to keep changes small, reviewable, and based on public
upstream source.

## Current Public Baseline

The `movian6` branch includes:

- modern GCC and Ubuntu build fixes;
- a reproducible Linux debug configure helper;
- raw screenshot HTTP API support;
- WSL2 GLX runtime compatibility;
- WebP probing, loading, decoding, and `/api/image` content type support;
- bundled FFmpeg 4.4.7 media backend with existing `libav` option names kept
  for compatibility;
- local SteamOS/Steam Deck Flatpak packaging;
- a documented plugin debug workflow;
- documented public-branch patterns for build, packaging, media, and smoke
  work.

## Feature Branches

Each feature is also published as its own branch, extracted from `movian6` as a
readable commit series.

Most start from the tag `clean-base-2026-08` — the shared ancestor plus the four
build commits that make it compile on current toolchains: GCC 14 (Debian 13,
Fedora 40+, Ubuntu 24.10) and the Flatpak packaging fix. Nothing under `src/`
differs between that tag and its parent, so those series apply to a plain tree.

**Two branches start earlier**, because the work they carry predates the tag.
Each branch's own start point is listed below; use it, not the tag:

```sh
git log --oneline clean-base-2026-08..feature/wsd-discovery
git format-patch cf2f66900..feature/rtmp
```

| Branch | Commits | Starts at | What it adds |
|---|---|---|---|
| `feature/smb` | 12 | `clean-base-2026-08` | SMB2 client: libsmb2 backend, pooled sessions, name resolution |
| `feature/smb-server` | 21 | `clean-base-2026-08` | Built-in SMB2 server — **includes `feature/smb`** |
| `feature/wsd-discovery` | 14 | `clean-base-2026-08` | WS-Discovery client for Windows hosts — **includes `feature/smb`** |
| `fix/glw-shutdown` | 1 | `clean-base-2026-08` | Removes a duplicate GLW thread spawn at shutdown |
| `devtools-mdev` | 7 | `clean-base-2026-08` | `mdev` harness: isolated launch, routes, screenshots, props |
| `devtools-lsp` | 30 | `clean-base-2026-08` | JavaScript language server for plugin authoring |
| `devtools-analyze` | 6 | `clean-base-2026-08` | `movian-analyze` static checker for views and plugin JS |
| `devtools-lifecycle` | 20 | `clean-base-2026-08` | GDB-backed startup/shutdown lifecycle inventory |
| `plugin-api` | 9 | `clean-base-2026-08` | Generated TypeScript declarations for the plugin API |
| `plugin-runtime-api` | 2 | `clean-base-2026-08` | Filesystem helpers and per-handle ACLs for plugin JS |
| `feature/core-http-prop` | 12 | `08a5f0601` | Core HTTP and property surface: non-creating lookups, indexed `prop_findv`, keypress semantics, raw screenshot API |
| `feature/rtmp` | 11 | `cf2f66900` | RTMP/RTMPS over FFmpeg: fileaccess backend, smoke helpers, Linux and Flatpak wiring |
| `feature/flatpak-steamos` | 5 | `9137bc64c` | SteamOS Flatpak packaging, Avahi discovery, Steam Deck remote testing guide |
| `feature/glw-recorder` | 4 | `622291d52` | GLW recorder: output cleanup, Flatpak opt-out, release policy |
| `feature/ffmpeg-backend` | 3 | `50d5955ce` | Bundled media backend moved to FFmpeg 4.4.7 with `libav` option names kept |
| `feature/html-parser` | 2 | `639371a02` | DOM-style aliases for the bundled HTML parser |
| `feature/wsl2-glx` | 1 | `275c334a1` | GLX context creation under WSL2 |
| `feature/steam-launch` | 1 | `f3f316fbd` | Skips the X11 fullscreen override path when launched from Steam |
| `feature/plugin-examples` | 3 | `4f8796552` | Twelve worked apiversion-2 plugin examples, run against a live instance |

### Two branches are stacked

`feature/smb-server` and `feature/wsd-discovery` both contain the whole of
`feature/smb` — the server and the discovery client are built on the SMB2
client and do not stand alone. Taking either one brings the client with it.
If you only want the client, take `feature/smb`.

Everything else in the table is independent and can be taken on its own.

### Taking part of a series

Commits within a series build on each other, so a series can be cut short but
not cherry-picked apart: `feature/smb` commit 9 rewrites what commit 4 set up,
and commit 1 adds the `libsmb2` submodule that the rest needs. Take a prefix —
`0001` through `0006` gives a working read-only SMB2 client — or take the whole
series.

### A note on overlapping files

A few files are touched by more than one feature, so a branch does not always
hold the same version of them as `movian6`. `src/api/httpcontrol.c` for instance
carries the keypress work in `feature/core-http-prop` and the WebP content type
in `movian6`. Each branch holds its own feature and nothing else, which is the
point — but it means a whole-file copy from one branch is not a safe substitute
for applying its patches.


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

## Plugin Debug Workflow

For plugin development, use the direct debug binary instead of a packaged app:

```sh
PLUGIN_PATH=/path/to/plugin \
START_URL=plugin:start \
EXPECTED_TITLE="Plugin Title" \
support/plugin-smoke/run-plugin-smoke.sh
```

The runner starts Movian with an isolated profile, opens the requested route
through the HTTP API, checks page state, captures logs, and attempts a raw
screenshot.

More details:

- `docs/Guides/PLUGIN_DEBUG_WORKFLOW.md`
- `support/plugin-smoke/run-plugin-smoke.sh`

## Runtime Features In This Stack

- WSL2 GLX handling is detected at runtime; there is no WSL configure option.
- `/api/screenshot/raw` returns a PNG directly.
- `/api/screenshot?raw=1` and `/api/screenshot?raw=true` use the same raw PNG
  path.
- Existing `/api/screenshot` upload behavior is preserved.
- WebP images are recognized by RIFF/WEBP magic, decoded through FFmpeg, and
  served from `/api/image` as `image/webp`.
- Existing command-line names such as `--libav-log` are preserved while the
  bundled backend uses FFmpeg 4.4.7.
- Flatpak builds use FFmpeg's RTMP-family protocols through SDK `gmp` and
  `gnutls`, while the old external `librtmp` backend remains disabled there.
- Steam launches avoid the X11 fullscreen path that can bounce back to the
  Steam loading screen.
- The hidden GLW recorder is treated as a developer/debug aid. Linux debug
  builds keep it enabled by default; release and Flatpak builds disable it
  unless `--enable-glw-rec` is passed explicitly.

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

The GLW recorder hotkey is disabled in the Flatpak package. Use
`/api/screenshot/raw` or a direct debug build for runtime smoke/debug captures.

More details:

- `docs/README.md`
- `docs/Guides/BUNDLED_MODULE_UPDATE_PLAN.md`
- `docs/Guides/PUBLIC_WORK_PATTERNS.md`
- `docs/Guides/LINUX_FLATPAK_SMOKE_CHECKLIST.md`
- `docs/Guides/FLATPAK_STEAMOS_GUIDE.md`
- `docs/Guides/PLUGIN_DEBUG_WORKFLOW.md`
- `support/flatpak/README.md`

## Project Scope

This fork is developed from public upstream source. New behavior should be
implemented as small patches on top of that public base.

Out of scope for the current stack:

- Flathub-ready pinned source archives and hashes.
- VAAPI or other hardware decode paths.
- Native `/dev/input/event*` controller input in the Flatpak sandbox.
- DVD support in the Flatpak profile.
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
