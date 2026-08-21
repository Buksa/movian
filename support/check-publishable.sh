#!/usr/bin/env bash
# Report machine-specific detail in a change and in its commit messages,
# before either becomes public. See AGENTS.md, "Before Publishing, Scan The
# Diff", for why this exists and how to convince yourself it works.
#
# Usage: support/check-publishable.sh <base-ref>
# Exit 0 when nothing is found, 1 when something is.
set -uo pipefail
base=${1:?usage: check-publishable.sh <base-ref>}

# Built at runtime so this file does not match its own pattern -- a check that
# always reports two known hits is a check people learn to skim past.
pattern="([0-9]{1,3}[.]){3}[0-9]{1,3}|/home/[a-z]+/|~/[.]ssh/|"'file'":///"

# Loopback and the wildcard bind are not machine-specific: they mean the same
# thing on every machine and appear legitimately in code. Excluding them is
# what keeps a hit meaningful -- the first real run of this script flagged
# `http://127.0.0.1:%d` in the harness, and a check with a standing false
# positive is one people stop reading.
benign="127[.]0[.]0[.]1|0[.]0[.]0[.]0|255[.]255[.]255[.]255"

scan() { grep -nHE "$pattern" -- "$1" | grep -vE "$benign"; }

found=0
while IFS= read -r f; do
  [ -f "$f" ] || continue
  if scan "$f"; then found=1; fi
done < <(git diff --name-only "$base"...HEAD)

if git log "$base"..HEAD --format=%B | grep -nE "$pattern" | grep -vE "$benign"; then
  echo "  ^ in a commit message" >&2
  found=1
fi

if [ "$found" -eq 0 ]; then
  echo "check-publishable: nothing machine-specific in the diff or the messages"
fi
exit "$found"
