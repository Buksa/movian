#!/bin/sh
# Runtime integration guard for ZIP member lookup and GLW #import (movian#185).
# Requires a configured debug build selected by MDEV (default: mdev).
set -eu

MDEV=${MDEV:-mdev}
NAME="zip-dotsegments-$$"
TESTDIR=$(mktemp -d "${TMPDIR:-/tmp}/movian-zip-glw.XXXXXX")
STATE_ROOT=${MDEV_ROOT:-/tmp/mdev}

cleanup() {
  "$MDEV" stop --name "$NAME" >/dev/null 2>&1 || true
  rm -rf "$TESTDIR"
}
trap cleanup EXIT HUP INT TERM

python3 - "$TESTDIR" <<'PY'
import json
import sys
import zipfile
from pathlib import Path

root = Path(sys.argv[1])
archive = root / "views.zip"
plugin = root / "plugin"
plugin.mkdir()

common = '''#define IMPORTED_LABEL() {
  widget(label, { caption: "ZIP import resolved"; align: center; });
}
'''
child = '''#import "../common.view"
widget(container_z, { IMPORTED_LABEL(); });
'''
plain = 'widget(container_z, { widget(label, { caption: "plain"; }); });\n'

with zipfile.ZipFile(archive, "w") as zf:
    zf.writestr("views/common.view", common)
    zf.writestr("views/sub/child.view", child)
    zf.writestr("mixed/a\\b/c.view", plain)
    zf.writestr("repeated/a//b.view", plain)
    zf.writestr("encoded/%2e%2e/view.view", plain)
    zf.writestr("plain.view", plain)

base = "zip://" + archive.resolve().as_uri() + "/"
views = {
    "import": "views/sub/child.view",
    "mixed": "mixed/a\\b/c.view",
    "repeated": "repeated/a//b.view",
    "encoded": "encoded/%2e%2e/view.view",
    "terminal": "plain.view/.",
}

(plugin / "plugin.json").write_text(json.dumps({
    "type": "ecmascript",
    "apiversion": 2,
    "id": "zip_dotsegments_test",
    "file": "test.js",
    "showtimeVersion": "5",
    "version": "1.0.0",
    "author": "Movian tests",
    "title": "ZIP dot-segment test",
    "category": "other",
}), encoding="utf-8")

(plugin / "test.js").write_text(
    "var page = require('movian/page');\n"
    "var base = " + json.dumps(base) + ";\n"
    "var views = " + json.dumps(views) + ";\n"
    "new page.Route('ziprepro:(.*)', function(page, id) {\n"
    "  page.type = 'raw';\n"
    "  page.metadata.title = 'ZIP ' + id;\n"
    "  page.metadata.glwview = base + views[id];\n"
    "  page.loading = false;\n"
    "});\n",
    encoding="utf-8",
)
PY

"$MDEV" run --name "$NAME" -p "$TESTDIR/plugin" ziprepro:import >/dev/null
PORT=$(python3 - "$STATE_ROOT/$NAME/state.json" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["port"])
PY
)

ui_ready=0
tries=0
while [ "$tries" -lt 100 ]; do
  log=$("$MDEV" log --name "$NAME" --tail 160)
  case "$log" in
    *"OpenGL Renderer"*) ui_ready=1; break ;;
  esac
  tries=$((tries + 1))
  sleep 0.1
done
if [ "$ui_ready" != 1 ]; then
  echo "GLW did not become ready" >&2
  "$MDEV" log --name "$NAME" --tail 160 >&2 || true
  exit 1
fi

for route in import mixed repeated encoded; do
  curl -fsS --get --data-urlencode "url=ziprepro:$route" \
    "http://127.0.0.1:$PORT/api/open" >/dev/null

  route_opened=0
  tries=0
  while [ "$tries" -lt 50 ]; do
    log=$("$MDEV" log --name "$NAME" --tail 240)
    case "$log" in
      *"Opening ziprepro:$route"*) route_opened=1; break ;;
    esac
    tries=$((tries + 1))
    sleep 0.1
  done
  if [ "$route_opened" != 1 ]; then
    echo "route did not open: ziprepro:$route" >&2
    printf '%s\n' "$log" >&2
    exit 1
  fi
  "$MDEV" shot --name "$NAME" --json >/dev/null
done

log=$("$MDEV" log --name "$NAME" --tail 500)
case "$log" in
  *"GLW             [ERROR]"*)
    echo "GLW error while loading ZIP views:" >&2
    printf '%s\n' "$log" >&2
    exit 1
    ;;
esac

"$MDEV" stop --name "$NAME" >/dev/null
NAME="${NAME}-terminal"
"$MDEV" run --name "$NAME" -p "$TESTDIR/plugin" ziprepro:terminal >/dev/null

directory_check=0
tries=0
while [ "$tries" -lt 100 ]; do
  log=$("$MDEV" log --name "$NAME" --tail 180)
  case "$log" in
    *"GLW             [ERROR]"*"plain.view/."*)
      directory_check=1
      break
      ;;
  esac
  tries=$((tries + 1))
  sleep 0.1
done
if [ "$directory_check" != 1 ]; then
  echo "terminal dot segment did not retain directory semantics" >&2
  printf '%s\n' "$log" >&2
  exit 1
fi

printf '%s\n' "ZIP GLW import and compatibility paths passed"
