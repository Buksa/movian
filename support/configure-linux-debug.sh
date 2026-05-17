#!/bin/sh
#
# Configure a Linux debug build with defaults that work on newer Ubuntu
# installs where VDPAU headers are often absent and OpenSSL is too new for
# bundled librtmp.

set -eu

TOPDIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$TOPDIR"

exec ./configure.linux --build=debug --disable-vdpau --enable-polarssl "$@"
