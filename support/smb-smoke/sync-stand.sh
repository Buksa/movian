#!/usr/bin/env bash
#
# Force-sync a test-stand checkout to the remote branch and rebuild.
#
# The stand is a build/run target, not a place to keep state: agents scp
# work-in-progress files onto it, verification runs leave logs, and manual
# experiments drift it away from the dev workspace. This script makes
# convergence a one-liner -- run it ON the stand (or via ssh) and the
# checkout ends up byte-identical to origin/<branch>, rebuilt and sane.
#
#   support/smb-smoke/sync-stand.sh [--branch BRANCH] [--enable-dvd]
#                                   [--no-build]
#
# DANGER: discards ALL local changes (tracked and untracked) in the
# checkout, except paths listed in KEEP below. Anything worth keeping must
# be committed and pushed from the dev workspace first -- that is the
# whole point.
#
# DVD support is configured out by default: the vendored ext/dvd code does
# not compile under GCC >= 14 (default-error promotion of
# implicit-function-declaration / incompatible-pointer-types; see
# Buksa/movian#68) and an SMB test stand has no use for it. Pass
# --enable-dvd on a stand that actually needs it (and an older compiler).

set -euo pipefail

BRANCH=""
DO_BUILD=1
DVD_FLAG="--disable-dvd"

# Untracked paths that survive the clean (site-local state, never committed).
KEEP=(-e .codex)

while [ $# -gt 0 ]; do
  case "$1" in
    --branch)     BRANCH="$2"; shift 2 ;;
    --enable-dvd) DVD_FLAG=""; shift ;;
    --no-build)   DO_BUILD=0; shift ;;
    -h|--help)    sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

TOPDIR=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$TOPDIR"

[ -n "$BRANCH" ] || BRANCH=$(git rev-parse --abbrev-ref HEAD)

log() { printf '\n=== %s ===\n' "$*"; }

log "Fetching origin/$BRANCH"
git fetch origin "$BRANCH"

log "Resetting to origin/$BRANCH (discarding local changes)"
git checkout -q "$BRANCH"
git reset --hard "origin/$BRANCH"

log "Cleaning untracked files (keeping: ${KEEP[*]})"
git clean -fd "${KEEP[@]}"

log "Syncing submodules to the committed gitlinks"
git submodule sync --recursive >/dev/null
git submodule update --init --recursive
git submodule foreach --recursive 'git reset --hard -q && git clean -fdq'

log "State"
echo "HEAD: $(git rev-parse HEAD)"
git status --short || true

if [ "$DO_BUILD" = 1 ]; then
  log "Configuring debug build ${DVD_FLAG:+($DVD_FLAG)}"
  # shellcheck disable=SC2086
  ./support/configure-linux-debug.sh $DVD_FLAG
  log "Building (make BUILD=debug -j$(nproc))"
  make BUILD=debug -j"$(nproc)"
  log "Binary sanity check"
  ./build.debug/movian --help | head -n 1
else
  log "Skipping build (--no-build)"
fi

log "Stand in sync with origin/$BRANCH"
