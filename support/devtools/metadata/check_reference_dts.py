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
from dataclasses import dataclass, replace
from enum import Enum, auto
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
    variadic: bool = False


class MemberSource(Enum):
    PROTOTYPE = auto()
    SHARED_OBJECT = auto()
    EXPORTED_INSTANCE = auto()
    OBJECT_CONSTRUCTOR = auto()
    INSTANCE_REFERENCES = auto()
    RETURNED_OBJECT = auto()
    LOCAL_CONSTRUCTOR = auto()
    PROXY_HANDLER = auto()
    LOCAL_OBJECT = auto()
    LOCAL_OBJECT_LITERAL = auto()
    CONSUMED_OBJECT = auto()
    NATIVE_RETURNED_OBJECT = auto()


@dataclass(frozen=True)
class NativeMemberSource:
    function: str
    path: Path


@dataclass(frozen=True)
class NativeArityFloor:
    method: str
    function: str
    minimum: int
    path: Path


@dataclass(frozen=True)
class MemberShape:
    """One source-backed member check for a declared type.

    A type may have multiple descriptors when independent runtime surfaces,
    such as its prototype and constructor-created fields, both need checking.
    """

    type_name: str
    source: MemberSource
    source_name: str
    excluded: tuple[str, ...] = ()
    exact_members: bool = False
    parameter: str | None = None
    inherited: NativeMemberSource | None = None
    native: NativeMemberSource | None = None
    arity_floors: tuple[NativeArityFloor, ...] = ()


class ExportLinkKind(Enum):
    ALIAS = auto()
    WRAPPER = auto()


@dataclass(frozen=True)
class ExportLink:
    export_name: str
    required_module: str
    required_member: str
    kind: ExportLinkKind = ExportLinkKind.ALIAS


@dataclass(frozen=True)
class ModuleSpec:
    name: str
    declaration: Path
    javascript: Path
    member_shapes: tuple[MemberShape, ...]
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
    # Modules with no source-backed nested class/interface surface.
    forbid_nested_types: bool = False
    # Opt-in inventory audit for issue #137 modules. Every native property
    # alias and exports.__proto__ re-export must be registered exactly.
    # Modules this one is expected to require. Membership in the artifact is
    # not enough by itself: retargeting a wrapper to another EXISTING module
    # passed, because the one-directional table check simply saw no calls for
    # the configured table. Declaring the expected set makes such a swap an
    # error rather than a silence.
    requires: tuple[str, ...] = ()
    audit_runtime_aliases: bool = False
    export_links: tuple[ExportLink, ...] = ()


JS_MODULE_DIR = REPO_ROOT / "res" / "ecmascript" / "modules" / "movian"
ECMASCRIPT_C_DIR = REPO_ROOT / "src" / "ecmascript"

