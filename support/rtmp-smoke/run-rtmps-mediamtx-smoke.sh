#!/usr/bin/env bash
set -euo pipefail

ARTIFACTS=${ARTIFACTS:-/tmp/movian-rtmps-smoke}
MEDIAMTX_IMAGE=${MEDIAMTX_IMAGE:-bluenviron/mediamtx:1}
RTMP_PUBLISH_PORT=${RTMP_PUBLISH_PORT:-19395}
RTMPS_PLAY_PORT=${RTMPS_PLAY_PORT:-19397}
STREAM_SECONDS=${STREAM_SECONDS:-90}
WAIT_SECONDS=${WAIT_SECONDS:-70}
MOVIAN_BIN=${MOVIAN_BIN:-}

usage() {
  cat <<'USAGE'
Usage:
  support/rtmp-smoke/run-rtmps-mediamtx-smoke.sh

Environment:
  MOVIAN_BIN              Movian binary or wrapper to test. If unset and
                          build.flatpak-builder exists, a temporary Flatpak
                          wrapper is generated automatically.
  ARTIFACTS               Artifact directory (default: /tmp/movian-rtmps-smoke)
  MEDIAMTX_IMAGE          Docker image (default: bluenviron/mediamtx:1)
  RTMP_PUBLISH_PORT       Host RTMP publish port (default: 19395)
  RTMPS_PLAY_PORT         Host RTMPS playback port (default: 19397)
  STREAM_SECONDS          Synthetic publisher duration (default: 90)
  WAIT_SECONDS            Movian playback timeout (default: 70)

This smoke publishes a synthetic stream to MediaMTX over RTMP, then opens the
same stream through RTMPS in Movian. It is intended for a Flatpak or equivalent
build whose bundled FFmpeg enables gmp and gnutls.
USAGE
}

fail() {
  echo "ERROR: $*" >&2
  if [ -n "${PUBLISHER_LOG:-}" ] && [ -f "$PUBLISHER_LOG" ]; then
    tail -n 120 "$PUBLISHER_LOG" >"$ARTIFACTS/publisher-tail.txt" || true
    echo "Saved publisher tail: $ARTIFACTS/publisher-tail.txt" >&2
  fi
  if [ -n "${MEDIAMTX_LOG:-}" ] && docker ps -a --format '{{.Names}}' |
    grep -Fxq "${CONTAINER_NAME:-}"; then
    docker logs "$CONTAINER_NAME" >"$MEDIAMTX_LOG" 2>&1 || true
    echo "Saved MediaMTX log: $MEDIAMTX_LOG" >&2
  fi
  exit 1
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

command -v docker >/dev/null 2>&1 || fail "docker is required"
command -v ffmpeg >/dev/null 2>&1 || fail "ffmpeg is required"
command -v openssl >/dev/null 2>&1 || fail "openssl is required"

case "$RTMP_PUBLISH_PORT" in
  ''|*[!0-9]*) fail "RTMP_PUBLISH_PORT must be a positive integer, got: $RTMP_PUBLISH_PORT" ;;
esac

case "$RTMPS_PLAY_PORT" in
  ''|*[!0-9]*) fail "RTMPS_PLAY_PORT must be a positive integer, got: $RTMPS_PLAY_PORT" ;;
esac

case "$STREAM_SECONDS" in
  ''|*[!0-9]*) fail "STREAM_SECONDS must be a positive integer, got: $STREAM_SECONDS" ;;
esac

case "$WAIT_SECONDS" in
  ''|*[!0-9]*) fail "WAIT_SECONDS must be a positive integer, got: $WAIT_SECONDS" ;;
esac

rm -rf "$ARTIFACTS"
mkdir -p "$ARTIFACTS"

