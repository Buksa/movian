#!/usr/bin/env bash

set -euo pipefail

ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "Not inside a Git repository" >&2
  exit 1
}
STATE_DIR="${ROOT}/.codex"
cd "${ROOT}"

codegraph_cmd() {
  if command -v codegraph >/dev/null 2>&1; then
    command -v codegraph
    return
  fi

  [[ -d "${HOME}/.nvm/versions/node" ]] || return
  find "${HOME}/.nvm/versions/node" \
    \( -path '*/bin/codegraph' -type l -o -path '*/bin/codegraph' -type f \) \
    2>/dev/null | sort -V | tail -1
}

sanitize_remote_url() {
  printf '%s\n' "$1" |
    sed -E 's#^([[:alpha:]][[:alnum:]+.-]*://)[^/@]*@#\1#'
}

print_state() {
  local upstream origin
  upstream=$(git rev-parse --abbrev-ref '@{upstream}' 2>/dev/null || echo none)
  origin=$(git remote get-url origin 2>/dev/null || echo none)
  origin=$(sanitize_remote_url "${origin}")

  echo "Repository: ${ROOT}"
  echo "Branch:     $(git branch --show-current)"
  echo "HEAD:       $(git rev-parse HEAD)"
  echo "Upstream:   ${upstream}"
  echo "Origin:     ${origin}"
  git status --short --branch
}

check() {
  print_state

  local current recorded codegraph
  current=$(git rev-parse HEAD)
  recorded=$(sed -n 's/^- HEAD: //p' "${STATE_DIR}/STATE.md" 2>/dev/null |
    head -1 || true)
  if [[ "${recorded}" == "${current}" ]]; then
    echo "Local state HEAD: current"
  else
    echo "Local state HEAD: stale or missing; run '$0 refresh'"
  fi

  codegraph=$(codegraph_cmd || true)
  if [[ -n "${codegraph}" && -d "${ROOT}/.codegraph" ]]; then
    "${codegraph}" status "${ROOT}"
  elif [[ -d "${ROOT}/.codegraph" ]]; then
    echo "CodeGraph: index exists, CLI not found"
  else
    echo "CodeGraph: not initialized"
  fi
}

refresh() {
  local generated branch head upstream origin merge status commits recovery
  mkdir -p "${STATE_DIR}"

  generated=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
  branch=$(git branch --show-current)
  head=$(git rev-parse HEAD)
  upstream=$(git rev-parse --abbrev-ref '@{upstream}' 2>/dev/null || echo none)
  origin=$(git remote get-url origin 2>/dev/null || echo none)
  origin=$(sanitize_remote_url "${origin}")
  merge=$(git log --merges -1 --format='%H %s' 2>/dev/null || echo none)
  status=$(git status --short)
  commits=$(git log --oneline --decorate -8)
  if [[ -x "${ROOT}/.codex/context.sh" ]]; then
    recovery=".codex/context.sh"
  else
    recovery="support/codex/context.sh"
  fi

  {
    echo "# Repository State"
    echo
    echo "- Generated: ${generated}"
    echo "- Branch: ${branch}"
    echo "- HEAD: ${head}"
    echo "- Upstream: ${upstream}"
    echo "- Origin: ${origin}"
    echo "- Latest merge: ${merge:-none}"
    echo
    echo "## Working Tree"
    echo
    printf '%s\n' "${status:-Clean.}"
    echo
    echo "## Recent Commits"
    echo
    printf '%s\n' "${commits}"
  } >"${STATE_DIR}/STATE.md"

  {
    echo "# Session Handoff"
    echo
    echo "Generated from Git on ${generated}."
    echo
    echo "## Resume"
    echo
    echo "1. Read AGENTS.md."
    echo "2. Run ${recovery} doctor."
    echo "3. Read .codex/STATE.md and inspect git diff."
    echo "4. Use CodeGraph before broad code exploration."
    echo
    echo "## Current Git Facts"
    echo
    echo "- Branch: ${branch}"
    echo "- HEAD: ${head}"
    echo "- Latest merge: ${merge:-none}"
  } >"${STATE_DIR}/HANDOFF.md"

  echo "Updated ${STATE_DIR}/STATE.md and ${STATE_DIR}/HANDOFF.md"
}

doctor() {
  local failed=0 codegraph pattern
  check

  [[ -f AGENTS.md ]] || { echo "Missing AGENTS.md" >&2; failed=1; }
  [[ -x support/configure-linux-debug.sh ]] ||
    { echo "Missing Linux build helper" >&2; failed=1; }

  for pattern in '/.codex/' '/.codegraph/' '/.codex-session.md'; do
    grep -Fxq "${pattern}" .gitignore ||
      { echo "Missing ignore rule: ${pattern}" >&2; failed=1; }
  done

  codegraph=$(codegraph_cmd || true)
  [[ -n "${codegraph}" ]] ||
    { echo "CodeGraph CLI not found" >&2; failed=1; }
  [[ -d .codegraph ]] ||
    { echo "CodeGraph index not initialized" >&2; failed=1; }

  ((failed == 0)) || return 1
  echo "Doctor: OK"
}

case "${1:-}" in
  check) check ;;
  refresh) refresh ;;
  doctor) doctor ;;
  *)
    echo "Usage: $0 check|refresh|doctor" >&2
    exit 2
    ;;
esac
