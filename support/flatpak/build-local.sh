#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)
log_dir="$repo_root/build.flatpak"
log_file="$log_dir/flatpak-build.log"
repo_dir="$log_dir/repo"
state_dir="$log_dir/state"
bundle_file="$log_dir/dev.uzver.Movian.flatpak"
app_dir="$repo_root/build.flatpak-builder"
version_override_file="$repo_root/.movian-version-override"

mkdir -p "$log_dir" "$repo_dir" "$state_dir"
rm -f "$bundle_file"

# `git describe --dirty` cannot run inside the sandboxed build: the module's
# `sources: type: dir, path: ../..` copy carries submodule .git gitlinks
# (relative paths computed for *this* checkout's location) into a copy at a
# different path, so the copy's dirty-check ("is a submodule modified?")
# fails with "fatal: not a git repository: ...". Compute the version here,
# on the host, where git has the real repository, and drop it in a file the
# copied source tree will contain so the sandboxed build (gitver.mk and the
# manifest's metainfo step) can read it instead of re-running git.
movian_version=$(cd "$repo_root" && git describe --dirty --abbrev=5 2>/dev/null || true)
if [ -z "$movian_version" ]; then
  echo "build-local.sh: 'git describe --dirty --abbrev=5' produced nothing in $repo_root -- refusing to build a versionless package." >&2
  exit 1
fi
echo "$movian_version" > "$version_override_file"
trap 'rm -f "$version_override_file"' EXIT
echo "Host-computed version: $movian_version"

cd "$script_dir"

{
  flatpak-builder \
    --user \
    --verbose \
    --keep-build-dirs \
    --force-clean \
    --state-dir="$state_dir" \
    --repo="$repo_dir" \
    --install-deps-from=flathub \
    "$app_dir" \
    dev.uzver.Movian.yml

  flatpak build-bundle \
    "$repo_dir" \
    "$bundle_file" \
    dev.uzver.Movian
} 2>&1 | tee "$log_file"

# Smoke-check the entrypoint the manifest's `command:` resolves to.
# flatpak-builder reports success purely from build-commands' exit codes; it
# has no idea whether `make`'s output target was actually relinked or was a
# stale/corrupt file `make` decided (from mtimes alone) needed no rebuild --
# which is exactly how a broken /app/bin/showtime shipped before (see
# Buksa/movian#64). Fail loudly here instead of shipping it.
entrypoint="$app_dir/files/bin/showtime"
if [ ! -s "$entrypoint" ]; then
  echo "build-local.sh: FAIL -- $entrypoint is missing or zero-length; refusing to ship a broken bundle." >&2
  exit 1
fi
if [ ! -x "$entrypoint" ]; then
  echo "build-local.sh: FAIL -- $entrypoint is not executable; refusing to ship a broken bundle." >&2
  exit 1
fi

echo "Smoke check: flatpak build $app_dir /app/bin/showtime --help"
if ! help_output=$(flatpak build "$app_dir" /app/bin/showtime --help 2>&1); then
  echo "build-local.sh: FAIL -- entrypoint smoke check failed:" >&2
  echo "$help_output" >&2
  exit 1
fi
echo "$help_output" | head -n1

metainfo_file="$app_dir/files/share/metainfo/dev.uzver.Movian.metainfo.xml"
metainfo_version=$(sed -n 's/.*<release version="\([^"]*\)".*/\1/p' "$metainfo_file" 2>/dev/null | head -n1)
if [ -z "$metainfo_version" ] || [ "$metainfo_version" = "0.0.0" ]; then
  echo "build-local.sh: FAIL -- $metainfo_file reports version '$metainfo_version'; version computation failed." >&2
  exit 1
fi
echo "Metainfo version: $metainfo_version"

echo "OK: entrypoint and version smoke checks passed."
