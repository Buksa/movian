#!/usr/bin/env bash
set -euo pipefail

smb_smoke_require_config() {
  : "${SMB_SMOKE_HOST:?set SMB_SMOKE_HOST}"
  : "${SMB_SMOKE_SHARE:?set SMB_SMOKE_SHARE}"
  : "${SMB_SMOKE_PATH:?set SMB_SMOKE_PATH}"
  : "${SMB_SMOKE_USER:?set SMB_SMOKE_USER}"
  : "${SMB_SMOKE_PASSWORD:?set SMB_SMOKE_PASSWORD}"

  SMB_SMOKE_DOMAIN="${SMB_SMOKE_DOMAIN:-WORKGROUP}"
  SMB_SMOKE_ROOT="${SMB_SMOKE_ROOT:-$(pwd)}"
  SMB_SMOKE_MOVIAN="${SMB_SMOKE_MOVIAN:-$SMB_SMOKE_ROOT/build.debug/movian}"
  SMB_SMOKE_TIMEOUT="${SMB_SMOKE_TIMEOUT:-45}"

  if [ ! -x "$SMB_SMOKE_MOVIAN" ]; then
    echo "Movian binary not executable: $SMB_SMOKE_MOVIAN" >&2
    exit 2
  fi
}

smb_smoke_trim_slashes() {
  local value="$1"
  value="${value#/}"
  value="${value%/}"
  printf '%s' "$value"
}

smb_smoke_base_url() {
  local path
  path="$(smb_smoke_trim_slashes "$SMB_SMOKE_PATH")"
  if [ -n "$path" ]; then
    printf 'smb2://%s/%s/%s' "$SMB_SMOKE_HOST" "$SMB_SMOKE_SHARE" "$path"
  else
    printf 'smb2://%s/%s' "$SMB_SMOKE_HOST" "$SMB_SMOKE_SHARE"
  fi
}

smb_smoke_keyring_json() {
  python3 - "$SMB_SMOKE_HOST" "$SMB_SMOKE_USER"     "$SMB_SMOKE_PASSWORD" "$SMB_SMOKE_DOMAIN" <<'PY'
import json
import sys

host, user, password, domain = sys.argv[1:5]
print(json.dumps({
    f"smb2:connection:{host}": {
        "username": user,
        "password": password,
        "domain": domain,
    }
}, separators=(",", ":")))
PY
}

smb_smoke_write_profile() {
  local profile="$1"
  mkdir -p "$profile/persistent/settings" "$profile/cache"
  smb_smoke_keyring_json >"$profile/persistent/settings/keyring"
  smb_smoke_write_local_bittorrent_cache "$profile"
}

smb_smoke_write_local_bittorrent_cache() {
  local profile="$1"
  local cache_dir="$profile/cache/bittorrentcache"
  mkdir -p "$profile/persistent/settings" "$cache_dir"
  python3 - "$cache_dir" >"$profile/persistent/settings/bittorrent" <<'PY'
import json
import sys

print(json.dumps({"path": sys.argv[1]}, separators=(",", ":")))
PY
}

smb_smoke_profile_summary() {
  local profile="$1"
  local output="$2"
  python3 - "$profile" >"$output" <<'PY'
import json
from pathlib import Path
import sys

profile = Path(sys.argv[1])
settings = profile / "persistent" / "settings"

def read_json(name, default):
    try:
        return json.loads((settings / name).read_text())
    except Exception:
        return default

print(f"profile={profile}")
print(f"bittorrent.path={read_json('bittorrent', {}).get('path', '<unset>')}")

keyring = read_json("keyring", {})
print("keyring.ids=" + ",".join(sorted(keyring)) if keyring else "keyring.ids=<none>")

bookmarks = read_json("bookmarks2", [])
urls = []
if isinstance(bookmarks, list):
    for bookmark in bookmarks:
        url = bookmark.get("url") if isinstance(bookmark, dict) else None
        if isinstance(url, str) and ("smb://" in url or "smb2://" in url):
            urls.append(url)
print("bookmarks.smb=" + ",".join(urls) if urls else "bookmarks.smb=<none>")
PY
}

smb_smoke_check_no_unexpected_remote_smb2_client() {
  local log="$1"
  local allowed="${2:-127\\.0\\.0\\.1}"

  [ "${SMB_SMOKE_ALLOW_REMOTE_CLIENTS:-0}" != "1" ] || return 0
  [ -f "$log" ] || return 0

  python3 - "$log" "$allowed" <<'PY'
import re
import sys
from pathlib import Path

log = Path(sys.argv[1])
allowed = sys.argv[2].split(",")
bad = []

for line in log.read_text(errors="replace").splitlines():
    if "SMB2-CLIENT" not in line or "Connecting to " not in line:
        continue
    target = line.split("Connecting to ", 1)[1].split()[0]
    host = target.split("/", 1)[0].rsplit(":", 1)[0]
    if not any(re.fullmatch(pattern, host) for pattern in allowed):
        bad.append(line)

if bad:
    print("Unexpected remote SMB2 client activity:", file=sys.stderr)
    for line in bad:
        print(line, file=sys.stderr)
    sys.exit(1)
PY
}

smb_smoke_sanitize_file() {
  local file="$1"
  [ -f "$file" ] || return 0
  python3 - "$file" "${SMB_SMOKE_PASSWORD:-}" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
password = sys.argv[2]
data = path.read_text(errors="replace")
if password:
    data = data.replace(password, "<redacted>")
data = re.sub(r'"password":"[^"]*"', '"password":"<redacted>"', data)
path.write_text(data)
PY
}

smb_smoke_cleanup_profiles() {
  local art="$1"
  find "$art" -maxdepth 1 -type d -name '*-profile' -prune -exec rm -rf {} +
  rm -rf "$art/profile"
}

smb_smoke_check_no_secret() {
  local art="$1"
  if [ -n "${SMB_SMOKE_PASSWORD:-}" ] &&
     grep -R --fixed-strings "$SMB_SMOKE_PASSWORD" "$art" >/dev/null 2>&1; then
    echo "Secret leaked into artifacts under $art" >&2
    return 1
  fi
}
