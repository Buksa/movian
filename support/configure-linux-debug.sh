#!/bin/sh
#
# Configure a Linux debug build with defaults that work on newer Ubuntu and
# WSL installs. The old bundled librtmp backend is disabled by default so RTMP
# playback uses FFmpeg's RTMP-family protocols, matching the Flatpak profile.

set -eu

TOPDIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$TOPDIR"

: "${LIBAV_COMMON_FLAGS:=--disable-x86asm --disable-inline-asm --disable-hwaccels --disable-vaapi --disable-vdpau --disable-cuda --disable-cuda-llvm --disable-cuvid --disable-ffnvcodec --disable-nvdec --disable-v4l2-m2m --enable-version3 --enable-gmp --enable-gnutls}"
export LIBAV_COMMON_FLAGS

if ! printf '%s\n' '#include <gmp.h>' 'int main(void) { return 0; }' |
    cc -x c - -lgmp -o /tmp/movian-gmp-check.$$ >/dev/null 2>&1; then
  rm -f /tmp/movian-gmp-check.$$
  echo "Missing libgmp development headers. Install libgmp-dev." >&2
  exit 1
fi
rm -f /tmp/movian-gmp-check.$$

if ! pkg-config --exists gnutls; then
  echo "Missing GnuTLS development metadata. Install libgnutls28-dev." >&2
  exit 1
fi

exec ./configure.linux \
  --build=debug \
  --disable-vdpau \
  --enable-polarssl \
  --disable-librtmp \
  "$@"
