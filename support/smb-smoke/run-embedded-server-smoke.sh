#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"

SMB_SMOKE_ROOT="${SMB_SMOKE_ROOT:-$(pwd)}"
SMB_SMOKE_MOVIAN="${SMB_SMOKE_MOVIAN:-$SMB_SMOKE_ROOT/build.debug/movian}"
SMB_SERVER_SMOKE_USER="${SMB_SERVER_SMOKE_USER:-testuser}"
SMB_SERVER_SMOKE_PASSWORD="${SMB_SERVER_SMOKE_PASSWORD:-testpass}"
SMB_SERVER_SMOKE_SHARE="${SMB_SERVER_SMOKE_SHARE:-share}"
SMB_SERVER_SMOKE_PORT_BASE="${SMB_SERVER_SMOKE_PORT_BASE:-1786}"
SMB_SERVER_SMOKE_ALLOW_EXISTING="${SMB_SERVER_SMOKE_ALLOW_EXISTING:-0}"
ART="${SMB_SMOKE_ART:-/tmp/movian-embedded-smb2-server-smoke}"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

[ -x "$SMB_SMOKE_MOVIAN" ] ||
  fail "Movian binary is not executable: $SMB_SMOKE_MOVIAN"
command -v smbclient >/dev/null ||
  fail "smbclient is required"

if [ "$SMB_SERVER_SMOKE_ALLOW_EXISTING" != "1" ]; then
  existing=$(pgrep -a -f '/movian( |$)|build\.(debug|release)/movian' || true)
  [ -z "$existing" ] || fail "Movian already appears to be running:
$existing
Set SMB_SERVER_SMOKE_ALLOW_EXISTING=1 to run anyway."
fi

rm -rf "$ART"
mkdir -p "$ART"

write_profile() {
  local profile="$1"
  local port="$2"
  local root="$3"
  local username="${4-$SMB_SERVER_SMOKE_USER}"
  local password="${5-$SMB_SERVER_SMOKE_PASSWORD}"

  mkdir -p "$profile/persistent/settings" "$profile/cache"
  cat >"$profile/persistent/settings/smbserver" <<EOF
{"enable":1,"port":"$port","username":"$username","password":"$password","share":"$SMB_SERVER_SMOKE_SHARE","root":"$root"}
EOF
  cat >"$profile/persistent/settings/dev" <<EOF
{"smbdebug":1,"httpdebug":1}
EOF
  if [ -n "$username" ]; then
    cat >"$profile/persistent/settings/keyring" <<EOF
{"smb2:connection:127.0.0.1:$port":{"username":"$username","password":"$password","domain":"WORKGROUP"}}
EOF
  fi
}

start_movian() {
  local profile="$1"
  local port="$2"
  local log="$3"

  cd "$SMB_SMOKE_ROOT"
  "$SMB_SMOKE_MOVIAN" -d --disable-upgrades \
    --persistent "$profile/persistent" \
    --cache "$profile/cache" \
    >"$log" 2>&1 &
  PID=$!

  for _ in $(seq 1 120); do
    kill -0 "$PID" 2>/dev/null ||
      fail "Movian exited while waiting for SMB2 server; see $log"
    http_port=$(sed -n 's/.*http-server: Listening on port \([0-9][0-9]*\).*/\1/p' "$log" | tail -1)
    if [ -n "${http_port:-}" ] && grep -q "Listening on port $port" "$log"; then
      BASE="http://127.0.0.1:$http_port"
      return 0
    fi
    sleep 0.25
  done
  fail "Timed out waiting for SMB2 server port $port; see $log"
}

stop_movian() {
  if [ -n "${PID:-}" ]; then
    kill "$PID" 2>/dev/null || true
    for _ in $(seq 1 20); do
      kill -0 "$PID" 2>/dev/null || break
      sleep 0.1
    done
    kill -KILL "$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
    unset PID
  fi
}

sanitize_artifacts() {
  find "$ART" -type f -print0 2>/dev/null |
    while IFS= read -r -d '' file; do
      smb_smoke_sanitize_file "$file"
    done
}

