#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=../movian-procs.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/movian-procs.sh"

MOVIAN_BIN=${MOVIAN_BIN:-./build.debug/movian}
ARTIFACTS=${ARTIFACTS:-/tmp/movian-rtmp-smoke}
PORT=${PORT:-19368}
STREAM_SECONDS=${STREAM_SECONDS:-60}
WAIT_SECONDS=${WAIT_SECONDS:-40}
ALLOW_EXISTING_MOVIAN=${ALLOW_EXISTING_MOVIAN:-0}
RTMP_URL=${RTMP_URL:-}
EXPECT_CONTAINER=${EXPECT_CONTAINER:-flv}
EXPECT_VIDEO=${EXPECT_VIDEO:-h264}
EXPECT_AUDIO=${EXPECT_AUDIO:-aac}

usage() {
  cat <<'USAGE'
Usage:
  MOVIAN_BIN=./build.debug/movian \
    support/rtmp-smoke/run-rtmp-smoke.sh

Environment:
  MOVIAN_BIN              Movian binary to test (default: ./build.debug/movian)
  ARTIFACTS               Artifact directory (default: /tmp/movian-rtmp-smoke)
  PORT                    Local RTMP port (default: 19368)
  STREAM_SECONDS          Synthetic stream duration (default: 60)
  WAIT_SECONDS            Startup/playback timeout (default: 40)
  ALLOW_EXISTING_MOVIAN=1 Allow another Movian process to already be running
  RTMP_URL                Optional external RTMP-family URL. When set, the
                          script skips the local ffmpeg RTMP server.
  EXPECT_CONTAINER        Expected container in the playback log (default: flv)
  EXPECT_VIDEO            Expected video codec in the playback log (default: h264)
  EXPECT_AUDIO            Expected audio codec in the playback log (default: aac)

The target Movian build should be configured without the old librtmp backend
when validating the FFmpeg-backed RTMP path. The standard Linux debug helper
does this by default.
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

[ -x "$MOVIAN_BIN" ] || fail "Movian binary is not executable: $MOVIAN_BIN"
command -v curl >/dev/null 2>&1 || fail "curl is required"

if [ -z "$RTMP_URL" ]; then
  command -v ffmpeg >/dev/null 2>&1 || fail "ffmpeg is required"
fi

case "$PORT" in
  ''|*[!0-9]*) fail "PORT must be a positive integer, got: $PORT" ;;
esac

case "$STREAM_SECONDS" in
  ''|*[!0-9]*) fail "STREAM_SECONDS must be a positive integer, got: $STREAM_SECONDS" ;;
esac

case "$WAIT_SECONDS" in
  ''|*[!0-9]*) fail "WAIT_SECONDS must be a positive integer, got: $WAIT_SECONDS" ;;
esac

if [ "$ALLOW_EXISTING_MOVIAN" != "1" ]; then
  # The awk filter that used to sit here dropped only $$ and $PPID -- a
  # symptom-level patch for this same self-match. The path can equally sit in
  # a grandparent's argv, or in any unrelated process that names it.
  existing=$(movian_running_procs)
  [ -z "$existing" ] || fail "Movian already appears to be running. Set ALLOW_EXISTING_MOVIAN=1 to continue.
$existing"
fi

rm -rf "$ARTIFACTS"
mkdir -p "$ARTIFACTS"

LOG="$ARTIFACTS/movian.log"
SERVER_LOG="$ARTIFACTS/ffmpeg-server.log"
SERVER_PID=

if [ -n "$RTMP_URL" ]; then
  case "$RTMP_URL" in
    rtmp://*|rtmpt://*|rtmpe://*|rtmps://*|rtmpte://*|rtmpts://*) ;;
    *) fail "RTMP_URL must use an RTMP-family scheme, got: $RTMP_URL" ;;
  esac
  URL=$RTMP_URL
