#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"

smb_smoke_require_config
command -v gdb >/dev/null || {
  echo "gdb not found" >&2
  exit 2
}

ART="${SMB_SMOKE_ART:-/tmp/fa_libsmb2-real-gdb-smoke}"
rm -rf "$ART"
mkdir -p "$ART"

sanitize_artifacts() {
  for f in "$ART"/*.log "$ART"/*.js "$ART"/summary.txt; do
    [ -e "$f" ] && smb_smoke_sanitize_file "$f"
  done
}

cleanup() {
  sanitize_artifacts
  smb_smoke_cleanup_profiles "$ART"
  smb_smoke_check_no_secret "$ART"
}
trap cleanup EXIT

summarize() {
  local name="$1"
  local target="$2"
  local rc="$3"
  local log="$4"
  local crash=0
  local exited=0

  if grep -Eq "Program received signal SIG(SEGV|ABRT|BUS|ILL)|AddressSanitizer|stack smashing|buffer overflow" "$log"; then
    crash=1
  fi
  if grep -q "Inferior 1.*exited normally" "$log"; then
    exited=1
  fi

  {
    echo "[$name]"
    echo "target=$target"
    echo "gdb_exit=$rc"
    if [ "$crash" -ne 0 ]; then
      echo "crash_signal=yes"
    else
      echo "crash_signal=no"
    fi
    if [ "$exited" -ne 0 ]; then
      echo "exited_normally=yes"
    else
      echo "exited_normally=no"
    fi
    echo "interesting_log_lines:"
    grep -E "Program received signal|exited normally|SMB2_GDB|SMB2|navigator|ERROR|AddressSanitizer|stack smashing|buffer overflow|Unable|TRACE" "$log" | tail -120 || true
    echo
  } | tee -a "$ART/summary.txt"

  if [ "$crash" -ne 0 ]; then
    return 1
  fi

  case "$rc" in
    0|124) return 0 ;;
    *) return 1 ;;
  esac
}

run_nav_case() {
  local name="$1"
  local url="$2"
  local profile="$ART/$name-profile"
  local log="$ART/$name.gdb.log"
  smb_smoke_write_profile "$profile"

  set +e
  timeout --signal=INT --kill-after=5s "$SMB_SMOKE_TIMEOUT"s \
    gdb -q --batch \
      -ex "set pagination off" \
      -ex "set confirm off" \
      -ex "set print thread-events off" \
      -ex "handle SIGPIPE nostop noprint pass" \
      -ex "run -d --no-ui --disable-upgrades --persistent $profile/persistent --cache $profile/cache $url" \
      -ex "info program" \
      -ex "info threads" \
      -ex "thread apply all bt full" \
      --args "$SMB_SMOKE_MOVIAN" \
      >"$log" 2>&1
  local rc=$?
  set -e
  smb_smoke_sanitize_file "$log"
  summarize "$name" "$url" "$rc" "$log"
}

run_js_case() {
  local name="js-write-truncate"
  local profile="$ART/$name-profile"
  local script="$ART/$name.js"
  local log="$ART/$name.gdb.log"
  local base_url="$1"
  smb_smoke_write_profile "$profile"

  cat >"$script" <<JS
var fs = require('native/fs');
var base = '$base_url';
var stamp = 'codex-gdb-' + Date.now();
var dir = base + '/' + stamp;
var file = dir + '/file.bin';
var renamed = dir + '/renamed.bin';

function destroy(handle) {
  if (handle) Core.resourceDestroy(handle);
}

console.log('SMB2_GDB start ' + dir);
fs.mkdirs(dir);

var fd = fs.open(file, 'w');
try {
  var data = Duktape.Buffer('abcdef');
  var written = fs.write(fd, data, 0, null, 0);
  console.log('SMB2_GDB written=' + written + ' size=' + fs.fsize(fd));
  fs.ftruncate(fd, 3);
  console.log('SMB2_GDB truncated size=' + fs.fsize(fd));
} finally {
  destroy(fd);
}

fd = fs.open(file, 'a');
try {
  var more = Duktape.Buffer('XYZ');
  console.log('SMB2_GDB append=' + fs.write(fd, more, 0, null, null));
  console.log('SMB2_GDB append size=' + fs.fsize(fd));
} finally {
  destroy(fd);
}

fd = fs.open(file, 'r');
try {
  var buf = new Duktape.Buffer(fs.fsize(fd));
  var read = fs.read(fd, buf.valueOf(), 0, buf.length, 0);
  console.log('SMB2_GDB read=' + read);
} finally {
  destroy(fd);
}

fs.rename(file, renamed);
fs.unlink(renamed);
fs.rmdir(dir);
console.log('SMB2_GDB done');
JS

  set +e
  timeout --signal=INT --kill-after=5s "$SMB_SMOKE_TIMEOUT"s \
    gdb -q --batch \
      -ex "set pagination off" \
      -ex "set confirm off" \
      -ex "set print thread-events off" \
      -ex "handle SIGPIPE nostop noprint pass" \
      -ex "run -d --no-ui --disable-upgrades --bypass-ecmascript-acl --persistent $profile/persistent --cache $profile/cache --ecmascript $script" \
      -ex "info program" \
      -ex "info threads" \
      -ex "thread apply all bt full" \
      --args "$SMB_SMOKE_MOVIAN" \
      >"$log" 2>&1
  local rc=$?
  set -e
  smb_smoke_sanitize_file "$log"
  smb_smoke_sanitize_file "$script"
  summarize "$name" "$script" "$rc" "$log"
  if ! grep -q "SMB2_GDB done" "$log"; then
    echo "SMB2_GDB done marker missing" | tee -a "$ART/summary.txt"
    return 1
  fi
}

cd "$SMB_SMOKE_ROOT"
: >"$ART/summary.txt"
BASE_URL="$(smb_smoke_base_url)"
run_nav_case "nav-share-path" "$BASE_URL/"
run_nav_case "nav-host-no-slash" "smb2://$SMB_SMOKE_HOST"
run_nav_case "nav-host-with-slash" "smb2://$SMB_SMOKE_HOST/"
run_js_case "$BASE_URL"

echo "ART=$ART"