prop_value() {
  curl -fsS "$BASE/api/prop/$1" 2>/dev/null | sed -n '1s/^.* is a //p' || true
}

open_movian_url() {
  local url="$1"
  curl -fsS --get --data-urlencode "url=search:$url" "$BASE/api/open" >/dev/null
  for _ in $(seq 1 120); do
    page_url=$(prop_value global/navigators/current/currentpage/url)
    loading=$(prop_value global/navigators/current/currentpage/model/loading)
    if [ "$page_url" = "$url" ] && { [ "$loading" = "0" ] || [ "$loading" = "(void)" ]; }; then
      return 0
    fi
    sleep 0.25
  done
  return 1
}

dump_current_nodes() {
  local output="$1"
  {
    echo "page_url=$(prop_value global/navigators/current/currentpage/url)"
    for i in $(seq 0 8); do
      title=$(prop_value "global/navigators/current/currentpage/model/nodes/*$i/metadata/title")
      type=$(prop_value "global/navigators/current/currentpage/model/nodes/*$i/type")
      node_url=$(prop_value "global/navigators/current/currentpage/model/nodes/*$i/url")
      [ -n "$title$type$node_url" ] &&
        printf 'node[%s]\ttype=%s\ttitle=%s\turl=%s\n' "$i" "$type" "$title" "$node_url"
    done
    true
  } >"$output"
}

run_smbclient() {
  local name="$1"
  local port="$2"
  local dialect="$3"
  shift 3
  smbclient "//127.0.0.1/$SMB_SERVER_SMOKE_SHARE" \
    -p "$port" -U "$SMB_SERVER_SMOKE_USER%$SMB_SERVER_SMOKE_PASSWORD" \
    -m "$dialect" "$@" >"$ART/$name.log" 2>&1
}