MODULES = (
    ModuleSpec(
        "movian/page",
        REFERENCE_DIR / "movian-page.d.ts",
        JS_MODULE_DIR / "page.js",
        tuple(MemberShape(name, MemberSource.PROTOTYPE, name)
              for name in ("Item", "Page", "Route", "Searcher")),
        native_c=ECMASCRIPT_C_DIR / "es_route.c",
        native_table="fnlist_route",
        native_module="route",
        native_kind="native-calls",
        requires=("movian/prop", "movian/settings", "native/hook", "native/metadata", "native/route"),
    ),
    ModuleSpec(
        "movian/prop",
        REFERENCE_DIR / "movian-prop.d.ts",
        JS_MODULE_DIR / "prop.js",
        (),
        native_c=ECMASCRIPT_C_DIR / "es_prop.c",
        native_table="fnlist_prop",
        native_kind="wrapped-exports",
        requires=("native/prop",),
    ),
    ModuleSpec(
        "movian/http",
        REFERENCE_DIR / "movian-http.d.ts",
        JS_MODULE_DIR / "http.js",
        (MemberShape("HttpResponse", MemberSource.PROTOTYPE, "HttpResponse"),),
        native_c=ECMASCRIPT_C_DIR / "es_io.c",
        native_table="fnlist_io",
        native_module="io",
        native_kind="native-calls",
        requires=("native/io", "native/string"),
    ),
    ModuleSpec(
        "movian/settings",
        REFERENCE_DIR / "movian-settings.d.ts",
        JS_MODULE_DIR / "settings.js",
        (
            MemberShape(
                "SettingsMethods", MemberSource.SHARED_OBJECT, "sp"),
            MemberShape(
                "globalSettings", MemberSource.EXPORTED_INSTANCE,
                "globalSettings", ("getvalue", "setvalue")),
            MemberShape(
                "kvstoreSettings", MemberSource.EXPORTED_INSTANCE,
                "kvstoreSettings", ("getvalue", "setvalue")),
        ),
        native_c=ECMASCRIPT_C_DIR / "es_kvstore.c",
        native_table="fnlist_kvstore",
        native_module="kvstore",
        native_kind="native-calls-exact",
        requires=("movian/prop", "movian/store", "native/fs", "native/kvstore"),
    ),
    ModuleSpec(
        "movian/service",
        REFERENCE_DIR / "movian-service.d.ts",
        JS_MODULE_DIR / "service.js",
        (MemberShape(
            "Service", MemberSource.PROTOTYPE, "Service",
            exact_members=True),),
        native_c=ECMASCRIPT_C_DIR / "es_service.c",
        native_table="fnlist_service",
        native_module="service",
        native_kind="native-calls-exact",
        requires=("native/service",),
    ),
    ModuleSpec(
        "movian/store",
        REFERENCE_DIR / "movian-store.d.ts",
        JS_MODULE_DIR / "store.js",
        (),
        requires=("fs", "native/fs"),
    ),
    ModuleSpec(
        "movian/html",
        REFERENCE_DIR / "movian-html.d.ts",
        JS_MODULE_DIR / "html.js",
        (
            MemberShape("Node", MemberSource.SHARED_OBJECT, "NodeProto"),
            MemberShape(
                "Node", MemberSource.OBJECT_CONSTRUCTOR, "NodeProto"),
            # exports.parse returns an object literal; without this the
            # declared ParsedDocument was connected to nothing and a phantom
            # member on it stayed green in both source-shape checks.
            MemberShape(
                "ParsedDocument", MemberSource.RETURNED_OBJECT, "parse"),
        ),
        requires=("native/gumbo",),
        audit_runtime_aliases=True,
        # Without the table none of the native/gumbo calls behind these
        # shapes were checked at all.
        native_c=ECMASCRIPT_C_DIR / "es_gumbo.c",
        native_table="fnlist_gumbo",
        native_module="gumbo",
        native_kind="native-calls",
    ),
    ModuleSpec(
        "movian/itemhook",
        REFERENCE_DIR / "movian-itemhook.d.ts",
        JS_MODULE_DIR / "itemhook.js",
        (
            MemberShape(
                "ItemHookHandle", MemberSource.RETURNED_OBJECT, "create"),
            # The callback argument was disconnected: a phantom method on
            # NavigationObject stayed green because only the create handle
            # was compared.
            MemberShape(
                "NavigationObject", MemberSource.LOCAL_OBJECT_LITERAL,
                "navobj"),
            MemberShape(
                "ItemHookConfig", MemberSource.CONSUMED_OBJECT, "create",
                parameter="conf"),
        ),
        requires=("movian/prop",),
        audit_runtime_aliases=True,
    ),
    ModuleSpec(
        "movian/popup",
        REFERENCE_DIR / "movian-popup.d.ts",
        JS_MODULE_DIR / "popup.js",
        (),
        export_links=(ExportLink("notify", "native/popup", "notify"),),
        requires=("native/popup",),
        audit_runtime_aliases=True,
    ),
    ModuleSpec(
        "movian/sqlite",
        REFERENCE_DIR / "movian-sqlite.d.ts",
        JS_MODULE_DIR / "sqlite.js",
        (MemberShape(
            "DB", MemberSource.PROTOTYPE, "DB", ("db",),
            exact_members=True,
            arity_floors=(NativeArityFloor(
                "query", "query", 2,
                ECMASCRIPT_C_DIR / "es_sqlite.c"),)),),
        native_c=ECMASCRIPT_C_DIR / "es_sqlite.c",
        native_table="fnlist_sqlite",
        native_module="sqlite",
        native_kind="native-calls",
        requires=("native/sqlite",),
        audit_runtime_aliases=True,
    ),
    ModuleSpec(
        "movian/subtitles",
        REFERENCE_DIR / "movian-subtitles.d.ts",
        JS_MODULE_DIR / "subtitles.js",
        (MemberShape(
            "SubtitleRequest", MemberSource.LOCAL_OBJECT, "req",
            inherited=NativeMemberSource(
                "esp_query", ECMASCRIPT_C_DIR / "es_subtitles.c")),),
        export_links=(
            ExportLink("getLanguages", "native/subtitle", "getLanguages"),
        ),
        requires=("native/subtitle",),
        audit_runtime_aliases=True,
    ),
    ModuleSpec(
        "movian/videoscrobbler",
        REFERENCE_DIR / "movian-videoscrobbler.d.ts",
        JS_MODULE_DIR / "videoscrobbler.js",
        (
            MemberShape(
                "VideoScrobbler", MemberSource.PROTOTYPE,
                "VideoScrobbler"),
            MemberShape(
                "VideoScrobbler", MemberSource.INSTANCE_REFERENCES,
                "VideoScrobbler", ("paused", "hook")),
        ),
        requires=("movian/prop", "native/hook"),
        audit_runtime_aliases=True,
    ),
    ModuleSpec(
        "movian/xml",
        REFERENCE_DIR / "movian-xml.d.ts",
        JS_MODULE_DIR / "xml.js",
        (MemberShape(
            "XmlProxy", MemberSource.PROXY_HANDLER, "htsmsgHandler"),),
        requires=("native/htsmsg",),
        audit_runtime_aliases=True,
    ),
    ModuleSpec(
        "movian/xmlrpc",
        REFERENCE_DIR / "movian-xmlrpc.d.ts",
        JS_MODULE_DIR / "xmlrpc.js",
        (),
        forbid_nested_types=True,
        requires=("movian/xml", "native/io"),
        audit_runtime_aliases=True,
    ),
    ModuleSpec(
        "fs",
        REFERENCE_DIR / "fs.d.ts",
        TOPLEVEL_MODULE_DIR / "fs.js",
        (),
        forbid_nested_types=True,
        requires=("native/fs",),
        audit_runtime_aliases=True,
        # The movian/store resolver only happens to cover open, read,
        # write and fsize; readdir/unlink/mkdirs/rmdir had nothing
        # validating them.
        native_c=ECMASCRIPT_C_DIR / "es_fs.c",
        native_table="fnlist_fs",
        native_module="fs",
        native_kind="native-calls",
    ),
    ModuleSpec(
        "http",
        REFERENCE_DIR / "http.d.ts",
        TOPLEVEL_MODULE_DIR / "http.js",
        (
            MemberShape(
                "HttpRequest", MemberSource.LOCAL_CONSTRUCTOR, "Request",
                ("onResponse", "onError")),
            MemberShape(
                "HttpResponse", MemberSource.LOCAL_CONSTRUCTOR, "Response",
                ("onData", "onEnd")),
        ),
        requires=("native/io", "native/string", "url"),
        audit_runtime_aliases=True,
    ),
    ModuleSpec(
        "https",
        REFERENCE_DIR / "https.d.ts",
        TOPLEVEL_MODULE_DIR / "https.js",
        (),
        export_links=(
            ExportLink(
                "request", "./http", "request", ExportLinkKind.WRAPPER),
            ExportLink("get", "./http", "get", ExportLinkKind.WRAPPER),
        ),
        audit_runtime_aliases=True,
    ),
    ModuleSpec(
        "querystring",
        REFERENCE_DIR / "querystring.d.ts",
        TOPLEVEL_MODULE_DIR / "querystring.js",
        (),
        export_links=(
            ExportLink("parse", "native/string", "queryStringSplit"),
        ),
        requires=("native/string",),
        audit_runtime_aliases=True,
    ),
    ModuleSpec(
        "url",
        REFERENCE_DIR / "url.d.ts",
        TOPLEVEL_MODULE_DIR / "url.js",
        (
            MemberShape(
                "UrlObject", MemberSource.CONSUMED_OBJECT, "format",
                ("port",), parameter="d"),
            MemberShape(
                "ParsedUrl", MemberSource.NATIVE_RETURNED_OBJECT,
                "parseURL", native=NativeMemberSource(
                    "parseURL", ECMASCRIPT_C_DIR / "es_string.c")),
        ),
        export_links=(
            ExportLink("parse", "native/string", "parseURL"),
            ExportLink("resolve", "native/string", "resolveURL"),
        ),
        # `port` is read by format -- and only through the broken
        # `':' + port` branch -- so it is deliberately NOT offered on the
        # input type. Excluded here rather than silently tolerated, so the
        # exception is visible instead of looking like an oversight.
        requires=("native/string",),
        audit_runtime_aliases=True,
    ),
    ModuleSpec(
        "websocket",
        REFERENCE_DIR / "websocket.d.ts",
        TOPLEVEL_MODULE_DIR / "websocket.js",
        (
            MemberShape(
                "w3cwebsocket", MemberSource.PROTOTYPE, "w3cwebsocket"),
            MemberShape(
                "w3cwebsocket", MemberSource.INSTANCE_REFERENCES,
                "w3cwebsocket", ("_sock",)),
        ),
        # Without this the wrapper's native calls were checked against nothing:
        # renaming ws.clientSend to a function that does not exist left the
        # whole gate green.
        native_c=ECMASCRIPT_C_DIR / "es_websocket.c",
        native_table="fnlist_websocket",
        native_module="websocket",
        native_kind="native-calls",
        requires=("native/websocket",),
        audit_runtime_aliases=True,
    ),
)

POSITIVE_FIXTURE = FIXTURE_DIR / "reference-positive.ts"
NEGATIVE_FIXTURE = FIXTURE_DIR / "reference-negative.ts"
# The fixtures above type-check REFERENCE_DIR -- the hand-written canon. The
# artifact plugins actually consume is generated/movian-api.d.ts, and until
# these three names existed nothing passed it to tsc at all: `gen.py --check`
# only proved it was byte-identical to what the generator emits, which is true
# of a wrong emission too. Two real defects shipped through that hole -- an
# `export *` that hid every local member of `movian/prop`, and a zero-formal
# `xmlrpc.call` that rejected its own call sites.
GENERATED_DTS = REPO_ROOT / "generated" / "movian-api.d.ts"
GENERATED_POSITIVE_FIXTURE = FIXTURE_DIR / "generated-positive.ts"
GENERATED_NEGATIVE_FIXTURE = FIXTURE_DIR / "generated-negative.ts"
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
PROTOTYPE_ALIAS_RE = re.compile(
    r"^\s*(?:exports\.)?([A-Za-z_$][A-Za-z0-9_$]*)\.prototype\."
    r"([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*"
    r"(?:exports\.)?([A-Za-z_$][A-Za-z0-9_$]*)\.prototype\."
    r"([A-Za-z_$][A-Za-z0-9_$]*)\s*;", re.MULTILINE)
OBJECT_METHOD_HEAD_RE = re.compile(
    r"^\s*([A-Za-z_$][A-Za-z0-9_$]*)\.([A-Za-z_$][A-Za-z0-9_$]*)"
    r"\s*=\s*function\s*\(", re.MULTILINE)
OBJECT_ASSIGN_RE = re.compile(
    r"^\s*([A-Za-z_$][A-Za-z0-9_$]*)\.([A-Za-z_$][A-Za-z0-9_$]*)"
    r"\s*=(?!=)", re.MULTILINE)
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
CALLBACK_FIELD_RE = re.compile(
    r"^\s*(?:readonly\s+)?([A-Za-z_$][A-Za-z0-9_$]*)\??\s*:\s*\(",
    re.MULTILINE)
NATIVE_ENTRY_RE = re.compile(
    r'\{\s*"([^"]+)"\s*,\s*[A-Za-z_]\w*\s*,\s*'
    r'(-?\d+|DUK_VARARGS)\s*\}')
DEFINE_PROPERTIES_HEAD_RE = re.compile(
    r"Object\.defineProperties\(\s*this\s*,\s*\{")
DEFINE_PROPERTY_ENTRY_RE = re.compile(
    r"^\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*:\s*\{", re.MULTILINE)
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
DEFINE_PROPERTIES_PROTOTYPE_HEAD_RE = re.compile(
    r"Object\.defineProperties\(\s*(?:exports\.)?"
    r"([A-Za-z_$][A-Za-z0-9_$]*)\.prototype\s*,\s*\{")
