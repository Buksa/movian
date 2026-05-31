#!/bin/sh
#
# Print the pinned submodule revisions and compare them with the configured
# upstream branch in .gitmodules. The script is read-only: it does not
# initialize, fetch, or update any submodule checkout.

set -eu

fail_on_outdated=0

case "${1:-}" in
  "")
    ;;
  --fail-on-outdated)
    fail_on_outdated=1
    ;;
  -h|--help)
    cat <<EOF
Usage: support/check-submodules.sh [--fail-on-outdated]

Compare submodule gitlinks in HEAD with their configured upstream branches.
By default this is an informational check and exits 0 even when a submodule is
outdated. Use --fail-on-outdated to make outdated or unresolved entries fail.
EOF
    exit 0
    ;;
  *)
    echo "Unknown option: $1" >&2
    echo "Try: support/check-submodules.sh --help" >&2
    exit 2
    ;;
esac

TOPDIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$TOPDIR"

if [ ! -f .gitmodules ]; then
  echo "No .gitmodules file found" >&2
  exit 1
fi

short_sha() {
  printf '%.12s' "$1"
}

status_code=0
entries=$(git config --file .gitmodules --get-regexp '^submodule\..*\.path$')

printf '%-28s %-14s %-12s %-12s %s\n' \
  "Submodule" "Branch" "Pinned" "Upstream" "Status"
printf '%-28s %-14s %-12s %-12s %s\n' \
  "---------" "------" "------" "--------" "------"

while read -r key path; do
  name=${key#submodule.}
  name=${name%.path}

  url=$(git config --file .gitmodules --get "submodule.$name.url")
  branch=$(git config --file .gitmodules --get "submodule.$name.branch" || true)

  if [ -z "$branch" ]; then
    branch=HEAD
    ref=HEAD
  else
    ref=refs/heads/$branch
  fi

  pinned=$(git ls-tree HEAD "$path" | awk '$1 == "160000" { print $3 }')
  upstream=$(git ls-remote "$url" "$ref" | awk 'NR == 1 { print $1 }')

  if [ -z "$pinned" ]; then
    status=missing-gitlink
    pinned_short=-
    upstream_short=$(short_sha "${upstream:--}")
    status_code=1
  elif [ -z "$upstream" ]; then
    status=missing-upstream
    pinned_short=$(short_sha "$pinned")
    upstream_short=-
    status_code=1
  elif [ "$pinned" = "$upstream" ]; then
    status=current
    pinned_short=$(short_sha "$pinned")
    upstream_short=$(short_sha "$upstream")
  else
    status=outdated
    pinned_short=$(short_sha "$pinned")
    upstream_short=$(short_sha "$upstream")
    status_code=1
  fi

  printf '%-28s %-14s %-12s %-12s %s\n' \
    "$path" "$branch" "$pinned_short" "$upstream_short" "$status"
done <<EOF
$entries
EOF

if [ "$fail_on_outdated" -eq 1 ]; then
  exit "$status_code"
fi

exit 0
