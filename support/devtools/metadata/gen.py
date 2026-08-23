#!/usr/bin/env python3
"""movian-metadata generator (issue #98).

Generates `generated/movian-metadata.json` (schema v1): the single
committed metadata artifact for the GLW view language, grown from the
`viewdoc` drift detector (issue #88, since moved to movian-plugin-sdk
along with the reference docs it validates). Sections:

- glw.functions   -- `token_func_t funcvec[]`, src/ui/glw/glw_view_eval.c
                      (name, nargs/variadic, ctor/dtor/preproc presence).
- glw.attributes  -- `token_attrib_t attribtab[]`, src/ui/glw/glw_view_attrib.c
                      (name, valueType inferred from the setter function,
                      raw setter/attribConst/fn fields, noSubscription flag,
                      and optional enumValues in C source order for setters
                      backed by an explicitly mapped strtab).
- glw.widgets     -- every `glw_class_t` designated initializer
                      (`.gc_name`/`.gc_name2`) across src/ui/glw/glw_*.c,
                      cross-referenced against `GLW_REGISTER_CLASS(...)`
                      call sites for the `registered` flag. Includes two
                      classes that define `.gc_name` but are never
                      registered (`view`, `style` -- internal-only, not
                      resolvable by name from a `.view` file).
- glw.operators   -- curated, `curated_operators.json` next to this file.
- glw.scopes      -- curated, `curated_scopes.json` next to this file.
- js.modules      -- native `ES_MODULE` registrations from
                     src/ecmascript/es_*.c (function names and arities), plus
                     statically scanned CommonJS exports and prototype/shared
                     object shapes from res/ecmascript/modules/**/*.js.
- js.pluginManifest -- curated `plugin.json` keys and mandatory status,
                       anchored to the loader in src/plugins.c.

Every record carries `source: {file, line}` pointing at the defining line
in the C source (scanned, not hand-typed -- see the table-entry scanner
below) or, for curated sections, a hand-verified anchor. Records discovered
under a C preprocessor guard additionally carry optional `condition` text;
active nested guards are joined with ` && `.

Python 3 stdlib only. Determinism: every list is sorted by its primary key
name, `json.dumps(..., sort_keys=True, indent=2)`, trailing newline.
`movianRevision` is the current `git rev-parse HEAD` -- it is the one field
that legitimately changes between two regenerations on different commits,
so `--check` compares content with `movianRevision` normalized out (see
`_strip_revision`) rather than requiring a pinned value.

Usage:
    gen.py            -- regenerate generated/movian-metadata.json in place
    gen.py --check    -- regenerate in memory and diff against the
                          committed artifact (ignoring movianRevision);
                          nonzero exit and a printed diff on drift
    gen.py --json     -- with --check, print the diff as JSON instead
"""

from __future__ import annotations

import argparse
import json
import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
METADATA_DIR = Path(__file__).resolve().parent
ARTIFACT_PATH = REPO_ROOT / "generated" / "movian-metadata.json"
DTS_PATH = REPO_ROOT / "generated" / "movian-api.d.ts"
# apiversion-1 plugins only: ecmascript.c:913 loads api-v1.js for them
# and for nobody else, so its `showtime` global is emitted to a file a
# v2 plugin never includes. Folding it into the main bundle would
# silently bless `showtime` in modern plugins, where it does not exist.
V1_DTS_PATH = REPO_ROOT / "generated" / "movian-api-v1.d.ts"
LEGACY_API_V1 = (
    REPO_ROOT / "res" / "ecmascript" / "legacy" / "api-v1.js")
REFERENCE_DTS_CHECKER = METADATA_DIR / "check_reference_dts.py"
RUNTIME_ORACLE_PATH = (
    REPO_ROOT / "support" / "devtools" / "api-introspector"
    / "runtime-api.json")
RUNTIME_ORACLE_VERSION = 2

RUNTIME_ORACLE_INPUTS_VERSION = 1
# The files the oracle is a reading OF. A stamp over more than this goes red
# on commits that cannot have moved the surface; over less, it misses the
# case this check exists for. `res/ecmascript/modules/**` is what the capture
# walks and `introspector.js` is what does the walking -- a change to either
# can move the answer, and nothing else can.
# 18 of the 52 modules the oracle observes are `native/*`, registered from
# these files. Leaving them out left the stamp guarding half of what the
# cross-check covers: a native registered in a shape the C scanner does not
# read is absent from the artifact, absent from a capture that predates it,
# and -- without this -- absent from the freshness key too. The same blind
# spot in the other language.
#
# These are also the only inputs the BINARY carries. Everything else reaches
# the runtime through `dataroot://` and is read from disk, which is why a
# capture can be taken against an unchanged build after a module edit but
# not after a C edit. `--adopt-oracle` enforces exactly that difference.
RUNTIME_ORACLE_COMPILED_GLOBS = (
    "src/ecmascript/**/*.c",
    "src/ecmascript/**/*.h",
)
RUNTIME_ORACLE_RUNTIME_GLOBS = (
    "res/ecmascript/modules/**/*.js",
    # The introspector declares apiversion 1, so ecmascript.c:913-919 runs
    # this bootstrap in its context before it -- anything the bootstrap adds
    # to a cached module is surface the capture sees and the static scanner
    # does not. The manifest is stamped too, because it is what selects the
    # bootstrap: flipping it to apiversion 2 changes what was observed while
    # every other input stays byte-identical.
    "res/ecmascript/legacy/api-v1.js",
    # Every loadable file in the plugin directory, not just these two:
    # es_modsearch() tries the plugin directory BEFORE
    # dataroot://res/ecmascript/modules (ecmascript.c:443-452), so a url.js
    # dropped next to the introspector shadows the core module for its own
    # require() and changes what the capture sees.
    "support/devtools/api-introspector/**/*.js",
    "support/devtools/api-introspector/**/*.json",
)
INTROSPECTOR_DIR = (
    REPO_ROOT / "support" / "devtools" / "api-introspector")
RUNTIME_ORACLE_INPUT_GLOBS = (
    RUNTIME_ORACLE_RUNTIME_GLOBS + RUNTIME_ORACLE_COMPILED_GLOBS)
RUNTIME_ORACLE_RECAPTURE = (
    "recapture in the checkout that owns build.debug:\n"
    "    mdev run -p support/devtools/api-introspector --name introspect \\\n"
    "        --extra-flags --bypass-ecmascript-acl\n"
    "    mdev open introspect:page\n"
    "  then adopt the payload printed after the route opened:\n"
    "    gen.py --adopt-oracle <captured.json>")

# `/` begins a regex literal after these words and division after any other
# identifier. Without the distinction `return /a\\/b/` reads as a division
# followed by a comment.
_JS_REGEX_KEYWORDS = frozenset((
    "return", "typeof", "instanceof", "in", "of", "new", "delete", "void",
    "throw", "case", "do", "else", "yield", "await",
    # ASI ends these at the newline, so what follows is a fresh statement and
    # may open with a regex: `debugger\n/x[/*]y/.test(v)` is live code.
    # Division never follows any of the three, so allowing a regex here can
    # only ever be right.
    "break", "continue", "debugger",
))
# `break outer` and `continue outer` put a LABEL where the keyword was, so
# the word before it is what says a statement just ended.
_JS_LABELLED_JUMPS = frozenset(("break", "continue"))
# The four LineTerminator code points. CR is one of them on its own -- a
# CR-only file is not a Windows artefact but valid JavaScript -- and Duktape
# also treats U+2028 and U+2029 as terminators
# (ext/duktape/duktape.c:10493-10494). A line comment ends at any of them and
# a regex literal crosses none. Scanning for LF alone reads the live code
# after one as part of the comment before it, which is the whole file in a
# CR-only source. CRLF still hashes the same as LF: the run of terminators
# collapses to a single newline either way.
_JS_LINE_TERMINATORS = "\n\r\u2028\u2029"
# A `)` that closes one of these closes a CONDITION, and a regex may follow
# it: `if (ready) /x[/*]y/.test(s)` is legal. Reading that `/` as division
# lets the `/*` inside the character class open a comment that runs to the
# next `*/` -- possibly the end of the file -- which deletes real code from
# the hash and leaves a stale oracle accepted. Every other `)` ends a call or
# a grouping, where `/` is division.
_JS_CONDITION_KEYWORDS = frozenset(("if", "while", "for", "with"))


def _js_next_terminator(source: str, start: int) -> int:
    """Index of the next JavaScript line terminator at or after `start`, or
    -1. LF is not the only one; see _JS_LINE_TERMINATORS."""
    best = -1
    for char in _JS_LINE_TERMINATORS:
        found = source.find(char, start)
        if found >= 0 and (best < 0 or found < best):
            best = found
    return best


def _js_regex_end(source: str, start: int) -> int:
    """Index just past the regex literal at `start`, or `start` if there is
    none. A literal cannot span a newline, so an unterminated scan is the
    signal that this `/` was division after all."""
    index = start + 1
    length = len(source)
    in_class = False
    while index < length:
        char = source[index]
        if char == "\\":
            index += 2
            continue
        if char in _JS_LINE_TERMINATORS:
            return start
        if in_class:
            if char == "]":
                in_class = False
        elif char == "[":
            in_class = True
        elif char == "/":
            index += 1
            while index < length and source[index].isalpha():
                index += 1
            return index
        index += 1
    return start


def _js_spans(source: str) -> list[tuple[str, str]]:
    """`source` split into ("code" | "literal" | "comment", text) spans.

    One scanner, because the three questions are the same question: a `//`
    means a comment only where a string, a template or a regex is not already
    open. Callers decide what to do with each kind; getting the split wrong
    in the literal direction deletes real code, which is the failure that
    passes green.

    C is scanned by the same routine. Its comment and string syntax is the
    same, it has no regex literals, and the regex heuristic cannot lose code
    on it: a span wrongly taken for a regex is emitted verbatim.
    """
    spans: list[tuple[str, str]] = []
    index = 0
    length = len(source)
    prev = ""
    word = ""
    prev_word = ""
    prev_word2 = ""
    code_start = 0
    # The word before each open paren, so a `)` knows whether it closed a
    # condition.
    paren_words: list[str] = []
    closed_word = ""

    def flush(stop: int) -> None:
        if stop > code_start:
            spans.append(("code", source[code_start:stop]))

    while index < length:
        char = source[index]
        if char in "\"'`":
            cursor = index + 1
            while cursor < length:
                current = source[cursor]
                if current == "\\":
                    cursor += 2
                    continue
                if current == char:
                    cursor += 1
                    break
                if current in _JS_LINE_TERMINATORS and char != "`":
                    break
                cursor += 1
            flush(index)
            spans.append(("literal", source[index:cursor]))
            code_start = cursor
            prev, word, prev_word = char, "", ""
            index = cursor
            continue
        if char == "/" and index + 1 < length and source[index + 1] == "/":
            newline = _js_next_terminator(source, index)
            stop = length if newline < 0 else newline
            flush(index)
            spans.append(("comment", source[index:stop]))
            code_start = stop
            index = stop
            continue
        if char == "/" and index + 1 < length and source[index + 1] == "*":
            close = source.find("*/", index + 2)
            stop = length if close < 0 else close + 2
            flush(index)
            spans.append(("comment", source[index:stop]))
            code_start = stop
            index = stop
            continue
        if char == "/" and _js_regex_allowed(
                prev, word or prev_word,
                prev_word if word else prev_word2, closed_word):
            stop = _js_regex_end(source, index)
            if stop > index:
                flush(index)
                spans.append(("literal", source[index:stop]))
                code_start = stop
                prev, word, prev_word = "/", "", ""
                index = stop
                continue
        if char == "(":
            paren_words.append(word or prev_word)
        elif char == ")":
            closed_word = paren_words.pop() if paren_words else ""
        # A word ends at anything that is not a word character, whitespace
        # included: `break outer` is two words, and gluing them across the
        # space hid the label case entirely.
        if char.isalnum() or char in "_$":
            word += char
        else:
            if word:
                prev_word2, prev_word = prev_word, word
                word = ""
            if not char.isspace():
                prev_word2, prev_word = "", ""
        if not char.isspace():
            prev = char
        index += 1
    flush(length)
    return spans


def _js_regex_allowed(prev: str, prev_word: str, prev_word2: str,
                      closed_word: str) -> bool:
    if prev == "":
        return True
    if prev == ")":
        return closed_word in _JS_CONDITION_KEYWORDS
    if prev == "]":
        return False
    if prev.isalnum() or prev in "_$":
        return (prev_word in _JS_REGEX_KEYWORDS
                or prev_word2 in _JS_LABELLED_JUMPS)
    # `}` ends a block (regex legal after it) or an object literal (division
    # legal, and absurd). Reading it as a regex start is the safe half: a
    # span wrongly taken for a regex is copied out verbatim, which can only
    # keep a comment, never drop code.
    return True


def _js_code_only(source: str) -> str:
    """`source` with its comments removed and everything else byte-identical.

    A comment becomes a space plus its newlines: the space so `a/*x*/b`
    cannot read as `ab`, the newlines because dropping them would join two
    statements that ASI kept apart.
    """
    out = []
    for kind, span in _js_spans(source):
        if kind == "comment":
            out.append(" " + "\n" * span.count("\n"))
        else:
            out.append(span)
    return "".join(out)


def _js_hash_text(source: str) -> str:
    """The text the freshness stamp is taken over.

    Comments are gone and whitespace BETWEEN tokens is normalized --
    re-indenting a module cannot move a member any more than commenting it
    can, and leaving either in makes the stamp go red for nothing. Two
    things are deliberately not normalized:

    - whitespace INSIDE a string, template or regex, because it is content:
      `exports["a  b"]` and `exports["a b"]` declare different members, and
      an introspector regex can change what it matches by a space alone;
    - newlines between lines of code, because automatic semicolon insertion
      reads them, so `return\n  x` and `return x` are different programs.
    """
    out = []
    for kind, span in _js_spans(source):
        if kind == "literal":
            out.append(span)
            continue
        if kind == "comment":
            span = " " + "\n" * sum(span.count(char)
                                    for char in _JS_LINE_TERMINATORS)
        span = re.sub(r"[\r\u2028\u2029]", "\n", span)
        span = re.sub(r"[^\S\n]+", " ", span)
        out.append(re.sub(r" ?\n\s*", "\n", span))
    return "".join(out).strip()


def _is_oracle_input(path: Path) -> bool:
    # The oracle itself lives in the plugin directory and is matched by the
    # `**/*.json` glob. Stamping it into its own stamp cannot converge:
    # writing the file changes the digest that was just written.
    return path.is_file() and path.name != RUNTIME_ORACLE_PATH.name


def runtime_oracle_input_digests(
        root: Path | None = None) -> dict[str, str]:
    """sha256 of each oracle input's code, comments removed."""
    base = REPO_ROOT if root is None else root
    digests: dict[str, str] = {}
    for pattern in RUNTIME_ORACLE_INPUT_GLOBS:
        for path in sorted(base.glob(pattern)):
            if not _is_oracle_input(path):
                continue
            digests[path.relative_to(base).as_posix()] = hashlib.sha256(
                _js_hash_text(path.read_text(encoding="utf-8"))
                .encode("utf-8")).hexdigest()
    return digests


def runtime_oracle_inputs_digest(digests: dict[str, str]) -> str:
    joined = "".join(
        "%s\0%s\n" % (name, digests[name]) for name in sorted(digests))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def runtime_oracle_stale_inputs(
        stamped: Any, root: Path | None = None) -> list[str]:
    """Human-readable reasons the stamp no longer describes the tree."""
    if not isinstance(stamped, dict):
        return ["the stamp carries no per-file digests"]
    current = runtime_oracle_input_digests(root)
    reasons = []
    for name in sorted(set(current) - set(stamped)):
        reasons.append("added since the capture: %s" % name)
    for name in sorted(set(stamped) - set(current)):
        reasons.append("gone since the capture: %s" % name)
    for name in sorted(set(stamped) & set(current)):
        if stamped[name] != current[name]:
            reasons.append("code changed since the capture: %s" % name)
    return reasons

ATTRIB_C = REPO_ROOT / "src" / "ui" / "glw" / "glw_view_attrib.c"
EVAL_C = REPO_ROOT / "src" / "ui" / "glw" / "glw_view_eval.c"
GLW_DIR = REPO_ROOT / "src" / "ui" / "glw"
ECMASCRIPT_DIR = REPO_ROOT / "src" / "ecmascript"
COMMONJS_DIR = REPO_ROOT / "res" / "ecmascript" / "modules"

CURATED_OPERATORS = METADATA_DIR / "curated_operators.json"
CURATED_SCOPES = METADATA_DIR / "curated_scopes.json"
CURATED_PLUGIN_MANIFEST = METADATA_DIR / "curated_plugin_manifest.json"
CURATED_INTERPRETER_GLOBALS = (
    METADATA_DIR / "curated_interpreter_globals.json")
CURATED_INTERPRETER_OBJECTS = (
    METADATA_DIR / "curated_interpreter_objects.json")

SCHEMA_VERSION = 1
GENERATED_BY = "support/devtools/metadata/gen.py"

ATTRIB_TABLE_DECL = "token_attrib_t attribtab[] = {"
FUNC_TABLE_DECL = "token_func_t funcvec[] = {"

WIDGET_DECL_RE = re.compile(r'^(?:static\s+)?glw_class_t\s+(\w+)\s*=\s*\{')
GC_NAME_RE = re.compile(r'\.gc_name\s*=\s*"([^"]+)"')
GC_NAME2_RE = re.compile(r'\.gc_name2\s*=\s*"([^"]+)"')
REGISTER_RE = re.compile(r'GLW_REGISTER_CLASS\((\w+)\)')
ES_MODULE_RE = re.compile(
    r'^\s*ES_MODULE\("([^"]+)",\s*([A-Za-z_]\w*)\s*\);')
COMMONJS_EXPORT_RE = re.compile(
    r'^\s*(?:module\.)?exports(?:\.([A-Za-z_$][A-Za-z0-9_$]*)'
    r'|\[\s*([\'\"])([^\'\"]+)\2\s*\])\s*=')

# Value type + confidence inferred from attribtab[]'s setter function (2nd
# field). A `|`-joined value type is a union; an `[]` suffix means a vector of
# that type. "high" = the setter is unambiguous about the wire type; "medium"
# = the setter is polymorphic (set_number dispatches to the target class's
# own gc_set_int/gc_set_float switch, so the actual type is class-specific).
VALUE_TYPE_MAP: dict[str, tuple[str, str]] = {
    "set_style": ("style", "high"),
    "set_rstring": ("string", "high"),
    "set_caption": ("string", "high"),
    "set_font": ("string", "high"),
    "set_fs": ("string", "high"),
    "set_source": ("string|string[]", "high"),
    "set_alt": ("string", "high"),
    "mod_hidden": ("bool", "high"),
    "mod_flag": ("bool", "high"),
    "set_float": ("float", "high"),
    "set_int": ("int", "high"),
    "set_number": ("number", "medium"),
    "set_float3": ("vec3", "high"),
    "set_float4": ("vec4", "high"),
    "set_int16_4": ("vec4i", "high"),
    "set_margin": ("vec4i", "high"),
    "set_align": ("enum", "high"),
    "set_transition_effect": ("enum", "high"),
    "set_args": ("block", "high"),
    "set_propref": ("propref", "high"),
}

# Enum provenance is intentionally explicit: each setter maps to the C strtab
# that defines its legal values. Attribute records using a mapped setter carry
# optional `enumValues`, preserving the table's source order.
ENUM_TABLE_MAP: dict[str, str] = {
    "set_align": "aligntab",
    "set_transition_effect": "transitiontab",
}


class GenError(Exception):
    pass


def rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


# ---------------------------------------------------------------------------
# Generic C array-of-struct-initializer scanner, shared by attribtab[] and
# funcvec[]: both are `static const token_*_t name[] = { {...}, {...}, };`
# tables whose entries are plain comma-separated identifier/literal fields
# (no nested braces) but may span multiple physical lines (attribtab's
# GLW_ATTRIB_FLAG_NO_SUBSCRIPTION entries do). Returns a list of
# (raw_entry_text, first_line_of_entry) tuples, one per top-level `{...}`.
# ---------------------------------------------------------------------------

def scan_array_block(path: Path, decl_marker: str,
                      close_marker: str = "};") -> list[tuple[str, int]]:
    if not path.is_file():
        raise GenError("source file not found: %s" % path)
    lines = path.read_text(encoding="utf-8").splitlines()

    start_idx = None
    for i, line in enumerate(lines):
        if decl_marker in line:
            start_idx = i
            break
    if start_idx is None:
        raise GenError("table %r not found in %s" % (decl_marker, path))

    end_idx = None
    for i in range(start_idx + 1, len(lines)):
        if lines[i].strip() == close_marker:
            end_idx = i
            break
    if end_idx is None:
        raise GenError("closing %r not found after %r in %s"
                        % (close_marker, decl_marker, path))

    entries: list[tuple[str, int]] = []
    depth = 0
    buf: list[str] = []
    entry_line = 0
    in_str = False
    str_q = ""

    for i_line in range(start_idx + 1, end_idx):
        line_no = i_line + 1  # 1-based
        text = lines[i_line]
        j = 0
        n = len(text)
        while j < n:
            c = text[j]
            if in_str:
                buf.append(c)
                if c == "\\" and j + 1 < n:
                    j += 1
                    buf.append(text[j])
                elif c == str_q:
                    in_str = False
                j += 1
                continue
            if c == "/" and j + 1 < n and text[j + 1] == "/":
                break  # line comment: rest of physical line is not code
            if c in ('"', "'"):
                if depth > 0:
                    in_str = True
                    str_q = c
                    buf.append(c)
                j += 1
                continue
            if c == "{":
                if depth == 0:
                    buf = []
                    entry_line = line_no
                else:
                    buf.append(c)
                depth += 1
                j += 1
                continue
            if c == "}":
                depth -= 1
                if depth < 0:
                    raise GenError("unbalanced '}' at %s:%d" % (path, line_no))
                if depth == 0:
                    entries.append(("".join(buf), entry_line))
                else:
                    buf.append(c)
                j += 1
                continue
            if depth > 0:
                buf.append(c)
            j += 1

    if depth != 0:
        raise GenError("unbalanced braces scanning %r in %s"
                        % (decl_marker, path))
    return entries


PREPROC_GUARD_RE = re.compile(r"^\s*#\s*(if|ifdef|ifndef|else|endif)\b")


def preprocessor_conditions(lines: list[str], path: Path) -> list[str | None]:
    """Return the active raw preprocessor guard text for every source line.

    `#else` is represented as the inverse of the guard it replaces. This is
    deliberately descriptive rather than an attempt to evaluate C macros.
    """
    guard_stack: list[str] = []
    conditions: list[str | None] = []
    for line_no, line in enumerate(lines, 1):
        match = PREPROC_GUARD_RE.match(line)
        if match:
            directive = match.group(1)
            if directive in ("if", "ifdef", "ifndef"):
                guard_stack.append(line.strip())
            elif directive == "else":
                if not guard_stack:
                    raise GenError("#else without an active guard at %s:%d"
                                   % (path, line_no))
                guard_stack[-1] = "!(%s)" % guard_stack[-1]
            else:
                if not guard_stack:
                    raise GenError("#endif without an active guard at %s:%d"
                                   % (path, line_no))
                guard_stack.pop()
        conditions.append(" && ".join(guard_stack) or None)
    if guard_stack:
        raise GenError("unterminated preprocessor guard in %s" % path)
    return conditions


def combine_conditions(*conditions: str | None) -> str | None:
    """Join distinct active guard strings while preserving source order."""
    terms: list[str] = []
    for condition in conditions:
        if condition and condition not in terms:
            terms.append(condition)
    return " && ".join(terms) or None


def split_fields(text: str) -> list[str]:
    """Top-level comma-separated fields of one entry's inner text (paren/
    bracket depth tracked so a field like `foo(a, b)` -- not used by either
    table today, but kept generic -- doesn't get split)."""
    fields: list[str] = []
    buf: list[str] = []
    depth = 0
    in_str = False
    str_q = ""
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if in_str:
            buf.append(c)
            if c == "\\" and i + 1 < n:
                i += 1
                buf.append(text[i])
            elif c == str_q:
                in_str = False
            i += 1
            continue
        if c in ('"', "'"):
            in_str = True
            str_q = c
            buf.append(c)
            i += 1
            continue
        if c in "([":
            depth += 1
            buf.append(c)
            i += 1
            continue
        if c in ")]":
            depth -= 1
            buf.append(c)
            i += 1
            continue
        if c == "," and depth == 0:
            fields.append("".join(buf).strip())
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        fields.append(tail)
    return [f for f in fields if f]


def unquote(field: str) -> str:
    if len(field) >= 2 and field[0] == '"' and field[-1] == '"':
        return field[1:-1]
    return field


# ---------------------------------------------------------------------------
# glw.attributes -- attribtab[]
# ---------------------------------------------------------------------------

def scan_strtab(table_name: str) -> list[str]:
    entries = scan_array_block(
        ATTRIB_C, "struct strtab %s[] = {" % table_name)
    values: list[str] = []
    for text, line in entries:
        fields = split_fields(text)
        if not fields or len(fields[0]) < 2 or not (
                fields[0].startswith('"') and fields[0].endswith('"')):
            raise GenError("invalid string entry in strtab %s at %s:%d"
                           % (table_name, ATTRIB_C, line))
        values.append(unquote(fields[0]))
    return values


def build_attributes() -> list[dict[str, Any]]:
    entries = scan_array_block(ATTRIB_C, ATTRIB_TABLE_DECL)
    enum_values = {
        setter: scan_strtab(table_name)
        for setter, table_name in ENUM_TABLE_MAP.items()
    }
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for text, line in entries:
        fields = split_fields(text)
        if not fields:
            continue
        name = unquote(fields[0])
        if name in seen:
            raise GenError("duplicate attribute name in source: %s" % name)
        seen.add(name)

        setter = fields[1] if len(fields) > 1 else None
        attrib_const = (fields[2] if len(fields) > 2 and fields[2] != "0"
                         else None)
        fn = fields[3] if len(fields) > 3 and fields[3] != "NULL" else None
        no_sub = (len(fields) > 4
                  and fields[4] == "GLW_ATTRIB_FLAG_NO_SUBSCRIPTION")
        value_type, confidence = VALUE_TYPE_MAP.get(setter, ("unknown", "low"))

        record = {
            "name": name,
            "valueType": value_type,
            "confidence": confidence,
            "setter": setter,
            "attribConst": attrib_const,
            "fn": fn,
            "noSubscription": no_sub,
            "source": {"file": rel(ATTRIB_C), "line": line},
        }
        if setter in enum_values:
            record["enumValues"] = enum_values[setter]
        records.append(record)
    records.sort(key=lambda r: r["name"])
    return records


# ---------------------------------------------------------------------------
# glw.functions -- funcvec[]
# ---------------------------------------------------------------------------

