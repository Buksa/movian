#!/usr/bin/env bash
set -euo pipefail

MOVIAN_BIN=${MOVIAN_BIN:-./build.debug/movian}
PLUGIN_PATH=${PLUGIN_PATH:-}
START_URL=${START_URL:-}
EXPECTED_TITLE=${EXPECTED_TITLE:-}
EXPECTED_TYPE=${EXPECTED_TYPE:-}
MIN_NODES=${MIN_NODES:-0}
ARTIFACTS=${ARTIFACTS:-/tmp/movian-plugin-smoke}
READY_LOG_PATTERN=${READY_LOG_PATTERN:-}
DEBUG_GLW=${DEBUG_GLW:-0}
LIBAV_LOG=${LIBAV_LOG:-0}
REQUIRE_SCREENSHOT=${REQUIRE_SCREENSHOT:-0}
ALLOW_EXISTING_MOVIAN=${ALLOW_EXISTING_MOVIAN:-0}

usage() {
  cat <<'USAGE'
Usage:
  PLUGIN_PATH=/path/to/plugin START_URL=plugin:start EXPECTED_TITLE="Title" \
    support/plugin-smoke/run-plugin-smoke.sh

Environment:
  MOVIAN_BIN             Movian binary (default: ./build.debug/movian)
  ARTIFACTS              Artifact directory (default: /tmp/movian-plugin-smoke)
  READY_LOG_PATTERN      Optional grep -E pattern to wait for before /api/open
  EXPECTED_TYPE          Optional expected currentpage.model.type
  MIN_NODES              Optional minimum node count (default: 0)
  REQUIRE_SCREENSHOT=1   Fail when /api/screenshot/raw is unavailable
  DEBUG_GLW=1            Add --debug-glw
  LIBAV_LOG=1            Add --libav-log
  ALLOW_EXISTING_MOVIAN=1 Allow another Movian process to already be running
USAGE
}

fail() {
  echo "ERROR: $*" >&2
  if [ -n "${LOG:-}" ] && [ -f "$LOG" ]; then
    tail -n 200 "$LOG" >"$ARTIFACTS/movian-tail.txt" || true
    echo "Saved log tail: $ARTIFACTS/movian-tail.txt" >&2
  fi
  exit 1
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

[ -n "$PLUGIN_PATH" ] || { usage >&2; exit 2; }
[ -n "$START_URL" ] || { usage >&2; exit 2; }
[ -n "$EXPECTED_TITLE" ] || { usage >&2; exit 2; }
[ -x "$MOVIAN_BIN" ] || fail "Movian binary is not executable: $MOVIAN_BIN"
[ -d "$PLUGIN_PATH" ] || fail "Plugin directory does not exist: $PLUGIN_PATH"

case "$MIN_NODES" in
  ''|*[!0-9]*) fail "MIN_NODES must be a non-negative integer, got: $MIN_NODES" ;;
esac

if [ "$ALLOW_EXISTING_MOVIAN" != "1" ]; then
  existing=$(pgrep -a -f '/movian( |$)|build\.(debug|release|debug-gdb|asan)/movian' || true)
  [ -z "$existing" ] || fail "Movian already appears to be running. Set ALLOW_EXISTING_MOVIAN=1 to continue.
$existing"
fi

rm -rf "$ARTIFACTS"
mkdir -p "$ARTIFACTS"

LOG="$ARTIFACTS/movian.log"
PERSIST="$ARTIFACTS/persistent"
CACHE="$ARTIFACTS/cache"
mkdir -p "$PERSIST" "$CACHE"

cmd=(
  "$MOVIAN_BIN"
  -d
  --disable-upgrades
  --persistent "$PERSIST"
  --cache "$CACHE"
)

[ "$DEBUG_GLW" = "1" ] && cmd+=(--debug-glw)
[ "$LIBAV_LOG" = "1" ] && cmd+=(--libav-log)

cmd+=(
  -p "$PLUGIN_PATH"
  "$START_URL"
)

printf '%q ' "${cmd[@]}" >"$ARTIFACTS/command.txt"
printf '\n' >>"$ARTIFACTS/command.txt"

"${cmd[@]}" >"$LOG" 2>&1 &
PID=$!

