#!/bin/sh
# Corpus guard for movian-analyze (#97).
#
# This is the regression test that makes the auto-generated abort-only
# stub strategy (gen-abort-stubs.sh) safe: it runs --check (and
# --tokens, for the lexer-only path) over every glwskins/**.view file
# plus the intentional-failure fixtures in tests/tooling/glw/fixtures/,
# and fails the whole run if the analyzer ever exits by signal (an
# abort()'d stub firing means core GLW code grew a new parse-time
# dependency this tool's shim doesn't know about -- see shim.c's
# contract table) instead of a plain exit code.
#
# glwskins/flat/**.view (98 files) is the acceptance-criterion corpus:
# every one of those MUST make both --check and --tokens exit 0 with empty
# stderr (issue #97: "All glwskins/flat/**.view (98) -> exit 0, nothing on
# stderr"). Everything
# else under glwskins/ (glwskins/old/, legacy/experimental) and the
# intentionally-broken fixtures are only held to "did not crash" --
# exit 0 (clean) or exit 1 (a real, reported parse error) are both fine
# there; a lexer/parser abort() or a shell-reported crash is not.
#
# Usage: run_corpus.sh <path-to-movian-analyze-binary>
set -u

if [ $# -lt 1 ]; then
  echo "usage: $0 <path-to-movian-analyze>" >&2
  exit 2
fi

ANALYZE=$1
TOPDIR=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
cd "$TOPDIR"

# NB: deliberately not `set -e` from here on -- every check_one() call
# below expects and handles non-zero exit codes (parse errors are the
# normal, correct outcome for the fixtures and for some of the loose
# corpora), so -e would abort the whole guard on the first one.

fail=0
flat_fail=0
crash=0
n_flat=0
n_other=0
n_fixtures=0
n_js=0
n_js_fixtures=0

check_one() {
  # $1 = view path
  # $2 = 1 if --check must exit 0 with empty stderr (strict parse)
  # $3 = 1 if --tokens must exit 0 with empty stderr (strict lex);
  #      include-fragments keep this at 1: --tokens stops before macro
  #      preprocessing, so a missing macro cannot excuse a lexer error.
  view=$1
  strict=$2
  strict_tokens=$3

  out=$("$ANALYZE" --check "$view" 2>/tmp/movian-analyze-corpus.stderr)
  rc=$?

  if [ "$rc" -gt 128 ]; then
    echo "CRASH (signal $((rc - 128))): $view" >&2
    crash=$((crash + 1))
    fail=1
    return
  fi
  if [ "$rc" != 0 ] && [ "$rc" != 1 ]; then
    echo "UNEXPECTED EXIT CODE $rc: $view" >&2
    fail=1
    return
  fi

  if [ "$strict" = 1 ]; then
    if [ "$rc" != 0 ]; then
      echo "FLAT CORPUS REGRESSION (exit $rc, expected 0): $view" >&2
      echo "  $out" >&2
      flat_fail=$((flat_fail + 1))
      fail=1
    elif [ -s /tmp/movian-analyze-corpus.stderr ]; then
      echo "FLAT CORPUS REGRESSION (nonempty stderr): $view" >&2
      sed 's/^/  stderr: /' /tmp/movian-analyze-corpus.stderr >&2
      flat_fail=$((flat_fail + 1))
      fail=1
    fi
  fi

  # --tokens: lexer-only path, exercises a different (smaller) part of
  # the object closure. Strict files must be clean here too; loose files
  # retain the crash-only contract.
  "$ANALYZE" --tokens "$view" >/dev/null 2>/tmp/movian-analyze-corpus.tokens.stderr
  trc=$?
  if [ "$trc" -gt 128 ]; then
    echo "CRASH in --tokens (signal $((trc - 128))): $view" >&2
    crash=$((crash + 1))
    fail=1
  elif [ "$strict_tokens" = 1 ]; then
    if [ "$trc" != 0 ]; then
      echo "FLAT CORPUS REGRESSION (--tokens exit $trc, expected 0): $view" >&2
      flat_fail=$((flat_fail + 1))
      fail=1
    elif [ -s /tmp/movian-analyze-corpus.tokens.stderr ]; then
      echo "FLAT CORPUS REGRESSION (--tokens nonempty stderr): $view" >&2
      sed 's/^/  stderr: /' /tmp/movian-analyze-corpus.tokens.stderr >&2
      flat_fail=$((flat_fail + 1))
      fail=1
    fi
  fi
}

# Include-fragments: files that are only ever #include/#import-ed into a
# document that has already imported theme.view, so they call macros
# (e.g. ListItemBevel from theme.view) that do not exist in a standalone
# parse. The real runtime rejects a standalone parse of these the same
# way, so a clean --check expectation would be FALSE parity; --check runs
# in the loose lane (must not crash, well-formed error allowed) while
# --tokens stays strict (lexing stops before macro preprocessing, so a
# missing macro cannot excuse a lexer error). See issue #106.
is_fragment() {
  case "$1" in
    glwskins/flat/menu/sidebar_common.view) return 0 ;;
  esac
  return 1
}

