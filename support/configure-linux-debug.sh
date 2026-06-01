#!/bin/sh
#
# Configure a Linux debug build with defaults that work on newer Ubuntu and
# WSL installs. The old bundled librtmp backend is disabled by default so RTMP
# playback uses the FFmpeg-backed path, matching the Flatpak direction for
# ordinary RTMP smoke tests.

set -eu

TOPDIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$TOPDIR"

exec ./configure.linux \
  --build=debug \
  --disable-vdpau \
  --enable-polarssl \
  --disable-librtmp \
  "$@"