def build_functions() -> list[dict[str, Any]]:
    lines = EVAL_C.read_text(encoding="utf-8").splitlines()
    conditions = preprocessor_conditions(lines, EVAL_C)
    entries = scan_array_block(EVAL_C, FUNC_TABLE_DECL)
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for text, line in entries:
        fields = split_fields(text)
        if not fields:
            continue
        name = unquote(fields[0])
        if name in seen:
            raise GenError("duplicate function name in source: %s" % name)
        seen.add(name)

        nargs = int(fields[1]) if len(fields) > 1 else None
        impl = fields[2] if len(fields) > 2 else None
        ctor = fields[3] if len(fields) > 3 and fields[3] != "NULL" else None
        dtor = fields[4] if len(fields) > 4 and fields[4] != "NULL" else None
        preproc = (fields[5] if len(fields) > 5 and fields[5] != "NULL"
                   else None)

        record = {
            "name": name,
            "nargs": nargs,
            "variadic": nargs == -1,
            "impl": impl,
            "ctor": ctor is not None,
            "dtor": dtor is not None,
            "preproc": preproc is not None,
            "source": {"file": rel(EVAL_C), "line": line},
        }
        condition = conditions[line - 1]
        if condition:
            record["condition"] = condition
        records.append(record)
    records.sort(key=lambda r: r["name"])
    return records


# ---------------------------------------------------------------------------
# glw.widgets -- glw_class_t designated initializers across glw_*.c
# ---------------------------------------------------------------------------

def build_widgets() -> list[dict[str, Any]]:
    files = sorted(GLW_DIR.glob("glw_*.c"))

    file_contents: dict[Path, tuple[list[str], list[str | None]]] = {}
    for f in files:
        lines = f.read_text(encoding="utf-8").splitlines()
        file_contents[f] = (lines, preprocessor_conditions(lines, f))

    registrations: dict[str, list[str | None]] = {}
    for f in files:
        lines, conditions = file_contents[f]
        for i, line in enumerate(lines):
            for match in REGISTER_RE.finditer(line):
                registrations.setdefault(match.group(1), []).append(conditions[i])

    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for f in files:
        lines, conditions = file_contents[f]
        n = len(lines)
        i = 0
        while i < n:
            m = WIDGET_DECL_RE.match(lines[i].strip())
            if not m:
                i += 1
                continue
            symbol = m.group(1)
            depth = lines[i].count("{") - lines[i].count("}")
            gc_name = None
            gc_name_line = None
            gc_name2 = None
            j = i + 1
            while j < n and depth > 0:
                line = lines[j]
                depth += line.count("{") - line.count("}")
                nm = GC_NAME_RE.search(line)
                if nm and gc_name is None:
                    gc_name = nm.group(1)
                    gc_name_line = j + 1
                nm2 = GC_NAME2_RE.search(line)
                if nm2:
                    gc_name2 = nm2.group(1)
                j += 1
            if gc_name is None:
                raise GenError("glw_class_t %s in %s has no .gc_name"
                                % (symbol, f))
            if gc_name in seen:
                raise GenError("duplicate widget gc_name: %s" % gc_name)
            seen.add(gc_name)
            record = {
                "name": gc_name,
                "aliases": [gc_name2] if gc_name2 else [],
                "symbol": symbol,
                "registered": symbol in registrations,
                "source": {"file": rel(f), "line": gc_name_line},
            }
            condition = combine_conditions(
                conditions[gc_name_line - 1], *registrations.get(symbol, []))
            if condition:
                record["condition"] = condition
            records.append(record)
            i = j
    records.sort(key=lambda r: r["name"])
    return records


# ---------------------------------------------------------------------------
# js.modules -- native ES_MODULE tables and static CommonJS exports
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Native signatures -- what the C body says about its own arguments
# ---------------------------------------------------------------------------
#
# `duk_function_list_entry` carries `{name, func, nargs}`, which is why every
# native was emitted `(...args: any[]) => any` and, after movian#208, still
# `(arg0?: any) => any`. But `func` names a C function in this same tree, and
# that body says a great deal more: which Duktape reader it applies to each
# argument index, what it calls the result, and what it pushes back.
#
# Three reader families appear, and the difference between them is the whole
# reason `reader` is recorded rather than flattened away:
#
#   duk_require_X(ctx, n)   argument n must already be an X; anything else
#                           throws before the body runs. Enforcement.
#   duk_to_X(ctx, n)        argument n is coerced to X. The runtime accepts
#                           anything -- duk_to_string turns 42 into "42" and
#                           does not complain. Intent, not enforcement.
#   duk_get_X(ctx, n)       argument n is read when it is an X and silently
#                           replaced by a default when it is not. Also intent.
#
# The declared type follows the intent in all three cases, because a type
# library exists to say what a function is for. `reader` keeps the difference
# in the metadata, so the choice stays visible and revisable without anyone
# re-reading the C.
NATIVE_ARG_READERS: dict[str, tuple[str, str]] = {
    "duk_require_string": ("string", "require"),
    "duk_require_number": ("number", "require"),
    "duk_require_int": ("number", "require"),
    "duk_require_uint": ("number", "require"),
    "duk_require_boolean": ("boolean", "require"),
    "duk_to_string": ("string", "coerce"),
    "duk_safe_to_string": ("string", "coerce"),
    "duk_to_lstring": ("string", "coerce"),
    "duk_to_number": ("number", "coerce"),
    "duk_to_int": ("number", "coerce"),
    "duk_to_int32": ("number", "coerce"),
    "duk_to_uint": ("number", "coerce"),
    "duk_to_boolean": ("boolean", "coerce"),
    "duk_get_string": ("string", "get"),
    "duk_get_number": ("number", "get"),
    "duk_get_int": ("number", "get"),
    "duk_get_uint": ("number", "get"),
    "duk_get_boolean": ("boolean", "get"),
}

# `es_get_native_obj(ctx, n, &es_native_prop)` says argument n is a wrapped C
# pointer of a named class. That class is written at the call site, so the
# type comes out of the call rather than out of any curated table -- and a
# wrapped pointer has no JavaScript shape at all, which is exactly why it
# deserves an opaque type instead of `any`.
NATIVE_HANDLE_READERS: dict[str, int] = {
    "es_get_native_obj": 2,
    "es_resource_get": 2,
}

# `es_get_native_obj_nothrow` looks identical to `es_get_native_obj` and means
# the opposite: it accepts anything and answers NULL instead of throwing
# (src/ecmascript/es_native_obj.c). `native/prop.isValue` is a predicate over
# arbitrary values and `native/prop.moveBefore` takes null as its second
# argument -- `res/ecmascript/modules/movian/page.js:105` calls it that way.
# Branding either would reject a call the runtime is built to accept.
NATIVE_PROBE_READERS: dict[str, int] = {
    "es_get_native_obj_nothrow": 2,
}

# Reads whose result has no honest JavaScript spelling here. They are recorded
# as FACTS rather than ignored, because ignoring them let a primitive reader in
# one branch speak for the whole slot. `native/websocket.clientSend` tries
# `duk_get_buffer_data(ctx, 1)` first and falls back to `duk_to_string` -- so a
# buffer is a first-class binary send, which this repository's own accepted
# oracle already states at tests/reference/websocket.d.ts:42-53, and emitting
# `buf?: string` contradicted it.
NATIVE_ARG_BUFFER_READERS: dict[str, str] = {
    "duk_require_buffer": "require",
    "duk_require_buffer_data": "require",
    "duk_to_buffer": "coerce",
    "duk_get_buffer": "get",
    "duk_get_buffer_data": "get",
}

# A pointer is not a buffer. `native/prop.release` reads argument 0 with
# duk_require_pointer and was being reported as taking a buffer -- the label
# was wrong, which is worse than having no label, because a residue nobody
# can trust is a residue nobody re-reads.
NATIVE_ARG_POINTER_READERS = frozenset(("duk_require_pointer",))

NATIVE_ARG_OPAQUE = (frozenset(NATIVE_ARG_BUFFER_READERS)
                     | NATIVE_ARG_POINTER_READERS)

# The one shape the accepted calibration corpus already settled, reused here
# verbatim rather than re-derived: tests/reference/movian-http.d.ts:13-40.
# The brand is uninhabitable on purpose. Without it the interface is purely
# structural, an ordinary `number[]` satisfies it, and it type-checks straight
# into `native/websocket.clientSend`'s binary branch -- where
# duk_get_buffer_data does not recognise an array and the value silently goes
# out as a stringified text frame instead.
NATIVE_BUFFER_TYPE = "DuktapeBuffer"
NATIVE_BUFFER_DECLARATION = (
    "interface %s {" % NATIVE_BUFFER_TYPE,
    "  readonly __duktapeBuffer__: never;",
    "  readonly length: number;",
    "  [index: number]: number;",
    "  toString(): string;",
    # res/ecmascript/modules/fs.js:19 calls buf.valueOf() before handing the
    # value to native/fs.read, so the raw view is part of the surface.
    "  valueOf(): %s;" % NATIVE_BUFFER_TYPE,
    "}",
)

# `duk_is_X(ctx, n)` asks a question. A body that asks is a body that accepts
# more than one shape at that index, so a test is never read as a type -- it
# is the opposite evidence, and it suppresses whatever a reader claims.
# `duk_get_prop_string(ctx, n, "key")` and the `es_prop_*` family read a NAMED
# PROPERTY off argument n. That proves n is an object, and it is the evidence
# that was missing when `native/prop.sendEvent` came out `string`: the
# `openurl` branch reads slot 2 as an options object (es_prop.c:834-841,
# used that way by res/ecmascript/modules/movian/itemhook.js:23-25) while a
# different branch reads it as a string, and only the string was visible.
NATIVE_ARG_OBJECT_READERS: dict[str, str] = {
    "es_prop_is_true": "boolean",
    "es_prop_to_rstr": "string",
    "es_prop_to_int": "number",
    "es_prop_to_double": "number",
    "duk_has_prop_string": "boolean",
    # `duk_get_prop_string` puts the member on the stack for the body to read
    # however it likes, so the key is evidence and its type is not.
    "duk_get_prop_string": "any",
}

# An options object is emitted with an index signature as well as its known
# keys. The keys are what the C actually reads; the index signature is the
# admission that reading only those is not the same as rejecting the rest --
# Duktape hands the object over untouched, and TypeScript's excess-property
# check would otherwise turn a call carrying one extra key into an error.
NATIVE_OPTIONS_INDEX_SIGNATURE = "[key: string]: any;"

NATIVE_ARG_TESTS = frozenset((
    "duk_is_string", "duk_is_number", "duk_is_boolean", "duk_is_null",
    "duk_is_undefined", "duk_is_null_or_undefined", "duk_is_object",
    "duk_is_object_coercible", "duk_is_buffer", "duk_is_function",
    "duk_is_array", "duk_is_pointer", "duk_is_nan",
))

# What `duk_push_X` leaves on the stack, for the return type. `duk_push_object`
# describes a shape this scan cannot name, so it resolves to nothing rather
# than to an invented interface -- the rule the CommonJS path already follows
# for anonymous returns.
NATIVE_PUSH_TYPES: dict[str, str] = {
    "duk_push_string": "string",
    "duk_push_lstring": "string",
    "duk_push_sprintf": "string",
    "duk_push_number": "number",
    "duk_push_int": "number",
    "duk_push_uint": "number",
    "duk_push_nan": "number",
    "duk_push_boolean": "boolean",
    "duk_push_true": "boolean",
    "duk_push_false": "boolean",
    "duk_push_null": "null",
    "duk_push_undefined": "undefined",
    "duk_push_array": "any[]",
    "duk_push_object": "object",
}

# TypeScript keywords, plus the padding spelling itself. A C local called
# `new` or `arg0` is a fine C local and an unusable parameter name.
NATIVE_NAME_REJECT = frozenset((
    "any", "arguments", "as", "async", "await", "boolean", "break", "case",
    "catch", "class", "const", "continue", "debugger", "declare", "default",
    "delete", "do", "else", "enum", "eval", "export", "extends", "false",
    "finally", "for", "function", "if", "implements", "import", "in",
    "instanceof", "interface", "let", "namespace", "new", "null", "number",
    "package", "private", "protected", "public", "return", "static", "string",
    "super", "switch", "symbol", "this", "throw", "true", "try", "type",
    "typeof", "undefined", "unknown", "var", "void", "while", "with", "yield",
))

# Calls that consume the value on the stack top. They are what separates an
# element being stored into a container from the container itself.
NATIVE_PUT_CALLS = frozenset((
    "duk_put_prop_index", "duk_put_prop_string", "duk_put_prop",
    "duk_put_prop_lstring",
))

NATIVE_HANDLE_PUSH = "es_push_native_obj"
NATIVE_RESOURCE_PUSH = "es_resource_push"
RESOURCE_CREATE_RE = re.compile(
    r"\bes_resource_(?:create|alloc)\s*\([^;]*?&\s*(es_resource_\w+)")

NATIVE_ARG_INDEX_RE = re.compile(r"^arg\d+$")
NATIVE_C_DEF_RE = re.compile(r"^([A-Za-z_]\w*)\(", re.M)

# How deep a fact is chased through helpers. es_file_basename -> get_filename
# is one hop; es_prop_setValue -> es_stprop_get -> es_get_native_obj is two.
# Nothing in src/ecmascript needs a third, and a cap keeps a cycle from
# turning into a hang.
NATIVE_HELPER_DEPTH = 3


def _brace_match(text: str, open_index: int, path: Path) -> int:
    """Index of the `}` (or `)`) closing the bracket at `open_index`."""
    closing = {"{": "}", "(": ")"}[text[open_index]]
    opening = text[open_index]
    depth = 0
    index = open_index
    while index < len(text):
        char = text[index]
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise GenError("unbalanced %s from offset %d in %s"
                   % (opening, open_index, rel(path)))


def _split_c_params(text: str) -> list[str]:
    """Top-level comma split of a C parameter list."""
    parts: list[str] = []
    depth = 0
    current = ""
    for char in text:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        parts.append(current.strip())
    return parts


def _c_param_name(declaration: str) -> str | None:
    match = re.search(r"([A-Za-z_]\w*)\s*$", declaration.strip())
    return match.group(1) if match else None


def scan_c_functions() -> dict[str, dict[str, Any]]:
    """Every C function definition in src/ecmascript, by name.

    The Duktape context parameter is not always spelled `ctx` -- es_string.c
    calls it `duk`, and two natives were invisible to an earlier draft of this
    scan for exactly that reason. Whatever it is called is captured and used,
    because every argument reader in the body is written against that name.
    """
    functions: dict[str, dict[str, Any]] = {}
    for path in sorted(ECMASCRIPT_DIR.glob("es_*.c")):
        text = path.read_text(encoding="utf-8")
        for match in NATIVE_C_DEF_RE.finditer(text):
            open_paren = match.end() - 1
            try:
                close_paren = _brace_match(text, open_paren, path)
            except GenError:
                continue
            tail = text[close_paren + 1:close_paren + 40]
            if not re.match(r"\s*\{", tail):
                continue
            params = _split_c_params(text[open_paren + 1:close_paren])
            if not params or not re.match(r"^\s*duk_context\s*\*", params[0]):
                continue
            name = match.group(1)
            open_brace = text.index("{", close_paren)
            close_brace = _brace_match(text, open_brace, path)
            if name in functions:
                # Two static helpers in different translation units may share
                # a name; C allows it and nothing here can tell which one a
                # call meant. Poisoning the entry makes both contribute no
                # facts, which is a narrower failure than guessing.
                functions[name] = None
                continue
            functions[name] = {
                "file": rel(path),
                "line": text[:match.start()].count("\n") + 1,
                "ctx": _c_param_name(params[0]),
                "params": [_c_param_name(part) for part in params],
                "body": text[open_brace:close_brace + 1],
            }
    return functions


def _handle_type_name(symbol: str) -> str:
    """`es_native_gumbo_node` -> `GumboNodeHandle`."""
    stem = re.sub(r"^es_(native|resource)_", "", symbol)
    return "".join(part.title() for part in stem.split("_")) + "Handle"


def _fact_reader(helper: str) -> dict[str, Any] | None:
    known = NATIVE_ARG_READERS.get(helper)
    if known is None:
        return None
    return {"type": known[0], "reader": known[1]}


def _merge_fact(target: dict[Any, Any], key: Any,
                fact: dict[str, Any] | None) -> None:
    """Record a fact about `key`, or mark it contested.

    Two helpers disagreeing about one argument index is not a tie to break --
    it means the body accepts more than one shape there, and `any` is the only
    truthful answer. `None` is the poison value that says so, and once poured
    it is never washed out.
    """
    if key in target and target[key] != fact:
        target[key] = None
        return
    if key not in target:
        target[key] = fact


def _c_calls(body: str, callee: str, ctx: str) -> list[list[str]]:
    """Argument lists of every `callee(ctx, ...)` call in `body`."""
    calls: list[list[str]] = []
    pattern = re.compile(r"\b%s\s*\(" % re.escape(callee))
    for match in pattern.finditer(body):
        open_paren = match.end() - 1
        depth = 0
        index = open_paren
        while index < len(body):
            if body[index] == "(":
                depth += 1
            elif body[index] == ")":
                depth -= 1
                if depth == 0:
                    break
            index += 1
        else:
            continue
        args = _split_c_params(body[open_paren + 1:index])
        if args and args[0].strip() == ctx:
            calls.append([arg.strip() for arg in args])
    return calls


def _function_facts(name: str, functions: dict[str, dict[str, Any]],
                    memo: dict[tuple[str, int], dict[str, Any]],
                    depth: int) -> dict[str, Any]:
    """What a C function does with each Duktape argument index.

    Returns `{"indices": {n: fact}, "params": {p: fact}, "tested": {n}}`:
    `indices` is what the body says about the caller's argument n, `params` is
    what it says about the argument index handed to its own parameter p --
    which is how `get_filename(ctx, 0, ec, 0)` transfers the string it reads
    onto argument 0 of whoever called it.

    A helper's `int` parameter is only treated as an argument index when the
    body is seen applying a reader to *that name*. es_string.c's
    `es_escape(ctx, how)` and es_timer.c's `set_timer(duk, repeat)` both take
    an int that is a mode flag, and both are called with a small literal that
    would read as an index. Requiring proof is what tells the two apart.
    """
    # Depth belongs in the key. Without it the first caller to reach a helper
    # fixes the answer every later caller gets, and the artifact starts
    # depending on the order modules happen to be scanned in.
    key = (name, depth)
    if key in memo:
        return memo[key]
    empty: dict[str, Any] = {"indices": {}, "params": {}, "tested": set()}
    record = functions.get(name)
    if record is None or depth <= 0:
        return empty
    memo[key] = empty           # cycle guard: a recursive helper sees nothing
    ctx = record["ctx"]
    body = record["body"]
    positions = {param: index for index, param in enumerate(record["params"])
                 if param}

    indices: dict[int, dict[str, Any] | None] = {}
    params: dict[int, dict[str, Any] | None] = {}
    tested: set[int] = set()
    shapes: dict[int, dict[str, str]] = {}
    primitives: dict[int, set[str]] = {}

    def place(slot: str, fact: dict[str, Any] | None) -> None:
        if re.fullmatch(r"-?\d+", slot):
            value = int(slot)
            if value >= 0:
                _merge_fact(indices, value, fact)
        elif slot in positions:
            _merge_fact(params, positions[slot], fact)

    for helper in NATIVE_ARG_TESTS:
        for args in _c_calls(body, helper, ctx):
            if len(args) >= 2 and re.fullmatch(r"\d+", args[1]):
                tested.add(int(args[1]))

    for helper in NATIVE_ARG_READERS:
        fact = _fact_reader(helper)
        for args in _c_calls(body, helper, ctx):
            if len(args) >= 2:
                place(args[1], fact)
                if fact is not None and re.fullmatch(r"\d+", args[1]):
                    primitives.setdefault(int(args[1]), set()).add(fact["type"])

    # An unspellable read is still a read. Recording it as its own fact lets
    # the ordinary conflict rule below poison a slot that some other branch
    # reads as a primitive, instead of letting that primitive stand alone.
    for helper, reader in NATIVE_ARG_BUFFER_READERS.items():
        for args in _c_calls(body, helper, ctx):
            if len(args) < 2:
                continue
            # `duk_require_buffer_data` demands a buffer. `duk_to_buffer`
            # COERCES, and a string is the coercion that matters: the accepted
            # corpus spells out at tests/reference/fs.d.ts:33-37 that
            # declaring the buffer alone rejects
            # `writeFileSync(dst, readFileSync(src))`, a round trip the
            # runtime supports. So the coercive readers keep the string.
            spelling = (NATIVE_BUFFER_TYPE if reader == "require"
                        else "string | " + NATIVE_BUFFER_TYPE)
            place(args[1], {"type": spelling, "reader": reader})

    for helper in NATIVE_ARG_POINTER_READERS:
        for args in _c_calls(body, helper, ctx):
            if len(args) >= 2:
                place(args[1], {"opaque": "pointer"})

    for helper, key_type in NATIVE_ARG_OBJECT_READERS.items():
        for args in _c_calls(body, helper, ctx):
            if len(args) < 2:
                continue
            place(args[1], {"opaque": "object"})
            if len(args) >= 3 and re.fullmatch(r"-?\d+", args[1]):
                key = args[2].strip()
                if key.startswith('"') and key.endswith('"') and len(key) > 2:
                    shapes.setdefault(int(args[1]), {})[key[1:-1]] = key_type

    for helper in NATIVE_PROBE_READERS:
        for args in _c_calls(body, helper, ctx):
            if len(args) >= 2:
                place(args[1], {"opaque": "probe"})

    for helper, class_position in NATIVE_HANDLE_READERS.items():
        for args in _c_calls(body, helper, ctx):
            if len(args) <= class_position:
                continue
            symbol = args[class_position].lstrip("&").strip()
            if not re.fullmatch(r"[A-Za-z_]\w*", symbol):
                continue
            place(args[1], {"handle": _handle_type_name(symbol),
                            "nativeClass": symbol})

    known = set(NATIVE_ARG_READERS) | set(NATIVE_HANDLE_READERS)
    inherited: list[dict[str, Any]] = []
    for callee, callee_record in functions.items():
        if callee == name or callee in known or callee_record is None:
            continue
        calls = _c_calls(body, callee, ctx)
        if not calls:
            continue
        inner = _function_facts(callee, functions, memo, depth - 1)
        inherited.append(inner)
        for args in calls:
            for index, fact in inner["indices"].items():
                _merge_fact(indices, index, fact)
            for position, fact in inner["params"].items():
                if position < len(args):
                    place(args[position], fact)
            for index in inner["tested"]:
                tested.add(index)

    for callee_facts in inherited:
        for index, shape in callee_facts.get("shapes", {}).items():
            shapes.setdefault(index, {}).update(shape)
        for index, kinds in callee_facts.get("primitives", {}).items():
            primitives.setdefault(index, set()).update(kinds)

    facts = {"indices": indices, "params": params, "tested": tested,
             "shapes": shapes, "primitives": primitives}
    memo[key] = facts
    return facts


def native_parameters(record: dict[str, Any], facts: dict[str, Any],
                      nargs: int) -> list[dict[str, Any]]:
    """Per-index facts about one native's arguments, read from its C body."""
    ctx = re.escape(record["ctx"])
    body = record["body"]
    # `const char *path = duk_to_string(ctx, 0);` names argument 0 far better
    # than `arg0` does. A parenthesised cast may sit between `=` and the call.
    # The assignment target has to be a bare local. `ehr->ehr_headreq = ...`
    # ends in an identifier too, and taking it produced the parameter name
    # `ehr_headreq` for what is an options object -- a struct field of the
    # callee's own bookkeeping, presented to a plugin author as the name of
    # their argument. The lookbehind is what keeps a member out.
    assign_re = re.compile(
        r"(?<![.\w>])([A-Za-z_]\w*)\s*=\s*(?:\([^();]*\)\s*)?"
        r"([A-Za-z_]\w*)\s*\(\s*%s\s*,\s*(\d+)" % ctx)
    names: dict[int, str] = {}
    for match in assign_re.finditer(body):
        variable, helper, index = (match.group(1), match.group(2),
                                   int(match.group(3)))
        if not (helper in NATIVE_ARG_READERS or helper in NATIVE_ARG_OPAQUE
                or helper.startswith("es_") or helper.startswith("get_")):
            continue
        if variable in NATIVE_NAME_REJECT or NATIVE_ARG_INDEX_RE.match(
                variable):
            continue
        names.setdefault(index, variable)

    params: list[dict[str, Any]] = []
    used: set[str] = set()
    for index in range(nargs):
        param: dict[str, Any] = {"index": index}
        name = names.get(index)
        fact_here = facts["indices"].get(index)
        if name is not None and name in facts["shapes"].get(index, {}):
            # The local holds ONE key off the options object, so it names a
            # member rather than the argument -- `sendEvent`'s slot 2 came out
            # `url`, which is a key of the object it accepts.
            name = None
        if fact_here is not None and fact_here.get("opaque") == "object":
            # The slot is an options object and the local holds ONE key off it.
            # `es_metadata.c:66-98` reads seven properties from index 2 and the
            # first assignment is `filename`, which advertised
            # `videoMetadataBind(root, urlstr, filename)` -- a name that names
            # a member, not the argument.
            name = None
        if name is not None and name not in used:
            param["name"] = name
            used.add(name)
        shape = facts["shapes"].get(index)
        fact = facts["indices"].get(index)
        if shape and index not in facts["tested"]:
            # The keys the C reads, plus an index signature. Whether anything
            # ELSE may sit at this index is decided by the primitives seen at
            # the same slot: `native/prop.sendEvent` reads argument 2 with
            # duk_require_string for `redirect` and as an options object for
            # `openurl` (es_prop.c:834-841), which is a union, not a conflict.
            others = sorted(facts["primitives"].get(index, set()))
            param["shape"] = dict(sorted(shape.items()))
            if not others:
                param["shapeUnion"] = []
            elif len(others) == 1:
                param["shapeUnion"] = others
            else:
                param.pop("shape")
                param["ambiguous"] = ["object"] + others
        elif fact is not None and index not in facts["tested"]:
            if "opaque" in fact:
                param["ambiguous"] = [fact["opaque"]]
            elif "handle" in fact:
                param["type"] = fact["handle"]
                param["nativeClass"] = fact["nativeClass"]
            else:
                param["type"] = fact["type"]
                param["reader"] = fact["reader"]
        elif fact is None and index in facts["indices"]:
            # Distinguish "the body reads this two ways" from "the body never
            # reads this at all". Both came out as an unexplained `any`, which
            # made the residue impossible to triage.
            param["ambiguous"] = ["conflict"]
            candidates = sorted(facts["primitives"].get(index, set()))
            if candidates:
                # Recorded, and deliberately NOT joined into a union.
                #
                # Whether the union is closed is a control-flow property this
                # scan cannot see. `native/prop.getChild` tests
                # duk_is_number and falls through to duk_require_string, so
                # anything else throws and `number | string` would be exact.
                # `native/kvstore.set` tests boolean, then number, then
                # object-coercible, and its final else stores KVSTORE_SET_VOID
                # -- undefined and null are accepted on purpose, so the same
                # union would reject a legal call. `native/htsmsg.get` falls
                # through to duk_safe_to_string, which coerces anything.
                #
                # The three are indistinguishable to a reader that sees which
                # accessors appear but not which of them the fall-through
                # reaches. Joining them would be the same over-reading this
                # file spent movian#209 removing, so the evidence is recorded
                # for a human and the emitted type stays `any`.
                param["candidates"] = candidates
        params.append(param)
    return params


