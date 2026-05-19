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
    "$repo_root/build.flatpak-builder" \
    dev.uzver.Movian.yml

  flatpak build-bundle \
    "$repo_dir" \
    "$bundle_file" \
    dev.uzver.Movian
} 2>&1 | tee "$log_file"
