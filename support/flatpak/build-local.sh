#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)
log_dir="$repo_root/build.flatpak"
log_file="$log_dir/flatpak-build.log"
repo_dir="$log_dir/repo"
state_dir="$log_dir/state"
bundle_file="$log_dir/dev.uzver.Movian.flatpak"

mkdir -p "$log_dir" "$repo_dir" "$state_dir"
rm -f "$bundle_file"

builder_keep_args=()
if [ "${FLATPAK_KEEP_BUILD_DIRS:-0}" = "1" ]; then
  builder_keep_args+=(--keep-build-dirs)
else
  rm -rf "$state_dir/build"
fi

cd "$script_dir"

{
  flatpak-builder \
    --user \
    --verbose \
    "${builder_keep_args[@]}" \
    --force-clean \
    --state-dir="$state_dir" \
    --repo="$repo_dir" \
    --install-deps-from=flathub \
    "$repo_root/build.flatpak-builder" \
    dev.uzver.Movian.yml

  flatpak build-bundle \
    "$repo_dir" \
    "$bundle_file" \
    dev.uzver.Movian
} 2>&1 | tee "$log_file"