REEXPORT_PROTO_RE = re.compile(
    r"^\s*exports\.__proto__\s*=\s*require\(['\"]([^'\"]+)['\"]\)",
    re.MULTILINE)
ALIAS_RE = re.compile(
    r"^\s*(?:var|let|const)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*="
    r"\s*require\(\s*['\"]([^'\"]+)['\"]\s*\)", re.MULTILINE)
ALIAS_EXPORT_RE = re.compile(
    r"^\s*exports\.([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*"
    r"([A-Za-z_$][A-Za-z0-9_$]*)\."
    r"([A-Za-z_$][A-Za-z0-9_$]*)\s*;", re.MULTILINE)
DIRECT_REQUIRE_ALIAS_EXPORT_RE = re.compile(
    r"^\s*exports\.([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*"
    r"require\(\s*['\"]([^'\"]+)['\"]\s*\)\."
    r"([A-Za-z_$][A-Za-z0-9_$]*)\s*;", re.MULTILINE)
RETURN_OBJECT_HEAD_RE = re.compile(r"\breturn\s*\{")
OBJECT_LITERAL_ENTRY_RE = re.compile(
    r"^\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*:\s*", re.MULTILINE)
OBJECT_LITERAL_FUNCTION_ENTRY_RE = re.compile(
    r"^\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*:\s*function\s*\(",
    re.MULTILINE)
OBJECT_LITERAL_ASSIGN_RE = re.compile(
    r"^\s*(?:var|let|const)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*\{",
    re.MULTILINE)
EXPLICIT_NAME_BRANCH_RE = re.compile(
    r"\bif\s*\(\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*={2,3}\s*"
    r"['\"]([^'\"]+)['\"]\s*\)")
DECL_INTERFACE_RE = re.compile(
    r"^\s*(?:export\s+)?interface\s+"
    r"([A-Za-z_$][A-Za-z0-9_$]*)\b", re.MULTILINE)
THIS_ALIAS_RE = re.compile(
    r"\b(?:var|let|const)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*this\b")
ARGUMENT_INDEX_RE = re.compile(r"\barguments\s*\[\s*(\d+)\s*\]")
ARGUMENT_REST_LOOP_RE = re.compile(
    r"\bfor\s*\(\s*var\s+[A-Za-z_$][A-Za-z0-9_$]*\s*=\s*(\d+)\s*;"
    r"\s*[A-Za-z_$][A-Za-z0-9_$]*\s*<\s*arguments\.length\b")


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
    rest = [part for part in parts if part.lstrip().startswith("...")]
    if len(rest) > 1 or (rest and parts[-1] != rest[0]):
        raise ValueError("rest parameter must be the final parameter")
    fixed = parts[:-1] if rest else parts
    required = sum(1 for part in fixed if _is_required(part))
    return Signature(required, len(fixed), bool(rest))


def _max_arity(signatures: list[Signature]) -> int:
    return max(signature.total for signature in signatures)


def _same_call_shape(source: Signature, declared: Signature) -> bool:
    return source.total == declared.total and \
        source.variadic == declared.variadic and \
        (not source.variadic or source.required == declared.required)


def _has_call_shape(source: Signature,
                    declared: list[Signature]) -> bool:
    return any(_same_call_shape(source, candidate)
               for candidate in declared)


def _render_signature_shape(signature: Signature) -> str:
    suffix = "+rest" if signature.variadic else ""
    return "%d%s" % (signature.total, suffix)


def _balanced_content(text: str, open_index: int,
                      opener: str, closer: str) -> tuple[str, int]:
    if open_index < 0 or open_index >= len(text) or \
            text[open_index] != opener:
        raise ValueError("narrow parser expected %r at offset %d" %
                         (opener, open_index))
    depth = 0
    quote: str | None = None
    escaped = False
    # Comments must be skipped, not just quotes: an ordinary apostrophe in a
    # doc comment ("end()'s callback") would otherwise open a string state
    # that never closes, so the scan runs past the real closing brace and
    # silently returns a block spanning the *following* declaration too.
    comment: str | None = None
    index = open_index
    while index < len(text):
        char = text[index]
        following = text[index + 1:index + 2]
        if comment == "line":
            if char == "\n":
                comment = None
        elif comment == "block":
            if char == "*" and following == "/":
                comment = None
                index += 1
        elif quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char == "/" and following in ("/", "*"):
            comment = "line" if following == "/" else "block"
            index += 1
        elif char in "'\"`":
            quote = char
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[open_index + 1:index], index
        index += 1
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


def _javascript_signature_at_paren(text: str, open_index: int) -> Signature:
    """Read a JS function signature, including `arguments`-driven rest.

    Some legacy CommonJS functions intentionally have no formal parameters
    but consume a fixed prefix plus a rest tail through `arguments`. The loop
    start is the fixed prefix length: sqlite query starts at zero, while
    xmlrpc starts at two and also references arguments[0] and arguments[1].
    """
    parameters, close_index = _balanced_content(
        text, open_index, "(", ")")
    signature = _signature(parameters)
    body_open = text.find("{", close_index + 1)
    if body_open == -1:
        return signature
    body, _ = _balanced_content(text, body_open, "{", "}")
    rest_starts = [
        int(match.group(1))
        for match in ARGUMENT_REST_LOOP_RE.finditer(body)
    ]
    if not rest_starts:
        return signature
    fixed = min(rest_starts)
    indexed = [
        int(match.group(1))
        for match in ARGUMENT_INDEX_RE.finditer(body)
    ]
    fixed = max(fixed, max(indexed, default=-1) + 1)
    return Signature(fixed, fixed, True)


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


def _declared_members(text: str, type_name: str,
                      include_callbacks: bool = False) -> set[str]:
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
    if include_callbacks:
        fields.update(
            match.group(1)
            for match in CALLBACK_FIELD_RE.finditer(field_block)
            if match.group(1) not in method_names
        )
    return accessor_names | fields


def _javascript_exports(text: str) \
        -> tuple[set[str], dict[str, Signature]]:
    masked = _mask_js(text)
    names = set(EXPORT_ASSIGN_RE.findall(masked)) - {"__proto__"}
    callables = {
        match.group(1): _javascript_signature_at_paren(
            masked, match.end() - 1)
        for match in EXPORT_FUNCTION_HEAD_RE.finditer(masked)
    }
    local_functions = {
        match.group(1): _javascript_signature_at_paren(
            masked, match.end() - 1)
        for match in LOCAL_FUNCTION_HEAD_RE.finditer(masked)
    }
    for exported, local in EXPORT_ALIAS_RE.findall(masked):
        if local in local_functions:
            callables[exported] = local_functions[local]
    return names, callables


def _javascript_methods(text: str, type_name: str) \
        -> dict[str, Signature]:
    masked = _mask_js(text)
    methods = {
        match.group(2): _javascript_signature_at_paren(
            masked, match.end() - 1)
        for match in PROTOTYPE_HEAD_RE.finditer(masked)
        if match.group(1) == type_name
    }
    unresolved = [
        (match.group(2), match.group(4))
        for match in PROTOTYPE_ALIAS_RE.finditer(masked)
        if match.group(1) == type_name and match.group(3) == type_name
    ]
    while unresolved:
        remaining = []
        progress = False
        for alias, target in unresolved:
            if target in methods:
                methods[alias] = methods[target]
                progress = True
            else:
                remaining.append((alias, target))
        if not progress:
            break
        unresolved = remaining
    return methods


def _javascript_object_methods(text: str, object_name: str) \
        -> dict[str, Signature]:
    masked = _mask_js(text)
    return {
        match.group(2): _javascript_signature_at_paren(
            masked, match.end() - 1)
        for match in OBJECT_METHOD_HEAD_RE.finditer(masked)
        if match.group(1) == object_name
    }


def _javascript_object_members(text: str, object_name: str) -> set[str]:
    masked = _mask_js(text)
    assigned = {
        match.group(2)
        for match in OBJECT_ASSIGN_RE.finditer(masked)
        if match.group(1) == object_name
    }
    return assigned - set(_javascript_object_methods(text, object_name))