def _pushed_handle(callee: str, args: list[str],
                   record: dict[str, Any]) -> tuple[str, str] | None:
    """The handle class a push call leaves on the stack, if it names one.

    `es_push_native_obj(ctx, &es_native_prop, p)` names its class at the call
    site, exactly as `es_get_native_obj` does on the reading side -- refusing
    it here while accepting it there would leave `native/prop.create()` at
    `any` and make every handle in the surface unobtainable.

    `es_resource_push` is the case that has to be resolved rather than read.
    It pushes through `es_push_native_obj(ctx, &es_native_resource, er)`
    (src/ecmascript/ecmascript.c:283) -- EVERY resource reaches JS as that one
    class, and `es_resource_get` then rejects the wrong one at runtime by
    comparing `er_class`. Declaring the pushed value by that base class would
    make the round trip `fs.read(fs.open(...))` a type error while the runtime
    accepts it, so the specific class comes from the `es_resource_create` in
    the same body -- and only when the body creates exactly one.
    """
    if callee == NATIVE_RESOURCE_PUSH:
        classes = {match.group(1) for match
                   in RESOURCE_CREATE_RE.finditer(record["body"])}
        if len(classes) != 1:
            return None
        symbol = next(iter(classes))
        return _handle_type_name(symbol), symbol
    if callee != NATIVE_HANDLE_PUSH or len(args) < 2:
        return None
    symbol = args[1].lstrip("&").strip()
    if not re.fullmatch(r"es_(?:native|resource)_\w+", symbol):
        return None
    return _handle_type_name(symbol), symbol


def _helper_push_type(name: str, functions: dict[str, Any],
                      depth: int) -> tuple[str, str | None] | None:
    """The single type a helper leaves on the stack, or None.

    `push_gumbo_node()` is how `nodeChilds` fills its array; without following
    one hop the element type is unknowable and the array degrades to `any[]`.
    """
    record = functions.get(name)
    if record is None or depth <= 0:
        return None
    kinds = {(kind, symbol)
             for _, kind, symbol in _push_sites(record, functions, depth - 1)}
    if len(kinds) != 1:
        return None
    return next(iter(kinds))


def _push_sites(record: dict[str, Any], functions: dict[str, Any],
                depth: int) -> list[tuple[int, str, str | None]]:
    """`(position, type, class)` for every value the body leaves on the stack.

    `class` is the `es_native_*`/`es_resource_*` symbol behind a handle, kept
    so a class named only by a return still gets its interface declared.
    """
    ctx = record["ctx"]
    body = record["body"]
    sites: list[tuple[int, str, str | None]] = []
    for match in re.finditer(
            r"\b(\w+)\s*\(\s*%s\b" % re.escape(ctx), body):
        callee = match.group(1)
        if callee in NATIVE_PUSH_TYPES:
            sites.append((match.start(), NATIVE_PUSH_TYPES[callee], None))
            continue
        if callee in (NATIVE_HANDLE_PUSH, NATIVE_RESOURCE_PUSH):
            args = _c_calls(body, callee, ctx)
            handle = _pushed_handle(callee, args[0] if args else [], record)
            if handle is not None:
                sites.append((match.start(), handle[0], handle[1]))
            continue
        if callee in functions and functions[callee] is not None:
            helper = _helper_push_type(callee, functions, depth)
            if helper is not None:
                sites.append((match.start(), helper[0], helper[1]))
    return sites


def native_return_type(record: dict[str, Any],
                       functions: dict[str, Any]
                       ) -> tuple[str, str | None] | None:
    """A native's return type, or None when the body does not decide one.

    A Duktape native returns the number of values it left on the stack: `0`
    means the call evaluates to `undefined`, `1` means the top of the stack.
    A body whose every `return` is `0` and which pushes nothing returns
    nothing; a body with a single `return 1` and one value pushed returns that.

    The second shape a body may take is a container it fills:

        duk_push_array(ctx);
        RB_FOREACH(fde, &fd->fd_entries, fde_link) {
          duk_push_string(ctx, name);
          duk_put_prop_index(ctx, -2, idx++);
        }
        return 1;

    Reading the nearest push there gives the ELEMENT, and `readdir` comes out
    `string` when it returns `string[]`. So a body with several pushes is read
    only when the first is a container and every later push is consumed by a
    `duk_put_prop_*` before the next one arrives -- a shape that cannot be
    mistaken for anything else. Everything past those two -- a mix of `0` and
    `1`, disagreeing pushes, a container filled some other way -- has no
    single answer and is given none.
    """
    ctx = re.escape(record["ctx"])
    body = record["body"]
    returns = set(re.findall(r"\breturn\s+(\d+)\s*;", body))
    sites = _push_sites(record, functions, NATIVE_HELPER_DEPTH)
    if returns == {"0"} and not sites:
        return "void", None
    if returns != {"1"} or not sites:
        return None
    if len(sites) == 1:
        _, kind, symbol = sites[0]
        return None if kind in ("object", "any[]") else (kind, symbol)

    if sites[0][1] != "any[]":
        return None
    puts = [match.start() for match in re.finditer(
        r"\b(?:%s)\s*\(\s*%s\b"
        % ("|".join(sorted(NATIVE_PUT_CALLS)), ctx), body)]
    elements: set[tuple[str, str | None]] = set()
    for order, (position, kind, symbol) in enumerate(sites[1:], start=1):
        following = [put for put in puts if put > position]
        after = sites[order + 1][0] if order + 1 < len(sites) else None
        if not following or (after is not None and following[0] > after):
            return None
        if kind in ("object", "any[]"):
            return None
        elements.add((kind, symbol))
    if len(elements) != 1:
        return None
    kind, symbol = next(iter(elements))
    return kind + "[]", symbol


def annotate_native_signatures(records: list[dict[str, Any]]) -> None:
    """Attach the implementation anchor, parameters and return to each native."""
    functions = scan_c_functions()
    memo: dict[tuple[str, int], dict[str, Any]] = {}
    for module in records:
        for function in module["functions"]:
            record = functions.get(function["impl"])
            if record is None:
                raise GenError(
                    "no C body for %s.%s -> %s(), declared at %s:%d"
                    % (module["name"], function["name"], function["impl"],
                       function["source"]["file"], function["source"]["line"]))
            function["implSource"] = {"file": record["file"],
                                      "line": record["line"]}
            if not function["variadic"] and function["nargs"] > 0:
                facts = _function_facts(function["impl"], functions, memo,
                                        NATIVE_HELPER_DEPTH)
                params = native_parameters(record, facts, function["nargs"])
                for param in params:
                    if "shape" in param:
                        param["shapeName"] = _options_type_name(
                            function["name"])
                if params:
                    function["params"] = params
            returns = native_return_type(record, functions)
            if returns is not None:
                function["returns"] = returns[0]
                if returns[1] is not None:
                    function["returnsNativeClass"] = returns[1]


def _options_type_name(function_name: str) -> str:
    """`httpReq` -> `HttpReqOptions`."""
    return function_name[:1].upper() + function_name[1:] + "Options"


def native_options_shapes(
        module: dict[str, Any]) -> list[tuple[str, dict[str, str], list[str]]]:
    """Every options-object interface one native module needs, in order."""
    shapes: dict[str, tuple[dict[str, str], list[str]]] = {}
    for function in module.get("functions", []):
        for param in function.get("params", []):
            if "shape" not in param:
                continue
            name = param["shapeName"]
            if name in shapes:
                raise GenError(
                    "two options shapes want the name %s in %s"
                    % (name, module["name"]))
            shapes[name] = (param["shape"], param.get("shapeUnion", []))
    return [(name, *shapes[name]) for name in sorted(shapes)]


