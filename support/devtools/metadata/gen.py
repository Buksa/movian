#!/usr/bin/env python3
"""movian-metadata generator (issue #98).

Generates `generated/movian-metadata.json` (schema v1): the single
committed metadata artifact for the GLW view language, grown from
`support/devtools/mdevlib/viewdoc.py` (issue #88). Sections:

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
                     statically scanned CommonJS exports from
                     res/ecmascript/modules/**/*.js.
- js.pluginManifest -- curated plugin.json keys and mandatory status,
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


def _function_params(region: str) -> list[str] | None:
    """Parameter names of the function assigned at the head of `region`,
    or None when the export is not assigned a function literal."""
    match = COMMONJS_FUNCTION_RE.search(region)
    if match is None:
        return None
    raw = match.group(1).strip()
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


IDENT_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")


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
        region = "\n".join(
            _mask_js_strings(line)
            for line in masked_lines[line_index:next_line])
        record = {
            "name": export_name,
            "source": {"file": rel(path), "line": line_index + 1},
        }
        params = _function_params(region)
        if params is not None:
            record["params"] = params
            record["nargs"] = len(params)
        if re.search(r"\bthis\b", region):
            record["constructor"] = True
        exports.append(record)
    exports.sort(key=lambda r: r["name"])
    return exports


PROTO_EXPORT_RE = re.compile(
    r"^\s*(?:module\.)?exports\.__proto__\s*=\s*"
    r"([A-Za-z_$][A-Za-z0-9_$]*)\s*;", re.M)


def _proto_parent(path: Path) -> str | None:
    """`exports.__proto__ = np;` where `np = require('native/prop')` makes the
    module inherit that module's whole surface at load time. Static and
    resolvable, unlike the per-instance `this.__proto__ = sp` idiom inside
    constructors, which only a runtime probe can see."""
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
        record = {
            "name": module_name,
            "kind": "commonjs",
            "exports": exports,
            "source": {"file": rel(path), "line": 1},
        }
        parent = _proto_parent(path)
        if parent is not None:
            record["inherits"] = parent
        records.append(record)
    records.sort(key=lambda r: r["name"])
    return records


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




def _strip_revision(artifact: dict[str, Any]) -> dict[str, Any]:
    """A copy of `artifact` with movianRevision normalized out -- the one
    field that legitimately differs between two regenerations run on
    different commits (see module docstring)."""
    clone = dict(artifact)
    clone["movianRevision"] = None
    return clone




def cmd_generate(_args: argparse.Namespace) -> int:
    artifact = build_artifact()
    ARTIFACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT_PATH.write_text(dumps(artifact), encoding="utf-8")
    print("wrote %s" % rel(ARTIFACT_PATH))
    return 0

def cmd_check(args: argparse.Namespace) -> int:
    fresh = build_artifact()
    if not ARTIFACT_PATH.is_file():
        print("metadata: committed artifact not found: %s"
              % ARTIFACT_PATH, file=sys.stderr)
        return 1
    committed = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    fresh_norm = _strip_revision(fresh)
    committed_norm = _strip_revision(committed)
    if fresh_norm == committed_norm:
        if args.json:
            print(json.dumps({"metadata": "ok"}, indent=2))
        else:
            print("METADATA OK (movianRevision: committed=%s current=%s)"
                  % (committed.get("movianRevision"),
                     fresh.get("movianRevision")))
        return 0

    diff = diff_artifacts(committed_norm, fresh_norm)
    if args.json:
        print(json.dumps({"metadata": "drift", "diff": diff},
                         ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("METADATA DRIFT")
        for line in format_diff(diff):
            print(line)
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
        description="Generate/check source-derived Movian metadata.")
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
