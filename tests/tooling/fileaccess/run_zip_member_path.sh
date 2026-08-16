#!/bin/sh
set -eu

TOPDIR=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
TESTDIR=$(mktemp -d "${TMPDIR:-/tmp}/movian-zip-path.XXXXXX")
trap 'rm -rf "$TESTDIR"' EXIT HUP INT TERM

${CC:-cc} -std=c11 -Wall -Wextra -Werror \
  -I"$TOPDIR/src/fileaccess" \
  "$TOPDIR/src/fileaccess/fa_zip_path.c" \
  "$TOPDIR/tests/tooling/fileaccess/test_zip_member_path.c" \
  -o "$TESTDIR/test_zip_member_path"

"$TESTDIR/test_zip_member_path"
