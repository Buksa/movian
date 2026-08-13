#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"

smb_smoke_require_config

ART="${SMB_SMOKE_ART:-/tmp/fa_libsmb2-http-nav-smoke}"
rm -rf "$ART"
mkdir -p "$ART/profile/persistent/settings" "$ART/profile/cache"
smb_smoke_write_profile "$ART/profile"

cat <<EOF >"$ART/profile/persistent/settings/smbserver"
{
	"share": "${SMB_SMOKE_SHARE}",
	"port": "1445",
	"username": "",
	"password": "",
	"enable": 1,
	"root": "${SMB_SMOKE_ROOT:-$(pwd)}"
}
EOF

cat <<EOF >"$ART/profile/persistent/settings/dev"
{
	"smbdebug": 1
}
EOF

cleanup() {
  if [ -n "${PID:-}" ]; then
    kill "$PID" 2>/dev/null || true
    for _ in $(seq 1 20); do
      if ! kill -0 "$PID" 2>/dev/null; then
        break
      fi
      sleep 0.1
    done
    kill -KILL "$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
  fi
  rm -f "$ART/profile/persistent/settings/keyring"
  smb_smoke_sanitize_file "$ART/movian.log"
  smb_smoke_check_no_secret "$ART"
}
trap cleanup EXIT

cd "$SMB_SMOKE_ROOT"
if [ "${SMB_SMOKE_USE_GDB:-0}" = "1" ]; then
  command -v gdb >/dev/null || {
    echo "SMB_SMOKE_USE_GDB=1 requires gdb" >&2
    exit 2
  }
  gdb -ex r -ex "thread apply all bt full" -ex q --args "$SMB_SMOKE_MOVIAN" \
    -d --disable-upgrades \
    --persistent "$ART/profile/persistent" \
    --cache "$ART/profile/cache" \
    >"$ART/movian.log" 2>&1 &
else
  "$SMB_SMOKE_MOVIAN" -d --disable-upgrades \
    --persistent "$ART/profile/persistent" \
    --cache "$ART/profile/cache" \
    >"$ART/movian.log" 2>&1 &
fi
PID=$!

for _ in $(seq 1 120); do
  port=$(sed -n 's/.*http-server: Listening on port \([0-9][0-9]*\).*/\1/p' "$ART/movian.log" | tail -1)
  [ -n "${port:-}" ] && break
  sleep 0.25
done
[ -n "${port:-}" ]
BASE="http://127.0.0.1:$port"

wait_http() {
  for _ in $(seq 1 80); do
    if curl -fsS "$BASE/api/diag" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done
  return 1
}

wait_http
sleep "${SMB_SMOKE_UI_SETTLE:-5}"

prop_value() {
  curl -fsS "$BASE/api/prop/$1" 2>/dev/null | sed -n '1s/^.* is a //p' || true
}

open_url() {
  local url="$1"
  local open_target="$url"
  case "$url" in
    smb://*|smb2://*) open_target="search:$url" ;;
  esac
  for _ in $(seq 1 80); do
    if curl -fsS --get --data-urlencode "url=$open_target" "$BASE/api/open" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done
  return 1
}

wait_page() {
  local expected="$1"
  local expected_url
  for _ in $(seq 1 160); do
    page_url=$(prop_value global/navigators/current/currentpage/url)
    loading=$(prop_value global/navigators/current/currentpage/model/loading)
    case "$expected" in
      "smb2://$SMB_SMOKE_HOST") expected_url="smb2://$SMB_SMOKE_HOST/" ;;
      *) expected_url="$expected" ;;
    esac
    if [ "$page_url" = "$expected_url" ] && [ "$loading" = "0" ]; then
      return 0
    fi
    sleep 0.25
  done
  return 1
}

dump_case() {
  local name="$1"
  local url="$2"
  open_url "$url"
  if ! wait_page "$url"; then
    {
      echo "[$name]"
      echo "url=$url"
      echo "status=failed"
      echo "page_url=$(prop_value global/navigators/current/currentpage/url)"
      echo "title=$(prop_value global/navigators/current/currentpage/model/metadata/title)"
      echo "loading=$(prop_value global/navigators/current/currentpage/model/loading)"
      echo
    } | tee -a "$ART/summary.txt"
    return 1
  fi
  {
    echo "[$name]"
    echo "url=$url"
    echo "page_url=$(prop_value global/navigators/current/currentpage/url)"
    echo "title=$(prop_value global/navigators/current/currentpage/model/metadata/title)"
    echo "loading=$(prop_value global/navigators/current/currentpage/model/loading)"
    for i in $(seq 0 8); do
      title=$(prop_value "global/navigators/current/currentpage/model/nodes/*$i/metadata/title")
      type=$(prop_value "global/navigators/current/currentpage/model/nodes/*$i/type")
      node_url=$(prop_value "global/navigators/current/currentpage/model/nodes/*$i/url")
      [ -n "$title$type$node_url" ] &&
        printf 'node[%s]\ttype=%s\ttitle=%s\turl=%s\n' "$i" "$type" "$title" "$node_url"
    done
    echo
  } | tee -a "$ART/summary.txt"
}

BASE_URL="$(smb_smoke_base_url)"
dump_case host-no-slash "smb2://$SMB_SMOKE_HOST"
dump_case host-with-slash "smb2://$SMB_SMOKE_HOST/"
dump_case share-path "$BASE_URL/"

echo "ART=$ART"