run_file_root_case() {
  local port="$SMB_SERVER_SMOKE_PORT_BASE"
  local case_art="$ART/file-root"
  local profile="$case_art/profile"
  local root="$case_art/share-root"
  mkdir -p "$root/dir"
  printf 'original media\n' >"$root/movie.mkv"
  printf 'nested media\n' >"$root/dir/nested.mp4"
  printf 'upload\n' >"$case_art/upload.txt"

  write_profile "$profile" "$port" "$root"
  start_movian "$profile" "$port" "$case_art/movian.log"

  run_smbclient file-smb2-ls "$port" SMB2 -c 'ls; cd dir; ls'
  grep -q 'movie.mkv' "$ART/file-smb2-ls.log" || fail "SMB2 ls did not show movie.mkv"
  grep -q 'nested.mp4' "$ART/file-smb2-ls.log" || fail "SMB2 ls did not show nested.mp4"

  set +e
  smbclient "//127.0.0.1/$SMB_SERVER_SMOKE_SHARE" \
    -p "$port" -U "$SMB_SERVER_SMOKE_USER%$SMB_SERVER_SMOKE_PASSWORD" \
    -m SMB3 -c 'ls; cd dir; ls' >"$ART/file-smb3-ls.log" 2>&1
  smb3_ls_rc=$?
  set -e
  if [ "$smb3_ls_rc" -ne 0 ]; then
    echo "WARN: password SMB3 listing failed; see $ART/file-smb3-ls.log" \
      >"$ART/file-smb3-ls.warning"
    [ "${SMB_SERVER_SMOKE_REQUIRE_PASSWORD_SMB3:-0}" != "1" ] ||
      fail "password SMB3 listing failed"
  else
    grep -q 'movie.mkv' "$ART/file-smb3-ls.log" || fail "SMB3 ls did not show movie.mkv"
    grep -q 'nested.mp4' "$ART/file-smb3-ls.log" || fail "SMB3 ls did not show nested.mp4"
  fi

  local file_dialect="${SMB_SERVER_SMOKE_FILE_DIALECT:-SMB2}"

  set +e
  smbclient "//127.0.0.1/$SMB_SERVER_SMOKE_SHARE" \
    -p "$port" -U "$SMB_SERVER_SMOKE_USER%wrongpass" \
    -m SMB3 -c 'ls' >"$ART/file-wrong-password.log" 2>&1
  wrong_rc=$?
  set -e
  [ "$wrong_rc" -ne 0 ] || fail "wrong password unexpectedly succeeded"

  run_smbclient file-get "$port" "$file_dialect" -c "get movie.mkv $case_art/downloaded.mkv"
  cmp "$root/movie.mkv" "$case_art/downloaded.mkv"
  run_smbclient file-put "$port" "$file_dialect" -c "put $case_art/upload.txt uploaded.txt"
  [ -f "$root/uploaded.txt" ] || fail "put did not create uploaded.txt"
  run_smbclient file-mkdir "$port" "$file_dialect" -c 'mkdir made_by_smb'
  [ -d "$root/made_by_smb" ] || fail "mkdir did not create made_by_smb"
  run_smbclient file-rename "$port" "$file_dialect" -c 'rename uploaded.txt renamed.txt'
  [ -f "$root/renamed.txt" ] || fail "rename did not create renamed.txt"
  printf 'src\n' >"$root/collision_src.txt"
  printf 'dst\n' >"$root/collision_dst.txt"
  set +e
  smbclient "//127.0.0.1/$SMB_SERVER_SMOKE_SHARE" \
    -p "$port" -U "$SMB_SERVER_SMOKE_USER%$SMB_SERVER_SMOKE_PASSWORD" \
    -m "$file_dialect" -c 'rename collision_src.txt collision_dst.txt' \
    >"$ART/file-rename-collision.log" 2>&1
  rename_collision_rc=$?
  set -e
  [ "$rename_collision_rc" -ne 0 ] ||
    fail "rename over an existing target unexpectedly succeeded"
  grep -qx 'dst' "$root/collision_dst.txt" ||
    fail "rename collision overwrote destination content"
  grep -qx 'src' "$root/collision_src.txt" ||
    fail "rename collision removed source content"
  run_smbclient file-del "$port" "$file_dialect" -c 'del renamed.txt'
  [ ! -e "$root/renamed.txt" ] || fail "del did not remove renamed.txt"
  run_smbclient file-rmdir "$port" "$file_dialect" -c 'rmdir made_by_smb'
  [ ! -e "$root/made_by_smb" ] || fail "rmdir did not remove made_by_smb"

  set +e
  smbclient "//127.0.0.1/$SMB_SERVER_SMOKE_SHARE" \
    -p "$port" -U "$SMB_SERVER_SMOKE_USER%$SMB_SERVER_SMOKE_PASSWORD" \
    -m "$file_dialect" -c "put $case_art/upload.txt ../escape.txt" \
    >"$ART/file-traversal.log" 2>&1
  set -e
  [ ! -e "$case_art/escape.txt" ] || fail "traversal escaped share root"
  rm -f "$root/escape.txt"

  grep -q 'Read OK' "$case_art/movian.log" ||
    fail "SMB2 file read was not observed"

  stop_movian
}

