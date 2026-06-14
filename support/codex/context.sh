#!/usr/bin/env bash

set -euo pipefail

usage() {
  echo "Usage: $0 check|refresh|doctor" >&2
  exit 2
}

repo_root() {
  git rev-parse --show-toplevel 2>/dev/null
}

find_codegraph() {
  if command -v codegraph >/dev/null 2>&1; then
    command -v codegraph
    return
  fi

  local candidate
  candidate=$(find "${HOME}/.nvm/versions/node" \
    \( -path '*/bin/codegraph' -type l -o -path '*/bin/codegraph' -type f \) \
    2>/dev/null | sort -V | tail -1)
  if [[ -n "${candidate}" ]]; then
    printf '%s\n' "${candidate}"
  fi
}

git_value() {
  git "$@" 2>/dev/null || printf '%s\n' "(none)"
}

print_git_state() {
  local branch head upstream origin
  branch=$(git_value branch --show-current)
  head=$(git_value rev-parse HEAD)
  upstream=$(git_value rev-parse --abbrev-ref '@{upstream}')
  origin=$(git_value remote get-url origin)

  printf 'Repository: %s\n' "${ROOT}"
  printf 'Branch:     %s\n' "${branch}"
  printf 'HEAD:       %s\n' "${head}"
  printf 'Upstream:   %s\n' "${upstream}"
  printf 'Origin:     %s\n' "${origin}"
  git status --short --branch
}

check_state() {
  print_git_state

  if [[ -f "${STATE_DIR}/STATE.md" ]]; then
    local recorded current
    recorded=$(sed -n 's/^- HEAD: `\([^`]*\)`/\1/p' \
      "${STATE_DIR}/STATE.md" | head -1)
    current=$(git rev-parse HEAD)
    if [[ "${recorded}" == "${current}" ]]; then
      echo "Local state: current"
    else
      echo "Local state: stale; run '$0 refresh'"
    fi
  else
    echo "Local state: missing; run '$0 refresh'"
  fi

  local codegraph
  codegraph=$(find_codegraph || true)
  if [[ -n "${codegraph}" && -d "${ROOT}/.codegraph" ]]; then
    "${codegraph}" status "${ROOT}"
  elif [[ -d "${ROOT}/.codegraph" ]]; then
    echo "CodeGraph: index exists, CLI not found"
  else
    echo "CodeGraph: not initialized"
  fi
}

refresh_state() {
  mkdir -p "${STATE_DIR}"

  local generated branch head upstream origin merge status commits
  generated=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
  branch=$(git_value branch --show-current)
  head=$(git_value rev-parse HEAD)
  upstream=$(git_value rev-parse --abbrev-ref '@{upstream}')
  origin=$(git_value remote get-url origin)
  merge=$(git log --merges -1 --format='%H %s' 2>/dev/null || true)
  status=$(git status --short)
  commits=$(git log --oneline --decorate -8)

  {
    echo "# Repository State"
    echo
    echo "- Generated: \`${generated}\`"
    echo "- Branch: \`${branch}\`"
    echo "- HEAD: \`${head}\`"
    echo "- Upstream: \`${upstream}\`"
    echo "- Origin: \`${origin}\`"
    echo "- Latest merge: \`${merge:-none}\`"
    echo
    echo "## Working Tree"
    echo
    if [[ -n "${status}" ]]; then
      printf '```text\n%s\n```\n' "${status}"
    else
      echo "Clean."
    fi
    echo
    echo "## Recent Commits"
    echo
    printf '```text\n%s\n```\n' "${commits}"
  } >"${STATE_DIR}/STATE.md"

  {
    echo "# Session Handoff"
    echo
    echo "Generated from Git on \`${generated}\`."
    echo
    echo "## Resume"
    echo
    echo "1. Read \`AGENTS.md\`."
    echo "2. Run \`.codex/context.sh doctor\`."
    echo "3. Read \`.codex/STATE.md\` and inspect \`git diff\`."
    echo "4. Use CodeGraph before broad code exploration."
    echo
    echo "## Current Git Facts"
    echo
    echo "- Branch: \`${branch}\`"
    echo "- HEAD: \`${head}\`"
    echo "- Latest merge: \`${merge:-none}\`"
    echo
    echo "Add task-specific decisions and remaining work below this generated section."
  } >"${STATE_DIR}/HANDOFF.md"

  echo "Updated ${STATE_DIR}/STATE.md and ${STATE_DIR}/HANDOFF.md"
}

doctor() {
  local failed=0 codegraph

  check_state

  for path in \
    "${ROOT}/AGENTS.md" \
    "${ROOT}/support/configure-linux-debug.sh"; do
    if [[ ! -f "${path}" ]]; then
      echo "Missing required file: ${path}" >&2
      failed=1
    fi
  done

  for pattern in '/.codex/' '/.codegraph/' '/.codex-session.md'; do
    if ! grep -Fxq "${pattern}" "${ROOT}/.gitignore"; then
      echo "Missing ignore rule: ${pattern}" >&2
      failed=1
    fi
  done

  codegraph=$(find_codegraph || true)
  if [[ -z "${codegraph}" ]]; then
    echo "CodeGraph CLI not found" >&2
    failed=1
  elif [[ ! -d "${ROOT}/.codegraph" ]]; then
    echo "CodeGraph index not initialized" >&2
    failed=1
  fi

  if ((failed)); then
    return 1
  fi
  echo "Doctor: OK"
}

ROOT=$(repo_root) || {
  echo "Not inside a Git repository" >&2
  exit 1
}
STATE_DIR="${ROOT}/.codex"
cd "${ROOT}"

case "${1:-}" in
  check)
    check_state
    ;;
  refresh)
    refresh_state
    ;;
  doctor)
    doctor
    ;;
  *)
    usage
    ;;
esac