def _javascript_shared_object_methods(
        text: str, object_name: str) -> dict[str, Signature]:
    """Methods on either `object.method` or `Constructor.prototype.method`."""
    methods = _javascript_object_methods(text, object_name)
    methods.update(_javascript_methods(text, object_name))
    return methods


def _top_level_matches(
        text: str, pattern: re.Pattern[str],
        mask_strings: bool = True) -> list[re.Match[str]]:
    """Return pattern matches outside nested (), [] and {} blocks."""
    masked = _mask_js(text, mask_strings=mask_strings)
    matches = list(pattern.finditer(masked))
    if not matches:
        return []
    result: list[re.Match[str]] = []
    target = 0
    depths = {"(": 0, "[": 0, "{": 0}
    closes = {")": "(", "]": "[", "}": "{"}
    for index, char in enumerate(masked):
        while target < len(matches) and matches[target].start() == index:
            if not any(depths.values()):
                result.append(matches[target])
            target += 1
        if char in depths:
            depths[char] += 1
        elif char in closes and depths[closes[char]]:
            depths[closes[char]] -= 1
    return result


def _object_literal_shape(
        block: str) -> tuple[dict[str, Signature], set[str]]:
    """Top-level function-valued methods and non-function fields."""
    method_matches = _top_level_matches(
        block, OBJECT_LITERAL_FUNCTION_ENTRY_RE)
    methods = {
        match.group(1): _javascript_signature_at_paren(
            block, match.end() - 1)
        for match in method_matches
    }
    entries = {
        match.group(1)
        for match in _top_level_matches(block, OBJECT_LITERAL_ENTRY_RE)
    }
    return methods, entries - set(methods)


def _returned_object_shape(
        text: str, export_name: str) -> tuple[dict[str, Signature], set[str]]:
    body = _exported_function_body(text, export_name)
    returns = _top_level_matches(body, RETURN_OBJECT_HEAD_RE)
    if len(returns) != 1:
        raise ValueError(
            "exported function %s has %d top-level returned object literals" %
            (export_name, len(returns)))
    block, _ = _balanced_content(
        body, returns[0].end() - 1, "{", "}")
    return _object_literal_shape(block)


def _assigned_object_literal(text: str, object_name: str) -> str:
    masked = _mask_js(text)
    matches = [
        match for match in OBJECT_LITERAL_ASSIGN_RE.finditer(masked)
        if match.group(1) == object_name
    ]
    if len(matches) != 1:
        raise ValueError(
            "object literal %s has %d source assignments" %
            (object_name, len(matches)))
    return _balanced_content(
        text, matches[0].end() - 1, "{", "}")[0]


def _proxy_handler_shape(
        text: str, object_name: str) \
        -> tuple[dict[str, Signature], set[str]]:
    """Named values exposed by explicit string branches in handler.get()."""
    block = _assigned_object_literal(text, object_name)
    get_matches = [
        match for match in _top_level_matches(
            block, OBJECT_LITERAL_FUNCTION_ENTRY_RE)
        if match.group(1) == "get"
    ]
    if len(get_matches) != 1:
        raise ValueError(
            "proxy handler %s has %d get methods" %
            (object_name, len(get_matches)))
    get_match = get_matches[0]
    parameters, close_index = _balanced_content(
        block, get_match.end() - 1, "(", ")")
    parameter_names = [item.strip() for item in _split_parameters(parameters)]
    if not parameter_names:
        raise ValueError("proxy handler %s.get has no name parameter" %
                         object_name)
    discriminator = parameter_names[-1]
    if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", discriminator):
        raise ValueError(
            "proxy handler %s.get has unsupported name parameter %s" %
            (object_name, discriminator))
    body_open = block.find("{", close_index + 1)
    get_body, _ = _balanced_content(block, body_open, "{", "}")
    branches = [
        match for match in EXPLICIT_NAME_BRANCH_RE.finditer(
            _mask_js(get_body, mask_strings=False))
        if match.group(1) == discriminator
    ]
    methods: dict[str, Signature] = {}
    members: set[str] = set()
    for index, branch in enumerate(branches):
        end = branches[index + 1].start() \
            if index + 1 < len(branches) else len(get_body)
        segment = get_body[branch.end():end]
        function_return = re.search(r"\breturn\s+function\s*\(", segment)
        if function_return is not None:
            methods[branch.group(2)] = _javascript_signature_at_paren(
                segment, function_return.end() - 1)
        else:
            # An explicit `return undefined` branch is still a named member of
            # the proxy surface, and a source-definite one: dropping it meant
            # the accurate declaration was rejected as a phantom while leaving
            # the property typed only through the index signature.
            members.add(branch.group(2))
    return methods, members


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
        for candidate in EXPORT_FUNCTION_HEAD_RE.finditer(text):
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


def _javascript_prototype_defined_properties(
        text: str, type_name: str) -> set[str]:
    """Top-level keys from balanced defineProperties(Type.prototype, {...})."""
    names: set[str] = set()
    masked = _mask_js(text)
    for header in DEFINE_PROPERTIES_PROTOTYPE_HEAD_RE.finditer(masked):
        if header.group(1) != type_name:
            continue
        open_index = header.end() - 1
        block, _ = _balanced_content(masked, open_index, "{", "}")
        names.update(
            entry.group(1)
            for entry in DEFINE_PROPERTY_ENTRY_RE.finditer(block)
        )
    return names


def _javascript_members(text: str, type_name: str) -> set[str]:
    """Every non-method member the constructor gives an instance: plain
    top-level `this.x = ...` assignments (excluding anything inside a
    nested function literal -- an event-handler closure's own `this.y =`
    is not an instance field of the type whose constructor hosts it) plus
    every Object.defineProperties() entry, regardless of its shape."""
    body = _constructor_body(text, type_name)
    fields: set[str] = set()
    if body is not None:
        masked_body = _mask_nested_functions(_mask_js(body))
        fields.update(THIS_FIELD_RE.findall(masked_body))
        fields.update(_javascript_defined_properties(body))
    fields.update(_javascript_prototype_defined_properties(text, type_name))
    return fields - {"__proto__"}


def _exported_function_members(text: str, export_name: str) -> set[str]:
    body = _exported_function_body(text, export_name)
    masked_body = _mask_nested_functions(_mask_js(body))
    return set(THIS_FIELD_RE.findall(masked_body)) - {"__proto__"}


def _exported_instance_references(text: str, export_name: str) -> set[str]:
    """All members referenced through `this` and direct aliases to `this`.

    Unlike constructor-field extraction, this intentionally retains nested
    callbacks: public callback slots are often only read there.
    """
    body = _exported_function_body(text, export_name)
    masked = _mask_js(body)
    receivers = {"this"} | set(THIS_ALIAS_RE.findall(masked))
    members: set[str] = set()
    for receiver in receivers:
        pattern = re.compile(
            r"(?<![.\w])%s\.([A-Za-z_$][A-Za-z0-9_$]*)\b" %
            re.escape(receiver))
        members.update(pattern.findall(masked))
    return members - {"__proto__"}


def _javascript_exact_aliases(
        text: str) -> dict[str, tuple[str, str]]:
    masked = _mask_js(text, mask_strings=False)
    requires = {
        match.group(1): match.group(2)
        for match in ALIAS_RE.finditer(masked)
    }
    aliases: dict[str, tuple[str, str]] = {}
    for match in ALIAS_EXPORT_RE.finditer(masked):
        module = requires.get(match.group(2))
        if module is not None:
            aliases[match.group(1)] = (module, match.group(3))
    for match in DIRECT_REQUIRE_ALIAS_EXPORT_RE.finditer(masked):
        aliases[match.group(1)] = (match.group(2), match.group(3))
    return aliases


def _declared_type_kind(text: str, name: str) -> str | None:
    def declares(pattern: re.Pattern[str]) -> bool:
        return any(match.group(1) == name for match in pattern.finditer(text))

    if declares(DECL_INTERFACE_RE):
        return "interface"
    if declares(DECL_CLASS_RE):
        return "class"
    if declares(DECL_FUNCTION_RE):
        return "function"
    if name in DECL_VALUE_RE.findall(text):
        return "value"
    return None