run_vfs_root_case() {
  local port=$((SMB_SERVER_SMOKE_PORT_BASE + 1))
  local case_art="$ART/vfs-root"
  local profile="$case_art/profile"
  local zona="$case_art/zona"
  mkdir -p "$zona/subdir"
  printf 'vfs movie\n' >"$zona/movie.mkv"
  printf 'nested media\n' >"$zona/subdir/nested.mp4"

  write_profile "$profile" "$port" "/" "" ""
  cat >"$profile/persistent/settings/bookmarks2" <<EOF
[{"id":"smb-smoke-zona","title":"zona","svctype":"other","url":"file://$zona"}]
EOF
  start_movian "$profile" "$port" "$case_art/movian.log"

  smbclient -L "//127.0.0.1" -p "$port" -U 'anonymous%' -m SMB2 \
    >"$ART/vfs-root-enum-smb2.log" 2>&1
  grep -q "$SMB_SERVER_SMOKE_SHARE" "$ART/vfs-root-enum-smb2.log" ||
    fail "SMB2 host-root enumeration did not show share"
  smbclient -L "//127.0.0.1" -p "$port" -U 'anonymous%' -m SMB3 \
    >"$ART/vfs-root-enum-smb3.log" 2>&1
  grep -q "$SMB_SERVER_SMOKE_SHARE" "$ART/vfs-root-enum-smb3.log" ||
    fail "SMB3 host-root enumeration did not show share"

  smbclient "//127.0.0.1/$SMB_SERVER_SMOKE_SHARE" \
    -p "$port" -U 'anonymous%' -m SMB3 -c 'ls; cd zona; ls' \
    >"$ART/vfs-root-ls.log" 2>&1
  if grep -Eq '(^|[[:space:]])(bin|etc|usr)[[:space:]]+D' "$ART/vfs-root-ls.log"; then
    fail "default root exposed raw filesystem instead of vfs:///"
  fi
  grep -q 'zona' "$ART/vfs-root-ls.log" ||
    fail "default VFS root did not return exported zona mapping"
  grep -q 'movie.mkv' "$ART/vfs-root-ls.log" ||
    fail "nested VFS browse did not return zona/movie.mkv"

  open_movian_url "vfs:///" ||
    fail "Movian did not open vfs:///"
  sleep 2
  dump_current_nodes "$ART/vfs-root-vfs-nodes.txt"
  open_movian_url "smb2://127.0.0.1:$port/" ||
    fail "Movian did not open SMB2 server host root"
  sleep 1
  dump_current_nodes "$ART/vfs-root-smb-host-nodes.txt"
  grep -q "title=$SMB_SERVER_SMOKE_SHARE" "$ART/vfs-root-smb-host-nodes.txt" ||
    fail "Movian SMB2 host root did not expose configured share"
  open_movian_url "smb2://127.0.0.1:$port/$SMB_SERVER_SMOKE_SHARE/" ||
    fail "Movian did not open SMB2 VFS root"
  sleep 2
  dump_current_nodes "$ART/vfs-root-smb-nodes.txt"
  grep -q 'title=zona' "$ART/vfs-root-smb-nodes.txt" ||
    fail "SMB2 VFS root did not expose zona mapping"
  open_movian_url "smb2://127.0.0.1:$port/$SMB_SERVER_SMOKE_SHARE/zona/" ||
    fail "Movian did not open SMB2 VFS child"
  sleep 2
  dump_current_nodes "$ART/vfs-root-smb-zona-nodes.txt"
  grep -q 'title=movie' "$ART/vfs-root-smb-zona-nodes.txt" ||
    fail "SMB2 VFS child did not expose media file"
  grep -q 'title=subdir' "$ART/vfs-root-smb-zona-nodes.txt" ||
    fail "SMB2 VFS child did not expose nested directory"
  grep -q 'SMB2-SERVER' "$case_art/movian.log" ||
    fail "server debug component SMB2-SERVER was not observed"
  grep -q 'SMB2-CLIENT' "$case_art/movian.log" ||
    fail "client debug component SMB2-CLIENT was not observed"
  grep -q 'PIPE_TRANSCEIVE' "$case_art/movian.log" ||
    fail "srvsvc PIPE_TRANSCEIVE was not observed during host-root enum"
  grep -q 'QueryInfo: FILE/ALL' "$case_art/movian.log" ||
    fail "compound stat QueryInfo was not observed during VFS child browse"
  grep -q 'scan directory done url=smb2://127.0.0.1:' "$case_art/movian.log" ||
    fail "SMB2 client directory scan completion was not observed"

  stop_movian
}

trap 'stop_movian; sanitize_artifacts' EXIT
run_file_root_case
run_vfs_root_case

sanitize_artifacts
smb_smoke_check_no_secret "$ART"

echo "Embedded SMB2 server smoke passed"
echo "ART=$ART"