cleanup() {
  if kill -0 "$PID" 2>/dev/null; then
    kill "$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

wait_log() {
  pattern=$1
  label=$2

  for _ in $(seq 1 160); do
    if grep -Eq "$pattern" "$LOG"; then
      return 0
    fi
    if ! kill -0 "$PID" 2>/dev/null; then
      wait "$PID" || true
      fail "Movian exited while waiting for $label"
    fi
    sleep 0.25
  done

  fail "Timed out waiting for $label: $pattern"
}

wait_log 'http-server: Listening on port [0-9]+' 'HTTP port'
PORT=$(sed -n 's/.*http-server: Listening on port \([0-9][0-9]*\).*/\1/p' "$LOG" | tail -1)
[ -n "$PORT" ] || fail "Could not parse HTTP port"
printf '%s\n' "$PORT" >"$ARTIFACTS/port.txt"

if [ -n "$READY_LOG_PATTERN" ]; then
  wait_log "$READY_LOG_PATTERN" 'plugin ready log pattern'
fi

BASE="http://127.0.0.1:$PORT"
printf '%s\n' "$BASE" >"$ARTIFACTS/base-url.txt"

prop_get() {
  curl -fsS "$BASE/api/prop/$1"
}

prop_value() {
  sed -n -E '1{
    s/^.* is an? //
    s/^.* is //
    p
  }'
}

open_url() {
  curl -fsS --get --data-urlencode "url=$1" "$BASE/api/open" >"$ARTIFACTS/open.html"
}

current_title() {
  prop_get 'global/navigators/current/currentpage/model/metadata/title' | prop_value
}

current_loading() {
  prop_get 'global/navigators/current/currentpage/model/loading' | prop_value
}

current_type() {
  prop_get 'global/navigators/current/currentpage/model/type' | prop_value
}

is_loading_done() {
  value=$1
  case "$value" in
    1|true|TRUE|True|*' 1'|*true*) return 1 ;;
    *) return 0 ;;
  esac
}

open_url "$START_URL" || fail "Failed to open route: $START_URL"

for _ in $(seq 1 120); do
  title=$(current_title || true)
  loading=$(current_loading || true)
  printf 'title=%s\nloading=%s\n' "$title" "$loading" >"$ARTIFACTS/current-state.txt"

  if printf '%s\n' "$title" | grep -Fq "$EXPECTED_TITLE" && is_loading_done "$loading"; then
    break
  fi
  sleep 0.5
done

title=$(current_title || true)
loading=$(current_loading || true)
type=$(current_type || true)

printf '%s\n' "$title" >"$ARTIFACTS/title.txt"
printf '%s\n' "$loading" >"$ARTIFACTS/loading.txt"
printf '%s\n' "$type" >"$ARTIFACTS/type.txt"

printf '%s\n' "$title" | grep -Fq "$EXPECTED_TITLE" || \
  fail "Expected title containing '$EXPECTED_TITLE', got '$title'"

is_loading_done "$loading" || fail "Page is still loading: $loading"

if [ -n "$EXPECTED_TYPE" ] && [ "$type" != "$EXPECTED_TYPE" ]; then
  fail "Expected type '$EXPECTED_TYPE', got '$type'"
fi

if prop_get 'global/navigators/current/currentpage/model/nodes' >"$ARTIFACTS/nodes.txt"; then
  node_count=$(grep -Ec '^  ' "$ARTIFACTS/nodes.txt" || true)
else
  node_count=0
  : >"$ARTIFACTS/nodes.txt"
fi

if [ "$MIN_NODES" -gt 0 ] && [ "$node_count" -lt "$MIN_NODES" ]; then
  fail "Expected at least $MIN_NODES nodes, got $node_count"
fi

if timeout 8s curl -fsS -o "$ARTIFACTS/screenshot.png" \
  "$BASE/api/screenshot/raw" 2>"$ARTIFACTS/screenshot-error.txt"; then
  rm -f "$ARTIFACTS/screenshot-error.txt"
  file "$ARTIFACTS/screenshot.png" >"$ARTIFACTS/screenshot-file.txt" || true
else
  rm -f "$ARTIFACTS/screenshot.png"
  if [ "$REQUIRE_SCREENSHOT" = "1" ]; then
    fail "Screenshot capture failed"
  fi
  echo "WARNING: screenshot capture failed" >"$ARTIFACTS/screenshot-warning.txt"
fi

grep -Ein 'TypeError|ReferenceError|Cannot read propert|Route .*exception|JavaScript error' \
  "$LOG" >"$ARTIFACTS/log-signals.txt" || true

if [ -s "$ARTIFACTS/log-signals.txt" ]; then
  fail "JavaScript crash signatures found in log"
fi

cat <<EOF
PASS
ARTIFACTS=$ARTIFACTS
PORT=$PORT
TITLE=$title
LOADING=$loading
TYPE=$type
NODE_COUNT=$node_count
EOF
