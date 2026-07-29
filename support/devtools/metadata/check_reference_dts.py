#!/usr/bin/env python3
"""Check the accepted reference .d.ts calibration fixtures.

The parser is intentionally limited to the declaration/source forms used by
these calibration fixtures. It is not a JavaScript, C, or TypeScript parser.
Python standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
METADATA_DIR = Path(__file__).resolve().parent
TOPLEVEL_MODULE_DIR = REPO_ROOT / "res" / "ecmascript" / "modules"
METADATA_FILE = REPO_ROOT / "generated" / "movian-metadata.json"
REFERENCE_DIR = METADATA_DIR / "tests" / "reference"
FIXTURE_DIR = METADATA_DIR / "tests" / "fixtures"

# subprocess.run(timeout=...) only signals the direct child; a hung tsc
# grandchild is not reaped when this fires. Bounding it keeps a stuck
# compiler from wedging `gen.py --check` (and its own outer timeout,
# mdevlib/lspdoctor.py's `_check_metadata`) indefinitely.
SUBPROCESS_TIMEOUT_SECONDS = 60

VARARGS_NARGS = -1


@dataclass(frozen=True)
class Signature:
    required: int
    total: int


@dataclass(frozen=True)
class ModuleSpec:
    name: str
    declaration: Path
    javascript: Path
    prototypes: tuple[str, ...]
    # Native C function table this module's JS wraps, if any. Two shapes:
    # - "wrapped-exports": `exports.NAME` in the JS is expected to be a
    #   thin wrapper around a same-named native function (movian/prop's
    #   pattern) -- full name+arity cross-check.
    # - "native-calls": JS calls `require('native/X').NAME(...)` (directly
    #   or via a `var alias = require('native/X')` alias) -- existence
    #   cross-check only (a full call-site arity audit is out of scope for
    #   this narrow parser; see _native_call_names). `native_module` is
    #   the "X" in `native/X` -- required so a file that requires several
    #   different native/* modules (http.js also uses native/string,
    #   page.js also uses native/metadata and native/hook) doesn't have
    #   an unrelated module's calls attributed to this table.
    native_c: Path | None = None
    native_table: str | None = None
    native_module: str | None = None
    native_kind: str = "wrapped-exports"
    # A JS object used as the shared prototype rather than `Type.prototype`.
    # Entries are (declaration interface name, JavaScript object name).
    object_prototypes: tuple[tuple[str, str], ...] = ()
    # Types whose constructor-created public fields are exact in both
    # directions. The #135 types default to phantom-only because their
    # constructors also carry intentionally private bookkeeping fields.
    exact_member_types: tuple[str, ...] = ()
    # Exported anonymous constructor functions whose public `this.*` fields
    # are checked against their same-named declaration class. Each entry is
    # (export name, internal field names to exclude).
    export_instances: tuple[tuple[str, tuple[str, ...]], ...] = ()


JS_MODULE_DIR = REPO_ROOT / "res" / "ecmascript" / "modules" / "movian"
ECMASCRIPT_C_DIR = REPO_ROOT / "src" / "ecmascript"

MODULES = (
    ModuleSpec(
        "movian/page",
        REFERENCE_DIR / "movian-page.d.ts",
        JS_MODULE_DIR / "page.js",
        ("Item", "Page", "Route", "Searcher"),
        native_c=ECMASCRIPT_C_DIR / "es_route.c",
        native_table="fnlist_route",
        native_module="route",
        native_kind="native-calls",
    ),
    ModuleSpec(
        "movian/prop",
        REFERENCE_DIR / "movian-prop.d.ts",
        JS_MODULE_DIR / "prop.js",
        (),
        native_c=ECMASCRIPT_C_DIR / "es_prop.c",
        native_table="fnlist_prop",
        native_kind="wrapped-exports",
    ),
    ModuleSpec(
        "movian/http",
        REFERENCE_DIR / "movian-http.d.ts",
        JS_MODULE_DIR / "http.js",
        ("HttpResponse",),
        native_c=ECMASCRIPT_C_DIR / "es_io.c",
        native_table="fnlist_io",
        native_module="io",
        native_kind="native-calls",
    ),
    ModuleSpec(
        "movian/settings",
        REFERENCE_DIR / "movian-settings.d.ts",
        JS_MODULE_DIR / "settings.js",
        (),
        native_c=ECMASCRIPT_C_DIR / "es_kvstore.c",
        native_table="fnlist_kvstore",
        native_module="kvstore",
        native_kind="native-calls-exact",
        object_prototypes=(("SettingsMethods", "sp"),),
        export_instances=(
            ("globalSettings", ("getvalue", "setvalue")),
            ("kvstoreSettings", ("getvalue", "setvalue")),
        ),
    ),
    ModuleSpec(
        "movian/service",
        REFERENCE_DIR / "movian-service.d.ts",
        JS_MODULE_DIR / "service.js",
        ("Service",),
        native_c=ECMASCRIPT_C_DIR / "es_service.c",
        native_table="fnlist_service",
        native_module="service",
        native_kind="native-calls-exact",
        exact_member_types=("Service",),
    ),
    ModuleSpec(
        "movian/store",
        REFERENCE_DIR / "movian-store.d.ts",
        JS_MODULE_DIR / "store.js",
        (),
    ),
    ModuleSpec(
        "movian/html",
        REFERENCE_DIR / "movian-html.d.ts",
        JS_MODULE_DIR / "html.js",
        (),
        (),
    ),
    ModuleSpec(
        "movian/itemhook",
        REFERENCE_DIR / "movian-itemhook.d.ts",
        JS_MODULE_DIR / "itemhook.js",
        (),
    ),
    ModuleSpec(
        "movian/popup",
        REFERENCE_DIR / "movian-popup.d.ts",
        JS_MODULE_DIR / "popup.js",
        (),
    ),
    ModuleSpec(
        "movian/sqlite",
        REFERENCE_DIR / "movian-sqlite.d.ts",
        JS_MODULE_DIR / "sqlite.js",
        (),
        (),
    ),
    ModuleSpec(
        "movian/subtitles",
        REFERENCE_DIR / "movian-subtitles.d.ts",
        JS_MODULE_DIR / "subtitles.js",
        (),
    ),
    ModuleSpec(
        "movian/videoscrobbler",
        REFERENCE_DIR / "movian-videoscrobbler.d.ts",
        JS_MODULE_DIR / "videoscrobbler.js",
        ("VideoScrobbler",),
    ),
    ModuleSpec(
        "movian/xml",
        REFERENCE_DIR / "movian-xml.d.ts",
        JS_MODULE_DIR / "xml.js",
        (),
    ),
    ModuleSpec(
        "movian/xmlrpc",
        REFERENCE_DIR / "movian-xmlrpc.d.ts",
        JS_MODULE_DIR / "xmlrpc.js",
        (),
        (),
    ),
    ModuleSpec(
        "fs",
        REFERENCE_DIR / "fs.d.ts",
        TOPLEVEL_MODULE_DIR / "fs.js",
        (),
    ),
    ModuleSpec(
        "http",
        REFERENCE_DIR / "http.d.ts",
        TOPLEVEL_MODULE_DIR / "http.js",
        (),
    ),
    ModuleSpec(
        "https",
        REFERENCE_DIR / "https.d.ts",
        TOPLEVEL_MODULE_DIR / "https.js",
        (),
    ),
    ModuleSpec(
        "querystring",
        REFERENCE_DIR / "querystring.d.ts",
        TOPLEVEL_MODULE_DIR / "querystring.js",
        (),
    ),
    ModuleSpec(
        "url",
        REFERENCE_DIR / "url.d.ts",
        TOPLEVEL_MODULE_DIR / "url.js",
        (),
    ),
    ModuleSpec(
        "websocket",
        REFERENCE_DIR / "websocket.d.ts",
        TOPLEVEL_MODULE_DIR / "websocket.js",
        ("w3cwebsocket",),
    ),
)

POSITIVE_FIXTURE = FIXTURE_DIR / "reference-positive.ts"
NEGATIVE_FIXTURE = FIXTURE_DIR / "reference-negative.ts"
PLUGIN_DECLARATION = REFERENCE_DIR / "movian-plugin.d.ts"
PLUGIN_SOURCE = ECMASCRIPT_C_DIR / "ecmascript.c"

EXPORT_ASSIGN_RE = re.compile(
    r"^\s*exports\.([A-Za-z_$][A-Za-z0-9_$]*)\s*=", re.MULTILINE)
EXPORT_FUNCTION_HEAD_RE = re.compile(
    r"^\s*exports\.([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*function\s*\(",
    re.MULTILINE)
EXPORT_ALIAS_RE = re.compile(
    r"^\s*exports\.([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*"
    r"([A-Za-z_$][A-Za-z0-9_$]*)\s*;", re.MULTILINE)
LOCAL_FUNCTION_HEAD_RE = re.compile(
    r"^\s*function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(", re.MULTILINE)
PROTOTYPE_HEAD_RE = re.compile(
    r"^\s*(?:exports\.)?([A-Za-z_$][A-Za-z0-9_$]*)\.prototype\."
    r"([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*function\s*\(",
    re.MULTILINE)
OBJECT_METHOD_HEAD_RE = re.compile(
    r"^\s*([A-Za-z_$][A-Za-z0-9_$]*)\.([A-Za-z_$][A-Za-z0-9_$]*)"
    r"\s*=\s*function\s*\(", re.MULTILINE)
DECL_FUNCTION_RE = re.compile(
    r"^\s*export\s+function\s+([A-Za-z_$][A-Za-z0-9_$]*)"
    r"(?:\s*<[^;{]*?>)?\s*\(", re.MULTILINE)
DECL_VALUE_RE = re.compile(
    r"^\s*export\s+(?:const|var|let)\s+"
    r"([A-Za-z_$][A-Za-z0-9_$]*)\s*:", re.MULTILINE)
DECL_CLASS_RE = re.compile(
    r"^\s*(?:export\s+)?class\s+([A-Za-z_$][A-Za-z0-9_$]*)"
    r"(?:\s+(?:extends|implements)\s+[^{]+)?\s*\{", re.MULTILINE)
METHOD_RE = re.compile(
    r"^\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*\(", re.MULTILINE)
ACCESSOR_RE = re.compile(
    r"^\s*(get|set)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(", re.MULTILINE)
FIELD_RE = re.compile(
    r"^\s*(?:readonly\s+)?([A-Za-z_$][A-Za-z0-9_$]*)\??\s*:(?!\s*\()",
    re.MULTILINE)
NATIVE_ENTRY_RE = re.compile(
    r'\{\s*"([^"]+)"\s*,\s*[A-Za-z_]\w*\s*,\s*'
    r'(-?\d+|DUK_VARARGS)\s*\}')
DEFINE_PROPERTIES_HEAD_RE = re.compile(
    r"Object\.defineProperties\(\s*this\s*,\s*\{")
DEFINE_PROPERTY_ENTRY_RE = re.compile(
    r"^\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*:\s*\{", re.MULTILINE)
DEFINE_PROPERTY_GET_RE = re.compile(r"\bget\s*:\s*function\b")
JS_CONSTRUCTOR_HEAD_RE = re.compile(
    r"^\s*function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(", re.MULTILINE)
NESTED_FUNCTION_HEAD_RE = re.compile(r"\bfunction\b[^{]*\{")
THIS_FIELD_RE = re.compile(
    r"(?<![.\w])this\.([A-Za-z_$][A-Za-z0-9_$]*)\s*=(?!=)")
DIAGNOSTIC_RE = re.compile(
    r"^(.*?)\((\d+),(\d+)\): error TS(\d+):", re.MULTILINE)
EXPECTED_DIAGNOSTIC_RE = re.compile(r"EXPECT_TS(\d+)")
PLUGIN_ASSIGN_RE = re.compile(
    r'duk_put_prop_string\(\s*ctx\s*,\s*plugin_obj_idx\s*,\s*"([^"]+)"'
    r"\s*\)")
PLUGIN_GLOBAL_DECL_RE = re.compile(
    r"^\s*declare\s+var\s+Plugin\s*:\s*MovianPluginGlobal\s*;",
    re.MULTILINE)


def _read(path: Path) -> str:
    if not path.is_file():
        raise ValueError("required file is missing: %s" % path.relative_to(
            REPO_ROOT))
    return path.read_text(encoding="utf-8")


def _mask_js(text: str, mask_strings: bool = True) -> str:
    """Blank out // and /* */ comments, preserving line/column positions,
    so export/call regexes can't misfire on text that only *looks* like a
    match inside a comment. Quoted strings are ALSO blanked by default
    (mask_strings=True) for the same reason -- but callers that need to
    read string contents (e.g. a require('native/x') module path) must
    pass mask_strings=False, since blanking the quotes themselves would
    make those strings unrecoverable."""
    chars = list(text)
    i = 0
    length = len(chars)
    quote: str | None = None
    in_block_comment = False
    while i < length:
        char = chars[i]
        if in_block_comment:
            if char == "*" and i + 1 < length and chars[i + 1] == "/":
                chars[i] = chars[i + 1] = " "
                in_block_comment = False
                i += 2
            else:
                if char != "\n":
                    chars[i] = " "
                i += 1
            continue
        if quote is not None:
            if char == "\\" and i + 1 < length:
                if mask_strings:
                    chars[i] = chars[i + 1] = " "
                i += 2
                continue
            if char == quote:
                quote = None
            if mask_strings and char != "\n":
                chars[i] = " "
            i += 1
            continue
        if char in "'\"`":
            quote = char
            if mask_strings:
                chars[i] = " "
            i += 1
            continue
        if char == "/" and i + 1 < length and chars[i + 1] == "/":
            j = i
            while j < length and chars[j] != "\n":
                chars[j] = " "
                j += 1
            i = j
            continue
        if char == "/" and i + 1 < length and chars[i + 1] == "*":
            chars[i] = chars[i + 1] = " "
            in_block_comment = True
            i += 2
            continue
        i += 1
    return "".join(chars)


def _split_parameters(parameters: str) -> list[str]:
    parameters = parameters.strip()
    if not parameters:
        return []
    parts: list[str] = []
    start = 0
    depths = {"(": 0, "[": 0, "{": 0, "<": 0}
    closes = {")": "(", "]": "[", "}": "{", ">": "<"}
    quote: str | None = None
    escaped = False
    for index, char in enumerate(parameters):
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in "'\"`":
            quote = char
        elif char in depths:
            depths[char] += 1
        elif char in closes:
            opener = closes[char]
            if depths[opener]:
                depths[opener] -= 1
        elif char == "," and not any(depths.values()):
            parts.append(parameters[start:index].strip())
            start = index + 1
    parts.append(parameters[start:].strip())
    return [part for part in parts if part]


def _is_required(part: str) -> bool:
    """A parameter has no default value unless it has a *top-level* `=`
    (not inside nested brackets/parens/braces, and not part of an
    arrow-function type's `=>`)."""
    name_and_type = part.split(":", 1)
    if "?" in name_and_type[0]:
        return False
    depth = 0
    escaped = False
    quote: str | None = None
    chars = part
    i = 0
    while i < len(chars):
        char = chars[i]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            i += 1
            continue
        if char in "'\"`":
            quote = char
        elif char in "([{<":
            depth += 1
        elif char in ")]}>":
            depth = max(0, depth - 1)
        elif char == "=" and depth == 0:
            if chars[i:i + 2] == "=>":
                i += 2
                continue
            return False
        i += 1
    return True


def _signature(parameters: str) -> Signature:
    parts = _split_parameters(parameters)
    required = sum(1 for part in parts if _is_required(part))
    return Signature(required, len(parts))


def _max_arity(signatures: list[Signature]) -> int:
    return max(signature.total for signature in signatures)


def _balanced_content(text: str, open_index: int,
                      opener: str, closer: str) -> tuple[str, int]:
    if open_index < 0 or open_index >= len(text) or \
            text[open_index] != opener:
        raise ValueError("narrow parser expected %r at offset %d" %
                         (opener, open_index))
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(open_index, len(text)):
        char = text[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in "'\"`":
            quote = char
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[open_index + 1:index], index
    raise ValueError("narrow parser found unterminated %s" % opener)


def _declaration_block(text: str, kind: str, name: str) -> str:
    match = re.search(
        r"^\s*(?:export\s+)?%s\s+%s(?:\s*<[^>{}]*>)?"
        r"(?:\s*(?:extends|implements)\s+[^{]+)?\s*\{" %
        (re.escape(kind), re.escape(name)), text, re.MULTILINE)
    if match is None:
        raise ValueError("missing %s %s declaration" % (kind, name))
    open_index = text.find("{", match.start())
    return _balanced_content(text, open_index, "{", "}")[0]


def _signature_at_paren(text: str, search_start: int) -> Signature:
    open_index = text.find("(", search_start)
    parameters, _ = _balanced_content(text, open_index, "(", ")")
    return _signature(parameters)


def _signatures_after_head_matches(text: str,
                                   matches: list[re.Match[str]],
                                   name_group: int = 1) \
        -> dict[str, list[Signature]]:
    """Like _signatures_after_matches, but `matches` are HEAD-only regexes
    that consume through their own opening '(' (DECL_FUNCTION_RE-style),
    so the parameter list always starts at match.end() - 1 -- avoiding the
    "find the first '(' in the whole span" trap when the head contains an
    earlier unrelated '(' (e.g. a generic constraint's function type)."""
    result: dict[str, list[Signature]] = {}
    for match in matches:
        name = match.group(name_group)
        open_index = match.end() - 1
        parameters, _ = _balanced_content(text, open_index, "(", ")")
        result.setdefault(name, []).append(_signature(parameters))
    return result


def _declared_runtime(text: str) \
        -> tuple[set[str], dict[str, list[Signature]], dict[str, str]]:
    function_matches = list(DECL_FUNCTION_RE.finditer(text))
    signatures = _signatures_after_head_matches(text, function_matches)
    values = set(signatures)
    kinds = {name: "function" for name in signatures}

    for name in DECL_VALUE_RE.findall(text):
        values.add(name)
        kinds[name] = "value"

    for match in DECL_CLASS_RE.finditer(text):
        name = match.group(1)
        block = _declaration_block(text, "class", name)
        constructor_matches = [
            item for item in METHOD_RE.finditer(block)
            if item.group(1) == "constructor"
        ]
        if not constructor_matches:
            raise ValueError("class %s has no constructor" % name)
        constructor = _signatures_after_head_matches(
            block, constructor_matches)
        signatures[name] = constructor["constructor"]
        values.add(name)
        kinds[name] = "class"
    return values, signatures, kinds


def _declared_methods(text: str, type_name: str) \
        -> dict[str, list[Signature]]:
    kind = "class" if re.search(
        r"^\s*(?:export\s+)?class\s+%s\b" % re.escape(type_name),
        text, re.MULTILINE) else "interface"
    block = _declaration_block(text, kind, type_name)
    matches = [
        match for match in METHOD_RE.finditer(block)
        if match.group(1) != "constructor"
        and not ACCESSOR_RE.match(
            block[max(0, match.start() - 4):match.end()])
    ]
    return _signatures_after_head_matches(block, matches)


def _mask_method_parameter_lists(block: str) -> str:
    """Blank out every method's `(...)` span so a parameter written on its
    own line (common with this file's multi-line signature style) can't be
    misread by FIELD_RE as a top-level `NAME: Type;` interface member."""
    chars = list(block)
    for match in METHOD_RE.finditer(block):
        open_index = match.end() - 1
        try:
            _, close_index = _balanced_content(block, open_index, "(", ")")
        except ValueError:
            continue
        for index in range(open_index, close_index + 1):
            if chars[index] != "\n":
                chars[index] = " "
    return "".join(chars)


def _declared_members(text: str, type_name: str) -> set[str]:
    """Every non-method member name declared on an interface/class:
    accessor pairs (`get X()`/`set X()`) and plain `readonly X: T;` / `X?:
    T;` fields, unified into one set -- the JS side may realize either as
    a `this.x = ...` assignment or as an Object.defineProperties() entry
    (with or without get/set), and that shape is not part of the public
    contract this checker verifies; only presence of the member is."""
    kind = "class" if re.search(
        r"^\s*(?:export\s+)?class\s+%s\b" % re.escape(type_name),
        text, re.MULTILINE) else "interface"
    block = _declaration_block(text, kind, type_name)
    method_names = {match.group(1) for match in METHOD_RE.finditer(block)}
    accessor_names = {match.group(2) for match in ACCESSOR_RE.finditer(block)}
    field_block = _mask_method_parameter_lists(block)
    fields = {
        match.group(1) for match in FIELD_RE.finditer(field_block)
        if match.group(1) not in method_names
    }
    return accessor_names | fields


def _javascript_exports(text: str) \
        -> tuple[set[str], dict[str, Signature]]:
    masked = _mask_js(text)
    names = set(EXPORT_ASSIGN_RE.findall(masked)) - {"__proto__"}
    callables = {
        match.group(1): _signature_at_paren(masked, match.end() - 1)
        for match in EXPORT_FUNCTION_HEAD_RE.finditer(masked)
    }
    local_functions = {
        match.group(1): _signature_at_paren(masked, match.end() - 1)
        for match in LOCAL_FUNCTION_HEAD_RE.finditer(masked)
    }
    for exported, local in EXPORT_ALIAS_RE.findall(masked):
        if local in local_functions:
            callables[exported] = local_functions[local]
    return names, callables


def _javascript_methods(text: str, type_name: str) \
        -> dict[str, Signature]:
    masked = _mask_js(text)
    return {
        match.group(2): _signature_at_paren(masked, match.end() - 1)
        for match in PROTOTYPE_HEAD_RE.finditer(masked)
        if match.group(1) == type_name
    }


def _javascript_object_methods(text: str, object_name: str) \
        -> dict[str, Signature]:
    masked = _mask_js(text)
    return {
        match.group(2): _signature_at_paren(masked, match.end() - 1)
        for match in OBJECT_METHOD_HEAD_RE.finditer(masked)
        if match.group(1) == object_name
    }


def _exported_function_body(text: str, export_name: str) -> str:
    match = next(
        (candidate for candidate in EXPORT_FUNCTION_HEAD_RE.finditer(text)
         if candidate.group(1) == export_name),
        None,
    )
    if match is None:
        raise ValueError("exported function %s not found" % export_name)
    open_index = text.find("{", match.end() - 1)
    if open_index == -1:
        raise ValueError("exported function %s has no body" % export_name)
    return _balanced_content(text, open_index, "{", "}")[0]


def _constructor_body(text: str, type_name: str) -> str | None:
    match = None
    for candidate in JS_CONSTRUCTOR_HEAD_RE.finditer(text):
        if candidate.group(1) == type_name:
            match = candidate
            break
    if match is None:
        return None
    open_index = text.find("{", match.end() - 1)
    if open_index == -1:
        return None
    try:
        body, _ = _balanced_content(text, open_index, "{", "}")
    except ValueError:
        return None
    return body


def _mask_nested_functions(body: str) -> str:
    """Blank out every nested `function(...) {...}` literal in a
    constructor body so top-level `this.x = ...` assignments can be told
    apart from assignments inside a closure (event handler, accessor
    getter/setter) that are not meant to be public instance fields."""
    chars = list(body)
    for match in NESTED_FUNCTION_HEAD_RE.finditer(body):
        open_index = match.end() - 1
        if chars[open_index] != "{":
            continue
        if chars[open_index] == " ":
            continue  # already masked by an enclosing nested function
        try:
            _, close_index = _balanced_content(body, open_index, "{", "}")
        except ValueError:
            continue
        for index in range(match.start(), close_index + 1):
            if chars[index] != "\n":
                chars[index] = " "
    return "".join(chars)


def _javascript_defined_properties(body: str) -> set[str]:
    """Top-level keys of every `Object.defineProperties(this, {...})` call
    in a constructor body -- covers both accessor entries (`{get, set}`)
    and plain read-only value entries (`{value: ...}`); the checker does
    not need to distinguish the two shapes, only that the member exists."""
    names: set[str] = set()
    for header in DEFINE_PROPERTIES_HEAD_RE.finditer(body):
        open_index = header.end() - 1
        try:
            block, _ = _balanced_content(body, open_index, "{", "}")
        except ValueError:
            continue
        names.update(
            entry.group(1) for entry in DEFINE_PROPERTY_ENTRY_RE.finditer(block))
    return names


def _javascript_members(text: str, type_name: str) -> set[str]:
    """Every non-method member the constructor gives an instance: plain
    top-level `this.x = ...` assignments (excluding anything inside a
    nested function literal -- an event-handler closure's own `this.y =`
    is not an instance field of the type whose constructor hosts it) plus
    every Object.defineProperties() entry, regardless of its shape."""
    body = _constructor_body(text, type_name)
    if body is None:
        return set()
    masked_body = _mask_nested_functions(_mask_js(body))
    fields = set(THIS_FIELD_RE.findall(masked_body)) - {"__proto__"}
    return fields | _javascript_defined_properties(body)


def _exported_function_members(text: str, export_name: str) -> set[str]:
    body = _exported_function_body(text, export_name)
    masked_body = _mask_nested_functions(_mask_js(body))
    return set(THIS_FIELD_RE.findall(masked_body)) - {"__proto__"}


def _native_functions(path: Path, table_name: str) -> dict[str, int]:
    text = _read(path)
    table_match = re.search(
        r"static\s+const\s+duk_function_list_entry\s+%s\[\]\s*="
        r"\s*\{(.*?)^\s*\};" % re.escape(table_name), text,
        re.MULTILINE | re.DOTALL)
    if table_match is None:
        raise ValueError("%s: %s[] not found" %
                         (path.relative_to(REPO_ROOT), table_name))
    entries = {
        name: (VARARGS_NARGS if nargs == "DUK_VARARGS" else int(nargs))
        for name, nargs in NATIVE_ENTRY_RE.findall(table_match.group(1))
    }
    if not entries:
        raise ValueError("%s: %s[] is empty" %
                         (path.relative_to(REPO_ROOT), table_name))
    return entries


def _native_call_sites(javascript: str,
                       native_module: str) -> tuple[str, list[tuple[str, int]]]:
    """Masked source plus (native name, opening-paren index) call sites."""
    module = re.escape(native_module)
    alias_re = re.compile(
        r"(?:var|let|const)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*"
        r"require\(\s*['\"]native/%s['\"]\s*\)" % module)
    call_re = re.compile(
        r"(?:require\(\s*['\"]native/%s['\"]\s*\)|"
        r"\b([A-Za-z_$][A-Za-z0-9_$]*)\b)"
        r"\s*\.\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*\(" % module)
    masked = _mask_js(javascript, mask_strings=False)
    aliases = {match.group(1) for match in alias_re.finditer(masked)}
    sites: list[tuple[str, int]] = []
    for match in call_re.finditer(masked):
        receiver = match.group(1)
        if receiver is None or receiver in aliases:
            sites.append((match.group(2), match.end() - 1))
    return masked, sites


def _native_call_names(javascript: str, native_module: str) -> set[str]:
    """Names called through require('native/MODULE') or a direct alias."""
    _, sites = _native_call_sites(javascript, native_module)
    return {name for name, _ in sites}


def _native_call_arities(javascript: str,
                         native_module: str) -> dict[str, list[int]]:
    """Call argument counts for one native module, including alias calls."""
    masked, sites = _native_call_sites(javascript, native_module)
    calls: dict[str, list[int]] = {}
    for name, open_index in sites:
        arguments, _ = _balanced_content(masked, open_index, "(", ")")
        calls.setdefault(name, []).append(len(_split_parameters(arguments)))
    return calls


def _compare_name_sets(errors: list[str], module: str, label: str,
                       source: set[str], declared: set[str]) -> None:
    for name in sorted(source - declared):
        member = label + name if label.endswith(".") else label + " " + name
        errors.append("%s: %s missing declaration" % (module, member))
    for name in sorted(declared - source):
        member = label + name if label.endswith(".") else label + " " + name
        errors.append("%s: phantom declaration %s" % (module, member))


def _check_native_table(errors: list[str], spec: ModuleSpec,
                        declared_signatures: dict[str, list[Signature]],
                        declared_kinds: dict[str, str],
                        javascript: str) -> None:
    if spec.native_c is None or spec.native_table is None:
        return
    native = _native_functions(spec.native_c, spec.native_table)

    if spec.native_kind == "native-calls":
        # Some native tables (fnlist_io, fnlist_route) are shared across
        # several JS modules (e.g. fnlist_io's `xmlrpc` entry is called
        # from movian/xmlrpc.js, not http.js) -- this checker only has
        # this one module's source in scope, so it verifies the direction
        # it can actually prove: every native call this module's source
        # DOES make must name a function that genuinely exists in the
        # table, catching drift where the JS calls something renamed or
        # removed from the C side. It does not require full table
        # coverage from a single JS file.
        called = _native_call_names(javascript, spec.native_module)
        for name in sorted(called - set(native)):
            errors.append(
                "%s: calls native %s.%s, which does not exist in %s" %
                (spec.name, spec.native_table, name,
                 spec.native_c.relative_to(REPO_ROOT)))
        return

    if spec.native_kind == "native-calls-exact":
        # Exact mode is only for tables wholly consumed by this one wrapper;
        # shared native tables must use the one-direction "native-calls" mode.
        calls = _native_call_arities(javascript, spec.native_module)
        for name in sorted(set(native) - set(calls)):
            errors.append(
                "%s: native %s member %s not exercised by source" %
                (spec.name, spec.native_table, name))
        for name in sorted(set(calls) - set(native)):
            errors.append(
                "%s: source calls missing native %s.%s" %
                (spec.name, spec.native_table, name))
        for name, nargs in sorted(native.items()):
            arities = calls.get(name)
            if not arities or nargs == VARARGS_NARGS:
                continue
            # duk_function_list_entry.nargs is the registered maximum; wrapper
            # calls may omit trailing optional arguments, so compare the
            # maximum source call arity.
            source_nargs = max(arities)
            if source_nargs != nargs:
                errors.append(
                    "%s: native %s.%s nargs=%d vs %d source call args" %
                    (spec.name, spec.native_table, name, nargs,
                     source_nargs))
        return

    # wrapped-exports: exports.NAME is expected to be a thin wrapper
    # around the same-named native function (movian/prop's pattern).
    for name, nargs in sorted(native.items()):
        if name == "global":
            if nargs != 0:
                errors.append(
                    "%s: native global nargs=%d vs 0 wrapped" %
                    (spec.name, nargs))
            if declared_kinds.get(name) != "value":
                errors.append(
                    "%s: global must declare the wrapped np.global() "
                    "value" % spec.name)
            if not re.search(r"exports\.global\s*=.*np\.global\(\s*\)",
                             javascript, re.DOTALL):
                errors.append(
                    "%s: global does not map fnlist_prop global nargs=0 "
                    "through np.global()" % spec.name)
            continue
        signatures = declared_signatures.get(name)
        if signatures is None:
            continue
        if nargs == VARARGS_NARGS:
            continue
        declared_nargs = _max_arity(signatures)
        if declared_nargs != nargs:
            errors.append(
                "%s: native %s nargs=%d vs %d declared" %
                (spec.name, name, nargs, declared_nargs))


def _check_module(errors: list[str], spec: ModuleSpec) -> None:
    declaration = _read(spec.declaration)
    javascript = _read(spec.javascript)
    declared_names, declared_signatures, declared_kinds = \
        _declared_runtime(declaration)
    js_names, js_callables = _javascript_exports(javascript)
    runtime_names = set(js_names)
    if spec.native_c is not None and spec.native_table is not None and \
            spec.native_kind == "wrapped-exports":
        runtime_names.update(_native_functions(
            spec.native_c, spec.native_table))
    _compare_name_sets(errors, spec.name, "export",
                       runtime_names, declared_names)

    # Signature.required is computed (see _is_required) but deliberately
    # NOT compared here: this codebase's declarations routinely mark a
    # trailing JS parameter optional in the .d.ts (e.g. Item.prototype.
    # addOptAction's `subtype`) even though the JS function itself has no
    # `= default` -- JS callers can omit a trailing argument regardless,
    # and several accepted fixtures already rely on that looser contract.
    # A strict required-vs-required check was tried while fixing this
    # file and produced false positives against those already-accepted
    # signatures, so only .total (the argument COUNT) is enforced.
    for name, source_signature in sorted(js_callables.items()):
        signatures = declared_signatures.get(name)
        if signatures is None:
            continue
        if _max_arity(signatures) != source_signature.total:
            errors.append(
                "%s: %s call shape is %d source args vs %d declared" %
                (spec.name, name, source_signature.total,
                 _max_arity(signatures)))

    for type_name in spec.prototypes:
        source_methods = _javascript_methods(javascript, type_name)
        try:
            declared_methods = _declared_methods(declaration, type_name)
        except ValueError as error:
            errors.append("%s: %s" % (spec.name, error))
            continue
        _compare_name_sets(errors, spec.name, type_name + ".",
                           set(source_methods), set(declared_methods))
        for method, source_signature in sorted(source_methods.items()):
            signatures = declared_methods.get(method)
            if signatures is None:
                continue
            if _max_arity(signatures) != source_signature.total:
                errors.append(
                    "%s: %s.%s call shape is %d source args vs %d "
                    "declared" %
                    (spec.name, type_name, method,
                     source_signature.total, _max_arity(signatures)))

        # Non-method members (accessors and plain fields) are checked in
        # the PHANTOM direction only: every declared member must actually
        # exist on the real instance. The reverse (every instance
        # property must be declared) is deliberately not enforced --
        # constructors legitimately carry private bookkeeping state
        # (e.g. Item/Page's internal `eventhandlers`) that was never
        # meant to be part of the public .d.ts surface, and this narrow
        # parser cannot reliably distinguish "public" from "private" JS
        # convention beyond the underscore-suffix idiom (Page's own
        # `paginator_`) that this file already excludes via the nested-
        # function masking in _javascript_members.
        source_members = _javascript_members(javascript, type_name)
        declared_members = _declared_members(declaration, type_name)
        if type_name in spec.exact_member_types:
            _compare_name_sets(
                errors, spec.name, type_name + ".",
                source_members, declared_members)
        else:
            for member in sorted(declared_members - source_members):
                errors.append("%s: phantom declaration %s.%s" %
                              (spec.name, type_name, member))

    for type_name, object_name in spec.object_prototypes:
        source_methods = _javascript_object_methods(javascript, object_name)
        try:
            declared_methods = _declared_methods(declaration, type_name)
        except ValueError as error:
            errors.append("%s: %s" % (spec.name, error))
            continue
        _compare_name_sets(errors, spec.name, type_name + ".",
                           set(source_methods), set(declared_methods))
        for method, source_signature in sorted(source_methods.items()):
            signatures = declared_methods.get(method)
            if signatures is None:
                continue
            if _max_arity(signatures) != source_signature.total:
                errors.append(
                    "%s: %s.%s call shape is %d source args vs %d "
                    "declared" %
                    (spec.name, type_name, method,
                     source_signature.total, _max_arity(signatures)))

    for export_name, excluded in spec.export_instances:
        source_members = _exported_function_members(
            javascript, export_name) - set(excluded)
        declared_members = _declared_members(declaration, export_name)
        _compare_name_sets(
            errors, spec.name, export_name + ".",
            source_members, declared_members)

    _check_native_table(errors, spec, declared_signatures, declared_kinds,
                        javascript)


def _receiver_call_names(text: str, receiver: str) -> set[str]:
    masked = _mask_js(text)
    pattern = re.compile(
        r"\b%s\.([A-Za-z_$][A-Za-z0-9_$]*)\s*\(" %
        re.escape(receiver))
    return {match.group(1) for match in pattern.finditer(masked)}


def _store_native_resolution(errors: list[str]) -> dict[str, set[str]]:
    """Resolve movian/store exports through the top-level fs.js wrapper.

    `createFromPath` calls the CommonJS `fs` wrapper, while `create` shadows
    that alias with native/fs and then calls createFromPath. The resolution is
    intentionally scope-aware so the shadowed `fs` identifier cannot make
    writeFileSync/readFileSync look like phantom native names.
    """
    store_text = _read(JS_MODULE_DIR / "store.js")
    fs_wrapper_path = REPO_ROOT / "res" / "ecmascript" / "modules" / "fs.js"
    fs_wrapper_text = _read(fs_wrapper_path)
    native_fs_path = ECMASCRIPT_C_DIR / "es_fs.c"
    native_fs = _native_functions(native_fs_path, "fnlist_fs")

    if not re.search(
            r"(?:var|let|const)\s+fs\s*=\s*require\(\s*['\"]fs['\"]\s*\)",
            _mask_js(store_text, mask_strings=False)):
        errors.append(
            "movian/store: top-level fs wrapper alias is missing")

    fs_exports, _ = _javascript_exports(fs_wrapper_text)
    fs_native_by_export: dict[str, set[str]] = {}
    for export_name in fs_exports:
        body = _exported_function_body(fs_wrapper_text, export_name)
        fs_native_by_export[export_name] = _native_call_names(body, "fs")

    store_exports, _ = _javascript_exports(store_text)
    direct_by_export: dict[str, set[str]] = {}
    nested_by_export: dict[str, set[str]] = {}
    for export_name in store_exports:
        body = _exported_function_body(store_text, export_name)
        direct = _native_call_names(body, "fs")
        local_native_fs = re.search(
            r"(?:var|let|const)\s+fs\s*=\s*"
            r"require\(\s*['\"]native/fs['\"]\s*\)",
            _mask_js(body, mask_strings=False),
        )
        wrapper_calls = set() if local_native_fs else \
            _receiver_call_names(body, "fs")
        for wrapper_call in sorted(wrapper_calls):
            if wrapper_call not in fs_native_by_export:
                errors.append(
                    "movian/store: %s calls phantom fs wrapper member %s" %
                    (export_name, wrapper_call))
                continue
            direct.update(fs_native_by_export[wrapper_call])
        direct_by_export[export_name] = direct
        nested_by_export[export_name] = _receiver_call_names(body, "exports")

    resolved: dict[str, set[str]] = {}

    def resolve(export_name: str, visiting: set[str]) -> set[str]:
        if export_name in resolved:
            return resolved[export_name]
        if export_name in visiting:
            raise ValueError(
                "movian/store export recursion at %s" % export_name)
        visiting.add(export_name)
        names = set(direct_by_export[export_name])
        for nested in nested_by_export[export_name]:
            if nested not in direct_by_export:
                errors.append(
                    "movian/store: %s calls phantom store export %s" %
                    (export_name, nested))
                continue
            names.update(resolve(nested, visiting))
        visiting.remove(export_name)
        resolved[export_name] = names
        return names

    for export_name in store_exports:
        resolve(export_name, set())

    for export_name, names in sorted(resolved.items()):
        if not names:
            errors.append(
                "movian/store: export %s has no resolved native/fs calls" %
                export_name)
        for name in sorted(names - set(native_fs)):
            errors.append(
                "movian/store: %s resolves phantom native fnlist_fs.%s" %
                (export_name, name))
    return resolved


def _check_plugin_global(errors: list[str]) -> None:
    declaration = _read(PLUGIN_DECLARATION)
    source = _read(PLUGIN_SOURCE)
    if PLUGIN_GLOBAL_DECL_RE.search(declaration) is None:
        errors.append(
            "Plugin: global declaration must be `declare var Plugin: "
            "MovianPluginGlobal`")
    source_fields = set(PLUGIN_ASSIGN_RE.findall(_mask_js(
        source, mask_strings=False)))
    declared_fields = _declared_members(declaration, "MovianPluginGlobal")
    _compare_name_sets(
        errors, "Plugin", "field", source_fields, declared_fields)


def check_source_shapes() -> tuple[list[str], dict[str, set[str]]]:
    errors: list[str] = []
    for spec in MODULES:
        try:
            _check_module(errors, spec)
        except ValueError as error:
            # A parse failure in one module's source/declaration must not
            # silently abort verification of the other modules.
            errors.append("%s: %s" % (spec.name, error))
    resolution: dict[str, set[str]] = {}
    try:
        resolution = _store_native_resolution(errors)
    except ValueError as error:
        errors.append("movian/store: %s" % error)
    try:
        _check_plugin_global(errors)
    except ValueError as error:
        errors.append("Plugin: %s" % error)
    return errors, resolution


def _tsc_command(tsc: str, fixture: Path) -> list[str]:
    inputs = [spec.declaration for spec in MODULES] + [
        PLUGIN_DECLARATION,
        fixture,
    ]
    return [
        tsc,
        "--noEmit",
        "--strict",
        "--target", "ES2015",
        "--lib", "ES2015",
        "--module", "commonjs",
        "--pretty", "false",
        "--noErrorTruncation",
        *[str(path.relative_to(REPO_ROOT)) for path in inputs],
    ]


def _run_tsc(tsc: str, fixture: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _tsc_command(tsc, fixture),
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )


def _expected_diagnostics() -> set[tuple[str, int, int]]:
    expected: set[tuple[str, int, int]] = set()
    for line_number, line in enumerate(_read(NEGATIVE_FIXTURE).splitlines(), 1):
        for code in EXPECTED_DIAGNOSTIC_RE.findall(line):
            expected.add((NEGATIVE_FIXTURE.name, line_number, int(code)))
    if not expected:
        raise ValueError("negative fixture has no EXPECT_TS markers")
    return expected


def _actual_diagnostics(output: str) -> set[tuple[str, int, int]]:
    return {
        (Path(path).name, int(line), int(code))
        for path, line, _column, code in DIAGNOSTIC_RE.findall(output)
    }


def check_typescript(tsc: str) -> list[str]:
    errors: list[str] = []
    positive = _run_tsc(tsc, POSITIVE_FIXTURE)
    if positive.returncode != 0:
        errors.append("positive fixture failed:\n%s" % positive.stdout.rstrip())

    negative = _run_tsc(tsc, NEGATIVE_FIXTURE)
    expected = _expected_diagnostics()
    actual = _actual_diagnostics(negative.stdout)
    if negative.returncode == 0:
        errors.append("negative fixture unexpectedly passed")
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        if missing:
            errors.append("negative fixture missing diagnostics: %s" % missing)
        if extra:
            errors.append("negative fixture extra diagnostics: %s\n%s" %
                          (extra, negative.stdout.rstrip()))
    return errors



def _load_metadata() -> dict:
    """Load the generated movian-metadata.json."""
    text = _read(METADATA_FILE)
    return json.loads(text)




def _check_commonjs_coverage() -> list[str]:
    """Check CommonJS module coverage using live metadata."""
    errors: list[str] = []

    metadata = _load_metadata()
    all_modules = metadata.get("js", {}).get("modules", [])

    # Partition CommonJS and native modules
    commonjs_modules = {m["name"] for m in all_modules if m.get("kind") == "commonjs"}
    native_modules = {m["name"] for m in all_modules if m.get("kind") == "native"}
    deferred_count = len(native_modules)

    # Subtract the six previously accepted modules
    accepted = {
        "movian/page",
        "movian/prop",
        "movian/http",
        "movian/settings",
        "movian/service",
        "movian/store",
    }

    target_modules = commonjs_modules - accepted

    # Get registered module names from MODULES
    registered_modules = {spec.name for spec in MODULES}

    # Check for missing fixtures
    missing = []
    phantom = []

    for module_name in target_modules:
        if module_name.startswith("movian/"):
            basename = module_name[len("movian/"):].replace("/", "-")
            decl_path = REFERENCE_DIR / ("movian-" + basename + ".d.ts")
        else:
            decl_path = REFERENCE_DIR / (module_name + ".d.ts")

        if not decl_path.is_file():
            missing.append(module_name)

    # Check for phantom fixtures
    existing_decls = list(REFERENCE_DIR.glob("*.d.ts"))
    for decl_path in existing_decls:
        if decl_path.name == "movian-plugin.d.ts":
            continue

        # Convert filename back to module name
        if decl_path.name.startswith("movian-"):
            module_name = "movian/" + decl_path.name[len("movian-"):len(decl_path.name)-5].replace("-", "/")
        else:
            module_name = decl_path.name[:-5]  # Remove .d.ts

        # Skip accepted modules
        if module_name in accepted:
            continue

        if module_name not in target_modules:
            phantom.append(module_name)

    # Check for modules in fixtures but not in MODULES registry
    fixture_modules = set()
    for module_name in target_modules:
        if module_name.startswith("movian/"):
            basename = module_name[len("movian/"):].replace("/", "-")
            decl_path = REFERENCE_DIR / ("movian-" + basename + ".d.ts")
        else:
            decl_path = REFERENCE_DIR / (module_name + ".d.ts")
        if decl_path.is_file():
            fixture_modules.add(module_name)

    registry_missing = fixture_modules - registered_modules
    if registry_missing:
        errors.append("registry missing %d: %s" % (len(registry_missing), ", ".join(sorted(registry_missing))))

    if missing:
        errors.append("missing %d: %s" % (len(missing), ", ".join(sorted(missing))))

    if phantom:
        errors.append("phantom %d: %s" % (len(phantom), ", ".join(sorted(phantom))))

    if not missing and not phantom and not registry_missing:
        print("reference-dts: CommonJS coverage OK (missing 0, phantom 0, deferred-native %d)" % deferred_count)

    return errors

def main() -> int:
    parser = argparse.ArgumentParser(description="Check reference .d.ts calibration fixtures")
    parser.add_argument("--commonjs", action="store_true", help="Check CommonJS module coverage")
    args = parser.parse_args()

    if args.commonjs:
        errors = _check_commonjs_coverage()
        for error in errors:
            print("reference-dts: %s" % error, file=sys.stderr)
        return 1 if errors else 0

    try:
        errors, resolution = check_source_shapes()
    except (OSError, ValueError) as error:
        print("reference-dts: %s: %s" % (type(error).__name__, error),
              file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print("reference-dts: %s" % error, file=sys.stderr)
        return 1
    rendered_resolution = "; ".join(
        "%s -> native/fs[%s]" % (name, ",".join(sorted(native_names)))
        for name, native_names in sorted(resolution.items())
    )
    print("reference-dts: source shapes OK "
          "(%d modules + Plugin; native names+nargs)" % len(MODULES))
    print("reference-dts: movian/store resolution: %s" %
          rendered_resolution)

    tsc = shutil.which("tsc")
    if tsc is None:
        print("reference-dts: tsc unavailable; skipping TypeScript fixtures")
        return 0

    try:
        errors = check_typescript(tsc)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print("reference-dts: TypeScript check failed to run: %s: %s" %
              (type(error).__name__, error), file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print("reference-dts: %s" % error, file=sys.stderr)
        return 1
    print("reference-dts: tsc positive fixture OK")
    print("reference-dts: tsc negative diagnostics OK (%d expected)" %
          len(_expected_diagnostics()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