echo "== glwskins/flat (strict: must exit 0, empty stderr; fragments loose) =="
for f in $(find glwskins/flat -name '*.view'); do
  n_flat=$((n_flat + 1))
  if is_fragment "$f"; then
    check_one "$f" 0 1
  else
    check_one "$f" 1 1
  fi
done

echo "== glwskins/old (loose: must not crash) =="
if [ -d glwskins/old ]; then
  for f in $(find glwskins/old -name '*.view'); do
    n_other=$((n_other + 1))
    check_one "$f" 0 0
  done
fi

echo "== tests/tooling/glw/fixtures (loose: must not crash) =="
for f in tests/tooling/glw/fixtures/*.view; do
  [ -f "$f" ] || continue
  n_fixtures=$((n_fixtures + 1))
  # --check invokes the real preprocessor, which has no analyzer
  # --max-depth option. The self-cycle is checked below by the bounded
  # --tokens walker instead.
  case "$f" in
    tests/tooling/glw/fixtures/self-include.view)
      continue
      ;;
  esac
  check_one "$f" 0 0
done

echo "== tests/tooling/glw/golden (strict: must exit 0, empty stderr) =="
for f in tests/tooling/glw/golden/*.view; do
  [ -f "$f" ] || continue
  n_flat=$((n_flat + 1))
  check_one "$f" 1 1
done

rm -f /tmp/movian-analyze-corpus.stderr /tmp/movian-analyze-corpus.tokens.stderr

echo "== golden JSON byte-compare (tests/tooling/glw/golden/small.view) =="
golden_fail=0

check_tokens_golden() {
  # $1 = source view, $2 = expected --tokens JSON, $3 = label
  golden_view=$1
  golden_path=$2
  golden_label=$3

  "$ANALYZE" --tokens "$golden_view" \
    > /tmp/movian-analyze-corpus.golden.tokens \
    2> /tmp/movian-analyze-corpus.golden.stderr
  golden_rc=$?

  if [ "$golden_rc" != 0 ] || \
     [ -s /tmp/movian-analyze-corpus.golden.stderr ] || \
     ! cmp -s /tmp/movian-analyze-corpus.golden.tokens "$golden_path"; then
    echo "GOLDEN MISMATCH (--tokens $golden_label):" >&2
    if [ "$golden_rc" != 0 ]; then
      echo "  exit: $golden_rc (expected 0)" >&2
    fi
    if [ -s /tmp/movian-analyze-corpus.golden.stderr ]; then
      sed 's/^/  stderr: /' /tmp/movian-analyze-corpus.golden.stderr >&2
    fi
    if ! cmp -s /tmp/movian-analyze-corpus.golden.tokens "$golden_path"; then
      sed 's/^/  got:  /' /tmp/movian-analyze-corpus.golden.tokens >&2
      sed 's/^/  want: /' "$golden_path" >&2
    fi
    golden_fail=1
    fail=1
  fi
}

if [ -f tests/tooling/glw/golden/small.check.json ]; then
  got=$("$ANALYZE" --check tests/tooling/glw/golden/small.view 2>/dev/null)
  want=$(cat tests/tooling/glw/golden/small.check.json)
  if [ "$got" != "$want" ]; then
    echo "GOLDEN MISMATCH (--check small.view):" >&2
    echo "  got:  $got" >&2
    echo "  want: $want" >&2
    golden_fail=1
    fail=1
  fi
fi
if [ -f tests/tooling/glw/golden/small.tokens.json ]; then
  check_tokens_golden tests/tooling/glw/golden/small.view \
    tests/tooling/glw/golden/small.tokens.json "small.view"
fi
if [ -f tests/tooling/glw/golden/deescape-drop.tokens.json ]; then
  check_tokens_golden tests/tooling/glw/fixtures/deescape-drop.view \
    tests/tooling/glw/golden/deescape-drop.tokens.json "deescape-drop.view"
fi
if [ -f tests/tooling/glw/golden/repeated-include.tokens.json ]; then
  check_tokens_golden tests/tooling/glw/fixtures/repeated-include.view \
    tests/tooling/glw/golden/repeated-include.tokens.json "repeated-include.view"
fi

if [ -f tests/tooling/glw/golden/self-include.depth-1.tokens.json ] && \
   [ -f tests/tooling/glw/golden/self-include.depth-1.stderr ]; then
  echo "== self-include depth guard (--tokens --max-depth 1) =="
  timeout 5 "$ANALYZE" --tokens --max-depth 1 \
    tests/tooling/glw/fixtures/self-include.view \
    > /tmp/movian-analyze-corpus.self-include.tokens \
    2> /tmp/movian-analyze-corpus.self-include.stderr
  self_rc=$?
  if [ "$self_rc" != 0 ] || \
     ! cmp -s /tmp/movian-analyze-corpus.self-include.tokens \
       tests/tooling/glw/golden/self-include.depth-1.tokens.json || \
     ! cmp -s /tmp/movian-analyze-corpus.self-include.stderr \
       tests/tooling/glw/golden/self-include.depth-1.stderr; then
    echo "SELF-INCLUDE REGRESSION (--tokens --max-depth 1):" >&2
    echo "  exit: $self_rc (expected 0)" >&2
    if ! cmp -s /tmp/movian-analyze-corpus.self-include.tokens \
      tests/tooling/glw/golden/self-include.depth-1.tokens.json; then
      sed 's/^/  got:  /' /tmp/movian-analyze-corpus.self-include.tokens >&2
      sed 's/^/  want: /' \
        tests/tooling/glw/golden/self-include.depth-1.tokens.json >&2
    fi
    if ! cmp -s /tmp/movian-analyze-corpus.self-include.stderr \
      tests/tooling/glw/golden/self-include.depth-1.stderr; then
      sed 's/^/  stderr: /' /tmp/movian-analyze-corpus.self-include.stderr >&2
      sed 's/^/  wanted stderr: /' \
        tests/tooling/glw/golden/self-include.depth-1.stderr >&2
    fi
    golden_fail=1
    fail=1
  fi
fi

rm -f /tmp/movian-analyze-corpus.golden.tokens \
  /tmp/movian-analyze-corpus.golden.stderr \
  /tmp/movian-analyze-corpus.self-include.tokens \
  /tmp/movian-analyze-corpus.self-include.stderr

echo "== JavaScript metadata module coverage =="
expected_native_modules=$(grep -h '^[[:space:]]*ES_MODULE("' \
  src/ecmascript/es_*.c | wc -l | tr -d ' ')
expected_commonjs_modules=$(find res/ecmascript/modules -type f -name '*.js' \
  | wc -l | tr -d ' ')
actual_native_modules=unreadable
actual_commonjs_modules=unreadable
metadata_counts=$(
  python3 - generated/movian-metadata.json <<'PY'
import json
import sys

modules = json.load(open(sys.argv[1], encoding="utf-8"))["js"]["modules"]
names = [module["name"] for module in modules]
if len(names) != len(set(names)):
    raise SystemExit("duplicate js.modules names")
print(sum(module["kind"] == "native" for module in modules),
      sum(module["kind"] == "commonjs" for module in modules))
PY
)
metadata_rc=$?
if [ "$metadata_rc" != 0 ]; then
  echo "JAVASCRIPT METADATA REGRESSION: unable to read module coverage" >&2
  fail=1
else
  set -- $metadata_counts
  if [ "$#" != 2 ]; then
    echo "JAVASCRIPT METADATA REGRESSION: malformed coverage counts" >&2
    fail=1
  else
    actual_native_modules=$1
    actual_commonjs_modules=$2
  fi
  if [ "$#" = 2 ] && \
     { [ "$actual_native_modules" != "$expected_native_modules" ] || \
       [ "$actual_commonjs_modules" != "$expected_commonjs_modules" ]; }; then
    echo "JAVASCRIPT METADATA REGRESSION: native $actual_native_modules/$expected_native_modules, commonjs $actual_commonjs_modules/$expected_commonjs_modules" >&2
    fail=1
  fi
fi

echo "== JavaScript runtime and plugin examples (strict compile-only) =="
for f in $(find plugin_examples res/ecmascript support/devtools/viewpreview \
  -name '*.js' | sort); do
  n_js=$((n_js + 1))
  "$ANALYZE" --js "$f" \
    > /tmp/movian-analyze-corpus.js \
    2> /tmp/movian-analyze-corpus.js.stderr
  js_rc=$?
  if [ "$js_rc" != 0 ] || \
     [ -s /tmp/movian-analyze-corpus.js.stderr ]; then
    echo "JAVASCRIPT CORPUS REGRESSION (exit $js_rc): $f" >&2
    sed 's/^/  stdout: /' /tmp/movian-analyze-corpus.js >&2
    sed 's/^/  stderr: /' /tmp/movian-analyze-corpus.js.stderr >&2
    fail=1
  fi
done

echo "== JavaScript intentional failures (must reject cleanly) =="
for f in tests/tooling/js/fixtures/*.js; do
  [ -f "$f" ] || continue
  n_js_fixtures=$((n_js_fixtures + 1))
  "$ANALYZE" --js "$f" \
    > /tmp/movian-analyze-corpus.js \
    2> /tmp/movian-analyze-corpus.js.stderr
  js_rc=$?
  if [ "$js_rc" != 1 ] || \
     [ -s /tmp/movian-analyze-corpus.js.stderr ] || \
     ! grep -q '^{"file":.*,"line":1,"error":".*"}$' \
       /tmp/movian-analyze-corpus.js; then
    echo "JAVASCRIPT FAILURE REGRESSION (exit $js_rc): $f" >&2
    sed 's/^/  stdout: /' /tmp/movian-analyze-corpus.js >&2
    sed 's/^/  stderr: /' /tmp/movian-analyze-corpus.js.stderr >&2
    fail=1
  fi
done

if [ "$n_js_fixtures" != 3 ]; then
  echo "JAVASCRIPT FIXTURE REGRESSION: found $n_js_fixtures, expected 3" >&2
  fail=1
fi

rm -f /tmp/movian-analyze-corpus.js \
  /tmp/movian-analyze-corpus.js.stderr

echo
echo "flat+golden: $n_flat, glwskins/old: $n_other, fixtures: $n_fixtures, crashes: $crash, flat regressions: $flat_fail, golden mismatches: $golden_fail"
echo "metadata modules: $actual_native_modules native, $actual_commonjs_modules commonjs"
echo "javascript: $n_js strict files, $n_js_fixtures intentional failures"

if [ "$fail" != 0 ]; then
  echo "CORPUS GUARD FAILED" >&2
  exit 1
fi
echo "CORPUS GUARD OK"