def _native_metadata_functions(module_name: str) -> dict[str, Signature]:
    metadata = _load_metadata()
    for module in metadata.get("js", {}).get("modules", []):
        if module.get("name") != module_name:
            continue
        if module.get("kind") != "native":
            raise ValueError("%s is not native metadata" % module_name)
        return {
            function["name"]: Signature(
                function["nargs"],
                function["nargs"],
                bool(function.get("variadic")),
            )
            for function in module.get("functions", [])
        }
    raise ValueError("native metadata module %s not found" % module_name)


def _javascript_wrapper_target(
        text: str, export_name: str) -> tuple[str, str] | None:
    try:
        body = _exported_function_body(text, export_name)
    except ValueError:
        inherited = REEXPORT_PROTO_RE.search(
            _mask_js(text, mask_strings=False))
        if inherited is None:
            return None
        return inherited.group(1), export_name
    masked = _mask_js(body, mask_strings=False)
    match = re.search(
        r"\brequire\(\s*['\"]([^'\"]+)['\"]\s*\)\s*\."
        r"([A-Za-z_$][A-Za-z0-9_$]*)\s*\(",
        masked,
    )
    if match is None:
        return None
    return match.group(1), match.group(2)


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


# Any redeclaration of the identifier shadows an earlier native alias, not
# only another `require("native/...")`. Tracking only native rebindings let a
# later `require("fs")` -- or any other assignment -- leave the earlier native
# binding effective, so a retargeted wrapper kept borrowing its sibling's.
ANY_ALIAS_REBIND_RE = re.compile(
    r"(?:var|let|const)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=")


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
    # An alias binds only from its own declaration onward. Collecting them
    # file-wide let one wrapper's `var fs = require('native/fs')` vouch for a
    # sibling wrapper that had been retargeted to a different module, so the
    # registration protected nothing for independently scoped functions.
    binds = [(match.start(), match.group(1))
             for match in alias_re.finditer(masked)]
    rebinds = [(match.start(), match.group(1))
               for match in ANY_ALIAS_REBIND_RE.finditer(masked)]
    sites: list[tuple[str, int]] = []
    for match in call_re.finditer(masked):
        receiver = match.group(1)
        if receiver is None:
            sites.append((match.group(2), match.end() - 1))
            continue
        prior = [pos for pos, name in binds
                 if name == receiver and pos < match.start()]
        if not prior:
            continue
        shadow = [pos for pos, name in rebinds
                  if name == receiver and prior[-1] < pos < match.start()]
        if shadow:
            continue
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


ES_SET_MEMBER_RE = re.compile(
    r'\bes_set_[a-z]+\s*\(\s*ctx\s*,\s*-1\s*,\s*"([A-Za-z_][A-Za-z0-9_]*)"')


def _plain_c_function_body(text: str, symbol: str) -> str | None:
    """Body of a C function defined as `symbol(...)` at column zero."""
    definition = re.search(r"^%s\s*\(" % re.escape(symbol), text, re.MULTILINE)
    if definition is None:
        return None
    open_index = text.find("{", definition.end())
    if open_index < 0:
        return None
    try:
        body, _ = _balanced_content(text, open_index, "{", "}")
    except ValueError:
        return None
    return body


def _native_object_members(native_c: Path, builder: str) -> set[str] | None:
    """Member names `builder` attaches to the object at stack index -1.

    Scoped to that one function on purpose: a file-wide scan would attribute
    an unrelated object's properties to this type, and would keep a removed
    property green as long as some other function in the same file still set a
    property of that name. Same failure the arity-floor check had.
    """
    body = _plain_c_function_body(_read(native_c), builder)
    if body is None:
        return None
    return set(ES_SET_MEMBER_RE.findall(_mask_c(body)))


C_COMMENT_RE = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)