def native_handle_types(
        records: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Every wrapped-pointer class named by a parameter, in emission order."""
    seen: dict[str, str] = {}
    for module in records:
        for function in module.get("functions", []):
            for param in function.get("params", []):
                if "nativeClass" in param:
                    seen[param["type"]] = param["nativeClass"]
            # A class a native only ever returns still needs its interface;
            # `native/route.create()` hands back a route handle nothing takes.
            symbol = function.get("returnsNativeClass")
            if symbol is not None:
                seen[(function["returns"] or "").rstrip("[]")] = symbol
    return sorted(seen.items())


def build_native_modules() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen_modules: set[str] = set()
    for path in sorted(ECMASCRIPT_DIR.glob("es_*.c")):
        lines = path.read_text(encoding="utf-8").splitlines()
        conditions = preprocessor_conditions(lines, path)
        for line_index, line_text in enumerate(lines):
            module_match = ES_MODULE_RE.match(line_text)
            if module_match is None:
                continue
            native_name, table_name = module_match.groups()
            module_name = "native/%s" % native_name
            if module_name in seen_modules:
                raise GenError("duplicate native module name: %s" % module_name)
            seen_modules.add(module_name)

            entries = scan_array_block(
                path, "duk_function_list_entry %s[] = {" % table_name)
            functions: list[dict[str, Any]] = []
            seen_functions: set[str] = set()
            for entry_text, entry_line in entries:
                fields = split_fields(entry_text)
                if not fields or fields[0] == "NULL":
                    continue
                if len(fields) < 3 or not (fields[0].startswith('"')
                                            and fields[0].endswith('"')):
                    raise GenError("invalid native function entry at %s:%d"
                                   % (path, entry_line))
                function_name = unquote(fields[0])
                if function_name in seen_functions:
                    raise GenError("duplicate function %s in native module %s"
                                   % (function_name, module_name))
                seen_functions.add(function_name)
                if fields[2] == "DUK_VARARGS":
                    nargs = -1
                else:
                    try:
                        nargs = int(fields[2])
                    except ValueError as error:
                        raise GenError(
                            "invalid nargs %r for %s.%s at %s:%d"
                            % (fields[2], module_name, function_name,
                               path, entry_line)) from error
                function = {
                    "name": function_name,
                    "impl": fields[1],
                    "nargs": nargs,
                    "variadic": nargs == -1,
                    "source": {"file": rel(path), "line": entry_line},
                }
                condition = conditions[entry_line - 1]
                if condition:
                    function["condition"] = condition
                functions.append(function)
            functions.sort(key=lambda r: r["name"])

            module = {
                "name": module_name,
                "kind": "native",
                "functions": functions,
                "source": {"file": rel(path), "line": line_index + 1},
            }
            condition = conditions[line_index]
            if condition:
                module["condition"] = condition
            records.append(module)
    records.sort(key=lambda r: r["name"])
    annotate_native_signatures(records)
    return records


def _mask_js_comments(line: str, in_block: bool) -> tuple[str, bool]:
    """Mask JS comments while retaining source columns and string literals."""
    chars = list(line)
    i = 0
    quote: str | None = None
    while i < len(chars):
        if in_block:
            if i + 1 < len(chars) and chars[i] == "*" and chars[i + 1] == "/":
                chars[i] = chars[i + 1] = " "
                in_block = False
                i += 2
            else:
                chars[i] = " "
                i += 1
            continue
        if quote is not None:
            if chars[i] == "\\" and i + 1 < len(chars):
                i += 2
                continue
            if chars[i] == quote:
                quote = None
            i += 1
            continue
        if chars[i] in ('"', "'"):
            quote = chars[i]
            i += 1
            continue
        if i + 1 < len(chars) and chars[i] == "/" and chars[i + 1] == "/":
            for j in range(i, len(chars)):
                chars[j] = " "
            break
        if i + 1 < len(chars) and chars[i] == "/" and chars[i + 1] == "*":
            chars[i] = chars[i + 1] = " "
            in_block = True
            i += 2
            continue
        i += 1
    return "".join(chars), in_block


def _mask_js_strings(line: str) -> str:
    """Mask quoted JS strings while retaining source columns."""
    chars = list(line)
    i = 0
    quote: str | None = None
    while i < len(chars):
        if quote is not None:
            if chars[i] == "\\" and i + 1 < len(chars):
                chars[i] = chars[i + 1] = " "
                i += 2
                continue
            if chars[i] == quote:
                quote = None
            chars[i] = " "
        elif chars[i] in ('"', "'"):
            quote = chars[i]
            chars[i] = " "
        i += 1
    return "".join(chars)


# `exports.request = function(url, ctrl, callback)` -- the parameter names are
# in the source, so v1's bare `const request: any` threw away information the
# generator already had in hand. Anchored at the assignment so a nested
# function literal further down the export's region cannot be mistaken for it.
COMMONJS_FUNCTION_RE = re.compile(
    r"=\s*function\s*(?:[A-Za-z_$][A-Za-z0-9_$]*\s*)?\(([^)]*)\)", re.S)


def _parse_params(raw: str) -> list[str] | None:
    raw = raw.strip()
    if not raw:
        return []
    params = []
    for part in raw.split(","):
        name = part.strip()
        if not IDENT_RE.fullmatch(name):
            # Destructuring or a default value: ES5.1 has neither, so this is
            # a source we do not understand -- report nothing rather than a
            # guess.
            return None
        params.append(name)
    return params


def _function_params(region: str) -> list[str] | None:
    """Parameter names of the function assigned at the head of `region`,
    or None when the export is not assigned a function literal."""
    match = COMMONJS_FUNCTION_RE.search(region)
    if match is None:
        return None
    return _parse_params(match.group(1))


NESTED_FUNCTION_RE = re.compile(r"\bfunction\b")
ARGUMENTS_RE = re.compile(r"\barguments\b")
BARE_RETURN_RE = re.compile(r"\breturn\s*;")


def _own_body(region: str) -> str:
    """The body of the function assigned at the head of `region`, with the
    bodies of nested function literals removed.

    Every question asked of a function body -- does it read `arguments`, does
    it `return;` without a value -- has to exclude nested callbacks, whose
    `arguments` and returns belong to them. Scoping also matters at the other
    end: an export's region runs to the next `exports.NAME =` assignment,
    which `exports.DB.prototype.query =` is not, so `movian/sqlite`'s region
    for `DB` swallows every prototype method below it. Without this, a plain
    search reported the constructor as variadic because a *method* reads
    `arguments`. Comments and string literals are already masked by the
    caller, so brace counting here is safe.

    Returns the empty string when `region` does not open with a function
    literal, which reads as "no evidence" at every call site.
    """
    match = COMMONJS_FUNCTION_RE.search(region)
    if match is None:
        return ""
    open_brace = region.find("{", match.end())
    if open_brace < 0:
        return ""
    kept: list[str] = []
    depth = 0
    # Depth of the innermost enclosing nested function literal, or None while
    # the scan is in the outer function's own body.
    nested_at: int | None = None
    index = open_brace
    while index < len(region):
        char = region[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                break
            if nested_at is not None and depth <= nested_at:
                nested_at = None
        elif nested_at is None and NESTED_FUNCTION_RE.match(region, index):
            # The nested body opens one level down and closes when depth
            # comes back to here.
            nested_at = depth
        if nested_at is None:
            kept.append(char)
        index += 1
    return "".join(kept)


def _uses_arguments(region: str) -> bool:
    """Whether the function at the head of `region` reads its own
    `arguments`."""
    return ARGUMENTS_RE.search(_own_body(region)) is not None


# The statement keyword, never a property of that name. `\b` alone matches the
# `return` in `iterator.return()`, which would be collected as a return
# statement and, being last and unguarded, would satisfy `_always_returns` for a
# function that plainly falls through.
RETURN_KW_RE = re.compile(r"(?<![.\w$])return\b")
VALUELESS_RETURN_RE = re.compile(r"return\s*\Z")


def _statement_end(text: str, start: int) -> int:
    """The index one past the end of the statement beginning at `start`.

    Ends at the `;` that closes it, or at the `}` that closes the enclosing
    block for a final `return x }` with no semicolon. Brackets are counted so
    that a callback, an object literal or a call argument list inside the
    statement does not terminate it early. Comments and string literals are
    masked by `_masked_js_text` before any of this runs, so a `;` found here is
    always real.
    """
    depth = 0
    index = start
    while index < len(text):
        char = text[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            if depth == 0:
                return index
            depth -= 1
        elif char == ";" and depth == 0:
            return index
        index += 1
    return len(text)


def _returns_after(text: str, open_brace: int) -> list[str]:
    """The `return` statements of the block opening at `open_brace`, verbatim,
    excluding the returns of functions nested inside it.

    The same walk as `_own_body`, asking the other question. `_own_body` needs
    callbacks *gone* -- whether a function reads `arguments`, or has a bare
    `return;`, must not be answered by somebody else's body. One question needs
    them kept: in

        return gumbo.findByTagName(this._gumboNode, tag).map(function (n) {
          return new Node(n);
        });

    the only evidence of the element type is inside the callback, while the
    statement that owns it belongs to the outer function. Deleting nested
    bodies loses the evidence; ignoring nesting misattributes the callback's
    `return` to the outer function. So track which function each `return`
    belongs to, and keep the text.
    """
    return [text[start:end]
            for start, end in _scan_returns(text, open_brace)[0]]


def _scan_returns(text: str, open_brace: int) -> tuple[list[tuple[int, int]], int]:
    """`(spans of the block's own returns, index of its closing brace)`.

    Offsets rather than text, because two questions need them: what each return
    says, and where it sits relative to everything else in the body.
    """
    spans: list[tuple[int, int]] = []
    close = len(text)
    depth = 0
    nested_at: int | None = None
    index = open_brace
    while index < len(text):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                close = index
                break
            if nested_at is not None and depth <= nested_at:
                nested_at = None
        elif nested_at is None and NESTED_FUNCTION_RE.match(text, index):
            nested_at = depth
        elif nested_at is None and RETURN_KW_RE.match(text, index):
            end = _statement_end(text, index)
            spans.append((index, end))
            # Resume ON the terminator, not past it: a statement that ended at
            # the block's `}` still has to close the depth it was found in.
            index = end
            continue
        index += 1
    return spans, close


GUARD_KW_RE = re.compile(
    r"\b(?:if|else|for|while|do|switch|case|default|try|catch|finally)\b")


def _always_returns(text: str, open_brace: int) -> bool:
    """Whether the block at `open_brace` must reach a `return <value>`.

    The question `_returned_shape` was not asking. It read every return in a
    body and required them to agree, which says nothing about the path that
    returns *nothing*: `function (n) { if (n) return new Node(n); }` has one
    return, it agrees with itself, and a falsey `n` yields `undefined` anyway
    (movian#190).

    Approximated, deliberately, by two textual facts about the LAST own return:

    * nothing but whitespace and `;` separates it from the block's closing
      brace -- so it is the final statement, and a `return` nested inside an
      `if { ... }` block fails here because the block's own `}` intervenes;
    * nothing but whitespace separates it from the previous statement boundary
      (`;`, `{` or `}`) -- so an unbraced `if (n) return ...;` fails, which the
      first test cannot see because such a return has no closing brace of its
      own.

    An approximation is correct here only because it errs toward `any`, which
    is what every other rule in this file already does when the evidence is not
    plain. `if (a) return X; else return X;` is refused although it is sound;
    nothing in `res/ecmascript/modules/**` is written that way, and the answer
    when one is would be `any` until this gets smarter -- never a wrong type.
    """
    spans, close = _scan_returns(text, open_brace)
    if not spans:
        return False
    start, end = spans[-1]
    # A bare `return;` as the last statement IS reached and yields `undefined`,
    # so a body ending in one has no value type however well its other returns
    # agree -- and the scalar scan below reads `return new Item(this)` right
    # past it.
    if VALUELESS_RETURN_RE.match(text[start:end].strip()):
        return False
    if text[end:close].replace(";", "").strip():
        return False
    return GUARD_KW_RE.search(text[_statement_start(text, start, open_brace):start]) is None


def _statement_start(text: str, index: int, floor: int) -> int:
    """Where the statement containing `index` begins: just past the previous
    `;`, `{` or `}` at this nesting level.

    Parenthesised groups are stepped over whole, because a control header
    carries its own semicolons -- `for (var i = 0; i < n; i++) return x;` would
    otherwise stop the scan inside the header, hiding the `for` that makes the
    return conditional.
    """
    cursor = index - 1
    while cursor > floor:
        char = text[cursor]
        if char == ")":
            depth = 0
            while cursor > floor:
                if text[cursor] == ")":
                    depth += 1
                elif text[cursor] == "(":
                    depth -= 1
                    if depth == 0:
                        break
                cursor -= 1
            cursor -= 1
            continue
        if char in ";{}":
            break
        cursor -= 1
    return cursor + 1


def _own_block(region: str) -> int | None:
    """The offset of the opening brace of the function assigned at the head of
    `region`, or `None` when `region` does not open with one."""
    match = COMMONJS_FUNCTION_RE.search(region)
    if match is None:
        return None
    open_brace = region.find("{", match.end())
    return None if open_brace < 0 else open_brace


def _own_returns(region: str) -> list[str]:
    """`_returns_after` for the function assigned at the head of `region`."""
    open_brace = _own_block(region)
    return [] if open_brace is None else _returns_after(region, open_brace)


def _callback_shape_index(
        region: str, param: str, shapes: list[str]) -> tuple[int | None, bool]:
    """Which argument of `param`'s invocation carries a `new <shape>(...)`.

    Assuming position 0 typed the wrong parameter: `movian/http`'s `request`
    calls `callback(null, new HttpResponse(res))` on success and
    `callback(err, null)` on failure, so annotating the first argument made
    `http.request(url, {}, res => res.toString())` compile and then
    dereference `null`. Searched over the whole region on purpose -- the
    invocation usually sits inside a nested callback, which is the one place
    `_own_body` deliberately does not look.

    `None` when no invocation carries a construction, which leaves the
    annotation off rather than guessing a position.
    """
    call_re = re.compile(r"\b%s\s*\(" % re.escape(param))
    new_res = [re.compile(r"\bnew\s+%s\s*\(" % re.escape(shape))
               for shape in shapes]
    index = None
    calls = []
    for call in call_re.finditer(region):
        end = _balanced_call_end(region, call.end() - 1)
        if end is None:
            continue
        args = _split_js_fields(region[call.end():end - 1])
        calls.append(args)
        if index is None:
            for position, arg in enumerate(args):
                if any(pattern.search(arg) for pattern in new_res):
                    index = position
                    break
    if index is None:
        return None, False
    # `callback(err, null)` on the failure path means the shape argument is
    # not always a value. The hand-written canon already had
    # `HttpResponse | null`; emitting it non-null let the new positive fixture
    # dereference it unguarded, which is exactly the runtime crash the
    # declaration is supposed to prevent.
    nullable = any(
        len(args) > index and args[index].strip() == "null"
        for args in calls)
    return index, nullable


def _returns_without_value(region: str) -> bool:
    """Whether the function at the head of `region` has a bare `return;` of
    its own -- an early exit that yields `undefined` regardless of what the
    function returns on its other paths."""
    return BARE_RETURN_RE.search(_own_body(region)) is not None


IDENT_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")


PROTOTYPE_FUNCTION_RE = re.compile(
    r"^\s*((?:exports\.)?[A-Za-z_$][A-Za-z0-9_$]*)\.prototype\."
    r"([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*function\s*"
    r"(?:[A-Za-z_$][A-Za-z0-9_$]*\s*)?\(([^)]*)\)", re.M)
PROTOTYPE_ALIAS_RE = re.compile(
    r"^\s*((?:exports\.)?[A-Za-z_$][A-Za-z0-9_$]*)\.prototype\."
    r"([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*"
    r"((?:exports\.)?[A-Za-z_$][A-Za-z0-9_$]*)\.prototype\."
    r"([A-Za-z_$][A-Za-z0-9_$]*)\s*;", re.M)
OBJECT_FUNCTION_RE = re.compile(
    r"^\s*([A-Za-z_$][A-Za-z0-9_$]*)\.([A-Za-z_$][A-Za-z0-9_$]*)"
    r"\s*=\s*function\s*"
    r"(?:[A-Za-z_$][A-Za-z0-9_$]*\s*)?\(([^)]*)\)", re.M)
SHARED_OBJECT_DECL_RE = re.compile(
    r"^\s*(?:var|let|const)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*="
    r"\s*\{\s*\}\s*;?", re.M)
THIS_PROTO_ASSIGN_RE = re.compile(
    r"\bthis\.__proto__\s*=\s*([A-Za-z_$][A-Za-z0-9_$]*)\b")
RECEIVER_FUNCTION_RE = re.compile(
    r"^\s*this\.([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*function\s*"
    r"(?:[A-Za-z_$][A-Za-z0-9_$]*\s*)?\(([^)]*)\)", re.M)
RECEIVER_ASSIGN_RE = re.compile(
    r"^\s*this\.([A-Za-z_$][A-Za-z0-9_$]*)\s*=(?!=)", re.M)
RETURN_OBJECT_RE = re.compile(r"\breturn\s*\{")
FUNCTION_HEAD_RE = re.compile(
    r"^\s*(?:(?P<assigned>(?:exports\.)?[A-Za-z_$][A-Za-z0-9_$]*)"
    r"\s*=\s*)?function\s*"
    r"(?P<declared>[A-Za-z_$][A-Za-z0-9_$]*)?\s*"
    r"\([^)]*\)\s*\{", re.M)
DEFINE_PROPERTIES_RE = re.compile(
    r"Object\.defineProperties\(\s*"
    r"((?:this|exports\.[A-Za-z_$][A-Za-z0-9_$]*|"
    r"[A-Za-z_$][A-Za-z0-9_$]*)(?:\.prototype)?)\s*,\s*\{")
DEFINE_PROPERTY_RE = re.compile(
    r"Object\.defineProperty\(\s*"
    r"((?:this|exports\.[A-Za-z_$][A-Za-z0-9_$]*|"
    r"[A-Za-z_$][A-Za-z0-9_$]*)(?:\.prototype)?)\s*,\s*"
    r"(['\"])([^'\"]+)\2\s*,\s*\{")
THIS_ASSIGN_RE = re.compile(
    r"\bthis\.([A-Za-z_$][A-Za-z0-9_$]*)\s*=(?!=)")

THIS_ALIAS_RE = re.compile(
    r"\b(?:var|let|const)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*this\b")
ALIAS_MEMBER_ASSIGN_RE = re.compile(
    r"\b([A-Za-z_$][A-Za-z0-9_$]*)\.([A-Za-z_$][A-Za-z0-9_$]*)\s*=(?!=)")
NESTED_FUNCTION_HEAD_RE = re.compile(
    r"\bfunction\s*(?:[A-Za-z_$][A-Za-z0-9_$]*\s*)?\([^)]*\)\s*\{")
DEFINE_PROPERTIES_CALL_RE = re.compile(
    r"Object\.defineProperties\(\s*([^,]+?)\s*,\s*\{")
DEFINE_PROPERTY_CALL_RE = re.compile(
    r"Object\.defineProperty\(\s*([^,]+?)\s*,\s*([^,]+?)\s*,\s*\{")
def _masked_js_text(path: Path, mask_strings: bool = True) -> str:
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    masked_lines: list[str] = []
    in_block_comment = False
    for raw_line in raw_lines:
        line, in_block_comment = _mask_js_comments(
            raw_line, in_block_comment)
        masked_lines.append(
            _mask_js_strings(line) if mask_strings else line)
    return "\n".join(masked_lines)


def _source_line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _top_level_matches(
        text: str, pattern: re.Pattern[str]) -> list[re.Match[str]]:
    matches = list(pattern.finditer(text))
    if not matches:
        return []
    depths = [0] * (len(text) + 1)
    depth = 0
    for index, char in enumerate(text):
        depths[index] = depth
        if char == "{":
            depth += 1
        elif char == "}" and depth:
            depth -= 1
    depths[len(text)] = depth
    return [
        match for match in matches
        if depths[match.start(1)] == 0
    ]


def _shape_owner(receiver: str, text: str) -> str:
    owner = receiver.rsplit(".", 1)[-1]
    if owner.endswith("Proto"):
        candidate = owner[:-5]
        if re.search(
                r"^\s*function\s+%s\s*\(" % re.escape(candidate),
                text, re.M):
            return candidate
    return owner


def _member_record(
        name: str, params: list[str] | None, path: Path, line: int,
        kind: str | None = None,
        alias_of: str | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "name": name,
        "source": {"file": rel(path), "line": line},
    }
    if kind is not None:
        record["kind"] = kind
    if params is not None:
        record["params"] = params
        record["nargs"] = len(params)
    if alias_of is not None:
        record["aliasOf"] = alias_of
    return record


def _shape_method(
        name: str, raw_params: str, path: Path, line: int,
        alias_of: str | None = None,
        region: str | None = None) -> dict[str, Any]:
    record = _member_record(
        name, _parse_params(raw_params), path, line, alias_of=alias_of)
    # Same rule the module exports get: a method that reads its own
    # `arguments` takes more than it declares. `movian/sqlite`'s
    # `DB.prototype.query` names no parameter and forwards every value in
    # `arguments`, so a zero-argument declaration rejected every real
    # `db.query('SELECT ...', value)`. `region` starts at the assignment, so
    # `_uses_arguments` brace-matches this method's own body.
    if region is not None and "params" in record and _uses_arguments(region):
        record["variadic"] = True
    if region is not None:
        returned = _returned_shape(region)
        if returned is not None:
            record["returns"] = returned
    return record


MAP_CALLBACK_RE = re.compile(
    r"\.\s*map\s*\(\s*function\s*(?:[A-Za-z_$][A-Za-z0-9_$]*\s*)?\([^)]*\)\s*\{")


def _mapped_element_shape(statement: str) -> str | None:
    """The element shape of `return <expr>.map(function (x) { ... })`.

    Answers only when the callback's own returns are all `new X(...)` for one
    X. A callback that returns two shapes, or a shape on one path and a plain
    value on another, gets nothing -- the same rule as the scalar forms, for
    the same reason: a wrong element type invents errors a plugin does not
    have.

    The callback must also always reach one of those returns. A conditional
    `function (n) { if (n) return new Node(n); }` produces `undefined` elements
    on the other path, and `Node[]` would then permit unchecked member access
    on them -- the failure #179 exists to prevent, one level in (movian#190).

    Deliberately narrow. Only `.map` with a literal `function` callback, whose
    result is returned directly. `.filter(...).map(...)` still works because
    only the last `.map` in the statement is read, but a mapped array stored in
    a local and returned later is not this pattern and keeps `any`.
    """
    match = None
    for match in MAP_CALLBACK_RE.finditer(statement):
        pass
    if match is None:
        return None
    # The whole `.map(...)` call must BE the returned value. `return {list:
    # xs.map(...)}` returns an object, and `return xs.map(...).length` a
    # number; both would otherwise be read as the array.
    tail = statement[_statement_end(statement, match.end() - 1) + 1:]
    if tail.strip():
        return None
    callback_brace = match.end() - 1
    if not _always_returns(statement, callback_brace):
        return None
    shapes = set()
    for returned in _returns_after(statement, callback_brace):
        constructed = re.match(
            r"return\s+new\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(", returned)
        if constructed is None:
            return None
        shapes.add(constructed.group(1))
    if len(shapes) != 1:
        return None
    return next(iter(shapes))


def _returned_shape(region: str) -> str | dict[str, Any] | None:
    """The shape a method returns, when it plainly returns one.

    Three forms. `return new Item(...)` directly; the construct-then-return
    that `movian/page` actually uses; and an array built by mapping a
    constructor over a native result, which is how every `movian/html`
    selector answers --

        Page.prototype.appendItem = function(url, type, metadata) {
          var item = new Item(this);
          ...
          return item;
        }

        NodeProto.prototype.getElementByTagName = function(tag) {
          return gumbo.findByTagName(this._gumboNode, tag).map(function(n) {
            return new Node(n);
          });
        }

    Module exports were already scanned for the first form; prototype methods
    were scanned for neither and emitted `: any` unconditionally. That is the
    hole `Item.onSelect` lived in: with the receiver typed `any`, a plugin
    could assign any member name and every gate stayed green (#177). The array
    form is the same hole one level out (#179): `interface Node` carries all
    eleven members, so a phantom directly on a Node is caught, but every
    selector that reaches one returned `any` and discarded the type --
    `plugin_examples/02-intermediate/02-html-parser` called `getAttribute`
    through exactly that path at four sites, type-checked clean, and rendered
    an `openerror` at runtime until it was fixed by hand (`5706c66cf`).

    Answers only when the evidence is unambiguous -- one shape, and every
    value-returning path agreeing. A method returning two different things, a
    shape mixed with a plain value, or an array mixed with a scalar, keeps
    `any`, because a wrong return type invents errors a plugin does not have.

    "Every value-returning path" is exact, and narrower than it sounds: a path
    that returns NO value is not consulted. `function (n) { if (n) return new
    "Every value-returning path" is exact, and it used to be the whole test,
    which left a path that returns NO value unexamined: `function (n) { if (n)
    return new Node(n); }` answered `Node`, and `function (t) { if (t) return
    new Item(this); }` answered `Item` from #178 onward, both yielding
    `undefined` on the other branch. `_always_returns` now gates both forms
    (movian#190). It cost nothing: all eleven members that carry a return type
    end their body with an unconditional `return`, measured before the change.

    Returns a shape name, or `{"kind": "array", "element": name}`.
    """
    body = _own_body(region)
    if not body:
        return None
    # Before reading what the returns say, ask whether one is always reached.
    # Everything below compares return VALUES and requires them to agree, which
    # is silent about the path that returns nothing at all -- and a function
    # that can fall out of its body yields `undefined` no matter how well its
    # explicit returns agree (movian#190).
    open_brace = _own_block(region)
    if open_brace is None or not _always_returns(region, open_brace):
        return None
    direct = set(re.findall(
        r"\breturn\s+new\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(", body))
    named = set(re.findall(r"\breturn\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*;", body))
    shapes = set(direct)
    for name in named:
        constructed = re.findall(
            r"\b(?:var|let|const)\s+%s\s*=\s*new\s+([A-Za-z_$][A-Za-z0-9_$]*)"
            r"\s*\(" % re.escape(name), body)
        if not constructed:
            # `return someArgument;` or a value built another way -- no claim.
            return None
        shapes.update(constructed)

    # Read from the unstripped statements, since the construction sits inside
    # the callback that `_own_body` removed. Every return of the function has
    # to be a mapped array of the same element: a method answering an array on
    # one path and a bare node on another has no single type, and guessing one
    # is how a declaration starts inventing errors.
    returns = _own_returns(region)
    elements = set()
    mapped = 0
    for statement in returns:
        element = _mapped_element_shape(statement)
        if element is not None:
            mapped += 1
            elements.add(element)
    if mapped:
        if shapes or len(elements) != 1 or mapped != len(returns):
            return None
        return {"kind": "array", "element": next(iter(elements))}

    if len(shapes) != 1:
        return None
    return next(iter(shapes))

def _split_js_fields(text: str) -> list[str]:
    fields: list[str] = []
    start = 0

    depths = {"(": 0, "[": 0, "{": 0}
    closing = {")": "(", "]": "[", "}": "{"}
    quote: str | None = None
    escaped = False
    for index, char in enumerate(text):
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
        elif char in depths:
            depths[char] += 1
        elif char in closing:
            depths[closing[char]] = max(
                0, depths[closing[char]] - 1)
        elif char == "," and not any(depths.values()):
            fields.append(text[start:index])
            start = index + 1
    fields.append(text[start:])
    return fields


def _function_regions(
        text: str
) -> list[tuple[str, int, int]]:
    regions: list[tuple[str, int, int]] = []
    for match in FUNCTION_HEAD_RE.finditer(text):
        owner = match.group("assigned") or match.group("declared")
        if owner is None:
            continue
        end = _balanced_end(text, match.end() - 1)
        if end is not None:
            regions.append((owner, match.end(), end - 1))
    return regions

def _mask_nested_function_bodies(text: str) -> str:
    """Keep constructor control flow while hiding nested callback bodies."""
    chars = list(text)
    for match in NESTED_FUNCTION_HEAD_RE.finditer(text):
        open_index = text.find("{", match.end() - 1)
        end = _balanced_end(text, open_index)
        if open_index < 0 or end is None:
            continue
        for index in range(open_index + 1, end - 1):
            if chars[index] != "\n":
                chars[index] = " "
    return "".join(chars)


def _shape_receiver_for_owner(
        owner: str, receivers: set[str]
) -> str | None:
    if owner in receivers:
        return owner
    owner_base = owner.rsplit(".", 1)[-1]
    for receiver in sorted(receivers):
        receiver_base = receiver.rsplit(".", 1)[-1]
        if receiver_base == owner_base + "Proto":
            return receiver
    return None


def _property_names(
        body: str, path: Path, text: str, offset: int
) -> list[tuple[str, int, str]]:
    names: list[tuple[str, int, str]] = []
    cursor = 0
    for field in _split_js_fields(body):
        entry = re.match(
            r"\s*(?:([A-Za-z_$][A-Za-z0-9_$]*)|"
            r"(['\"])([^'\"]+)\2)\s*:", field, re.S)
        if entry is None:
            if field.strip():
                _shape_diagnostic(
                    path, text, offset + cursor,
                    "ignored unsupported defineProperties key")
            cursor += len(field) + 1
            continue
        descriptor = field[entry.end():]
        kind = ("accessor"
                if re.search(r"\b(?:get|set)\s*:", descriptor)
                else "value")
        names.append((
            entry.group(1) or entry.group(3),
            offset + cursor + field.find(entry.group(0).lstrip()),
            kind))
        cursor += len(field) + 1
    return names


def _add_property(
        properties: dict[str, dict[str, dict[str, Any]]],
        receiver: str, name: str, path: Path, text: str, offset: int,
        kind: str = "value", optional: bool = False
) -> None:
    members = properties.setdefault(receiver, {})
    if optional and name in members:
        # A slot the module both guards and assigns is not optional; the
        # assignment is the stronger fact and already recorded it.
        return
    record = _member_record(
        name, None, path, _source_line(text, offset), kind=kind)
    if optional:
        record["optional"] = True
    members[name] = record


# `if(typeof this.asyncPaginator == 'function')` is the module DECLARING an
# optional slot: it tests the member precisely because a plugin is expected to
# assign it, and the module never assigns it itself. Nothing else in the source
# records those members, so narrowing a callback parameter to the interface
# turned every documented assignment into TS2339 -- `plugin_examples/
# async_page_load/async_page_load.js:27` and `plugin_examples/videoscrobbling/
# videoscrobbling_example.js:53-70` both stopped compiling. Seven such hooks
# exist in the whole corpus, on `Page` (3) and `VideoScrobbler` (4).
#
# Matched against text whose string literals are intact -- the default mask
# replaces `'function'` and the guard becomes invisible.
THIS_HOOK_GUARD_RE = re.compile(
    r"\btypeof\s*\(?\s*this\.([A-Za-z_$][A-Za-z0-9_$]*)\s*\)?"
    r"\s*[=!]==?\s*['\"]function['\"]")


def _shape_diagnostic(path: Path, text: str, offset: int, message: str) -> None:
    print(
        "gen.py: %s:%d: warning: %s" %
        (rel(path), _source_line(text, offset), message),
        file=sys.stderr)
def _scan_shape_properties(
        path: Path, text: str, receivers: set[str],
        shared_receivers: set[str]
) -> dict[str, dict[str, dict[str, Any]]]:
    comment_text = _masked_js_text(path, mask_strings=False)
    properties: dict[str, dict[str, dict[str, Any]]] = {}
    handled_calls: set[int] = set()

    def add_call_properties(
            source: str, match: re.Match[str], receiver: str,
            base_offset: int
    ) -> None:
        open_index = match.end() - 1
        end = _balanced_end(source, open_index)
        if end is None:
            _shape_diagnostic(
                path, comment_text, base_offset + match.start(1),
                "ignored unterminated Object.defineProperties call")
            return
        body_start = base_offset + open_index + 1
        for name, offset, kind in _property_names(
                source[open_index + 1:end - 1],
                path, comment_text, body_start):
            _add_property(properties, receiver, name,
                          path, comment_text, offset, kind)

    def add_property_call(
            source: str, match: re.Match[str], receiver: str,
            base_offset: int
    ) -> None:
        name = match.group(3)
        _add_property(
            properties, receiver, name, path, comment_text,
            base_offset + match.start(3))

    def target_receiver(target: str) -> str | None:
        if target.endswith(".prototype"):
            return target[:-len(".prototype")]
        return None

    def scan_body(
            body: str, owner_receiver: str, base_offset: int
    ) -> None:
        scan_text = _mask_nested_function_bodies(body)
        aliases = set(THIS_ALIAS_RE.findall(scan_text))

        def assignment_kind(
                assignment: re.Match[str]) -> str:
            return ("function"
                    if re.match(
                        r"\s*function\b", scan_text[assignment.end():])
                    else "value")

        for assignment in THIS_ASSIGN_RE.finditer(scan_text):
            name = assignment.group(1)
            if name != "__proto__":
                # An assignment nested inside a block is conditional, so the
                # member is not always present. `movian/page` sets
                # `this.options` only under `if(!flat)` (page.js:195-200) and
                # `Searcher` builds flat pages, so declaring it required made
                # `page.options` look guaranteed on a path that does not have
                # it. Exactly one member in the corpus is assigned this way.
                before = scan_text[:assignment.start()]
                conditional = before.count("{") > before.count("}")
                _add_property(
                    properties, owner_receiver, name, path, comment_text,
                    base_offset + assignment.start(1),
                    kind=assignment_kind(assignment),
                    optional=conditional)
        # Deliberately over `body` rather than `scan_text`: every one of these
        # guards sits inside a `.bind(this)` callback, where `this` is still
        # the shape, and masking nested bodies loses all seven.
        for guard in THIS_HOOK_GUARD_RE.finditer(body):
            _add_property(
                properties, owner_receiver, guard.group(1), path,
                comment_text, base_offset + guard.start(1),
                kind="function", optional=True)
        for assignment in ALIAS_MEMBER_ASSIGN_RE.finditer(scan_text):
            alias, name = assignment.groups()
            if alias in aliases and name != "__proto__":
                _add_property(
                    properties, owner_receiver, name, path, comment_text,
                    base_offset + assignment.start(2),
                    kind=assignment_kind(assignment))

        for match in _top_level_matches(body, DEFINE_PROPERTIES_RE):
            target = match.group(1)
            receiver = owner_receiver if target == "this" \
                else target_receiver(target)
            if receiver not in receivers and receiver not in shared_receivers:
                _shape_diagnostic(
                    path, comment_text, base_offset + match.start(1),
                    "ignored unsupported Object.defineProperties target %s" %
                    target)
                handled_calls.add(base_offset + match.start(1))
                continue
            add_call_properties(body, match, receiver, base_offset)
            handled_calls.add(base_offset + match.start(1))

        for match in _top_level_matches(body, DEFINE_PROPERTY_RE):
            target = match.group(1)
            receiver = owner_receiver if target == "this" \
                else target_receiver(target)
            if receiver not in receivers and receiver not in shared_receivers:
                _shape_diagnostic(
                    path, comment_text, base_offset + match.start(1),
                    "ignored unsupported Object.defineProperty target %s" %
                    target)
                handled_calls.add(base_offset + match.start(1))
                continue
            add_property_call(body, match, receiver, base_offset)
            handled_calls.add(base_offset + match.start(1))

    for match in _top_level_matches(comment_text, DEFINE_PROPERTIES_RE):
        target = match.group(1)
        receiver = target_receiver(target)
        if receiver not in receivers:
            _shape_diagnostic(
                path, comment_text, match.start(1),
                "ignored unsupported Object.defineProperties target %s" %
                target)
            handled_calls.add(match.start(1))
            continue
        add_call_properties(comment_text, match, receiver, 0)
        handled_calls.add(match.start(1))

    for match in _top_level_matches(comment_text, DEFINE_PROPERTY_RE):
        target = match.group(1)
        receiver = target_receiver(target)
        if receiver not in receivers:
            _shape_diagnostic(
                path, comment_text, match.start(1),
                "ignored unsupported Object.defineProperty target %s" %
                target)
            handled_calls.add(match.start(1))
            continue
        add_property_call(comment_text, match, receiver, 0)
        handled_calls.add(match.start(1))

    for owner, body_start, body_end in _function_regions(text):
        receiver = _shape_receiver_for_owner(owner, receivers)
        if receiver is not None:
            scan_body(
                comment_text[body_start:body_end],
                receiver, body_start)

    for match in OBJECT_FUNCTION_RE.finditer(text):
        receiver = match.group(1)
        if receiver not in shared_receivers:
            continue
        open_index = text.find("{", match.end())
        end = _balanced_end(text, open_index)
        if open_index < 0 or end is None:
            continue
        scan_body(
            comment_text[open_index + 1:end - 1],
            receiver, open_index + 1)

    for match in DEFINE_PROPERTIES_CALL_RE.finditer(comment_text):
        if match.start(1) not in handled_calls:
            _shape_diagnostic(
                path, comment_text, match.start(1),
                "ignored unsupported Object.defineProperties target %s" %
                match.group(1).strip())
    for match in DEFINE_PROPERTY_CALL_RE.finditer(comment_text):
        if match.start(1) not in handled_calls:
            _shape_diagnostic(
                path, comment_text, match.start(1),
                "ignored unsupported Object.defineProperty target %s" %
                match.group(1).strip())
    return properties

def scan_commonjs_shapes(path: Path) -> list[dict[str, Any]]:
    """Scan top-level prototype and shared-object assignments."""
    text = _masked_js_text(path)
    by_receiver: dict[str, dict[str, dict[str, Any]]] = {}
    prototype_matches = _top_level_matches(text, PROTOTYPE_FUNCTION_RE)
    top_prototype_starts = {
        match.start(1) for match in prototype_matches
    }
    properties_by_receiver: dict[str, dict[str, dict[str, Any]]] = {}
    for match in PROTOTYPE_FUNCTION_RE.finditer(text):
        if match.start(1) not in top_prototype_starts:
            _shape_diagnostic(
                path, text, match.start(1),
                "ignored conditional/non-top-level prototype member %s.%s" %
                (match.group(1), match.group(2)))
            continue
        receiver = match.group(1)
        methods = by_receiver.setdefault(receiver, {})
        methods[match.group(2)] = _shape_method(
            match.group(2), match.group(3), path,
            _source_line(text, match.start(1)),
            region=text[match.start(1):])

    unresolved_aliases: list[tuple[str, str, str, int]] = []
    alias_matches = list(PROTOTYPE_ALIAS_RE.finditer(text))
    top_alias_starts = {
        match.start(1) for match in _top_level_matches(text, PROTOTYPE_ALIAS_RE)
    }
    for match in alias_matches:
        receiver, name, target_receiver, target = match.groups()
        line = _source_line(text, match.start(1))
        if match.start(1) not in top_alias_starts:
            _shape_diagnostic(
                path, text, match.start(1),
                "ignored conditional/non-top-level prototype alias %s.%s" %
                (receiver, name))
        elif receiver != target_receiver:
            _shape_diagnostic(
                path, text, match.start(1),
                "ignored cross-receiver prototype alias %s.%s = %s.%s" %
                (receiver, name, target_receiver, target))
        else:
            unresolved_aliases.append((receiver, name, target, line))

    while unresolved_aliases:
        remaining: list[tuple[str, str, str, int]] = []
        progress = False
        for receiver, name, target, line in unresolved_aliases:
            methods = by_receiver.setdefault(receiver, {})
            if target in methods:
                method = dict(methods[target])
                method["name"] = name
                method["source"] = {"file": rel(path), "line": line}
                method["aliasOf"] = target
                methods[name] = method
                progress = True
            else:
                remaining.append((receiver, name, target, line))
        if not progress:
            break
        unresolved_aliases = remaining

    for receiver, name, target, line in unresolved_aliases:
        print(
            "gen.py: %s:%d: warning: unresolved prototype alias %s.%s -> %s" %
            (rel(path), line, receiver, name, target),
            file=sys.stderr)

    shared_names = {
        match.group(1)
        for match in _top_level_matches(text, SHARED_OBJECT_DECL_RE)
    }
    consumed_shared_names = (
        set(THIS_PROTO_ASSIGN_RE.findall(text)) & shared_names)
    object_matches = list(OBJECT_FUNCTION_RE.finditer(text))
    top_object_starts = {
        match.start(1) for match in _top_level_matches(text, OBJECT_FUNCTION_RE)
    }
    for match in object_matches:
        receiver, name = match.group(1), match.group(2)
        if receiver not in consumed_shared_names:
            continue
        if match.start(1) not in top_object_starts:
            _shape_diagnostic(
                path, text, match.start(1),
                "ignored conditional/non-top-level shared member %s.%s" %
                (receiver, name))
            continue
        methods = by_receiver.setdefault(receiver, {})
        methods[name] = _shape_method(
            name, match.group(3), path,
            _source_line(text, match.start(1)),
            region=text[match.start(1):])
    properties_by_receiver = _scan_shape_properties(
        path, text, set(by_receiver), consumed_shared_names)

    shapes: list[dict[str, Any]] = []
    for receiver in sorted(
            set(by_receiver) | set(properties_by_receiver)):
        methods = by_receiver.get(receiver, {})
        properties = properties_by_receiver.get(receiver, {})
        if not methods and not properties:
            continue
        is_shared = receiver in consumed_shared_names
        shape = {
            "kind": "shared" if is_shared else "prototype",
            "methods": [methods[name] for name in sorted(methods)],
            "name": receiver if is_shared else _shape_owner(receiver, text),
            "receiver": receiver,
            "source": {
                "file": rel(path),
                "line": min(
                    [method["source"]["line"]
                     for method in methods.values()] +
                    [prop["source"]["line"]
                     for prop in properties.values()]),
            },
        }
        if properties:
            shape["properties"] = [
                properties[name] for name in sorted(properties)]
        shapes.append(shape)
    return shapes


def _anonymous_return_shape(region: str) -> dict[str, Any] | None:
    matches = list(RETURN_OBJECT_RE.finditer(region))
    if not matches:
        return None

    depths = [0] * (len(region) + 1)
    depth = 0
    for index, char in enumerate(region):
        depths[index] = depth
        if char == "{":
            depth += 1
        elif char == "}" and depth:
            depth -= 1
    direct_returns = [
        match for match in matches
        if depths[match.start()] == 1
    ]
    if len(direct_returns) != 1:
        return None

    match = direct_returns[0]
    open_index = match.end() - 1
    depth = 0
    close_index = None
    for index in range(open_index, len(region)):
        if region[index] == "{":
            depth += 1
        elif region[index] == "}":
            depth -= 1
            if depth == 0:
                close_index = index
                break
    if close_index is None:
        return None

    fields: list[dict[str, str]] = []
    for field in split_fields(region[open_index + 1:close_index]):
        entry = re.fullmatch(
            r"\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*:\s*(.*?)\s*",
            field, re.S)
        if entry is None:
            return None
        constructor = re.fullmatch(
            r"new\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(.*\)",
            entry.group(2), re.S)
        if constructor is None:
            return None
        fields.append({
            "name": entry.group(1),
            "type": constructor.group(1),
        })
    return {"kind": "object", "fields": fields} if fields else None


def _receiver_members(
        region: str, path: Path, line_index: int
) -> list[dict[str, Any]]:
    functions: dict[str, dict[str, Any]] = {}
    for match in RECEIVER_FUNCTION_RE.finditer(region):
        functions[match.group(1)] = _member_record(
            match.group(1),
            _parse_params(match.group(2)),
            path,
            line_index + 1 + region.count(
                "\n", 0, match.start(1)),
            kind="function")

    members: dict[str, dict[str, Any]] = dict(functions)
    for match in RECEIVER_ASSIGN_RE.finditer(region):
        name = match.group(1)
        if name == "__proto__" or name in functions:
            continue
        members[name] = _member_record(
            name,
            None,
            path,
            line_index + 1 + region.count(
                "\n", 0, match.start(1)),
            kind="value")
    return [members[name] for name in sorted(members)]


def _merge_receiver_members(
        members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for member in members:
        name = member["name"]
        previous = merged.get(name)
        if previous is None:
            merged[name] = member
            continue
        if previous["kind"] != member["kind"]:
            continue
        if member["kind"] == "function":
            old_params = previous.get("params") or []
            new_params = member.get("params") or []
            if len(new_params) > len(old_params):
                merged[name] = member
    return [merged[name] for name in sorted(merged)]
def _balanced_call_end(text: str, open_index: int) -> int | None:
    """Index just past the `)` closing the call whose `(` is at `open_index`.

    `_balanced_end` below matches BRACES, and calling it for a parenthesised
    argument list silently runs to the next unbalanced `{` -- which made the
    callback-argument split return the whole rest of the function as one
    argument. The right index came out anyway on `movian/http`, by luck, which
    is the worst way for this to be wrong.
    """
    depth = 0
    for index in range(open_index, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")" and depth:
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def _balanced_end(text: str, open_index: int) -> int | None:
    depth = 0
    for index in range(open_index, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}" and depth:
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def _export_region(
        masked_lines: list[str], line_index: int, next_line: int
) -> str:
    region = "\n".join(
        _mask_js_strings(line)
        for line in masked_lines[line_index:next_line])
    function = COMMONJS_FUNCTION_RE.search(region)
    if function is None:
        return region
    open_index = region.find("{", function.end())
    if open_index < 0:
        return region
    end = _balanced_end(region, open_index)
    return region[:end] if end is not None else region

def scan_commonjs_exports(path: Path) -> list[dict[str, Any]]:
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    masked_lines: list[str] = []
    in_block_comment = False
    for raw_line in raw_lines:
        line, in_block_comment = _mask_js_comments(
            raw_line, in_block_comment)
        masked_lines.append(line)

    candidates: list[tuple[int, str]] = []
    for line_index, line in enumerate(masked_lines):
        match = COMMONJS_EXPORT_RE.match(line)
        if match is not None:
            candidates.append((line_index, match.group(1) or match.group(3)))

    exports: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate_index, (line_index, export_name) in enumerate(candidates):
        if export_name in seen:
            continue
        seen.add(export_name)
        next_line = (candidates[candidate_index + 1][0]
                     if candidate_index + 1 < len(candidates)
                     else len(masked_lines))
        region = _export_region(masked_lines, line_index, next_line)
        record = {
            "name": export_name,
            "source": {"file": rel(path), "line": line_index + 1},
        }
        params = _function_params(region)
        if params is not None:
            record["params"] = params
            record["nargs"] = len(params)
            # A function that reads `arguments` takes more than it declares --
            # `movian/xmlrpc`'s `call` declares none and uses arguments[0],
            # arguments[1] and the tail from index 2. Emitting the formal list
            # as the whole signature made tsc reject every real call site, so
            # the formal count is not the arity here: keep the names for
            # signature help and let a rest parameter carry what is unnamed.
            if _uses_arguments(region):
                record["variadic"] = True
        if re.search(r"this\.__proto__\s*=", region):
            # These functions mutate the receiver's prototype when called as
            # an exported function; they are not constructors in the public
            # module surface.
            record["receiverMutation"] = True
        elif re.search(r"\bthis\b", region):
            record["constructor"] = True
        returned = set(re.findall(
            r"\breturn\s+new\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(", region))
        if len(returned) == 1:
            record["returns"] = next(iter(returned))
        anonymous = _anonymous_return_shape(region)
        if anonymous is not None:
            record["returns"] = anonymous
        if record.get("receiverMutation"):
            record["receiverMembers"] = _receiver_members(
                region, path, line_index)
        callback_shapes = sorted(set(re.findall(
            r"\bnew\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(", region)))
        callback_params = [
            name for name in (params or [])
            if re.search(
                r"\b%s\s*(?:\.apply\s*\(|\()" %
                re.escape(name), region)
        ]
        if callback_shapes and len(callback_params) == 1:
            record["callbackShapes"] = callback_shapes
            record["callbackParam"] = callback_params[0]
            index, nullable = _callback_shape_index(
                region, callback_params[0], callback_shapes)
            if index is not None:
                record["callbackShapeIndex"] = index
                if nullable:
                    record["callbackShapeNullable"] = True
            # `movian/http`'s `request` returns `new HttpResponse(res)` on the
            # synchronous path, but when a callback is supplied it dispatches
            # and falls out through a bare `return;`. Promising the value type
            # unconditionally let `http.request(url, {}, cb).toString()` type
            # -check and then dereference `undefined`, so the callback form is
            # emitted as a separate overload returning `void`. Recorded only
            # when the callback parameter is unambiguous, above.
            if record.get("returns") is not None and \
                    _returns_without_value(region):
                record["voidWhen"] = callback_params[0]
        exports.append(record)
    exports.sort(key=lambda r: r["name"])
    return exports


PROTO_EXPORT_RE = re.compile(
    r"^\s*(?:module\.)?exports\.__proto__\s*=\s*"
    r"([A-Za-z_$][A-Za-z0-9_$]*)\s*;", re.M)


def _proto_parent(path: Path) -> str | None:
    """`exports.__proto__ = np;` where `np = require('native/prop')` makes the
    module inherit that module's whole surface at load time. Static and
    resolvable, unlike the per-instance receiver-prototype assignment idiom
    inside constructors, which only a runtime probe can see."""
    text = path.read_text(encoding="utf-8")
    match = PROTO_EXPORT_RE.search(text)
    if match is None:
        return None
    ident = match.group(1)
    require = re.search(
        r"\b(?:var|let|const)\s+%s\s*=\s*require\(\s*['\"]([^'\"]+)['\"]"
        % re.escape(ident), text)
    return require.group(1) if require is not None else None

# ---------------------------------------------------------------------------
# js.globals -- the environment es_create_env() builds on the global object
# ---------------------------------------------------------------------------

# The two C functions that populate the global object. `es_create_env`
# builds the environment every context gets; `ecmascript_plugin_load` adds
# `Plugin`, which only a loaded plugin sees. Same shape, so one scan reads
# both rather than a second special-cased reader.
ENV_FUNCTION_MARKERS = (
    "es_create_env(es_context_t *ec",
    "ecmascript_plugin_load(const char *id",
)
# `duk_put_function_list(ctx, -1, es_fnlist_timer)` puts a whole table on
# whatever is at -1. Inside es_create_env that is either the global object
# itself or an object just pushed, which the following put_prop_string names.
PUT_FUNCTION_LIST_RE = re.compile(
    r"duk_put_function_list\s*\(\s*ctx\s*,\s*(-?\d+|\w*obj_idx)\s*,"
    r"\s*([A-Za-z_]\w*)\s*\)")
PUT_PROP_STRING_RE = re.compile(
    r'duk_put_prop_string\s*\(\s*ctx\s*,\s*(-?\d+|\w*obj_idx)\s*,\s*"([^"]+)"\s*\)')
PUSH_OBJECT_RE = re.compile(r"duk_push_object\s*\(\s*ctx\s*\)")
PUSH_VALUE_RE = re.compile(r"duk_push_(int|string|number|boolean)\s*\(")
# One alternation so the scan sees the calls in source order; a separate
# finditer per pattern would lose the ordering the C depends on.
ENV_STATEMENT_RE = re.compile(
    "|".join("(?:%s)" % pattern.pattern for pattern in (
        PUSH_OBJECT_RE, PUSH_VALUE_RE,
        PUT_FUNCTION_LIST_RE, PUT_PROP_STRING_RE)))


def _table_functions(table: str) -> list[dict[str, Any]]:
    """Names and nargs from a `duk_function_list_entry` table, wherever it
    lives -- the env tables sit in ecmascript.c and es_*.c alike."""
    for path in sorted(ECMASCRIPT_DIR.glob("*.c")):
        try:
            entries = scan_array_block(
                path, "duk_function_list_entry %s[] = {" % table)
        except GenError:
            continue
        functions = []
        for entry_text, entry_line in entries:
            fields = split_fields(entry_text)
            if not fields or fields[0] == "NULL":
                continue
            nargs = (-1 if fields[2] == "DUK_VARARGS" else int(fields[2]))
            functions.append({
                "name": unquote(fields[0]),
                "nargs": nargs,
                "variadic": nargs == -1,
                "source": {"file": rel(path), "line": entry_line},
            })
        return sorted(functions, key=lambda f: f["name"])
    raise GenError("global function table not found: %s" % table)


def build_globals() -> dict[str, Any]:
    """The global surface, scanned out of `es_create_env()`.

    Nothing in `generated/movian-api.d.ts` described it, so every plugin using
    `console.log` or `setTimeout` -- both real globals installed here -- got a
    false "cannot find name". The table names are read from the C rather than
    listed here: `es_create_env` is the one function that builds the
    environment, and it says which table lands where.

    Read as a sequence, because that is what the C is: a `duk_push_object`
    opens an object, function lists and value properties land on whatever is
    open, and the `duk_put_prop_string(ctx, -2, "name")` that follows names
    and closes it. A function list arriving with nothing open goes on the
    global object itself, which is how the timer family is installed.
    """
    path = ECMASCRIPT_DIR / "ecmascript.c"
    text = path.read_text(encoding="utf-8")
    functions: list[dict[str, Any]] = []
    objects: list[dict[str, Any]] = []
    for marker in ENV_FUNCTION_MARKERS:
        found, found_objects = _scan_global_env(path, text, marker)
        functions.extend(found)
        objects.extend(found_objects)
    # Duktape installs globals of its own before Movian's C runs, and
    # `es_create_env` never mentions them -- so the scanner above cannot see
    # them and nothing declared them. `print` is the expensive case: the
    # plugin_examples audit (#169) counted its 16 uses as example rot on the
    # reasoning that only `native/prop.print` exists. It is a real global, and
    # this repo's own api-introspector plugin calls it to emit the capture
    # `gen.py --check` diffs against. Curated rather than scanned, because the
    # evidence is a builtins table plus two config macros rather than a call
    # sequence -- each entry carries both, and the audit below re-checks them.
    functions.extend(build_interpreter_globals())
    objects.extend(build_interpreter_objects())
    return {
        "functions": sorted(functions, key=lambda f: f["name"]),
        "objects": sorted(objects, key=lambda entry: entry["name"]),
    }


def render_v1_dts(artifact: dict[str, Any]) -> str:
    """`generated/movian-api-v1.d.ts` -- the apiversion-1 `showtime` global.

    A separate file, not a section of the main bundle, because the runtime
    rule is a branch: `ecmascript.c:913` loads api-v1.js only when the
    plugin's apiversion is 1. Declaring `showtime` unconditionally would make
    tsc accept it in a v2 plugin, where the name genuinely does not exist --
    a false accept on the exact legacy API this file documents.
    """
    records = artifact.get("js", {}).get("legacyGlobals", [])
    source = records[0].get("source", {}) if records else {}
    lines = [
        "// Generated by support/devtools/metadata/gen.py -- do not edit.",
        "// movianRevision: %s" % artifact.get("movianRevision", "unknown"),
        "//",
        "// apiversion 1 ONLY. src/plugins.c:712 defaults a plugin.json with",
        "// no `apiversion` to 1, and src/ecmascript/ecmascript.c:913 loads",
        "// %s for those plugins alone." % source.get("file", "api-v1.js"),
        "// Include this file ALONGSIDE movian-api.d.ts when checking such a",
        "// plugin, and never for an apiversion 2 one.",
        "",
    ]
    for record in records:
        lines.extend(_render_v1_object(record))
    return "\n".join(lines)


def _render_v1_object(record: dict[str, Any]) -> list[str]:
    """One `declare const <name>: { ... }` block."""
    lines = ["declare const %s: {" % record["name"]]
    for member in record.get("members", []):
        if member.get("callable"):
            params = list(member.get("params") or [])
            rendered = ", ".join("%s?: any" % name for name in params)
            if member.get("variadic"):
                rendered = ", ".join(
                    part for part in (rendered, "...args: any[]") if part)
            lines.append("  /** @arity %d */"
                         % (-1 if member.get("variadic") and not params
                            else len(params)))
            lines.append("  %s(%s): any;" % (member["name"], rendered))
        else:
            # An alias for a value or function defined elsewhere. The scan
            # records that the member exists, not what it aliases.
            lines.append("  %s: any;" % member["name"])
    lines.append("};")
    lines.append("")
    return lines


def build_legacy_globals() -> list[dict[str, Any]]:
    """The apiversion-1 globals, scanned out of `res/ecmascript/legacy/api-v1.js`.

    Two object literals, both real globals for a version-1 plugin: `showtime`
    (assigned sloppy-mode at :15) and `plugin` (a top-level `var` at :102, so
    a global binding of the program). Measured on a running instance: for such
    a plugin `typeof plugin === "object"`, `plugin.createService` is a
    function, and **`this === plugin`** at program top level -- which is why
    the legacy `(function(plugin){...})(this)` wrapper works.

    `src/plugins.c:712` -- the JavaScript branch; the identical read at :688
    is the bitcode one, inside `#if ENABLE_VMIR` -- defaults a plugin's
    apiversion to **1** when plugin.json omits it, and `ecmascript.c:913`
    loads api-v1.js for exactly those plugins. So `showtime` is real for them and absent for everyone
    else -- the plugin_examples audit (#169) counted its 7 uses as example rot
    on the strength of "no such global exists", which is true only of
    apiversion 2.
    """
    return [_scan_legacy_literal("showtime", "showtime = {"),
            _scan_legacy_literal("plugin", "var plugin = {")]


def _scan_legacy_literal(name: str, marker: str) -> dict[str, Any]:
    text = _masked_js_text(LEGACY_API_V1, mask_strings=True)
    start = text.find(marker)
    if start < 0:
        raise GenError("%s does not assign a %s global"
                       % (rel(LEGACY_API_V1), name))
    open_brace = start + len(marker) - 1
    end = _balanced_end(text, open_brace)
    if end is None:
        raise GenError("%s literal not terminated in %s"
                       % (name, rel(LEGACY_API_V1)))
    body = text[open_brace + 1:end]
    members: list[dict[str, Any]] = []
    seen: set[str] = set()
    depth = 0
    for match in re.finditer(
            r"[{}]|([A-Za-z_$][A-Za-z0-9_$]*)\s*:\s*(function\s*\(([^)]*)\))?",
            body):
        token = match.group(0)
        if token == "{":
            depth += 1
            continue
        if token == "}":
            depth -= 1
            continue
        # Only the literal's own keys. Nested object literals and the object
        # literals built inside its methods sit at depth > 0.
        if depth != 0 or not match.group(1):
            continue
        member_name = match.group(1)
        if member_name in seen:
            # `print: print` appears twice in the file; the surface is a set.
            continue
        seen.add(member_name)
        record: dict[str, Any] = {
            "name": member_name,
            "line": _source_line(text, open_brace + 1 + match.start()),
        }
        if match.group(2) is not None:
            params = [part.strip() for part in match.group(3).split(",")
                      if part.strip()]
            record["params"] = params
            record["callable"] = True
            # `xmlrpc` declares no formal parameters and reads the tail out of
            # `arguments`, the same idiom movian/xmlrpc.call uses. Without this
            # the emission would reject every real call.
            # `_own_body` (through COMMONJS_FUNCTION_RE) recognises a
            # function literal only after an `=`, and an object literal member
            # is introduced by `:`. Handing it the assignment shape it expects
            # reuses the nested-function stripping instead of re-implementing
            # it -- without this the helper returns "" and reports "no
            # evidence", which reads as non-variadic and silently rejects
            # every real showtime.xmlrpc call.
            if _uses_arguments("= " + body[match.start(2):]):
                record["variadic"] = True
        else:
            # An alias for something else (`print: print`,
            # `JSONDecode: JSON.parse`, `deviceId: Core.deviceId`). The value
            # kind is whatever it aliases, which this scan does not resolve.
            record["callable"] = False
        members.append(record)
    if not members:
        raise GenError("%s literal in %s scanned to nothing"
                       % (name, rel(LEGACY_API_V1)))
    # Two independent derivations of the same set, required to agree. The
    # brace-depth scan above cannot see a regex literal: `_masked_js_text`
    # masks comments and strings but not `/.../`, so a `/}/` inside a member
    # function drops the depth to zero and promotes the next `name:` to a
    # top-level member -- demonstrated, it invented a 27th. Indentation alone
    # is just as fragile in the other direction. Neither is trusted; a
    # disagreement is raised rather than resolved, because the correct answer
    # is unknown at exactly that point.
    # A quoted or computed key is missed by BOTH derivations -- they agree,
    # and agree wrongly -- so it cannot be caught by the cross-check below.
    # The emitter has no way to render one either, so the honest response is
    # to refuse rather than to emit a surface with a hole in it.
    # ...and the key is checked on the MASKED text, where a quoted key has
    # already lost its quotes along with its contents -- `"k": 2` arrives as
    # `      : 2`. The first version of this looked for the quotes and found
    # nothing, passing the very input it was written to reject. So: take
    # whatever precedes the colon at the literal's own indentation and
    # require it to be a plain identifier; blank and bracketed keys both
    # fail, which is the point.
    exotic = [
        match.group(1) for match in
        re.finditer(r"^  (?! )([^:\n]*):", body, re.M)
        if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*",
                            match.group(1).strip())
    ]
    if exotic:
        raise GenError(
            "%s literal in %s uses keys this scan cannot read (%s) -- "
            "quoted or computed; the emission cannot render them either"
            % (name, rel(LEGACY_API_V1),
               ", ".join(repr(key.strip()) for key in exotic)))
    indented = {match.group(1) for match in re.finditer(
        r"^  ([A-Za-z_$][A-Za-z0-9_$]*)\s*:", body, re.M)}
    scanned = {member["name"] for member in members}
    if scanned != indented:
        raise GenError(
            "%s literal in %s: brace-depth scan and indentation "
            "disagree (depth-only %s, indent-only %s) -- one of them is "
            "misreading the file"
            % (name, rel(LEGACY_API_V1), sorted(scanned - indented),
               sorted(indented - scanned)))
    return {
        "name": name,
        "apiversion": 1,
        "source": {"file": rel(LEGACY_API_V1),
                   "line": _source_line(text, start)},
        "members": sorted(members, key=lambda entry: entry["name"]),
    }


def build_interpreter_globals() -> list[dict[str, Any]]:
    """Globals the interpreter installs, with their evidence re-checked.

    A curated list is a place for a name to survive after the thing it
    describes is gone, so the anchors are not decoration: each is looked up in
    the file it names, and a missing one fails the build.
    """
    entries = load_curated(
        CURATED_INTERPRETER_GLOBALS,
        {"name", "nargs", "anchor", "source", "enabledBy", "why"})
    records: list[dict[str, Any]] = []
    for entry in entries:
        _require_anchor(entry["source"]["file"], entry["anchor"],
                        "interpreter global %s" % entry["name"])
        for macro in entry["enabledBy"]:
            _require_anchor(macro["source"]["file"],
                            "#define %s" % macro["macro"],
                            "interpreter global %s" % entry["name"])
        nargs = entry["nargs"]
        records.append({
            "name": entry["name"],
            "nargs": nargs,
            "provider": "duktape",
            "source": entry["source"],
            # -1 is how the native scanner spells DUK_VARARGS (line 549).
            "variadic": nargs == -1,
        })
    return records


def build_interpreter_objects() -> list[dict[str, Any]]:
    """Global OBJECTS the interpreter installs, with their evidence re-checked.

    `build_interpreter_globals` covers the functions; this covers the one
    object, `Duktape`. Movian reaches into it (`ecmascript.c:502`) rather than
    creating it, so `es_create_env` never names it and the scanner that reads
    that function cannot see it -- while two core modules use it and were
    reported as `Cannot find name 'Duktape'` against the emitted declarations.

    Every member carries an anchor that is looked up in live C, exactly as the
    curated functions are, because a curated list is otherwise the place a
    name survives the thing it described.
    """
    entries = load_curated(
        CURATED_INTERPRETER_OBJECTS,
        {"name", "why", "properties", "functions", "anchor", "source"})
    records: list[dict[str, Any]] = []
    for entry in entries:
        _require_anchor(entry["source"]["file"], entry["anchor"],
                        "interpreter object %s" % entry["name"])
        properties: list[dict[str, Any]] = []
        for prop in entry["properties"]:
            # Position, not just presence. `load_curated` checks the
            # top-level entry's anchor against its exact line and nothing was
            # doing that for members -- a wrong line number sailed straight
            # through, which is the same off-by-one that put fifteen bad
            # citations into a rejected PR once already. A citation nobody
            # can follow is worse than none.
            _require_anchor_at(prop["source"]["file"], prop["source"]["line"],
                               prop["anchor"],
                               "interpreter object %s.%s"
                               % (entry["name"], prop["name"]))
            properties.append({
                "name": prop["name"],
                "kind": prop["kind"],
                "source": prop["source"],
            })
        record: dict[str, Any] = {
            "name": entry["name"],
            "provider": "duktape",
            "functions": entry["functions"],
            "properties": properties,
        }
        index_signature = entry.get("indexSignature")
        if index_signature:
            record["indexSignature"] = index_signature["kind"]
        records.append(record)
    return records


def _require_anchor_at(relative_path: str, line: int, anchor: str,
                       what: str) -> None:
    """Fail unless `anchor` is on exactly that line of that file."""
    path = REPO_ROOT / relative_path
    if not path.is_file():
        raise GenError("%s: %s does not exist" % (what, relative_path))
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not isinstance(line, int) or isinstance(line, bool) or line < 1:
        raise GenError("%s: invalid source line %r" % (what, line))
    if line > len(lines):
        raise GenError("%s: %s:%d is past the end of the file"
                       % (what, relative_path, line))
    if anchor not in lines[line - 1]:
        raise GenError("%s: anchor %r is not at %s:%d"
                       % (what, anchor, relative_path, line))


def _require_anchor(relative_path: str, anchor: str, what: str) -> None:
    """Fail unless `anchor` appears in live C -- not in a comment.

    A raw substring search accepted `// #define DUK_USE_FILE_IO`, so an
    anchor could survive being commented out and go on vouching for a global
    that is no longer installed. Comments are stripped before the search;
    conditional compilation is not resolved, and the entry's `enabledBy`
    field records which macros the claim depends on so a reader can check
    what this cannot.
    """
    path = REPO_ROOT / relative_path
    if not path.is_file():
        raise GenError("%s: %s does not exist" % (what, relative_path))
    text = _strip_c_comments(path.read_text(encoding="utf-8",
                                            errors="replace"))
    if anchor not in text:
        raise GenError(
            "%s: anchor %r no longer appears in live code in %s"
            % (what, anchor, relative_path))


def _strip_c_comments(text: str) -> str:
    """Blank C comments, keeping every other character in place."""
    out = list(text)
    index = 0
    length = len(out)
    while index < length - 1:
        pair = text[index:index + 2]
        if pair == "//":
            end = text.find("\n", index)
            end = length if end < 0 else end
            for position in range(index, end):
                out[position] = " "
            index = end
        elif pair == "/*":
            end = text.find("*/", index + 2)
            end = length if end < 0 else end + 2
            for position in range(index, end):
                if out[position] != "\n":
                    out[position] = " "
            index = end
        else:
            index += 1
    return "".join(out)


def _scan_global_env(
        path: Path, text: str, marker: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    start = text.find(marker)
    if start < 0:
        raise GenError("%s not found in %s" % (marker, rel(path)))
    open_brace = text.find("{", start)
    end = _balanced_end(text, open_brace)
    if end is None:
        raise GenError("%s body not terminated in %s" % (marker, rel(path)))
    # Everything before `duk_push_global_object` belongs to the global STASH,
    # which is runtime bookkeeping and not plugin surface -- scanning from the
    # top of the function picked up its `roots` object as a global.
    global_object = text.find("duk_push_global_object", open_brace)
    if global_object < 0 or global_object > end:
        raise GenError(
            "%s does not push the global object in %s" % (marker, rel(path)))
    body = text[global_object:end]
    base_line = text[:global_object].count("\n") + 1

    def line_of(offset: int) -> int:
        return base_line + body[:offset].count("\n")

    functions: list[dict[str, Any]] = []
    objects: list[dict[str, Any]] = []
    # The object currently open, plus the pending value property whose
    # duk_push_* has been seen but whose duk_put_prop_string has not.
    open_object: dict[str, Any] | None = None
    pending_value: tuple[str, bool] | None = None

    for match in ENV_STATEMENT_RE.finditer(body):
        text_at = match.group(0)
        if PUSH_OBJECT_RE.match(text_at):
            # `duk_get_prop_string(ctx, -1, "Duktape")` also opens a scope,
            # but it reopens an EXISTING object rather than creating one, so
            # it is deliberately not matched here -- modSearch is Duktape
            # internals, not plugin surface.
            open_object = {
                "name": None,
                "functions": [],
                "properties": [],
                "source": {"file": rel(path), "line": line_of(match.start())},
            }
            continue
        push_value = PUSH_VALUE_RE.match(text_at)
        if push_value is not None:
            # `if(loaddir != NULL) {` guards two of Core's properties, so
            # they are not always present. Same optional rule the plugin hooks
            # get, for the same reason: requiring them would invent errors.
            guarded = body[:match.start()].rstrip().endswith("{")
            pending_value = (
                {"int": "number", "number": "number",
                 "string": "string", "boolean": "boolean"}[push_value.group(1)],
                guarded)
            continue
        function_list = PUT_FUNCTION_LIST_RE.match(text_at)
        if function_list is not None:
            entries = _table_functions(function_list.group(2))
            if open_object is not None:
                open_object["functions"].extend(entries)
            else:
                functions.extend(entries)
            continue
        prop = PUT_PROP_STRING_RE.match(text_at)
        if prop is None:
            continue
        name = prop.group(2)
        if pending_value is not None and open_object is not None:
            kind, guarded = pending_value
            open_object["properties"].append({
                "name": name,
                "kind": kind,
                "optional": guarded,
                "source": {"file": rel(path), "line": line_of(match.start())},
            })
            pending_value = None
            continue
        pending_value = None
        if open_object is not None:
            open_object["name"] = name
            open_object["functions"].sort(key=lambda f: f["name"])
            open_object["properties"].sort(key=lambda entry: entry["name"])
            objects.append(open_object)
            open_object = None

    if open_object is not None:
        raise GenError(
            "%s leaves an object unnamed at %s:%d"
            % (marker, rel(path), open_object["source"]["line"]))

    return functions, objects


def build_commonjs_modules() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(COMMONJS_DIR.rglob("*.js")):
        module_name = path.relative_to(COMMONJS_DIR).with_suffix("").as_posix()
        if module_name in seen:
            raise GenError("duplicate CommonJS module name: %s" % module_name)
        seen.add(module_name)
        exports = [e for e in scan_commonjs_exports(path)
                   if e["name"] != "__proto__"]
        shapes = scan_commonjs_shapes(path)
        shape_names = {shape["name"] for shape in shapes}
        receiver_members: list[dict[str, Any]] = []
        for export in exports:
            # Kept per-export as well as merged. The two `movian/settings`
            # initializers install DIFFERENT surfaces -- globalSettings adds
            # id and properties, kvstoreSettings does not -- so one merged
            # instance type made `kvstoreSettings(...).properties` compile and
            # then read undefined. The hand-written canon
            # (tests/reference/movian-settings.d.ts) models them separately;
            # this follows it, which is what #160's calibration section asks
            # for when the canon and the generator disagree.
            receiver_members.extend(export.get("receiverMembers", []))
            returned = export.get("returns")
            if isinstance(returned, str) and returned not in shape_names:
                export.pop("returns", None)
                # The overload split only says something once there is a
                # value type to contrast `void` with; without one both forms
                # render identically and the extra signature is noise.
                export.pop("voidWhen", None)
            callback_param = export.pop("callbackParam", None)
            callback_shapes = [
                shape for shape in export.pop("callbackShapes", [])
                if shape in shape_names
            ]
            if len(callback_shapes) == 1 and callback_param is not None:
                export["callbackShape"] = callback_shapes[0]
                export["callbackParam"] = callback_param
            else:
                export.pop("callbackShapeIndex", None)
                export.pop("callbackShapeNullable", None)
        receiver_members = _merge_receiver_members(receiver_members)
        record = {
            "name": module_name,
            "kind": "commonjs",
            "exports": exports,
            "source": {"file": rel(path), "line": 1},
        }
        if receiver_members:
            record["receiverMembers"] = receiver_members
        if shapes:
            record["shapes"] = shapes
        parent = _proto_parent(path)
        if parent is not None:
            record["inherits"] = parent
        records.append(record)
    records.sort(key=lambda r: r["name"])
    return records

def _source_shape_inventory() -> set[tuple[str, str, str, str]]:
    inventory: set[tuple[str, str, str, str]] = set()
    for path in sorted(COMMONJS_DIR.rglob("*.js")):
        module_name = path.relative_to(COMMONJS_DIR).with_suffix("").as_posix()
        for shape in scan_commonjs_shapes(path):
            receiver = shape.get("receiver", shape["name"])
            for method in shape["methods"]:
                inventory.add((
                    module_name, shape["kind"], receiver, method["name"]))
            for prop in shape.get("properties", []):
                inventory.add((
                    module_name, "property", receiver, prop["name"]))
        for export in scan_commonjs_exports(path):
            for member in export.get("receiverMembers", []):
                inventory.add((
                    module_name, "receiver", "module", member["name"]))
    return inventory


def _artifact_shape_inventory(
        artifact: dict[str, Any]) -> set[tuple[str, str, str, str]]:
    inventory: set[tuple[str, str, str, str]] = set()
    for module in artifact.get("js", {}).get("modules", []):
        module_name = module["name"]
        for shape in module.get("shapes", []):
            receiver = shape.get("receiver", shape["name"])
            for method in shape["methods"]:
                inventory.add((
                    module_name, shape["kind"], receiver, method["name"]))
            for prop in shape.get("properties", []):
                inventory.add((
                    module_name, "property", receiver, prop["name"]))
        for member in module.get("receiverMembers", []):
            inventory.add((
                module_name, "receiver", "module", member["name"]))
    return inventory


def _format_shape_member(
        member: tuple[str, str, str, str]) -> str:
    module, kind, receiver, name = member
    return "%s:%s:%s.%s" % (module, kind, receiver, name)


def _check_commonjs_shape_coverage(
        artifact: dict[str, Any]) -> tuple[bool, str]:
    source = _source_shape_inventory()
    emitted = _artifact_shape_inventory(artifact)
    missing = sorted(source - emitted)
    phantom = sorted(emitted - source)
    if not missing and not phantom:
        return True, (
            "COMMONJS shape coverage OK "
            "(source %d, artifact %d, missing 0, phantom 0)" %
            (len(source), len(emitted)))

    lines = ["COMMONJS SHAPE COVERAGE DRIFT"]
    if missing:
        lines.append("missing (source, artifact):")
        lines.extend("  " + _format_shape_member(member)
                     for member in missing)
    if phantom:
        lines.append("phantom (artifact, source):")
        lines.extend("  " + _format_shape_member(member)
                     for member in phantom)
    return False, "\n".join(lines)


def _runtime_member_kind(record: Any) -> str | None:
    if not isinstance(record, dict):
        return None
    record_type = record.get("type")
    if record_type == "function":
        return "function"
    if record_type == "accessor":
        return "accessor"
    if record_type in {
            "object", "string", "number", "boolean", "undefined", "null"}:
        return "value"
    return None


def _runtime_prototype_levels(
        prototype: Any) -> list[dict[str, str]]:
    levels: list[dict[str, str]] = []
    current = prototype
    while isinstance(current, dict):
        members: dict[str, str] = {}
        for name, record in (current.get("keys") or {}).items():
            kind = _runtime_member_kind(record)
            if kind is not None:
                members[name] = kind
        levels.append(members)
        current = current.get("prototype")
    return levels


def _runtime_stage_reason(
        stage: Any, fallback: str,
        owner: str | None = None) -> str:
    if not isinstance(stage, dict):
        return fallback
    parts: list[str] = []
    status = stage.get("status")
    if isinstance(status, str):
        parts.append("status=%s" % status)
    for key in ("reason", "error"):
        value = stage.get(key)
        if isinstance(value, str) and value not in parts:
            parts.append(value)
    for entry in stage.get("unreachable", []):
        if not isinstance(entry, dict):
            continue
        entry_owner = entry.get("class")
        if owner is not None and entry_owner not in (owner, None):
            continue
        value = entry.get("reason")
        if isinstance(value, str) and value not in parts:
            parts.append(value)
    return "; ".join(parts) or fallback


def _resolve_required_module(path: Path, spec: str) -> str | None:
    if not (spec.startswith("./") or spec.startswith("../")):
        return spec
    target = (path.parent / spec).resolve()
    if target.suffix != ".js":
        target = target.with_suffix(".js")
    try:
        return target.relative_to(COMMONJS_DIR).with_suffix("").as_posix()
    except ValueError:
        return None


def _required_aliases(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    aliases: dict[str, str] = {}
    for match in re.finditer(
            r"\b(?:var|let|const)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*="
            r"\s*require\(\s*['\"]([^'\"]+)['\"]\s*\)", text):
        module_name = _resolve_required_module(path, match.group(2))
        if module_name is not None:
            aliases[match.group(1)] = module_name
    return aliases


def _static_export_kind(
        module_name: str, export_name: str,
        modules: dict[str, dict[str, Any]],
        cache: dict[tuple[str, str], str],
        visiting: set[tuple[str, str]]) -> str:
    key = (module_name, export_name)
    if key in cache:
        return cache[key]
    if key in visiting:
        return "value"
    visiting.add(key)

    module = modules.get(module_name)
    if module is None:
        result = "value"
    elif module.get("kind") == "native":
        result = ("function"
                 if any(f.get("name") == export_name
                        for f in module.get("functions", []))
                 else "value")
    else:
        export = next(
            (entry for entry in module.get("exports", [])
             if entry.get("name") == export_name),
            None)
        if export is None:
            result = "value"
        elif "params" in export or export.get("constructor"):
            result = "function"
        else:
            source = REPO_ROOT / export["source"]["file"]
            lines = source.read_text(encoding="utf-8").splitlines()
            line_number = export["source"]["line"]
            line = lines[line_number - 1] if line_number <= len(lines) else ""
            assignment = re.search(r"=\s*(.*?)\s*;?\s*$", line)
            rhs = assignment.group(1).strip() if assignment else ""
            if re.match(r"function\b", rhs):
                result = "function"
            elif re.fullmatch(
                    r"[A-Za-z_$][A-Za-z0-9_$]*", rhs):
                function_declared = re.search(
                    r"\bfunction\s+%s\s*\(" % re.escape(rhs),
                    source.read_text(encoding="utf-8")) is not None
                result = "function" if function_declared else "value"
            else:
                dotted = re.fullmatch(
                    r"([A-Za-z_$][A-Za-z0-9_$]*)\.([A-Za-z_$]"
                    r"[A-Za-z0-9_$]*)", rhs)
                if dotted is None:
                    result = "value"
                else:
                    target = _required_aliases(source).get(dotted.group(1))
                    result = (
                        _static_export_kind(
                            target, dotted.group(2), modules,
                            cache, visiting)
                        if target is not None else "value")
    visiting.remove(key)
    cache[key] = result
    return result


def _format_runtime_member(
        record: dict[str, Any]) -> str:
    text = (
        "module=%s shape=%s member=%s"
        % (record["module"], record["shape"], record["member"]))
    if record.get("kind") is not None:
        text += " kind=%s" % record["kind"]
    if record.get("missing") == "artifact":
        text += " missing-from=artifact"
    elif record.get("missing") == "oracle":
        text += " missing-from=oracle"
    elif record.get("missing") == "kind":
        text += (
            " missing-from=neither kind-mismatch=%s/%s"
            % (record.get("artifactKind"), record.get("oracleKind")))
    if record.get("reason"):
        text += " reason=%s" % record["reason"]
    return text


# The reviewed exclusion list: every member the capture could not reach, as
# (module, shape, member). It is data under review precisely because the
# cross-check's best score is otherwise achieved by observing nothing -- with
# the oracle's tiers emptied the run reported `match 174, unreachable 105`
# and still exited 0. Growth here has to show up in a diff, not in a green
# run, so the set is compared exactly: an entry that arrives unreviewed fails,
# and an entry that becomes reachable fails too, because leaving it listed
# lets the floor keep credit for a member nobody observes any more.
RUNTIME_ORACLE_UNREACHABLE: tuple[tuple[str, str, str], ...] = (
    # The capture never constructs a Request. The reason it records --
    # "the request factory starts network I/O" -- is false: http.js:61-64
    # formats a URL and calls `new Request(url)`, and the socket only
    # opens in `end()`. These six are therefore the six most likely to
    # leave this list, and they leave it by the introspector attempting
    # the construction, not by anyone editing the excuse.
    ("http", "Request", "end"),
    ("http", "Request", "headers"),
    ("http", "Request", "on"),
    ("http", "Request", "onError"),
    ("http", "Request", "onResponse"),
    ("http", "Request", "url"),
    # A Response exists only as the result of a transfer.
    ("http", "Response", "bytes"),
    ("http", "Response", "encoding"),
    ("http", "Response", "on"),
    ("http", "Response", "onData"),
    ("http", "Response", "onEnd"),
    ("http", "Response", "setEncoding"),
    ("http", "Response", "statusCode"),
    # Same: movian/http hands back a response object the capture cannot
    # obtain without performing the request.
    ("movian/http", "HttpResponse", "allheaders"),
    ("movian/http", "HttpResponse", "bytes"),
    ("movian/http", "HttpResponse", "contenttype"),
    ("movian/http", "HttpResponse", "convertFromEncoding"),
    ("movian/http", "HttpResponse", "headers"),
    ("movian/http", "HttpResponse", "headers_lc"),
    ("movian/http", "HttpResponse", "multiheaders"),
    ("movian/http", "HttpResponse", "multiheaders_lc"),
    ("movian/http", "HttpResponse", "statuscode"),
    ("movian/http", "HttpResponse", "toString"),
    # Constructing a Searcher registers a global search hook, which would
    # outlive the capture and change what later tiers observe.
    ("movian/page", "Searcher", "searcher"),
    # service.create mutates global service state -- the same state the
    # home screen reads.
    ("movian/service", "Service", "destroy"),
    ("movian/service", "Service", "enabled"),
    ("movian/service", "Service", "id"),
    # The shared settings receiver could not be constructed safely; the
    # capture records the attempt rather than a hand-written excuse.
    ("movian/settings", "sp", "zombie"),
    # Opening a DB creates a file in the persistent path.
    ("movian/sqlite", "DB", "db"),
    # The constructor calls native hook.register, a global registration.
    ("movian/videoscrobbler", "VideoScrobbler", "hook"),
    ("movian/videoscrobbler", "VideoScrobbler", "onpause"),
    ("movian/videoscrobbler", "VideoScrobbler", "onresume"),
    ("movian/videoscrobbler", "VideoScrobbler", "onstart"),
    ("movian/videoscrobbler", "VideoScrobbler", "onstop"),
    ("movian/videoscrobbler", "VideoScrobbler", "paused"),
)
# Members the capture must actually agree with. Derived, not chosen: it has
# to equal expected minus the reviewed exclusions minus the plugin-supplied
# slots, and the check below says so, so the number cannot carry slack that
# would let coverage fall without anyone noticing.
RUNTIME_ORACLE_MIN_MATCH = 242


def _runtime_oracle_floor_problems(
        matches: int,
        unreachable: list[dict[str, Any]],
        expected_total: int,
        plugin_supplied: int) -> list[str]:
    problems: list[str] = []
    reviewed = set(RUNTIME_ORACLE_UNREACHABLE)
    observed = {(entry["module"], entry["shape"], entry["member"])
                for entry in unreachable}
    for key in sorted(observed - reviewed):
        problems.append(
            "unreviewed exclusion %s.%s.%s -- the capture stopped reaching "
            "it; review why and add it to RUNTIME_ORACLE_UNREACHABLE"
            % key)
    for key in sorted(reviewed - observed):
        problems.append(
            "stale exclusion %s.%s.%s -- the capture reaches it now; drop it "
            "from RUNTIME_ORACLE_UNREACHABLE" % key)
    if matches < RUNTIME_ORACLE_MIN_MATCH:
        problems.append(
            "coverage %d is below the floor of %d"
            % (matches, RUNTIME_ORACLE_MIN_MATCH))
    tight = expected_total - len(reviewed) - plugin_supplied
    if RUNTIME_ORACLE_MIN_MATCH < tight and not (observed - reviewed):
        problems.append(
            "the floor carries %d of slack: %d members are neither excluded "
            "nor plugin-supplied, so RUNTIME_ORACLE_MIN_MATCH must be %d"
            % (tight - RUNTIME_ORACLE_MIN_MATCH, tight, tight))
    return problems


_ES_MODULE_RE = re.compile(r'ES_MODULE\s*\(\s*"([^"]+)"')
# Every invocation, literal name or not. A registration written as
# `ES_MODULE(MODULE_NAME, ...)` after a #define is a real native module that
# the regex above cannot name -- and neither can the artifact scanner, nor
# the capture, since natives are not files to discover. All three blind is
# the shape this work exists to refuse, so an invocation that cannot be
# resolved is reported rather than skipped.
_ES_MODULE_ANY_RE = re.compile(r'ES_MODULE\s*\(')


def expected_runtime_modules() -> dict[str, str]:
    """Every module the runtime must be able to load, and where that is
    known from.

    The capture used to inspect a hand-written list, so a module nobody added
    to it was unobserved -- and in syntax the static scanner cannot read, it
    was missing from the artifact too. Both sides blind, and a cross-check
    between two blind sides agrees about nothing.

    Discovery in the introspector closes that going forward; this closes it
    from the other end, where no build is needed, so a capture that stops
    seeing a module cannot pass here either.
    """
    expected: dict[str, str] = {}
    modules_dir = REPO_ROOT / "res" / "ecmascript" / "modules"
    for path in sorted(modules_dir.rglob("*.js")):
        name = path.relative_to(modules_dir).as_posix()[:-len(".js")]
        expected[name] = "a module file"
        if name.startswith("movian/"):
            # es_modsearch rewrites the prefix unconditionally
            # (ecmascript.c:435-439), so every movian/* module is reachable
            # under showtime/* as a separate instance.
            expected["showtime/" + name[len("movian/"):]] = (
                "the showtime alias of a module file")
    for path in sorted((REPO_ROOT / "src" / "ecmascript").rglob("*.c")):
        # Comments first, or `/* ES_MODULE("dead", ...) */` becomes a module
        # this tree does not register and the census goes red on a correct
        # runtime. The same rule as the recipe parser, in the other language;
        # the stripper is the one already checked against these 26 files.
        source = _js_code_only(
            path.read_text(encoding="utf-8", errors="replace"))
        for name in _ES_MODULE_RE.findall(source):
            expected["native/" + name] = (
                "ES_MODULE in %s" % path.relative_to(REPO_ROOT).as_posix())
    return expected


def unresolved_native_registrations() -> list[str]:
    """`ES_MODULE(...)` invocations whose name is not a literal."""
    unresolved = []
    for path in sorted((REPO_ROOT / "src" / "ecmascript").rglob("*.c")):
        source = _js_code_only(
            path.read_text(encoding="utf-8", errors="replace"))
        extra = (len(_ES_MODULE_ANY_RE.findall(source))
                 - len(_ES_MODULE_RE.findall(source)))
        if extra > 0:
            unresolved.append(
                "%s registers %d native module(s) under a name this census "
                "cannot read; nothing else can read it either"
                % (path.relative_to(REPO_ROOT).as_posix(), extra))
    return unresolved


def shadowing_plugin_modules() -> list[str]:
    """Files in the introspector directory whose module id is a core one.

    es_modsearch tries the plugin directory first (ecmascript.c:443-452), so
    such a file is what `require()` returns -- the capture would then be
    describing the plugin's copy while the artifact describes the core
    module, under the same name and with nothing saying so.
    """
    core = set(expected_runtime_modules())
    shadows = []
    for path in sorted(INTROSPECTOR_DIR.rglob("*.js")):
        module_id = path.relative_to(INTROSPECTOR_DIR).as_posix()[:-len(".js")]
        if module_id in core:
            shadows.append(
                "%s shadows the core module %s; the capture would describe "
                "this file under that name"
                % (path.relative_to(REPO_ROOT).as_posix(), module_id))
    return shadows


def runtime_oracle_census(oracle: Any) -> list[str]:
    """Every module this tree provides, against what the capture OBSERVED.

    Not against `modules`, which is the list of names the run attempted: the
    introspector now builds that by walking the same directory this function
    walks, so comparing the two would be a check whose sides cannot disagree
    -- the tautology the whole runtime-oracle line exists to remove. A module
    whose `require()` threw is in `modules` and in `loadErrors`, with a
    `tier1` record of `unavailable` and no members at all, and for a module
    the static scanner also cannot read there is then nothing left to
    compare. So the question asked here is whether the capture WALKED it.
    """
    if not isinstance(oracle, dict):
        return ["the oracle is not a JSON object"]
    tier1 = oracle.get("tier1")
    if not isinstance(tier1, dict):
        return ["the oracle records no tier1, so nothing says what it "
                "actually walked"]
    load_errors = oracle.get("loadErrors")
    load_errors = load_errors if isinstance(load_errors, dict) else {}
    # `not-applicable` is what a module exporting null or a primitive gets:
    # loaded fine, no object to walk. Treating that as unobserved would make
    # the gate permanently red for a valid module, and the artifact records
    # no members for it either, so there is nothing being hidden.
    loaded = {"walked", "not-applicable"}
    walked = {name for name, record in tier1.items()
              if isinstance(record, dict) and record.get("status") in loaded}

    expected = expected_runtime_modules()
    problems = []
    for name, reason in sorted(expected.items()):
        if name in load_errors:
            problems.append("%s exists (%s) and the capture could not load "
                            "it: %s" % (name, reason, load_errors[name]))
        elif name not in walked:
            status = (tier1.get(name) or {}).get("status", "never attempted")
            problems.append("%s exists (%s) and the capture did not walk it "
                            "(%s)" % (name, reason, status))
    attempted = oracle.get("modules")
    seen = walked | (set(attempted) if isinstance(attempted, list) else set())
    problems.extend(
        "%s was loaded by the capture and nothing in this tree provides it"
        % name for name in sorted(seen - set(expected)))
    problems.extend(unresolved_native_registrations())
    problems.extend(shadowing_plugin_modules())
    return problems


def _format_runtime_oracle_report(
        report: dict[str, Any]) -> str:
    status = report.get("status")
    if status == "failed":
        lines = ["RUNTIME ORACLE CROSS-CHECK FAILED"]
        lines.append(
            "counts: match %d, drift %d, oracle-unreachable %d"
            % (report.get("match", 0), report.get("drift", 0),
               report.get("oracleUnreachable", 0)))
        if report.get("error"):
            lines.append("error: %s" % report["error"])
        return "\n".join(lines)

    missing_modules = report.get("missingModules", [])
    state = "OK" if (report.get("drift", 0) == 0 and not missing_modules
                     and not report.get("floorProblems")) else "DRIFT"
    lines = [
        "RUNTIME ORACLE CROSS-CHECK %s" % state,
        "counts: match %d, drift %d, missing-modules %d, plugin-supplied %d,"
        " oracle-unreachable %d" %
        (report["match"], report["drift"], len(missing_modules),
         report.get("pluginSupplied", 0),
         report["oracleUnreachable"]),
    ]
    if missing_modules:
        lines.append(
            "modules the runtime has and the artifact dropped entirely:")
        lines.extend("  " + name for name in missing_modules)
    drift = report.get("driftMembers", [])
    if drift:
        lines.append("drift members:")
        lines.extend("  " + _format_runtime_member(entry)
                     for entry in drift)
    supplied = report.get("pluginSuppliedMembers", [])
    if supplied:
        lines.append("plugin-supplied members (declared optional, unset):")
        lines.extend("  " + _format_runtime_member(entry)
                     for entry in supplied)
    problems = report.get("floorProblems", [])
    if problems:
        lines.append("coverage floor:")
        lines.extend("  " + problem for problem in problems)
    lines.append("oracle-unreachable members:")
    unreachable = report.get("unreachableMembers", [])
    if unreachable:
        lines.extend("  " + _format_runtime_member(entry)
                     for entry in unreachable)
    else:
        lines.append("  none")
    return "\n".join(lines)


def _check_runtime_oracle(
        artifact: dict[str, Any],
        oracle: Any) -> tuple[bool, str, dict[str, Any]]:
    if not isinstance(oracle, dict):
        report = {
            "status": "failed",
            "match": 0,
            "drift": 0,
            "oracleUnreachable": 0,
            "error": "runtime oracle is not a JSON object",
        }
        return False, _format_runtime_oracle_report(report), report
    if oracle.get("version") != RUNTIME_ORACLE_VERSION:
        report = {
            "status": "failed",
            "match": 0,
            "drift": 0,
            "oracleUnreachable": 0,
            "error": (
                "runtime oracle version %r, expected %d"
                % (oracle.get("version"), RUNTIME_ORACLE_VERSION)),
        }
        return False, _format_runtime_oracle_report(report), report
    # The introspector emits twice per run and only the post-route payload is
    # complete. Absent means the capture predates the marker split; an
    # explicit false means someone committed the load-time payload, whose
    # tier3 members are unattempted rather than absent.
    if oracle.get("tier3PageOpened") is False:
        report = {
            "status": "failed",
            "match": 0,
            "drift": 0,
            "oracleUnreachable": 0,
            "error": (
                "runtime oracle is the partial load-time payload "
                "(tier3PageOpened false); capture the payload emitted after "
                "opening the introspect:page route"),
        }
        return False, _format_runtime_oracle_report(report), report
    stamp = oracle.get("inputs")
    if (not isinstance(stamp, dict)
            or stamp.get("version") != RUNTIME_ORACLE_INPUTS_VERSION):
        report = {
            "status": "failed",
            "match": 0,
            "drift": 0,
            "oracleUnreachable": 0,
            "error": (
                "runtime oracle carries no freshness stamp, so nothing "
                "binds it to the sources it was captured from; "
                + RUNTIME_ORACLE_RECAPTURE),
        }
        return False, _format_runtime_oracle_report(report), report
    stale = runtime_oracle_stale_inputs(stamp.get("files"))
    if stale:
        report = {
            "status": "failed",
            "match": 0,
            "drift": 0,
            "oracleUnreachable": 0,
            "error": (
                "runtime oracle is stale -- it was captured from a different "
                "source tree, so agreeing with it proves nothing about this "
                "one:\n  " + "\n  ".join(stale) + "\n  "
                + RUNTIME_ORACLE_RECAPTURE),
        }
        return False, _format_runtime_oracle_report(report), report

    modules = {
        module["name"]: module
        for module in artifact.get("js", {}).get("modules", [])
        if isinstance(module, dict) and isinstance(module.get("name"), str)
    }
    scopes: dict[
            tuple[str, str, str], tuple[dict[str, str], bool, str]] = {}
    reasons: dict[tuple[str, str, str], str] = {}

    def add_reason(key: tuple[str, str, str], reason: str) -> None:
        if reason:
            reasons.setdefault(key, reason)

    def add_scope(
            key: tuple[str, str, str],
            members: dict[str, str],
            complete: bool,
            reason: str) -> None:
        known = {name: kind for name, kind in members.items()
                 if kind is not None}
        previous = scopes.get(key)
        if previous is None:
            scopes[key] = (known, complete, reason)
            return
        merged = dict(previous[0])
        merged.update(known)
        scopes[key] = (
            merged, previous[1] and complete, previous[2] or reason)

    def stage_members(stage: Any, field: str) -> tuple[dict[str, str], bool]:
        members: dict[str, str] = {}
        complete = isinstance(stage, dict)
        for name, record in (stage.get(field) or {}).items() \
                if isinstance(stage, dict) else []:
            kind = _runtime_member_kind(record)
            if kind is None:
                complete = False
            else:
                members[name] = kind
        return members, complete

    before_all = oracle.get("before")
    tier1_all = oracle.get("tier1")
    tier2_all = oracle.get("tier2")
    if not isinstance(before_all, dict):
        before_all = {}
    if not isinstance(tier1_all, dict):
        tier1_all = {}
    if not isinstance(tier2_all, dict):
        tier2_all = {}

    # Every comparison below walks the ARTIFACT's modules, so a module the
    # runtime observed and the artifact no longer declares was invisible:
    # dropping a whole module from the scanner made this leg GREENER (fewer
    # members to disagree about) instead of redder. Only modules the capture
    # actually reached count -- one that failed to load then must not invent
    # drift now.
    # `showtime/x` has no module record of its own: the bundle emits it as an
    # `export * from 'movian/x'` alias block, so it is covered exactly when
    # its canonical module is. Resolving the name here keeps the check honest
    # in both directions -- dropping `movian/prop` reports `showtime/prop`
    # missing too, because the alias block goes with it.
    oracle_modules = {
        name for name, stage in before_all.items()
        if isinstance(stage, dict) and stage.get("keys")
    }
    missing_modules = sorted(
        name for name in oracle_modules
        if ("movian/" + name.split("/", 1)[1]
            if name.startswith("showtime/") else name) not in modules)

    for module_name, module in modules.items():
        before = before_all.get(module_name)
        members, complete = stage_members(before, "keys")
        if members or complete:
            add_scope(
                (module_name, "module", "own"), members, complete,
                "runtime-api.json before module walk")
        else:
            add_reason(
                (module_name, "module", "own"),
                _runtime_stage_reason(
                    before,
                    "runtime oracle did not reach module %s" % module_name))

    after_settings = oracle.get("afterGlobalSettings")
    if not isinstance(after_settings, dict):
        after_settings = {}
    for module_name, module in modules.items():
        if not module.get("receiverMembers"):
            continue
        after = after_settings.get(module_name)
        members, complete = stage_members(after, "keys")
        if members or complete:
            add_scope(
                (module_name, "module", "own"), members, complete,
                "runtime-api.json afterGlobalSettings")
        else:
            add_reason(
                (module_name, "module", "own"),
                _runtime_stage_reason(
                    after,
                    "runtime oracle did not reach mutated module %s"
                    % module_name))

    for module_name, module in modules.items():
        tier1 = tier1_all.get(module_name)
        function_exports = (
            tier1.get("functionExports", {})
            if isinstance(tier1, dict) else {})
        for shape in module.get("shapes", []):
            receiver = shape.get("receiver", "")
            if not isinstance(receiver, str) or \
                    not receiver.startswith("exports."):
                continue
            export_name = receiver.split(".", 1)[1]
            function_export = function_exports.get(export_name)
            prototype = (
                function_export.get("prototype")
                if isinstance(function_export, dict) else None)
            if isinstance(function_export, dict) and \
                    function_export.get("status") == "walked" and \
                    isinstance(prototype, dict):
                levels = _runtime_prototype_levels(prototype)
                for index, level in enumerate(levels[:2]):
                    add_scope(
                        (module_name, shape["name"],
                         "prototype" if index == 0 else "prototype2"),
                        level, True,
                        "runtime-api.json tier1 %s" % export_name)
            else:
                reason_stage = (
                    tier1 if function_export is not None
                    else tier2_all.get(module_name))
                add_reason(
                    (module_name, shape["name"], "prototype"),
                    _runtime_stage_reason(
                        function_export if function_export is not None
                        else reason_stage,
                        _runtime_stage_reason(
                            reason_stage,
                            "runtime oracle did not reach shape %s"
                            % shape["name"]),
                        export_name))

    # A tier2 result's nested objects are named by the `returns` facts that
    # the generator already emits. No constructor or arity assumptions enter
    # this comparison.
    for module_name, module in modules.items():
        stage = tier2_all.get(module_name)
        if not isinstance(stage, dict) or \
                stage.get("status") != "constructed":
            continue
        result = stage.get("result")
        nested = result.get("nested", {}) if isinstance(result, dict) else {}
        for export in module.get("exports", []):
            returned = export.get("returns")
            targets: list[tuple[str, str]] = []
            if isinstance(returned, dict):
                for field in returned.get("fields", []):
                    if isinstance(field, dict) and \
                            field.get("name") in nested and \
                            isinstance(field.get("type"), str):
                        targets.append((field["name"], field["type"]))
            elif isinstance(returned, str):
                targets = [(name, returned) for name in nested]
            for nested_name, shape_name in targets:
                child = nested.get(nested_name)
                if not isinstance(child, dict):
                    continue
                members, complete = stage_members(child, "keys")
                add_scope(
                    (module_name, shape_name, "own"),
                    members, complete,
                    "runtime-api.json tier2 %s" % nested_name)
                levels = _runtime_prototype_levels(child.get("prototype"))
                for index, level in enumerate(levels[:2]):
                    add_scope(
                        (module_name, shape_name,
                         "prototype" if index == 0 else "prototype2"),
                        level, True,
                        "runtime-api.json tier2 %s" % nested_name)

    # A shared shape is installed as a module prototype by the runtime call.
    # Its constructor-created fields remain unreachable because tier2 records
    # that no side-effect-free construction was performed.
    for module_name, module in modules.items():
        shared_shapes = [
            shape for shape in module.get("shapes", [])
            if shape.get("kind") == "shared"
        ]
        if not shared_shapes:
            continue
        after = after_settings.get(module_name)
        if not isinstance(after, dict):
            for shape in shared_shapes:
                add_reason(
                    (module_name, shape["name"], "prototype"),
                    _runtime_stage_reason(
                        after,
                        "runtime oracle did not reach shared shape %s"
                        % shape["name"]))
            continue
        prototype = after.get("prototype")
        levels = _runtime_prototype_levels(prototype)
        for index, level in enumerate(levels[:2]):
            for shape in shared_shapes:
                add_scope(
                    (module_name, shape["name"],
                     "prototype" if index == 0 else "prototype2"),
                    level, True,
                    "runtime-api.json afterGlobalSettings")
        module_names = {
            entry.get("name") for entry in module.get("exports", [])
        } | {
            entry.get("name") for entry in module.get("receiverMembers", [])
        }
        own, _ = stage_members(after, "keys")
        own = {name: kind for name, kind in own.items()
               if name not in module_names}
        for shape in shared_shapes:
            add_scope(
                (module_name, shape["name"], "own"), own, False,
                "runtime-api.json tier2 status=skipped: "
                "shared receiver instance was not safely constructed")

    tier3 = oracle.get("tier3")
    if not isinstance(tier3, dict):
        tier3 = {}

    def tier3_candidates(key: str) -> list[tuple[str, dict[str, Any]]]:
        candidates: list[tuple[str, dict[str, Any]]] = []
        if key == "items":
            for module_name, module in modules.items():
                for shape in module.get("shapes", []):
                    if str(shape.get("name", "")).lower() == "item":
                        candidates.append((module_name, shape))
        else:
            for module_name, module in modules.items():
                for shape in module.get("shapes", []):
                    if str(shape.get("name", "")).lower() == key.lower():
                        candidates.append((module_name, shape))
            if not candidates and key in modules:
                module = modules[key]
                candidates = [
                    (key, shape) for shape in module.get("shapes", [])
                    if any(
                        entry.get("constructor") and
                        entry.get("name") == shape.get("receiver", "")[8:]
                        for entry in module.get("exports", [])
                    )
                ]
            if not candidates:
                for module_name, module in modules.items():
                    for shape in module.get("shapes", []):
                        receiver = str(shape.get("receiver", ""))
                        if receiver.lower() == "exports." + key.lower():
                            candidates.append((module_name, shape))
        return candidates if len(candidates) == 1 else []

    def add_tier3_result(
            key: str, value: Any,
            entry_name: str | None = None) -> None:
        candidates = tier3_candidates(key)
        if len(candidates) != 1:
            return
        module_name, shape = candidates[0]
        shape_name = shape["name"]
        result = value.get("result") if isinstance(value, dict) else None
        constructed = (
            isinstance(value, dict) and value.get("status") == "constructed"
            and isinstance(result, dict))
        if not constructed:
            add_reason(
                (module_name, shape_name, "own"),
                _runtime_stage_reason(
                    value,
                    "runtime oracle did not reach tier3 shape %s"
                    % shape_name,
                    shape_name))
            add_reason(
                (module_name, shape_name, "prototype"),
                _runtime_stage_reason(
                    value,
                    "runtime oracle did not reach tier3 shape %s"
                    % shape_name,
                    shape_name))
            return
        members, complete = stage_members(
            result, "properties" if "properties" in result else "keys")
        label = "runtime-api.json tier3 %s" % key
        if entry_name is not None:
            label += ".%s" % entry_name
        add_scope(
            (module_name, shape_name, "own"), members, complete, label)
        levels = _runtime_prototype_levels(result.get("prototype"))
        for index, level in enumerate(levels[:2]):
            add_scope(
                (module_name, shape_name,
                 "prototype" if index == 0 else "prototype2"),
                level, True, label)

    for key, value in tier3.items():
        if key == "items" and isinstance(value, dict):
            for entry_name, entry in value.items():
                add_tier3_result(key, entry, entry_name)
        else:
            add_tier3_result(key, value)

    static_kind_cache: dict[tuple[str, str], str] = {}
    # The trailing flag marks a member the module only guards with
    # `typeof this.X === 'function'` -- a slot a PLUGIN fills. Absent from
    # a capture where no plugin filled it, which is the expected state, not
    # drift. Kept as its own bucket rather than folded into `match`, so the
    # set stays visible and cannot quietly absorb a real disagreement.
    expected: list[tuple[str, str, str, str, str, bool]] = []
    for module_name, module in modules.items():
        for function in module.get("functions", []):
            expected.append(
                (module_name, "module", function["name"], "function",
                 "own", False))
        for export in module.get("exports", []):
            kind = _static_export_kind(
                module_name, export["name"], modules,
                static_kind_cache, set())
            expected.append(
                (module_name, "module", export["name"], kind, "own",
                 False))
        for member in module.get("receiverMembers", []):
            expected.append(
                (module_name, "module", member["name"],
                 member["kind"], "own", False))
        for shape in module.get("shapes", []):
            shape_name = shape["name"]
            receiver = str(shape.get("receiver", ""))
            method_scope = (
                "prototype2" if receiver.endswith("Proto")
                else "prototype")
            for method in shape.get("methods", []):
                expected.append(
                    (module_name, shape_name, method["name"],
                     "function", method_scope, False))
            for prop in shape.get("properties", []):
                candidates = [
                    scope_name for scope_name in
                    ("own", "prototype", "prototype2")
                    if (module_name, shape_name, scope_name) in scopes
                    and prop["name"] in scopes[
                        (module_name, shape_name, scope_name)][0]
                ]
                if len(candidates) == 1:
                    prop_scope = candidates[0]
                else:
                    prop_scope = (
                        "prototype" if receiver.endswith("Proto")
                        else "own")
                expected.append(
                    (module_name, shape_name, prop["name"],
                     prop.get("kind", "value"), prop_scope,
                     bool(prop.get("optional"))))

    matches = 0
    drift: list[dict[str, Any]] = []
    unreachable: list[dict[str, Any]] = []
    plugin_supplied: list[dict[str, Any]] = []
    expected_keys = {(module, shape, name)
                     for module, shape, name, _kind, _scope, _opt
                     in expected}
    for module_name, shape_name, name, kind, scope_name, optional \
            in expected:
        scope_key = (module_name, shape_name, scope_name)
        scope = scopes.get(scope_key)
        if scope is None:
            unreachable.append({
                "module": module_name,
                "shape": shape_name,
                "member": name,
                "kind": kind,
                "reason": reasons.get(
                    scope_key,
                    _runtime_stage_reason(
                        tier2_all.get(module_name),
                        "runtime oracle did not reach shape %s"
                        % shape_name,
                        shape_name)),
            })
            continue
        observed = scope[0].get(name)
        if observed is None:
            if not scope[1]:
                unreachable.append({
                    "module": module_name,
                    "shape": shape_name,
                    "member": name,
                    "kind": kind,
                    "reason": scope[2] or
                    "runtime oracle did not complete this scope",
                })
            elif optional:
                # The scope was constructed and the member is genuinely not
                # there -- which is what an unfilled plugin hook looks like.
                plugin_supplied.append({
                    "module": module_name,
                    "shape": shape_name,
                    "member": name,
                    "kind": kind,
                    "reason": "declared by a typeof-guard; no plugin"
                              " assigned it in this capture",
                })
            else:
                drift.append({
                    "module": module_name,
                    "shape": shape_name,
                    "member": name,
                    "artifactKind": kind,
                    "oracleKind": None,
                    "missing": "oracle",
                })
        elif observed != kind:
            drift.append({
                "module": module_name,
                "shape": shape_name,
                "member": name,
                "artifactKind": kind,
                "oracleKind": observed,
                "missing": "kind",
            })
        else:
            matches += 1

    reported_extras: set[tuple[str, str, str]] = set()
    for (module_name, shape_name, _scope_name), (
            members, _complete, _reason) in scopes.items():
        for name, kind in members.items():
            key = (module_name, shape_name, name)
            if key in expected_keys or key in reported_extras:
                continue
            reported_extras.add(key)
            drift.append({
                "module": module_name,
                "shape": shape_name,
                "member": name,
                "artifactKind": None,
                "oracleKind": kind,
                "missing": "artifact",
            })

    drift.sort(key=lambda entry: (
        entry["module"], entry["shape"], entry["member"]))
    unreachable.sort(key=lambda entry: (
        entry["module"], entry["shape"], entry["member"]))
    floor_problems = runtime_oracle_census(oracle)
    if oracle.get("moduleDiscoveryError"):
        floor_problems.append(
            "the capture could not enumerate the module files: %s"
            % oracle["moduleDiscoveryError"])
    floor_problems += _runtime_oracle_floor_problems(
        matches, unreachable, len(expected), len(plugin_supplied))
    agreed = not drift and not missing_modules and not floor_problems
    report = {
        "status": "ok" if agreed else "drift",
        "floorProblems": floor_problems,
        "match": matches,
        "drift": len(drift),
        "pluginSupplied": len(plugin_supplied),
        "pluginSuppliedMembers": sorted(
            plugin_supplied,
            key=lambda e: (e["module"], e["shape"], e["member"])),
        "oracleUnreachable": len(unreachable),
        "driftMembers": drift,
        "missingModules": missing_modules,
        "unreachableMembers": unreachable,
    }
    return agreed, _format_runtime_oracle_report(report), report


def build_modules() -> list[dict[str, Any]]:
    modules = build_native_modules() + build_commonjs_modules()
    modules.sort(key=lambda r: r["name"])
    return modules


# ---------------------------------------------------------------------------
# glw.operators / glw.scopes / js.pluginManifest -- curated inputs
# ---------------------------------------------------------------------------

def load_curated(path: Path, required_keys: set[str]) -> list[dict[str, Any]]:
    if not path.is_file():
        raise GenError("curated input file not found: %s" % path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise GenError("%s: expected a top-level JSON array" % path)
    for entry in data:
        if not isinstance(entry, dict):
            raise GenError("%s: entry is not an object: %r" % (path, entry))
        entry_name = entry.get("name")
        if entry_name is None:
            entry_name = entry.get("key")
        if entry_name is None:
            symbols = entry.get("symbols")
            entry_name = symbols[0] if isinstance(symbols, list) and symbols else entry
        missing = (required_keys | {"anchor"}) - entry.keys()
        if missing:
            raise GenError("%s: entry %r missing keys %s"
                           % (path, entry_name, sorted(missing)))
        src = entry["source"]
        if not isinstance(src, dict) or "file" not in src or "line" not in src:
            raise GenError("%s: entry %r has malformed source"
                           % (path, entry_name))
        anchor = entry["anchor"]
        if not isinstance(anchor, str) or not anchor:
            raise GenError("%s: entry %r has malformed anchor: %r"
                           % (path, entry_name, anchor))
        src_path = REPO_ROOT / src["file"]
        if not src_path.is_file():
            raise GenError("%s: entry %r source.file does not exist: %s"
                           % (path, entry_name, src["file"]))
        line = src["line"]
        if not isinstance(line, int) or isinstance(line, bool) or line < 1:
            raise GenError("%s: entry %r has invalid source.line: %r"
                           % (path, entry_name, line))
        lines = src_path.read_text(encoding="utf-8").splitlines()
        if line > len(lines):
            raise GenError("%s: entry %r source.line is out of range: %s:%d"
                           % (path, entry_name, src["file"], line))
        if anchor not in lines[line - 1]:
            raise GenError("%s: entry %r anchor %r not found at %s:%d"
                           % (path, entry_name, anchor, src["file"], line))
    return data


def build_operators() -> list[dict[str, Any]]:
    ops = load_curated(CURATED_OPERATORS,
                        {"symbols", "token", "category", "semantics", "source"})
    ops.sort(key=lambda r: r["symbols"][0])
    return ops


def build_scopes() -> list[dict[str, Any]]:
    scopes = load_curated(CURATED_SCOPES, {"name", "meaning", "source"})
    scopes.sort(key=lambda r: r["name"])
    return scopes


def build_plugin_manifest() -> list[dict[str, Any]]:
    keys = load_curated(
        CURATED_PLUGIN_MANIFEST, {"key", "mandatory", "source"})
    keys.sort(key=lambda r: r["key"])
    return keys


# ---------------------------------------------------------------------------
# top level
# ---------------------------------------------------------------------------

def git_revision() -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def build_artifact() -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "movianRevision": git_revision(),
        "generatedBy": GENERATED_BY,
        "glw": {
            "functions": build_functions(),
            "attributes": build_attributes(),
            "widgets": build_widgets(),
            "operators": build_operators(),
            "scopes": build_scopes(),
        },
        "js": {
            "globals": build_globals(),
            "legacyGlobals": build_legacy_globals(),
            "modules": build_modules(),
            "pluginManifest": build_plugin_manifest(),
        },
    }


def dumps(artifact: dict[str, Any]) -> str:
    return json.dumps(artifact, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n"


# ---------------------------------------------------------------------------
# movian-api.d.ts -- TypeScript declarations derived from js.* metadata
# ---------------------------------------------------------------------------

def render_dts(artifact: dict[str, Any]) -> str:
    """Render a .d.ts file from js.modules data."""
    modules = artifact.get("js", {}).get("modules", [])
    globals_record = artifact.get("js", {}).get("globals", {})
    rev = artifact.get("movianRevision", "unknown")
    lines: list[str] = []
    lines.append("// Generated by %s -- do not edit." % GENERATED_BY)
    lines.append("// movianRevision: %s" % rev)
    lines.append("// Duktape ES5.1 -- no ES6+ in plugin code.")
    # The globals below collide with lib.dom, which tsc includes by default:
    # lib.dom declares both `console` and a DOM `Plugin`, so a plugin author
    # running plain `tsc` gets two TS2451 "cannot redeclare" errors on a
    # correct bundle. Reproduced on this tree. The gate compiles with
    # `--lib ES2015` and so never saw it; saying so in the file is what
    # reaches the author, who has no reason to read the checker.
    lines.append("// Compile with --lib ES2015 (or any lib set WITHOUT dom):")
    lines.append("// lib.dom declares `console` and `Plugin` too, and the")
    lines.append("// collision reports as TS2451 against this file.")
    lines.append("")

    # A native that reads an argument through es_get_native_obj() or
    # es_resource_get() is being handed a wrapped C pointer, not a value: the
    # class it names at the call site is the type. These have no JavaScript
    # shape to describe, so each is declared as a distinct empty brand -- no
    # plugin can build one, which is correct, and passing a string or the
    # wrong kind of handle stops type-checking, which is the point. The brand
    # member is `declare`-only and never exists at runtime.
    lines.extend(NATIVE_BUFFER_DECLARATION)
    lines.append("")

    handles = native_handle_types(modules)
    if handles:
        lines.append("// Wrapped C pointers. Obtained from a native call,"
                     " never constructed.")
        for type_name, native_class in handles:
            lines.append("interface %s { readonly __nativeClass: '%s'; }"
                         % (type_name, native_class))
        lines.append("")

    # The C tables give a name and an nargs, never parameter names, so every
    # global function is emitted variadic. `@arity` above it is the nargs as
    # documentation -- tsc cannot enforce it through a rest parameter, and
    # probing confirms `setTimeout()` and `Core.sleep(1, 2, 3)` both compile.
    GLOBAL_SIGNATURE = "...args: any[]"

    global_functions = globals_record.get("functions", [])
    global_objects = globals_record.get("objects", [])
    if global_functions or global_objects:
        lines.append("// Globals on the global object -- not modules, so no")
        lines.append("// require(). Most are installed by")
        lines.append("// src/ecmascript/ecmascript.c; `print` and `require`")
        lines.append("// are Duktape builtins (see")
        lines.append("// support/devtools/metadata/"
                     "curated_interpreter_globals.json).")
        lines.append("")
        for function in global_functions:
            lines.append("/** @arity %s */" % function["nargs"])
            lines.append("declare function %s(%s): any;"
                         % (function["name"], GLOBAL_SIGNATURE))
        if global_functions:
            lines.append("")
        for record in global_objects:
            lines.append("declare const %s: {" % record["name"])
            for function in record["functions"]:
                lines.append("  /** @arity %s */" % function["nargs"])
                lines.append("  %s(%s): any;"
                             % (function["name"], GLOBAL_SIGNATURE))
            for prop in record["properties"]:
                lines.append("  %s%s: %s;" % (
                    prop["name"],
                    "?" if prop.get("optional") else "",
                    prop["kind"]))
            if record.get("indexSignature"):
                lines.append("  [key: string]: %s;"
                             % record["indexSignature"])
            lines.append("};")
            lines.append("")

    def native_signature(func: dict[str, Any]) -> str:
        """The parameter list for a native ES_MODULE export.

        `duk_function_list_entry` gives a name and `nargs` and nothing else, so
        the parameters are positional and untyped -- but `nargs` is a real
        fact and was being thrown away. Every native function used to be
        emitted `(...args: any[])`, which accepts any call at all, while the
        `@arity` comment beside it said otherwise: `fs.basename('a','b','c')`
        type-checked against an arity of 1 (movian#207).

        Optional parameters bound the call from above only. Passing fewer
        arguments than `nargs` stays legal, which matches both the runtime --
        Duktape pads the missing ones with `undefined` -- and how the CommonJS
        exports in this same file are already emitted.

        A variadic native (`nargs == -1`, `DUK_VARARGS`) keeps the rest
        parameter, because for those the runtime really does accept anything.
        """
        if func.get("variadic"):
            return "...args: any[]"
        nargs = func["nargs"]
        if nargs <= 0:
            return ""
        params = func.get("params") or []
        rendered = []
        for index in range(nargs):
            param = params[index] if index < len(params) else {}
            if "shapeName" in param:
                spelling = " | ".join([param["shapeName"],
                                       *param.get("shapeUnion", [])])
            else:
                spelling = param.get("type", "any")
            rendered.append("%s?: %s" % (param.get("name", "arg%d" % index),
                                         spelling))
        return ", ".join(rendered)

    def params_signature(
            params: list[str] | None, export: dict[str, Any] | None = None,
            shape_names: set[str] | None = None
    ) -> str:
        if params is None:
            return "...args: any[]"
        parts = []
        for name in params:
            annotation = "any"
            if export is not None and name == export.get("callbackParam"):
                callback_shape = export.get("callbackShape")
                if callback_shape and shape_names and \
                        callback_shape in shape_names:
                    # The shape does not necessarily arrive first. `movian/
                    # http` calls `callback(null, new HttpResponse(res))`, so
                    # typing argument 0 as the response made an error-first
                    # callback look like a response-first one. The positions
                    # ahead of it are named `argN` rather than `err`: their
                    # position is measured from the call site, their meaning
                    # is not, and TypeScript requires some name.
                    index = export.get("callbackShapeIndex", 0)
                    shape_type = callback_shape
                    if export.get("callbackShapeNullable"):
                        shape_type += " | null"
                    callback_params = [
                        "arg%d: any" % position for position in range(index)
                    ] + ["value: %s" % shape_type, "...args: any[]"]
                    annotation = (
                        "(%s) => any" % ", ".join(callback_params))
            parts.append("%s?: %s" % (name, annotation))
        return ", ".join(parts)

    def member_signature(
            member: dict[str, Any],
            export: dict[str, Any] | None = None,
            shape_names: set[str] | None = None
    ) -> tuple[str | None, str]:
        """`@arity` text and parameter list for one callable member.

        Shared by every emission site -- module exports, prototype methods,
        shared-object methods and receiver members -- so the variadic rule
        cannot hold in one of them and not the others. Returns `None` for the
        arity when the parameter list did not parse, which is the caller's
        signal to omit the annotation.
        """
        params = member.get("params")
        variadic = member.get("variadic", False)
        signature = params_signature(params, export, shape_names)
        if variadic:
            # `params_signature(None)` is already `...args: any[]`, and
            # `variadic` is only recorded when the formal list parsed, so the
            # rest parameter is never appended twice.
            signature = ", ".join([signature, "...args: any[]"]) \
                if signature else "...args: any[]"
        if params is None:
            return None, signature
        return ("%d+" % len(params) if variadic else str(len(params)),
                signature)

    def render_return_type(
            returned: Any, shape_names: set[str]) -> str:
        if isinstance(returned, str):
            return returned if returned in shape_names else "any"
        if isinstance(returned, dict) and returned.get("kind") == "array":
            element = returned.get("element")
            # Same rule as every other shape reference here: a name the
            # emitted file does not declare becomes `any`, never a dangling
            # `Foo[]` that makes the artifact itself fail to compile.
            if isinstance(element, str) and element in shape_names:
                return "%s[]" % element
            return "any"
        if not isinstance(returned, dict) or \
                returned.get("kind") != "object":
            return "any"
        fields = returned.get("fields")
        if not isinstance(fields, list) or not fields:
            return "any"
        rendered: list[str] = []
        for field in fields:
            if not isinstance(field, dict):
                return "any"
            field_name = field.get("name")
            field_type = field.get("type")
            if not isinstance(field_name, str):
                return "any"
            if not isinstance(field_type, str) or field_type not in shape_names:
                field_type = "any"
            rendered.append("%s: %s;" % (field_name, field_type))
        return "{ %s }" % " ".join(rendered)


    for mod in modules:
        name = mod["name"]
        kind = mod.get("kind", "unknown")

        # Module path for declare-module:
        #   native/* -> 'native/*'
        #   movian/* -> 'movian/*'
        #   bare (fs, http, ...) -> quoted module name
        lines.append("declare module '%s' {" % name)

        if kind == "native":
            funcs = mod.get("functions", [])
            if not funcs:
                lines.append("}")
                lines.append("")
                continue
            shapes = native_options_shapes(mod)
            if shapes:
                lines.append("  // Options objects, keyed by what the C reads."
                             " The index signature is")
                lines.append("  // deliberate: the native ignores keys it does"
                             " not know, so an extra one")
                lines.append("  // is not an error at runtime and must not"
                             " become one here.")
                for shape_name, members, _union in shapes:
                    lines.append("  interface %s {" % shape_name)
                    for member, kind in members.items():
                        lines.append("    %s?: %s;" % (member, kind))
                    lines.append("    %s" % NATIVE_OPTIONS_INDEX_SIGNATURE)
                    lines.append("  }")
            lines.append("  // native ES_MODULE exports")
            for func in funcs:
                fname = func["name"]
                nargs = func["nargs"]
                lines.append("  /** @arity %s */" % nargs)
                lines.append(
                    "  function %s(%s): %s;"
                    % (fname, native_signature(func),
                       func.get("returns", "any")))
        elif kind == "commonjs":
            exports = mod.get("exports", [])
            shapes = mod.get("shapes", [])
            receiver_members = mod.get("receiverMembers", [])
            if (not exports and not mod.get("inherits") and not shapes
                    and not receiver_members):
                lines.append("}")
                lines.append("")
                continue
            inherits = mod.get("inherits")
            if inherits:
                lines.append("  // exports.__proto__ = require('%s') --"
                             " inherits its whole surface" % inherits)
                lines.append("  export * from '%s';" % inherits)
            # An ambient module block auto-exports its declarations only while
            # it carries no explicit export; the `export *` above flips that,
            # and unmarked locals stop being visible to importers. They also
            # have to be marked to shadow an inherited name of the same
            # spelling -- `movian/prop` sets `exports.global` to a proxied
            # object over `native/prop`'s `global` function, and the own
            # property is what a plugin gets at runtime.
            decl = "export " if inherits else ""

            prototype_shapes = [
                shape for shape in shapes
                if shape.get("kind") == "prototype"
            ]
            shared_shapes = [
                shape for shape in shapes
                if shape.get("kind") == "shared"
            ]
            # Defined before the first use below, not after it. The prototype
            # emission needs it to render a return type, and it used to be
            # assigned further down -- which would have read the PREVIOUS
            # module's set, making the output depend on module order.
            shape_names = {shape["name"] for shape in prototype_shapes}
            if prototype_shapes:
                lines.append("  // CommonJS prototype shapes")
                for shape in prototype_shapes:
                    lines.append("  interface %s {" % shape["name"])
                    for method in shape["methods"]:
                        arity, signature = member_signature(method)
                        if arity is not None:
                            lines.append("    /** @arity %s */" % arity)
                        # A method that plainly returns a shape says so.
                        # Emitting `any` left every member of the returned
                        # object unchecked: `Item.onSelect` shipped in two
                        # examples and was never called, because
                        # `appendItem(...)` was `any` and any assignment onto
                        # it type-checked (#177).
                        lines.append(
                            "    %s(%s): %s;" %
                            (method["name"], signature,
                             render_return_type(
                                 method.get("returns", "any"), shape_names)))
                    for prop in shape.get("properties", []):
                        # A plugin-supplied hook the module only guards is
                        # optional: requiring it would produce errors the
                        # runtime does not have, since nothing forces a plugin
                        # to set it.
                        lines.append("    %s%s: any;" % (
                            prop["name"],
                            "?" if prop.get("optional") else ""))
                    lines.append("  }")
                lines.append("")

            if shared_shapes:
                lines.append(
                    "  // CommonJS receiver-mutated shared object shapes")
                for shape in shared_shapes:
                    for method in shape["methods"]:
                        arity, signature = member_signature(method)
                        if arity is not None:
                            lines.append("  /** @arity %s */" % arity)
                        lines.append(
                            "  function %s(%s): any;" %
                            (method["name"], signature))
                    for prop in shape.get("properties", []):
                        lines.append("  var %s: any;" % prop["name"])
                lines.append("")
                # The hoisted members above describe a PLAIN call, which
                # mutates the module receiver -- and that is what the shipped
                # plugins do: movian-plugin-HDRezka/service.js:83,
                # movian-plugin-trakt/trakt.js:51 and
                # m7-jellyfin/src/settings.js:27 all call globalSettings
                # plainly and then use settings.createString/createBool on the
                # module. The in-repo callers construct instead
                # (res/ecmascript/legacy/api-v1.js:140,
                # res/ecmascript/modules/movian/page.js:197), and
                # `this.__proto__ = sp` serves both, so the shared surface has
                # to be reachable as an instance type as well.
                #
                # One interface PER INITIALIZER, not one shared: globalSettings
                # assigns id and properties, kvstoreSettings does not, so a
                # single merged type let `kvstoreSettings(...).properties`
                # compile and then read undefined. Named after the export, the
                # way the hand-written canon does it -- a value and a type of
                # the same name occupy different declaration spaces.
                for shape in shared_shapes:
                    lines.append("  interface %s {" % shape["name"])
                    for method in shape["methods"]:
                        arity, signature = member_signature(method)
                        if arity is not None:
                            lines.append("    /** @arity %s */" % arity)
                        # A method that plainly returns a shape says so.
                        # Emitting `any` left every member of the returned
                        # object unchecked: `Item.onSelect` shipped in two
                        # examples and was never called, because
                        # `appendItem(...)` was `any` and any assignment onto
                        # it type-checked (#177).
                        lines.append(
                            "    %s(%s): %s;" %
                            (method["name"], signature,
                             render_return_type(
                                 method.get("returns", "any"), shape_names)))
                    for prop in shape.get("properties", []):
                        lines.append("    %s%s: any;" % (
                            prop["name"],
                            "?" if prop.get("optional") else ""))
                    lines.append("  }")
                for export in exports:
                    if not export.get("receiverMutation"):
                        continue
                    own = export.get("receiverMembers", [])
                    bases = " extends %s" % ", ".join(
                        shape["name"] for shape in shared_shapes)
                    lines.append("  interface %s%s {" % (export["name"], bases))
                    for member in own:
                        if member["kind"] == "function":
                            arity, signature = member_signature(member)
                            if arity is not None:
                                lines.append("    /** @arity %s */" % arity)
                            lines.append(
                                "    %s(%s): any;" %
                                (member["name"], signature))
                        else:
                            lines.append("    %s: any;" % member["name"])
                    lines.append("  }")
                lines.append("")
            if receiver_members:
                lines.append(
                    "  // CommonJS receiver-mutated module exports")
                for member in receiver_members:
                    if member["kind"] == "function":
                        arity, signature = member_signature(member)
                        if arity is not None:
                            lines.append("  /** @arity %s */" % arity)
                        lines.append(
                            "  function %s(%s): any;" %
                            (member["name"], signature))
                    else:
                        lines.append("  var %s: any;" % member["name"])
                lines.append("")

            if exports:
                lines.append("  // CommonJS exports")
            # The instance type a receiver-mutating initializer produces.
            # Only unambiguous with exactly one shared shape in the module;
            # with none or several there is nothing to name, and the
            # construct signature falls back to `any` rather than guessing.
            # Each initializer now has its own instance interface, named
            # after the export, so the construct result is that -- not the
            # shared base every initializer happens to share.
            receiver_shapes = {
                export["name"] for export in exports
                if export.get("receiverMutation")
            } if shared_shapes else set()
            for exp in exports:
                ename = exp["name"]
                params = exp.get("params")
                # Parameters are emitted OPTIONAL on purpose. The names are
                # source-derived fact and give editors signature help; the
                # count is not a contract -- Duktape enforces no arity, and
                # Movian's own modules are routinely called with fewer
                # arguments than they declare. Requiring them would generate
                # errors the runtime does not have. The honest count stays in
                # @arity.
                arity, sig = member_signature(exp, exp, shape_names)
                if exp.get("receiverMutation"):
                    # `this.__proto__ = sp` does not choose between the two
                    # call forms: constructed, `this` is the new instance;
                    # called plainly, it is the module receiver (which is why
                    # the members are hoisted above). Emitting only the plain
                    # form cost `new settings.globalSettings(...)` its
                    # construct signature and produced TS7009 for both of the
                    # in-repo callers, so both signatures are declared. The
                    # plain form returns `void`: the initializers end by
                    # assigning to `this` and never return a value.
                    if arity is not None:
                        lines.append("  /** @arity %s */" % arity)
                    lines.append("  %sconst %s: {" % (decl, ename))
                    lines.append("    new (%s): %s;"
                                 % (sig, ename if ename in receiver_shapes
                                    else "any"))
                    lines.append("    (%s): void;" % sig)
                    lines.append("  };")
                elif exp.get("constructor"):
                    if arity is not None:
                        lines.append("  /** @arity %s */" % arity)
                    result_type = ename if ename in shape_names else "any"
                    lines.append("  %sconst %s: {" % (decl, ename))
                    lines.append("    new (%s): %s;" %
                                 (sig, result_type))
                    lines.append("  };")
                elif params is not None:
                    lines.append("  /** @arity %s */" % arity)
                    return_type = render_return_type(
                        exp.get("returns", "any"), shape_names)
                    void_when = exp.get("voidWhen")
                    if void_when is not None and void_when in params:
                        # Two forms, split at the callback parameter: without
                        # it the function returns its value, with it it
                        # dispatches and returns nothing. The synchronous
                        # overload is emitted first so it wins for calls that
                        # supply neither form's optional tail.
                        head = params[:params.index(void_when)]
                        head_member = dict(exp)
                        head_member["params"] = head
                        _, head_sig = member_signature(
                            head_member, exp, shape_names)
                        lines.append("  %sfunction %s(%s): %s;" %
                                     (decl, ename, head_sig, return_type))
                        lines.append("  %sfunction %s(%s): void;" %
                                     (decl, ename, sig))
                    else:
                        lines.append("  %sfunction %s(%s): %s;" %
                                     (decl, ename, sig, return_type))
                else:
                    lines.append("  %sconst %s: any;" % (decl, ename))

        lines.append("}")
        lines.append("")

    # `require('showtime/x')` is rewritten to `movian/x` by the loader
    # (src/ecmascript/ecmascript.c, mystrbegins(id, "showtime/")), so the
    # legacy names resolve at runtime. Without them here every legacy-style
    # plugin gets a false "Cannot find module" from tsc.
    aliases = sorted(mod["name"] for mod in modules
                     if mod["name"].startswith("movian/"))
    if aliases:
        lines.append("// Legacy aliases: the loader rewrites showtime/* to"
                     " movian/* at require time.")
        lines.append("")
        for name in aliases:
            legacy = "showtime/" + name.split("/", 1)[1]
            lines.append("declare module '%s' {" % legacy)
            lines.append("  export * from '%s';" % name)
            lines.append("}")
            lines.append("")

    return "\n".join(lines)


def _strip_revision(artifact: dict[str, Any]) -> dict[str, Any]:
    """A copy of `artifact` with movianRevision normalized out -- the one
    field that legitimately differs between two regenerations run on
    different commits (see module docstring)."""
    clone = dict(artifact)
    clone["movianRevision"] = None
    return clone


def _strip_dts_revision(text: str) -> str:
    """Normalize the generated revision just like ``_strip_revision()``."""
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith("// movianRevision: "):
            lines[index] = "// movianRevision: <normalized>\n"
            break
    return "".join(lines)


def cmd_generate(_args: argparse.Namespace) -> int:
    artifact = build_artifact()
    ARTIFACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT_PATH.write_text(dumps(artifact), encoding="utf-8")
    print("wrote %s" % rel(ARTIFACT_PATH))
    dts_text = render_dts(artifact)
    DTS_PATH.write_text(dts_text, encoding="utf-8")
    print("wrote %s" % rel(DTS_PATH))
    v1_text = render_v1_dts(artifact)
    V1_DTS_PATH.write_text(v1_text, encoding="utf-8")
    print("wrote %s" % rel(V1_DTS_PATH))
    return 0


def _run_reference_dts_check(
        extra_args: tuple[str, ...] = ()) -> tuple[bool, str]:
    """Run the source/fixture oracle without adding TypeScript as a build dep."""
    try:
        result = subprocess.run(
            [sys.executable, str(REFERENCE_DTS_CHECKER), *extra_args],
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            # The checker bounds its own tsc subprocesses, but this call
            # still needs its own ceiling: a hang anywhere in that chain
            # must not wedge `gen.py --check` (and mdevlib/lspdoctor.py's
            # own outer timeout) indefinitely.
            timeout=120,
        )
    except OSError as error:
        return False, "reference-dts: checker could not run: %s" % error
    except subprocess.TimeoutExpired as error:
        return False, "reference-dts: checker timed out: %s" % error
    return result.returncode == 0, result.stdout.rstrip()


def cmd_check(args: argparse.Namespace) -> int:
    fresh = build_artifact()
    if not ARTIFACT_PATH.is_file():
        print("mdev-metadata: committed artifact not found: %s"
              % ARTIFACT_PATH, file=sys.stderr)
        return 1
    committed = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    try:
        runtime_oracle = json.loads(
            RUNTIME_ORACLE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        runtime_oracle_report = {
            "status": "failed",
            "match": 0,
            "drift": 0,
            "oracleUnreachable": 0,
            "error": "could not load %s: %s" % (RUNTIME_ORACLE_PATH, error),
        }
        runtime_oracle_ok = False
        runtime_oracle_output = _format_runtime_oracle_report(
            runtime_oracle_report)
    else:
        runtime_oracle_ok, runtime_oracle_output, runtime_oracle_report = (
            _check_runtime_oracle(committed, runtime_oracle))

    fresh_norm = _strip_revision(fresh)
    committed_norm = _strip_revision(committed)

    metadata_ok = (fresh_norm == committed_norm)

    fresh_dts = render_dts(fresh)
    dts_ok = False
    if DTS_PATH.is_file():
        committed_dts = DTS_PATH.read_text(encoding="utf-8")
        dts_ok = (_strip_dts_revision(fresh_dts)
                  == _strip_dts_revision(committed_dts))
    # The v1 file is written by the same run and must be checked by it. A
    # generated artifact that no gate compares is one nobody notices going
    # stale -- the whole reason the main bundle grew fixtures.
    fresh_v1_dts = render_v1_dts(fresh)
    v1_dts_ok = False
    if V1_DTS_PATH.is_file():
        committed_v1 = V1_DTS_PATH.read_text(encoding="utf-8")
        v1_dts_ok = (_strip_dts_revision(fresh_v1_dts)
                     == _strip_dts_revision(committed_v1))
    dts_ok = dts_ok and v1_dts_ok
    reference_dts_ok, reference_dts_output = _run_reference_dts_check()
    # The default invocation carries the source-shape, member and alias
    # enforcement; `--commonjs` is the separate inventory audit. Running only
    # the first left the completeness guarantee outside every automated gate,
    # so a CommonJS module added to the metadata artifact without a fixture, or
    # a fixture deleted, failed nothing until somebody typed the flag by hand.
    coverage_ok, coverage_output = _run_reference_dts_check(("--commonjs",))
    shape_coverage_ok, shape_coverage_output = (
        _check_commonjs_shape_coverage(committed))
    reference_dts_ok = reference_dts_ok and coverage_ok
    if coverage_output:
        reference_dts_output = "\n".join(
            part for part in (reference_dts_output, coverage_output) if part)

    if (metadata_ok and dts_ok and reference_dts_ok
            and shape_coverage_ok and runtime_oracle_ok):
        if args.json:
            print(json.dumps({
                "metadata": "ok",
                "dts": "ok",
                "referenceDts": "ok",
                "shapeCoverage": "ok",
                "runtimeOracle": runtime_oracle_report["status"],
            }, indent=2))
        else:
            print("METADATA OK (movianRevision: committed=%s current=%s)"
                  % (committed.get("movianRevision"),
                     fresh.get("movianRevision")))
            print("DTS OK")
            print(shape_coverage_output)
            print(runtime_oracle_output)
            if reference_dts_output:
                print(reference_dts_output)
        return 0

    diff = None
    if not metadata_ok:
        diff = diff_artifacts(committed_norm, fresh_norm)

    if args.json:
        result = {
            "metadata": "ok" if metadata_ok else "drift",
            "dts": "ok" if dts_ok else "drift",
            "referenceDts": "ok" if reference_dts_ok else "failed",
            "shapeCoverage": "ok" if shape_coverage_ok else "failed",
            "runtimeOracle": runtime_oracle_report["status"],
        }
        if diff is not None:
            result["diff"] = diff
        if not reference_dts_ok and reference_dts_output:
            result["referenceDtsOutput"] = reference_dts_output
        if not shape_coverage_ok:
            result["shapeCoverageOutput"] = shape_coverage_output
        if not runtime_oracle_ok:
            result["runtimeOracleOutput"] = runtime_oracle_report
        print(json.dumps(result, ensure_ascii=False, indent=2,
                         sort_keys=True))
    else:
        if not metadata_ok:
            print("METADATA DRIFT")
            for line in format_diff(diff or {}):
                print(line)
        if not dts_ok:
            print("DTS DRIFT")
        if not runtime_oracle_ok:
            print(runtime_oracle_output)
        if not shape_coverage_ok:
            print(shape_coverage_output)
        if not reference_dts_ok:
            print(reference_dts_output or "reference-dts: checker failed")
    return 1


def _without_line(record: dict[str, Any]) -> dict[str, Any]:
    """`record` with `source.line` blanked out to classify semantic changes
    separately from line-only stale anchors (which are reported as lineOnly)."""
    clone = dict(record)
    src = dict(clone.get("source") or {})
    src["line"] = None
    clone["source"] = src
    return clone


def diff_artifacts(committed: dict[str, Any],
                    fresh: dict[str, Any]) -> dict[str, Any]:
    """Section-by-section (by name/symbols key) added/removed/changed
    report for every glw.* list, so drift is falsifiable and legible in
    both directions (added-since-commit vs removed-since-commit)."""
    result: dict[str, Any] = {}
    for section, key in (("functions", "name"), ("attributes", "name"),
                          ("widgets", "name")):
        c = {r[key]: r for r in committed["glw"][section]}
        f = {r[key]: r for r in fresh["glw"][section]}
        added = sorted(set(f) - set(c))
        removed = sorted(set(c) - set(f))
        changed = sorted(
            n for n in (set(f) & set(c))
            if _without_line(f[n]) != _without_line(c[n])
        )
        line_only = sorted(
            n for n in (set(f) & set(c))
            if f[n] != c[n] and _without_line(f[n]) == _without_line(c[n])
        )
        if added or removed or changed or line_only:
            result[section] = {"added": added, "removed": removed,
                                "changed": changed, "lineOnly": line_only}
    committed_js = committed.get("js") or {}
    fresh_js = fresh.get("js") or {}
    committed_modules = {
        r["name"]: r for r in committed_js.get("modules", [])
    }
    fresh_modules = {r["name"]: r for r in fresh_js.get("modules", [])}
    added = sorted(set(fresh_modules) - set(committed_modules))
    removed = sorted(set(committed_modules) - set(fresh_modules))
    changed = sorted(
        name for name in set(fresh_modules) & set(committed_modules)
        if fresh_modules[name] != committed_modules[name]
    )
    if added or removed or changed:
        result["js.modules"] = {
            "added": added,
            "removed": removed,
            "changed": changed,
            "lineOnly": [],
        }
    if (committed_js.get("pluginManifest")
            != fresh_js.get("pluginManifest")):
        result["js.pluginManifest"] = {
            "note": "curated section changed on disk vs committed artifact"
        }
    for section in ("operators", "scopes"):
        if committed["glw"][section] != fresh["glw"][section]:
            result[section] = {"note": "curated section changed on disk "
                                        "vs committed artifact"}
    return result


def format_diff(diff: dict[str, Any]) -> list[str]:
    lines = []
    for section, d in diff.items():
        if ("added" in d or "removed" in d or "changed" in d
                or "lineOnly" in d):
            for name in d.get("added", []):
                lines.append("added (%s): %s" % (section, name))
            for name in d.get("removed", []):
                lines.append("removed (%s): %s" % (section, name))
            for name in d.get("changed", []):
                lines.append("changed (%s): %s" % (section, name))
            for name in d.get("lineOnly", []):
                lines.append("line-moved (%s): %s" % (section, name))
        else:
            lines.append("changed (%s)" % section)
    return lines


# Which .c files the build actually compiles is a fact about the RECIPE, and
# the recipe is not in the sources. Dropping `es_fs.c` from SRCS, or moving it
# behind a different CONFIG gate, changes what the binary exposes while every
# .c and .h stays byte-identical.
#
# The key is the SELECTION, not the file. Hashing `Makefile` would be the "key
# over too much" trap in its worst form -- one file every part of the project
# touches. Measured before choosing: 18 commits changed `Makefile` since
# 2026-05-01 and NONE of them touched a line naming an ecmascript source. The
# selection is stable while the file around it moves.
_MAKEFILE_SRCS_RE = re.compile(r"^\s*(SRCS(?:-[^\s+=]+)?)\s*\+?=")
_MAKEFILE_ES_SOURCE_RE = re.compile(r"src/ecmascript/[A-Za-z0-9_./-]+\.c")
# `SRCS-$(CONFIG_X) +=` is one way to make a source conditional. A bare
# `SRCS +=` inside `ifeq (...) ... endif` is another, and the recipe already
# uses it (Makefile:283-285). Reading only the variable name makes those two
# forms indistinguishable, so a source moved from unconditional into an
# `ifeq` block would compile in one configuration and not another with every
# compared value unchanged.
_MAKEFILE_COND_RE = re.compile(
    r"^\s*(ifeq|ifneq|ifdef|ifndef)\b\s*(.*)$")
_MAKEFILE_ELSE_RE = re.compile(r"^\s*else\b\s*(.*)$")
_MAKEFILE_ENDIF_RE = re.compile(r"^\s*endif\b")
# A commented-out continuation line still contains the pathname, and reading
# it keeps a source in the selection that the next build will not compile.
# Over-stripping is the safe direction: a path wrongly dropped leaves the
# file on disk with nothing naming it, which `_selection_problems` reports.
_MAKEFILE_COMMENT_RE = re.compile(r"(?<!\\)#.*$")


def _makefile_uncommented(line: str) -> str:
    return _MAKEFILE_COMMENT_RE.sub("", line)


def makefile_ecmascript_selection(text: str) -> dict[str, str]:
    """Each ecmascript source the recipe names, mapped to the variable that
    names it -- `SRCS` or `SRCS-$(CONFIG_X)`, so a source moving behind a
    different toggle reads as a change."""
    selection: dict[str, str] = {}
    gate: str | None = None
    # Each level is (the `if` that opened it, the branch now in effect). The
    # opener is kept because `else ifeq ($(B),yes)` only takes effect when
    # the OUTER predicate was false -- dropping it makes two recipes with
    # different outer conditions read identically.
    conditions: list[tuple[str, str]] = []
    for raw in text.split("\n"):
        line = _makefile_uncommented(raw)
        opener = _MAKEFILE_COND_RE.match(line)
        if opener:
            conditions.append(
                ("%s %s" % (opener.group(1),
                            " ".join(opener.group(2).split())), ""))
        elif _MAKEFILE_ENDIF_RE.match(line):
            if conditions:
                conditions.pop()
        else:
            branch = _MAKEFILE_ELSE_RE.match(line)
            if branch and conditions:
                rest = " ".join(branch.group(1).split())
                conditions[-1] = (conditions[-1][0],
                                  rest if rest else "else")
        assignment = _MAKEFILE_SRCS_RE.match(line)
        if assignment:
            gate = assignment.group(1)
        for path in _MAKEFILE_ES_SOURCE_RE.findall(line):
            where = gate or "?"
            if conditions:
                where += " under " + " & ".join(
                    opener if not branch else "not(%s) %s" % (opener, branch)
                    for opener, branch in conditions)
            selection[path] = where
        if gate is not None and not line.rstrip().endswith("\\"):
            gate = None
    return selection


def selection_mismatch(here: dict[str, str], there: dict[str, str],
                       revision: str) -> list[str]:
    """How the recipe now differs from the recipe that built `revision`.

    The gate matters as much as the membership: a source moved from `SRCS` to
    `SRCS-$(CONFIG_X)` is compiled in one configuration and not another, and
    the file it names has not changed a byte.
    """
    reasons = []
    for name in sorted(set(there) - set(here)):
        reasons.append("%s was compiled into build %s and the recipe no "
                       "longer names it" % (name, revision))
    for name in sorted(set(here) - set(there)):
        reasons.append("%s is compiled now and build %s did not have it"
                       % (name, revision))
    for name in sorted(set(here) & set(there)):
        if here[name] != there[name]:
            reasons.append("%s moved from %s to %s since build %s"
                           % (name, there[name], here[name], revision))
    return reasons


def _selection_problems(selection: dict[str, str]) -> list[str]:
    """The extractor's own floor. A parser that stops matching returns an
    empty selection and would make this whole comparison vacuous -- green
    because it read nothing, which is the failure this file exists to close.
    """
    if not selection:
        return ["no ecmascript source is named in the Makefile, so the "
                "recipe parser has gone blind"]
    on_disk = {path.relative_to(REPO_ROOT).as_posix()
               for path in (REPO_ROOT / "src" / "ecmascript").rglob("*.c")}
    return ["%s exists and the recipe never names it" % name
            for name in sorted(on_disk - set(selection))]


def runtime_oracle_build_mismatch(version: Any) -> list[str]:
    """Reasons the build that produced a capture does not match this tree.

    Only the compiled inputs are compared. Everything else is read through
    `dataroot://` at run time, so an unchanged binary answers correctly for
    an edited module -- which is the ordinary case and must not cost a
    rebuild. A C edit is the opposite: the binary already in `build.debug`
    still reports the surface it was compiled with.
    """
    if not isinstance(version, str) or not version:
        return ["the capture does not say which build produced it"]
    match = re.search(r"g([0-9a-f]{5,40})$", version)
    if not match:
        return ["cannot read a commit out of the build version %r" % version]
    revision = match.group(1)
    resolved = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", revision + "^{commit}"],
        cwd=str(REPO_ROOT), capture_output=True, text=True)
    if resolved.returncode != 0:
        # `--verify` also fails when a short revision matches more than one
        # object, which is the right answer: an identity that names two
        # commits proves nothing about either.
        return ["build %s is not one commit in this repository" % revision]
    here = makefile_ecmascript_selection(
        (REPO_ROOT / "Makefile").read_text(encoding="utf-8"))
    reasons = _selection_problems(here)
    built = subprocess.run(["git", "show", "%s:Makefile" % revision],
                           cwd=str(REPO_ROOT), capture_output=True, text=True)
    if built.returncode != 0:
        reasons.append("build %s has no Makefile to compare" % revision)
    else:
        reasons += selection_mismatch(
            here, makefile_ecmascript_selection(built.stdout), revision)
    for pattern in RUNTIME_ORACLE_COMPILED_GLOBS:
        for path in sorted(REPO_ROOT.glob(pattern)):
            if not path.is_file():
                continue
            name = path.relative_to(REPO_ROOT).as_posix()
            shown = subprocess.run(
                ["git", "show", "%s:%s" % (revision, name)],
                cwd=str(REPO_ROOT), capture_output=True, text=True)
            if shown.returncode != 0:
                reasons.append("%s does not exist in build %s"
                               % (name, revision))
                continue
            if (_js_hash_text(shown.stdout)
                    != _js_hash_text(path.read_text(encoding="utf-8"))):
                reasons.append(
                    "%s differs from the copy build %s was compiled from"
                    % (name, revision))
    return reasons


def runtime_oracle_read_mismatch(recorded: Any, error: Any) -> list[str]:
    """Reasons the tree differs from the one the capture actually read.

    The `inputs` stamp is computed at adoption time, so on its own it binds
    the oracle to the tree it is committed with rather than to the tree it
    was captured from -- and those are routinely different checkouts, since
    the run happens where build.debug lives and the adoption happens in a
    worktree. This compares against what the run recorded reading.

    Raw bytes, not the comment-insensitive digest: capture and adoption are
    meant to be the same tree minutes apart, so there is nothing to be
    tolerant of, and tolerance here would only widen the gap.
    """
    if error:
        return ["the capture could not record what it read: %s" % error]
    if not isinstance(recorded, dict) or not recorded:
        return ["the capture does not record what it read"]

    def resolve(name: str) -> Path | None:
        if name.startswith("plugin/"):
            return INTROSPECTOR_DIR / name[len("plugin/"):]
        if name.startswith("res/"):
            return REPO_ROOT / name
        return None

    reasons = []
    seen: set[Path] = set()
    for name in sorted(recorded):
        path = resolve(name)
        if path is None:
            reasons.append("capture recorded an unplaceable input %r" % name)
            continue
        if not path.is_file():
            reasons.append("%s was read by the capture and is not here"
                           % name)
            continue
        seen.add(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != recorded[name]:
            reasons.append("%s differs from the copy the capture read"
                           % name)
    # The other direction: a file this tree has and the capture never saw is
    # a module the run could not have observed.
    for pattern in ("res/ecmascript/modules/**/*.js",
                    "support/devtools/api-introspector/**/*.js",
                    "support/devtools/api-introspector/**/*.json"):
        for path in sorted(REPO_ROOT.glob(pattern)):
            if _is_oracle_input(path) and path not in seen:
                reasons.append("%s is here and the capture never read it"
                               % path.relative_to(REPO_ROOT).as_posix())
    return reasons


def cmd_adopt_oracle(args: argparse.Namespace) -> int:
    """Stamp a fresh capture with the tree it was captured from and commit it
    as the oracle.

    Stamping is deliberately not a separate verb. A `--restamp` that blessed
    the file already on disk would be a one-word way to make a stale oracle
    green again, which is the whole defect this path exists to close, so the
    only way to move the stamp is to bring a new capture.
    """
    source = Path(args.adopt_oracle)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print("gen.py: could not read capture %s: %s" % (source, error),
              file=sys.stderr)
        return 1
    if not isinstance(payload, dict):
        print("gen.py: capture is not a JSON object", file=sys.stderr)
        return 1
    if payload.get("version") != RUNTIME_ORACLE_VERSION:
        print("gen.py: capture version %r, expected %d"
              % (payload.get("version"), RUNTIME_ORACLE_VERSION),
              file=sys.stderr)
        return 1
    if payload.get("tier3PageOpened") is False:
        print("gen.py: capture is the partial load-time payload; adopt the "
              "one printed after opening introspect:page", file=sys.stderr)
        return 1
    captured_at = payload.get("capturedAt")
    if not isinstance(captured_at, (int, float)):
        print("gen.py: capture carries no capturedAt; it predates the "
              "freshness work or is not a capture at all", file=sys.stderr)
        return 1
    # Freshness is a property of the RUN, not of what the run saw. An
    # implementation-only change to a module moves the stamp and moves no
    # member, so the honest recapture that follows it is byte-identical to
    # the committed oracle -- rejecting on content would leave that gate red
    # with no way to clear it. `capturedAt` separates the two cases: no two
    # runs share it, and a copy of the committed file cannot invent one.
    if RUNTIME_ORACLE_PATH.is_file():
        current = json.loads(
            RUNTIME_ORACLE_PATH.read_text(encoding="utf-8"))
        if current.get("capturedAt") == captured_at:
            print("gen.py: this capture is the committed oracle again -- "
                  "same capturedAt. Re-stamping it would certify old "
                  "observations against a tree they did not come from; run "
                  "the capture.", file=sys.stderr)
            return 1
    mismatch = runtime_oracle_build_mismatch(payload.get("movianVersion"))
    if mismatch:
        print("gen.py: the capture came from a build that does not match "
              "this tree, so stamping it would certify a reading nothing "
              "produced:", file=sys.stderr)
        for reason in mismatch:
            print("  %s" % reason, file=sys.stderr)
        print("  rebuild, recapture, and adopt that.", file=sys.stderr)
        return 1
    if payload.get("moduleDiscoveryError"):
        print("gen.py: the capture could not enumerate the module files, so "
              "it cannot say it saw them all: %s"
              % payload["moduleDiscoveryError"], file=sys.stderr)
        return 1
    unread = runtime_oracle_read_mismatch(
        payload.get("runtimeInputs"), payload.get("runtimeInputsError"))
    if unread:
        print("gen.py: the capture read a different tree than the one being "
              "stamped, so the stamp would describe sources it never saw:",
              file=sys.stderr)
        for reason in unread:
            print("  %s" % reason, file=sys.stderr)
        print("  recapture against this tree.", file=sys.stderr)
        return 1
    digests = runtime_oracle_input_digests()
    payload["inputs"] = {
        "version": RUNTIME_ORACLE_INPUTS_VERSION,
        "digest": runtime_oracle_inputs_digest(digests),
        "files": digests,
    }
    RUNTIME_ORACLE_PATH.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    print("adopted %s as %s (%d input files stamped)"
          % (source, RUNTIME_ORACLE_PATH.name, len(digests)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gen.py",
        description="Generate/check Movian metadata and API declarations.")
    parser.add_argument("--check", action="store_true",
                         help="diff regenerated content against the "
                              "committed artifacts (movianRevision "
                              "ignored); exit 1 on drift")
    parser.add_argument("--adopt-oracle", metavar="CAPTURE",
                         help="stamp a fresh introspector capture with the "
                              "sources it was taken from and install it as "
                              "the runtime oracle")
    parser.add_argument("--json", action="store_true",
                         help="machine-readable JSON output (--check only)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.adopt_oracle:
            return cmd_adopt_oracle(args)
        if args.check:
            return cmd_check(args)
        return cmd_generate(args)
    except GenError as error:
        print("gen.py: %s" % error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
