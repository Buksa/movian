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
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
METADATA_DIR = Path(__file__).resolve().parent
ARTIFACT_PATH = REPO_ROOT / "generated" / "movian-metadata.json"
DTS_PATH = REPO_ROOT / "generated" / "movian-api.d.ts"
REFERENCE_DTS_CHECKER = METADATA_DIR / "check_reference_dts.py"
RUNTIME_ORACLE_PATH = (
    REPO_ROOT / "support" / "devtools" / "api-introspector"
    / "runtime-api.json")
RUNTIME_ORACLE_VERSION = 2

ATTRIB_C = REPO_ROOT / "src" / "ui" / "glw" / "glw_view_attrib.c"
EVAL_C = REPO_ROOT / "src" / "ui" / "glw" / "glw_view_eval.c"
GLW_DIR = REPO_ROOT / "src" / "ui" / "glw"
ECMASCRIPT_DIR = REPO_ROOT / "src" / "ecmascript"
COMMONJS_DIR = REPO_ROOT / "res" / "ecmascript" / "modules"

CURATED_OPERATORS = METADATA_DIR / "curated_operators.json"
CURATED_SCOPES = METADATA_DIR / "curated_scopes.json"
CURATED_PLUGIN_MANIFEST = METADATA_DIR / "curated_plugin_manifest.json"

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
    return record

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
    state = "OK" if report.get("drift", 0) == 0 and not missing_modules \
        else "DRIFT"
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
    agreed = not drift and not missing_modules
    report = {
        "status": "ok" if agreed else "drift",
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
    rev = artifact.get("movianRevision", "unknown")
    lines: list[str] = []
    lines.append("// Generated by %s -- do not edit." % GENERATED_BY)
    lines.append("// movianRevision: %s" % rev)
    lines.append("// Duktape ES5.1 -- no ES6+ in plugin code.")
    lines.append("")

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
            lines.append("  // native ES_MODULE exports")
            for func in funcs:
                fname = func["name"]
                nargs = func["nargs"]
                lines.append("  /** @arity %s */" % nargs)
                lines.append(
                    "  function %s(...args: any[]): any;" % fname)
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
            if prototype_shapes:
                lines.append("  // CommonJS prototype shapes")
                for shape in prototype_shapes:
                    lines.append("  interface %s {" % shape["name"])
                    for method in shape["methods"]:
                        arity, signature = member_signature(method)
                        if arity is not None:
                            lines.append("    /** @arity %s */" % arity)
                        lines.append(
                            "    %s(%s): any;" %
                            (method["name"], signature))
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
                        lines.append(
                            "    %s(%s): any;" %
                            (method["name"], signature))
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
            shape_names = {
                shape["name"] for shape in prototype_shapes
            }
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gen.py",
        description="Generate/check Movian metadata and API declarations.")
    parser.add_argument("--check", action="store_true",
                         help="diff regenerated content against the "
                              "committed artifacts (movianRevision "
                              "ignored); exit 1 on drift")
    parser.add_argument("--json", action="store_true",
                         help="machine-readable JSON output (--check only)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.check:
            return cmd_check(args)
        return cmd_generate(args)
    except GenError as error:
        print("gen.py: %s" % error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
