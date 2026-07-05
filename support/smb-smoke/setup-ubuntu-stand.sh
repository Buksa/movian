#!/usr/bin/env bash
#
# Provision a fresh Ubuntu (or Debian) machine as a Movian SMB2 test stand:
# install the Movian build dependencies plus the tools the smb-smoke runners
# need, fetch/build the repo, and sanity-check the binary.
#
# Idempotent: safe to re-run. Does not touch credentials or configure any SMB
# target -- that stays site-specific (see docs/Guides/UBUNTU_TEST_STAND.md).
#
# Usage:
#   support/smb-smoke/setup-ubuntu-stand.sh [--repo-dir DIR] [--branch BRANCH]
#                                           [--no-build] [--no-apt]
#
# Run from an existing checkout, or standalone (it will clone into --repo-dir,
# default ~/GitHub/movian).

set -euo pipefail

REPO_URL="${MOVIAN_REPO_URL:-https://github.com/Buksa/movian.git}"
REPO_DIR="${HOME}/GitHub/movian"
BRANCH=""
DO_BUILD=1
DO_APT=1

while [ $# -gt 0 ]; do
  case "$1" in
    --repo-dir) REPO_DIR="$2"; shift 2 ;;
    --branch)   BRANCH="$2"; shift 2 ;;
    --no-build) DO_BUILD=0; shift ;;
    --no-apt)   DO_APT=0; shift ;;
    -h|--help)
      sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

log() { printf '\n=== %s ===\n' "$*"; }

# --- 1. system packages -----------------------------------------------------
# Build deps come from README.markdown + support/configure-linux-debug.sh;
# the last block adds the SMB2 test-stand tooling on top of a plain build box.
APT_PKGS=(
  # Movian Linux build toolchain
  build-essential pkg-config git curl yasm python-is-python3 cmake
  # Movian library deps (configure.linux pkg-config checks)
  libsqlite3-dev libfreetype6-dev libfontconfig1-dev
  libx11-dev libxext-dev libgl1-mesa-dev libpulse-dev libssl-dev
  libavahi-client-dev libxss-dev libxxf86vm-dev
  # FFmpeg RTMP-family TLS/crypto (configure-linux-debug.sh requires these)
  libgmp-dev libgnutls28-dev
  # SMB2 test-stand tooling
  smbclient samba-common-bin gdb xvfb
)

if [ "$DO_APT" = 1 ]; then
  log "Installing packages (sudo apt-get)"
  sudo apt-get update
  sudo apt-get install -y "${APT_PKGS[@]}"
else
  log "Skipping apt (--no-apt); assuming build deps are present"
fi

# --- 2. repository ----------------------------------------------------------
if [ -d "$REPO_DIR/.git" ]; then
  log "Using existing checkout: $REPO_DIR"
else
  log "Cloning $REPO_URL -> $REPO_DIR"
  mkdir -p "$(dirname "$REPO_DIR")"
  git clone "$REPO_URL" "$REPO_DIR"
fi

cd "$REPO_DIR"
if [ -n "$BRANCH" ]; then
  log "Checking out branch $BRANCH"
  git fetch origin "$BRANCH"
  git checkout "$BRANCH"
fi

log "Syncing submodules (ext/libsmb2, ext/libav, ...)"
git submodule update --init --recursive

# --- 3. build ---------------------------------------------------------------
if [ "$DO_BUILD" = 1 ]; then
  log "Configuring debug build"
  ./support/configure-linux-debug.sh
  log "Building (make BUILD=debug -j$(nproc))"
  make BUILD=debug -j"$(nproc)"
  log "Binary sanity check"
  ./build.debug/movian --help | head -n 1
else
  log "Skipping build (--no-build)"
fi

# --- 4. report --------------------------------------------------------------
log "Test-stand tool versions"
for t in smbclient nmblookup gdb xvfb-run cmake; do
  if command -v "$t" >/dev/null 2>&1; then
    printf '  %-10s %s\n' "$t" "$("$t" --version 2>&1 | head -n1)"
  else
    printf '  %-10s MISSING\n' "$t"
  fi
done

cat <<EOF

Stand ready at: $REPO_DIR

Next (site-specific, NOT done by this script):
  * Point smb-smoke at your SMB server via .codex/smb-smoke.env
    (SMB_SMOKE_HOST/SHARE/PATH/USER/PASSWORD/DOMAIN) -- git-ignored, never commit.
  * For the SMB2 CLIENT smoke against a real server, see
    docs/Guides/UBUNTU_TEST_STAND.md (keyring pre-seed + headless launch).
  * Headless GLW runs need a display: use 'xvfb-run -a ...' or DISPLAY=:0.
EOF