else
  URL="rtmp://127.0.0.1:${PORT}/live/test"

  ffmpeg -hide_banner -loglevel info -nostdin -re \
    -f lavfi -i testsrc2=size=320x180:rate=15 \
    -f lavfi -i sine=frequency=880:sample_rate=44100 \
    -t "$STREAM_SECONDS" \
    -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p -g 15 \
    -c:a aac -b:a 96k \
    -f flv -listen 1 "$URL" >"$SERVER_LOG" 2>&1 &
  SERVER_PID=$!
fi

"$MOVIAN_BIN" \
  -d \
  --libav-log \
  --disable-upgrades \
  --persistent "$ARTIFACTS/persistent" \
  --cache "$ARTIFACTS/cache" \
  >"$LOG" 2>&1 &
MOVIAN_PID=$!

cleanup() {
  kill "$MOVIAN_PID" ${SERVER_PID:+"$SERVER_PID"} 2>/dev/null || true
  wait "$MOVIAN_PID" 2>/dev/null || true
  if [ -n "$SERVER_PID" ]; then
    wait "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

check_server_alive() {
  [ -z "$SERVER_PID" ] || kill -0 "$SERVER_PID" 2>/dev/null
}

wait_log() {
  pattern=$1
  label=$2
  ticks=$((WAIT_SECONDS * 4))

  for _ in $(seq 1 "$ticks"); do
    if grep -Eq "$pattern" "$LOG"; then
      return 0
    fi
    kill -0 "$MOVIAN_PID" 2>/dev/null || fail "Movian exited while waiting for $label"
    check_server_alive ||
      fail "ffmpeg RTMP server exited while waiting for $label"
    sleep 0.25
  done

  fail "Timed out waiting for $label: $pattern"
}

wait_log_fixed() {
  pattern=$1
  label=$2
  ticks=$((WAIT_SECONDS * 4))

  for _ in $(seq 1 "$ticks"); do
    if grep -Fq "$pattern" "$LOG"; then
      return 0
    fi
    kill -0 "$MOVIAN_PID" 2>/dev/null || fail "Movian exited while waiting for $label"
    check_server_alive ||
      fail "ffmpeg RTMP server exited while waiting for $label"
    sleep 0.25
  done

  fail "Timed out waiting for $label: $pattern"
}

wait_log "http-server: Listening on port [0-9]+" "HTTP server"
wait_log_fixed "UI size scale changed" "GLW startup"

HTTP_PORT=$(sed -n 's/.*http-server: Listening on port \([0-9][0-9]*\).*/\1/p' "$LOG" | tail -1)
[ -n "$HTTP_PORT" ] || fail "Could not parse Movian HTTP port"
BASE="http://127.0.0.1:$HTTP_PORT"

printf '%s\n' "$URL" >"$ARTIFACTS/rtmp-url.txt"
printf '%s\n' "$BASE" >"$ARTIFACTS/base-url.txt"

sleep 1
curl -fsS -L --get --data-urlencode "url=$URL" "$BASE/api/open" >"$ARTIFACTS/open.html"

wait_log_fixed "Starting playback of $URL ($EXPECT_CONTAINER)" "RTMP playback start"
wait_log "Stream #[0-9]+: Video: $EXPECT_VIDEO" "$EXPECT_VIDEO video stream"
wait_log "Stream #[0-9]+: Audio: $EXPECT_AUDIO" "$EXPECT_AUDIO audio stream"

{
  echo "currentpage.source:"
  curl -fsS "$BASE/api/prop/global/navigators/current/currentpage/source" || true
  echo
  echo "currentpage.model.type:"
  curl -fsS "$BASE/api/prop/global/navigators/current/currentpage/model/type" || true
  echo
  echo "media.current.url:"
  curl -fsS "$BASE/api/prop/global/media/current/url" || true
  echo
} >"$ARTIFACTS/props.txt"

grep -Fq "is a $URL" "$ARTIFACTS/props.txt" ||
  fail "Current media URL did not switch to $URL"

{
  echo "RTMP smoke passed"
  echo "URL: $URL"
  echo "Movian log: $LOG"
  if [ -n "$SERVER_PID" ]; then
    echo "ffmpeg server log: $SERVER_LOG"
  fi
  echo "Props: $ARTIFACTS/props.txt"
} | tee "$ARTIFACTS/summary.txt"