def _mask_c(text: str) -> str:
    """Blank out C comments, preserving offsets and line structure.

    The member scans below take a commented-out statement as runtime evidence
    otherwise: deleting a real `duk_put_prop_string(...)` while leaving its
    text in a comment kept the property certified.
    """
    return C_COMMENT_RE.sub(
        lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def _native_c_function_body(text: str, native_name: str) -> str | None:
    """The C body registered for `native_name` in a duk function table."""
    entry = re.search(
        r'\{\s*"%s"\s*,\s*([A-Za-z_][A-Za-z0-9_]*)\s*,' %
        re.escape(native_name), text)
    if entry is None:
        return None
    symbol = entry.group(1)
    definition = re.search(
        r"^%s\s*\(duk_context\s*\*\s*ctx\s*\)" % re.escape(symbol),
        text, re.MULTILINE)
    if definition is None:
        return None
    open_index = text.find("{", definition.end())
    if open_index < 0:
        return None
    try:
        body, _ = _balanced_content(text, open_index, "{", "}")
    except ValueError:
        return None
    return body


NATIVE_PUT_PROP_RE = re.compile(
    r'\bduk_put_prop_string\s*\(\s*ctx\s*,\s*-?\w+\s*,\s*"([A-Za-z_]'
    r'[A-Za-z0-9_]*)"')


def _check_native_returned_object(
        errors: list[str], spec: "ModuleSpec", declaration: str,
        shape: MemberShape) -> None:
    """Compare an interface against the object its native builder creates."""
    if shape.native is None:
        raise ValueError("native returned object requires a native source")
    native_name = shape.native.function
    native_c = shape.native.path
    body = _native_c_function_body(_read(native_c), native_name)
    if body is None:
        errors.append(
            "%s: %s claims to mirror %s, but %s registers no table entry "
            "of that name" %
            (spec.name, shape.type_name, native_name, native_c.name))
        return
    source = set(NATIVE_PUT_PROP_RE.findall(_mask_c(body)))
    try:
        declared = _declared_members(declaration, shape.type_name)
    except ValueError as error:
        errors.append("%s: %s" % (spec.name, error))
        return
    _compare_name_sets(errors, spec.name, shape.type_name + ".",
                       source, declared)


WRAPPER_NATIVE_CALL_RE = re.compile(
    r"\b([A-Za-z_$][A-Za-z0-9_$]*)\.([A-Za-z_$][A-Za-z0-9_$]*)"
    r"\s*(?:\.\s*(?:apply|call)\s*)?\(")


def _wrapper_native_calls(javascript: str, type_name: str, method: str,
                          native_module: str | None = None
                          ) -> set[str] | None:
    """Native member names the JS body of `type.method` invokes.

    When `native_module` is given, only calls whose receiver is an alias bound
    to `require("native/<module>")` count. Without that the member name alone
    would satisfy the check, so re-pointing the module behind the alias --
    native/sqlite to native/nope -- would keep a floor certified against the
    old C function.
    """
    head = re.search(
        r"\b%s\.prototype\.%s\s*=\s*function" %
        (re.escape(type_name), re.escape(method)), javascript)
    if head is None:
        return None
    open_index = javascript.find("{", head.end())
    if open_index < 0:
        return None
    try:
        body, _ = _balanced_content(javascript, open_index, "{", "}")
    except ValueError:
        return None
    # Mask first: a commented-out copy of the old call would otherwise count
    # as arity evidence while the wrapper really invokes something else.
    body = _mask_js(body, mask_strings=False)
    calls = WRAPPER_NATIVE_CALL_RE.findall(body)
    if native_module is None:
        return {member for _receiver, member in calls}
    # Resolve the receiver against the nearest binding visible from the
    # wrapper body: a local `var sqlite = require('native/fs')` inside the
    # method shadows the file's top-level alias, and accepting any matching
    # alias anywhere in the file certified the floor against the wrong module.
    # A formal parameter shadows an outer alias exactly as a local declaration
    # does, so `function(sqlite)` must not inherit the file's `native/sqlite`
    # binding. Any lexical declaration of the name counts, whether or not it
    # binds a require.
    try:
        parameters = {
            name.strip()
            for name in _split_parameters(_balanced_content(
                javascript, javascript.find("(", head.end()), "(", ")")[0])
            if name.strip()
        }
    except ValueError:
        parameters = set()
    scopes = body + "\n" + javascript[:open_index]
    bound: dict[str, str | None] = {name: None for name in parameters}
    for match in re.finditer(
            r"\b(?:var|let|const|function)\s+([A-Za-z_$][A-Za-z0-9_$]*)"
            r"\s*(?:=\s*require\(\s*['\"]([^'\"]+)['\"]\s*\))?", scopes):
        bound.setdefault(match.group(1), match.group(2))
    wanted = "native/%s" % native_module
    return {member for receiver, member in calls
            if bound.get(receiver) == wanted}


ANY_REQUIRE_RE = re.compile(
    r"require\(\s*['\"]([^'\"]+)['\"]\s*\)")


def _consumed_object_members(javascript: str, export_name: str,
                             parameter: str) -> set[str] | None:
    """Property names an exported function reads off one of its parameters."""
    try:
        body = _exported_function_body(javascript, export_name)
    except ValueError:
        return None
    masked = _mask_js(body, mask_strings=False)
    return set(re.findall(
        r"\b%s\s*\.\s*([A-Za-z_$][A-Za-z0-9_$]*)" % re.escape(parameter),
        masked))


def _check_consumed_object(
        errors: list[str], spec: "ModuleSpec", declaration: str,
        javascript: str, shape: MemberShape) -> None:
    if shape.parameter is None:
        raise ValueError("consumed object requires a parameter name")
    source = _consumed_object_members(
        javascript, shape.source_name, shape.parameter)
    if source is None:
        errors.append(
            "%s: %s claims to describe %s's %s parameter, but no such "
            "exported function was found" %
            (spec.name, shape.type_name, shape.source_name, shape.parameter))
        return
    try:
        declared = _declared_members(
            declaration, shape.type_name, include_callbacks=True)
    except ValueError as error:
        errors.append("%s: %s" % (spec.name, error))
        return
    _compare_name_sets(errors, spec.name, shape.type_name + ".",
                       source - set(shape.excluded), declared)


def _check_native_dependencies(
        errors: list[str], spec: "ModuleSpec", native_modules: set[str],
        known_modules: set[str]) -> None:
    """Every `require("native/X")` in a module must name a real native module.

    Registering one native table per spec is not enough on its own: a wrapper
    can be retargeted to a module that does not exist and the table check then
    simply sees fewer calls for the module it was configured with, which is
    silently green. Checking the dependency names catches that directly, and
    covers the modules whose native calls no spec had registered at all.
    """
    for target in sorted(set(ANY_REQUIRE_RE.findall(
            _mask_js(_read(spec.javascript), mask_strings=False)))):
        if target.startswith("."):
            continue                      # relative sibling, e.g. https -> ./http
        if target == spec.name:
            errors.append(
                "%s: requires itself; a wrapper retargeted this way keeps "
                "type-checking while recursing at runtime" % spec.name)
            continue
        if target.startswith("native/"):
            if target[len("native/"):] not in native_modules:
                errors.append(
                    "%s: requires %s, which is not a native module in "
                    "generated/movian-metadata.json" % (spec.name, target))
                continue
        elif target not in known_modules:
            errors.append(
                "%s: requires %s, which is not a module in "
                "generated/movian-metadata.json" % (spec.name, target))
            continue
        if spec.requires and target not in spec.requires:
            errors.append(
                "%s: requires %s, which is not among its declared "
                "dependencies (%s)" %
                (spec.name, target, ", ".join(sorted(spec.requires))))


def _check_native_arity_floor(
        errors: list[str], spec: "ModuleSpec") -> dict[tuple[str, str], int]:
    """Validate each declared native arity floor against the C source.

    The guard must live in the body of the function the native table actually
    registers for that name -- a file-wide search is not enough, because an
    unrelated `argc < N` elsewhere in the same file would rubber-stamp any
    claim (es_io.c carries one, which silently validated a DB.query entry
    pointed at the wrong file until this was tightened).
    """
    floors: dict[tuple[str, str], int] = {}
    javascript = _read(spec.javascript)
    configured_floors = (
        (shape.type_name, floor)
        for shape in spec.member_shapes
        for floor in shape.arity_floors
    )
    for type_name, configured in configured_floors:
        method = configured.method
        native_name = configured.function
        floor = configured.minimum
        native_c = configured.path
        # The configured name is a claim about the wrapper, so check it: find
        # the method body and require it to actually call that native function.
        # Otherwise retargeting `sqlite.query.apply(...)` to another native
        # would leave the floor certified against the old callee.
        called = _wrapper_native_calls(
            javascript, type_name, method, spec.native_module)
        if called is None:
            errors.append(
                "%s: %s.%s claims a native arity floor, but no wrapper body "
                "for it was found in %s" %
                (spec.name, type_name, method, spec.javascript.name))
            continue
        if native_name not in called:
            errors.append(
                "%s: %s.%s claims native callee %s, but its wrapper calls %s" %
                (spec.name, type_name, method, native_name,
                 ", ".join(sorted(called)) or "nothing native"))
            continue
        body = _native_c_function_body(_read(native_c), native_name)
        if body is None:
            errors.append(
                "%s: %s.%s claims a native arity floor, but %s registers no "
                "table entry named %s" %
                (spec.name, type_name, method, native_c.name, native_name))
            continue
        if not re.search(r"\bargc\s*<\s*%d\b" % floor, body):
            errors.append(
                "%s: %s.%s claims a native arity floor of %d, but the body "
                "registered for %s in %s has no `argc < %d` guard" %
                (spec.name, type_name, method, floor, native_name,
                 native_c.name, floor))
            continue
        floors[(type_name, method)] = floor
    return floors


def _check_call_shapes(
        errors: list[str], module_name: str, owner: str | None,
        source_methods: dict[str, Signature],
        declared_methods: dict[str, list[Signature]],
        arity_floors: dict[str, int] | None = None) -> None:
    prefix = "" if owner is None else owner + "."
    for method, source_signature in sorted(source_methods.items()):
        signatures = declared_methods.get(method)
        if signatures is None:
            continue
        floor = (arity_floors or {}).get(method)
        if floor is not None and source_signature.variadic:
            # The wrapper unshifts its own handle before calling native, so a
            # native floor of N leaves N-1 arguments required on the JS side.
            source_signature = replace(
                source_signature,
                required=max(source_signature.required, floor - 1),
                total=max(source_signature.total, floor - 1))
        if not _has_call_shape(source_signature, signatures):
            errors.append(
                "%s: %s%s call shape is %s source args vs %s declared" %
                (module_name, prefix, method,
                 _render_signature_shape(source_signature),
                 ", ".join(_render_signature_shape(item)
                           for item in signatures)))


def _check_declared_object_shape(
        errors: list[str], module_name: str, declaration: str,
        type_name: str, source_methods: dict[str, Signature],
        source_members: set[str],
        arity_floors: dict[str, int] | None = None) -> None:
    try:
        declared_methods = _declared_methods(declaration, type_name)
        declared_members = _declared_members(declaration, type_name)
    except ValueError as error:
        errors.append("%s: %s" % (module_name, error))
        return
    _compare_name_sets(
        errors, module_name, type_name + ".",
        set(source_methods), set(declared_methods))
    _compare_name_sets(
        errors, module_name, type_name + ".",
        source_members, declared_members)
    _check_call_shapes(
        errors, module_name, type_name, source_methods, declared_methods,
        arity_floors)


def _check_member_shape(
        errors: list[str], spec: ModuleSpec, shape: MemberShape,
        declaration: str, javascript: str,
        declared_kinds: dict[str, str],
        js_callables: dict[str, Signature],
        arity_floors: dict[tuple[str, str], int]) -> None:
    type_name = shape.type_name
    source_name = shape.source_name

    if shape.source is MemberSource.PROTOTYPE:
        if source_name in js_callables and \
                declared_kinds.get(type_name) != "class":
            errors.append(
                "%s: %s source constructor must declare an exported class" %
                (spec.name, type_name))
        source_methods = _javascript_methods(javascript, source_name)
        try:
            declared_methods = _declared_methods(declaration, type_name)
        except ValueError as error:
            errors.append("%s: %s" % (spec.name, error))
            return
        _compare_name_sets(errors, spec.name, type_name + ".",
                           set(source_methods), set(declared_methods))
        _check_call_shapes(
            errors, spec.name, type_name, source_methods, declared_methods,
            {method: floor
             for (owner, method), floor in arity_floors.items()
             if owner == type_name})

        # Most constructor-backed types are phantom-only because their
        # instances also carry private bookkeeping fields. Exact shapes opt in
        # and name any intentional exclusions on their descriptor.
        source_members = _javascript_members(javascript, source_name)
        source_members -= set(shape.excluded)
        declared_members = _declared_members(declaration, type_name)
        if shape.exact_members:
            _compare_name_sets(
                errors, spec.name, type_name + ".",
                source_members, declared_members)
        else:
            for member in sorted(declared_members - source_members):
                errors.append("%s: phantom declaration %s.%s" %
                              (spec.name, type_name, member))
        return

    if shape.source is MemberSource.SHARED_OBJECT:
        source_methods = _javascript_shared_object_methods(
            javascript, source_name)
        try:
            declared_methods = _declared_methods(declaration, type_name)
        except ValueError as error:
            errors.append("%s: %s" % (spec.name, error))
            return
        _compare_name_sets(errors, spec.name, type_name + ".",
                           set(source_methods), set(declared_methods))
        _check_call_shapes(
            errors, spec.name, type_name, source_methods, declared_methods)
        return

    if shape.source is MemberSource.OBJECT_CONSTRUCTOR:
        body = _constructor_body(javascript, source_name)
        if body is None:
            errors.append(
                "%s: object constructor %s not found for %s" %
                (spec.name, source_name, type_name))
            return
        source_members = _javascript_defined_properties(body)
        declared_members = _declared_members(declaration, type_name)
        _compare_name_sets(
            errors, spec.name, type_name + ".",
            source_members, declared_members)
        return

    if shape.source is MemberSource.EXPORTED_INSTANCE:
        source_members = _exported_function_members(
            javascript, source_name) - set(shape.excluded)
        declared_members = _declared_members(declaration, type_name)
        _compare_name_sets(
            errors, spec.name, type_name + ".",
            source_members, declared_members)
        return

    if shape.source is MemberSource.INSTANCE_REFERENCES:
        source_members = _exported_instance_references(
            javascript, source_name) - set(shape.excluded)
        declared_members = _declared_members(
            declaration, type_name, include_callbacks=True)
        _compare_name_sets(
            errors, spec.name, type_name + ".",
            source_members, declared_members)
        return

    if shape.source is MemberSource.RETURNED_OBJECT:
        try:
            source_methods, source_members = _returned_object_shape(
                javascript, source_name)
        except ValueError as error:
            errors.append("%s: %s" % (spec.name, error))
            return
        _check_declared_object_shape(
            errors, spec.name, declaration, type_name,
            source_methods, source_members)
        return

    if shape.source is MemberSource.LOCAL_CONSTRUCTOR:
        kind = _declared_type_kind(declaration, type_name)
        if kind != "interface":
            errors.append(
                "%s: local constructor %s must map to interface %s, not %s" %
                (spec.name, source_name, type_name, kind or "missing"))
        source_methods = _javascript_methods(javascript, source_name)
        all_source_members = _javascript_members(javascript, source_name)
        stale_exclusions = set(shape.excluded) - all_source_members
        for member in sorted(stale_exclusions):
            errors.append(
                "%s: private exclusion %s.%s is absent from source" %
                (spec.name, source_name, member))
        _check_declared_object_shape(
            errors, spec.name, declaration, type_name,
            source_methods, all_source_members - set(shape.excluded),
            {method: floor
             for (owner, method), floor in arity_floors.items()
             if owner == type_name})
        return

    if shape.source is MemberSource.PROXY_HANDLER:
        try:
            source_methods, source_members = _proxy_handler_shape(
                javascript, source_name)
        except ValueError as error:
            errors.append("%s: %s" % (spec.name, error))
            return
        _check_declared_object_shape(
            errors, spec.name, declaration, type_name,
            source_methods, source_members)
        return

    if shape.source is MemberSource.LOCAL_OBJECT_LITERAL:
        try:
            methods, members = _object_literal_shape(
                _assigned_object_literal(javascript, source_name))
        except ValueError as error:
            errors.append("%s: %s" % (spec.name, error))
            return
        _check_declared_object_shape(
            errors, spec.name, declaration, type_name, methods, members)
        return

    if shape.source is MemberSource.LOCAL_OBJECT:
        inherited: set[str] = set()
        if shape.inherited is not None:
            inherited_members = _native_object_members(
                shape.inherited.path, shape.inherited.function)
            if inherited_members is None:
                errors.append(
                    "%s: %s claims to inherit from %s, but %s defines no "
                    "such function" %
                    (spec.name, type_name, shape.inherited.function,
                     shape.inherited.path.name))
            else:
                inherited = inherited_members
        _check_declared_object_shape(
            errors, spec.name, declaration, type_name,
            _javascript_object_methods(javascript, source_name),
            _javascript_object_members(javascript, source_name) | inherited)
        return

    if shape.source is MemberSource.CONSUMED_OBJECT:
        _check_consumed_object(
            errors, spec, declaration, javascript, shape)
        return

    if shape.source is MemberSource.NATIVE_RETURNED_OBJECT:
        _check_native_returned_object(errors, spec, declaration, shape)
        return

    raise ValueError("unsupported member source: %s" % shape.source)


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

    if spec.forbid_nested_types:
        nested_types = set(DECL_INTERFACE_RE.findall(declaration))
        nested_types.update(DECL_CLASS_RE.findall(declaration))
        for type_name in sorted(nested_types):
            errors.append(
                "%s: phantom nested declaration surface %s" %
                (spec.name, type_name))

    # Signature.required is computed (see _is_required) but deliberately
    # NOT compared here: this codebase's declarations routinely mark a
    # trailing JS parameter optional in the .d.ts (e.g. Item.prototype.
    # addOptAction's `subtype`) even though the JS function itself has no
    # `= default` -- JS callers can omit a trailing argument regardless,
    # and several accepted fixtures already rely on that looser contract.
    # A strict required-vs-required check was tried while fixing this
    # file and produced false positives against those already-accepted
    # signatures, so fixed .total (the argument COUNT) is enforced. For
    # source-inferred `arguments` rest signatures, variadic shape and the
    # required fixed prefix are also exact.
    _check_call_shapes(
        errors, spec.name, None, js_callables, declared_signatures)

    arity_floors = _check_native_arity_floor(errors, spec)

    for shape in spec.member_shapes:
        _check_member_shape(
            errors, spec, shape, declaration, javascript,
            declared_kinds, js_callables, arity_floors)

    actual_aliases = _javascript_exact_aliases(javascript)
    alias_links = tuple(
        link for link in spec.export_links
        if link.kind is ExportLinkKind.ALIAS)
    if spec.audit_runtime_aliases:
        native_aliases = {
            name: target for name, target in actual_aliases.items()
            if target[0].startswith("native/")
        }
        registered_aliases = {
            link.export_name: (link.required_module, link.required_member)
            for link in alias_links
        }
        for name in sorted(set(native_aliases) - set(registered_aliases)):
            module, member = native_aliases[name]
            errors.append(
                "%s: unregistered native alias export %s -> %s.%s" %
                (spec.name, name, module, member))
        for name in sorted(set(registered_aliases) - set(native_aliases)):
            errors.append(
                "%s: registered native alias %s is missing from source" %
                (spec.name, name))

        # No module in this set re-exports through `exports.__proto__`
        # (movian/prop does, but it is a #135 module and does not opt into
        # this audit). Any target found here is therefore unregistered: fail
        # loudly rather than carry a declaration hook no spec uses.
        reexport_targets = set(REEXPORT_PROTO_RE.findall(
            _mask_js(javascript, mask_strings=False)))
        for target in sorted(reexport_targets):
            errors.append(
                "%s: unexpected re-export target %s" % (spec.name, target))

    for link in alias_links:
        export_name = link.export_name
        required_module = link.required_module
        required_member = link.required_member
        expected = (required_module, required_member)
        actual = actual_aliases.get(export_name)
        if actual != expected:
            errors.append(
                "%s: %s alias target is %s vs required %s.%s" %
                (spec.name, export_name,
                 "<missing>" if actual is None else "%s.%s" % actual,
                 required_module, required_member))
            continue
        native_functions = _native_metadata_functions(required_module)
        native_signature = native_functions.get(required_member)
        if native_signature is None:
            errors.append(
                "%s: %s alias target %s.%s is absent from native metadata" %
                (spec.name, export_name, required_module, required_member))
            continue
        if declared_kinds.get(export_name) != "function":
            errors.append(
                "%s: %s native alias must declare a function" %
                (spec.name, export_name))
            continue
        signatures = declared_signatures.get(export_name, [])
        if not _has_call_shape(native_signature, signatures):
            errors.append(
                "%s: %s native alias shape is %s args vs %s declared" %
                (spec.name, export_name,
                 _render_signature_shape(native_signature),
                 ", ".join(_render_signature_shape(item)
                           for item in signatures) or "<missing>"))

    for link in spec.export_links:
        if link.kind is not ExportLinkKind.WRAPPER:
            continue
        export_name = link.export_name
        required_module = link.required_module
        required_member = link.required_member
        actual = _javascript_wrapper_target(javascript, export_name)
        expected = (required_module, required_member)
        if actual != expected:
            errors.append(
                "%s: %s wrapper target is %s vs required %s.%s" %
                (spec.name, export_name,
                 "<missing>" if actual is None else "%s.%s" % actual,
                 required_module, required_member))
        if declared_kinds.get(export_name) != "function":
            errors.append(
                "%s: %s wrapper must declare a function" %
                (spec.name, export_name))

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
    try:
        all_modules = _load_metadata()["js"]["modules"]
        native_modules = {
            module["name"][len("native/"):]
            for module in all_modules if module.get("kind") == "native"
        }
        known_modules = {module["name"] for module in all_modules}
    except (OSError, ValueError, KeyError):
        native_modules = set()
        known_modules = set()
    for spec in MODULES:
        if native_modules:
            _check_native_dependencies(
                errors, spec, native_modules, known_modules)
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


def _generated_tsc_command(tsc: str, fixture: Path) -> list[str]:
    inputs = [GENERATED_DTS, fixture]
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


def _run_generated_tsc(
        tsc: str, fixture: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _generated_tsc_command(tsc, fixture),
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )


def _expected_diagnostics(
        fixture: Path = NEGATIVE_FIXTURE) -> set[tuple[str, int, int]]:
    expected: set[tuple[str, int, int]] = set()
    for line_number, line in enumerate(_read(fixture).splitlines(), 1):
        for code in EXPECTED_DIAGNOSTIC_RE.findall(line):
            expected.add((fixture.name, line_number, int(code)))
    if not expected:
        raise ValueError("%s has no EXPECT_TS markers" % fixture.name)
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


# Minimum call sites the positive fixture must exercise. Without this the gate
# passes on a fixture that imports nothing -- the failure mode the rest of this
# checker keeps rediscovering. Named modules, not a count, so deleting the
# `movian/prop` block cannot be masked by adding lines elsewhere.
FIXTURE_IMPORT_RE = re.compile(
    r"^\s*import\s+\w+\s*=\s*require\(\s*['\"]([^'\"]+)['\"]\s*\)",
    re.MULTILINE)

GENERATED_FIXTURE_REQUIRED_MODULES = (
    "movian/videoscrobbler",
    "movian/prop",
    "movian/xmlrpc",
    "showtime/prop",
)


def check_generated_typescript(tsc: str) -> list[str]:
    """Type-check `generated/movian-api.d.ts` -- the artifact plugins import.

    `gen.py --check` proves only that the file matches what the generator
    emits; it cannot tell a correct emission from a wrong one. This does.
    """
    errors: list[str] = []

    # Parsed from the import statements, not grepped from the raw text: the
    # first version matched anywhere in the file, so a fixture whose entire
    # content was a COMMENT naming the modules satisfied the floor. A guard
    # against an emptied fixture that an empty fixture passes is worse than
    # no guard, because it reads as covered.
    fixture_text = _read(GENERATED_POSITIVE_FIXTURE)
    imported = set(FIXTURE_IMPORT_RE.findall(fixture_text))
    absent = [name for name in GENERATED_FIXTURE_REQUIRED_MODULES
              if name not in imported]
    if absent:
        errors.append("generated positive fixture no longer imports %s"
                      % ", ".join(absent))

    positive = _run_generated_tsc(tsc, GENERATED_POSITIVE_FIXTURE)
    if positive.returncode != 0:
        errors.append("generated positive fixture failed:\n%s"
                      % positive.stdout.rstrip())

    negative = _run_generated_tsc(tsc, GENERATED_NEGATIVE_FIXTURE)
    expected = _expected_diagnostics(GENERATED_NEGATIVE_FIXTURE)
    actual = _actual_diagnostics(negative.stdout)
    if negative.returncode == 0:
        errors.append("generated negative fixture unexpectedly passed")
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        if missing:
            errors.append(
                "generated negative fixture missing diagnostics: %s" % missing)
        if extra:
            errors.append(
                "generated negative fixture extra diagnostics: %s\n%s"
                % (extra, negative.stdout.rstrip()))
    return errors


def _load_metadata() -> dict:
    """Load the generated movian-metadata.json."""
    text = _read(METADATA_FILE)
    return json.loads(text)


def _check_commonjs_coverage() -> list[str]:
    """Check CommonJS module coverage using live metadata."""
    errors: list[str] = []
    all_modules = _load_metadata().get("js", {}).get("modules", [])
    commonjs_modules = {
        module["name"] for module in all_modules
        if module.get("kind") == "commonjs"
    }
    native_modules = {
        module["name"] for module in all_modules
        if module.get("kind") == "native"
    }
    accepted = {
        "movian/page",
        "movian/prop",
        "movian/http",
        "movian/settings",
        "movian/service",
        "movian/store",
    }
    # `accepted` records which modules landed in the earlier issues; it must
    # NOT narrow the audit. Subtracting it from both sides meant a module
    # accepted by (#135)/(#136) could vanish from the live metadata, or be
    # reclassified, and coverage still reported complete -- the audit only ever
    # policed the modules this issue added. Every CommonJS module in the
    # artifact is now required to have a fixture and a registration.
    target_modules = commonjs_modules
    registered_modules = {spec.name for spec in MODULES}
    fixture_modules: set[str] = set()
    for declaration in REFERENCE_DIR.glob("*.d.ts"):
        if declaration == PLUGIN_DECLARATION:
            continue
        stem = declaration.name[:-5]
        name = "movian/" + stem[len("movian-"):].replace("-", "/") \
            if stem.startswith("movian-") else stem
        fixture_modules.add(name)

    missing = target_modules - fixture_modules
    phantom = fixture_modules - target_modules
    registry_missing = target_modules - registered_modules
    registry_phantom = registered_modules - target_modules

    if missing:
        errors.append("missing %d: %s" %
                      (len(missing), ", ".join(sorted(missing))))
    if phantom:
        errors.append("phantom %d: %s" %
                      (len(phantom), ", ".join(sorted(phantom))))
    if registry_missing:
        errors.append("registry missing %d: %s" %
                      (len(registry_missing),
                       ", ".join(sorted(registry_missing))))
    if registry_phantom:
        errors.append("registry phantom %d: %s" %
                      (len(registry_phantom),
                       ", ".join(sorted(registry_phantom))))
    # The deferred-native count is reported, never asserted against a literal:
    # `generated/movian-metadata.json` is the binding inventory, and child C2
    # adding a native module must not redden this CommonJS coverage check.
    if not errors:
        print(
            "reference-dts: CommonJS coverage OK "
            "(missing 0, phantom 0, deferred-native %d)" %
            len(native_modules))

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check reference .d.ts calibration fixtures")
    parser.add_argument(
        "--commonjs", action="store_true",
        help="Check CommonJS module coverage")
    args = parser.parse_args()

    if args.commonjs:
        try:
            errors = _check_commonjs_coverage()
        except (OSError, ValueError) as error:
            print("reference-dts: %s: %s" % (type(error).__name__, error),
                  file=sys.stderr)
            return 1
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
        # Hard failure, not a skip. The generated-d.ts gate lives below this
        # point, so a host without tsc silently ran NONE of it while
        # `gen.py --check` still printed every status `ok` -- a guard that
        # disappears on the machines least likely to notice.
        print("reference-dts: tsc unavailable; the TypeScript fixtures are "
              "the only check on generated/movian-api.d.ts, so this is a "
              "failure rather than a skip", file=sys.stderr)
        return 1

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

    try:
        errors = check_generated_typescript(tsc)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print("reference-dts: generated-dts check failed to run: %s: %s" %
              (type(error).__name__, error), file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print("reference-dts: %s" % error, file=sys.stderr)
        return 1
    print("reference-dts: generated-dts positive fixture OK")
    print("reference-dts: generated-dts negative diagnostics OK (%d expected)"
          % len(_expected_diagnostics(GENERATED_NEGATIVE_FIXTURE)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