CERT="$ARTIFACTS/server.crt"
KEY="$ARTIFACTS/server.key"
PUBLISHER_LOG="$ARTIFACTS/ffmpeg-publisher.log"
MEDIAMTX_LOG="$ARTIFACTS/mediamtx.log"
SMOKE_ARTIFACTS="$ARTIFACTS/movian"
CONTAINER_NAME="movian-rtmps-mediamtx-$$"
PUBLISH_URL="rtmp://127.0.0.1:${RTMP_PUBLISH_PORT}/live/test"
PLAY_URL="rtmps://127.0.0.1:${RTMPS_PLAY_PORT}/live/test"
PUBLISHER_PID=

if [ -z "$MOVIAN_BIN" ]; then
  if [ -d build.flatpak-builder ]; then
    MOVIAN_BIN="$ARTIFACTS/run-flatpak-movian.sh"
    {
      printf '%s\n' '#!/usr/bin/env bash'
      printf '%s\n' 'exec flatpak build build.flatpak-builder /app/bin/showtime "$@"'
    } >"$MOVIAN_BIN"
    chmod +x "$MOVIAN_BIN"
  else
    fail "MOVIAN_BIN is unset and build.flatpak-builder is missing"
  fi
fi

[ -x "$MOVIAN_BIN" ] || fail "Movian binary or wrapper is not executable: $MOVIAN_BIN"

cleanup() {
  status=$?
  trap - EXIT
  if [ -n "${PUBLISHER_PID:-}" ]; then
    kill "$PUBLISHER_PID" 2>/dev/null || true
    wait "$PUBLISHER_PID" 2>/dev/null || true
  fi
  if docker ps -a --format '{{.Names}}' | grep -Fxq "$CONTAINER_NAME"; then
    docker logs "$CONTAINER_NAME" >"$MEDIAMTX_LOG" 2>&1 || true
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap cleanup EXIT

openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$KEY" \
  -out "$CERT" \
  -sha256 \
  -days 1 \
  -subj '/CN=localhost' >"$ARTIFACTS/openssl.log" 2>&1

docker run -d --rm --name "$CONTAINER_NAME" \
  -p "127.0.0.1:${RTMP_PUBLISH_PORT}:1935" \
  -p "127.0.0.1:${RTMPS_PLAY_PORT}:1937" \
  -v "$KEY:/server.key:ro" \
  -v "$CERT:/server.crt:ro" \
  -e MTX_RTMPENCRYPTION=optional \
  -e MTX_RTMPSADDRESS=:1937 \
  -e MTX_RTMPSERVERKEY=/server.key \
  -e MTX_RTMPSERVERCERT=/server.crt \
  "$MEDIAMTX_IMAGE" >"$ARTIFACTS/container-id.txt"

sleep 2

ffmpeg -hide_banner -loglevel info -nostdin -re \
  -f lavfi -i testsrc2=size=320x180:rate=15 \
  -f lavfi -i sine=frequency=700:sample_rate=44100 \
  -t "$STREAM_SECONDS" \
  -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p -g 15 \
  -c:a aac -b:a 96k \
  -f flv "$PUBLISH_URL" >"$PUBLISHER_LOG" 2>&1 &
PUBLISHER_PID=$!

sleep 4
kill -0 "$PUBLISHER_PID" 2>/dev/null ||
  fail "ffmpeg RTMP publisher exited before the RTMPS playback check"

ARTIFACTS="$SMOKE_ARTIFACTS" \
WAIT_SECONDS="$WAIT_SECONDS" \
RTMP_URL="$PLAY_URL" \
MOVIAN_BIN="$MOVIAN_BIN" \
  support/rtmp-smoke/run-rtmp-smoke.sh

{
  echo "RTMPS MediaMTX smoke passed"
  echo "Publish URL: $PUBLISH_URL"
  echo "Playback URL: $PLAY_URL"
  echo "Movian artifacts: $SMOKE_ARTIFACTS"
  echo "MediaMTX log: $MEDIAMTX_LOG"
  echo "Publisher log: $PUBLISHER_LOG"
} | tee "$ARTIFACTS/summary.txt"
