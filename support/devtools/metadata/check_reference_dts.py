#!/usr/bin/env python3
"""Check the accepted movian/page, movian/prop and movian/http .d.ts fixtures.

The parser is intentionally limited to the declaration/source forms used by
these calibration fixtures. It is not a JavaScript, C, or TypeScript parser.
Python standard library only.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
METADATA_DIR = Path(__file__).resolve().parent
REFERENCE_DIR = METADATA_DIR / "tests" / "reference"
FIXTURE_DIR = METADATA_DIR / "tests" / "fixtures"


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


MODULES = (
    ModuleSpec(
        "movian/page",
        REFERENCE_DIR / "movian-page.d.ts",
        REPO_ROOT / "res" / "ecmascript" / "modules" / "movian" /
        "page.js",
        ("Item", "Page", "Route", "Searcher"),
    ),
    ModuleSpec(
        "movian/prop",
        REFERENCE_DIR / "movian-prop.d.ts",
        REPO_ROOT / "res" / "ecmascript" / "modules" / "movian" /
        "prop.js",
        (),
    ),
    ModuleSpec(
        "movian/http",
        REFERENCE_DIR / "movian-http.d.ts",
        REPO_ROOT / "res" / "ecmascript" / "modules" / "movian" /
        "http.js",
        ("HttpResponse",),
    ),
)

PROP_C = REPO_ROOT / "src" / "ecmascript" / "es_prop.c"
POSITIVE_FIXTURE = FIXTURE_DIR / "reference-positive.ts"
NEGATIVE_FIXTURE = FIXTURE_DIR / "reference-negative.ts"

EXPORT_ASSIGN_RE = re.compile(
    r"^\s*exports\.([A-Za-z_$][A-Za-z0-9_$]*)\s*=", re.MULTILINE)
EXPORT_FUNCTION_RE = re.compile(
    r"^\s*exports\.([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*"
    r"function\s*\(([^)]*)\)", re.MULTILINE)
EXPORT_ALIAS_RE = re.compile(
    r"^\s*exports\.([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*"
    r"([A-Za-z_$][A-Za-z0-9_$]*)\s*;", re.MULTILINE)
LOCAL_FUNCTION_RE = re.compile(
    r"^\s*function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(([^)]*)\)",
    re.MULTILINE)
PROTOTYPE_RE = re.compile(
    r"^\s*(?:exports\.)?([A-Za-z_$][A-Za-z0-9_$]*)\.prototype\."
    r"([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*function\s*\(([^)]*)\)",
    re.MULTILINE)
DECL_FUNCTION_RE = re.compile(
    r"^\s*export\s+function\s+([A-Za-z_$][A-Za-z0-9_$]*)"
    r"(?:\s*<[^;{]*?>)?\s*\(", re.MULTILINE)
DECL_VALUE_RE = re.compile(
    r"^\s*export\s+(?:const|var|let)\s+"
    r"([A-Za-z_$][A-Za-z0-9_$]*)\s*:", re.MULTILINE)
DECL_CLASS_RE = re.compile(
    r"^\s*(?:export\s+)?class\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\{",
    re.MULTILINE)
METHOD_RE = re.compile(
    r"^\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*\(", re.MULTILINE)
NATIVE_ENTRY_RE = re.compile(
    r'\{\s*"([^"]+)"\s*,\s*[A-Za-z_]\w*\s*,\s*(-?\d+)\s*\}')
DIAGNOSTIC_RE = re.compile(
    r"^(.*?)\((\d+),(\d+)\): error TS(\d+):", re.MULTILINE)
EXPECTED_DIAGNOSTIC_RE = re.compile(r"EXPECT_TS(\d+)")


def _read(path: Path) -> str:
    if not path.is_file():
        raise ValueError("required file is missing: %s" % path.relative_to(
            REPO_ROOT))
    return path.read_text(encoding="utf-8")


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


def _signature(parameters: str) -> Signature:
    parts = _split_parameters(parameters)
    required = sum(
        1 for part in parts
        if "?" not in part.split(":", 1)[0] and "=" not in part
    )
    return Signature(required, len(parts))


def _balanced_content(text: str, open_index: int,
                      opener: str, closer: str) -> tuple[str, int]:
    if open_index >= len(text) or text[open_index] != opener:
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
        r"^\s*(?:export\s+)?%s\s+%s(?:\s*<[^>{}]*>)?\s*\{" %
        (re.escape(kind), re.escape(name)), text, re.MULTILINE)
    if match is None:
        raise ValueError("missing %s %s declaration" % (kind, name))
    open_index = text.find("{", match.start())
    return _balanced_content(text, open_index, "{", "}")[0]


def _signatures_after_matches(text: str,
                              matches: list[re.Match[str]]) \
        -> dict[str, list[Signature]]:
    result: dict[str, list[Signature]] = {}
    for match in matches:
        name = match.group(1)
        open_index = text.find("(", match.start(), match.end() + 1)
        parameters, _ = _balanced_content(text, open_index, "(", ")")
        result.setdefault(name, []).append(_signature(parameters))
    return result


def _declared_runtime(text: str) \
        -> tuple[set[str], dict[str, list[Signature]], dict[str, str]]:
    function_matches = list(DECL_FUNCTION_RE.finditer(text))
    signatures = _signatures_after_matches(text, function_matches)
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
        constructor = _signatures_after_matches(block, constructor_matches)
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
    ]
    return _signatures_after_matches(block, matches)


def _javascript_exports(text: str) \
        -> tuple[set[str], dict[str, Signature]]:
    names = set(EXPORT_ASSIGN_RE.findall(text)) - {"__proto__"}
    callables = {
        name: _signature(parameters)
        for name, parameters in EXPORT_FUNCTION_RE.findall(text)
    }
    local_functions = {
        name: _signature(parameters)
        for name, parameters in LOCAL_FUNCTION_RE.findall(text)
    }
    for exported, local in EXPORT_ALIAS_RE.findall(text):
        if local in local_functions:
            callables[exported] = local_functions[local]
    return names, callables


def _javascript_methods(text: str, type_name: str) \
        -> dict[str, Signature]:
    return {
        method: _signature(parameters)
        for owner, method, parameters in PROTOTYPE_RE.findall(text)
        if owner == type_name
    }


def _native_prop_functions() -> dict[str, int]:
    text = _read(PROP_C)
    table_match = re.search(
        r"static\s+const\s+duk_function_list_entry\s+fnlist_prop\[\]\s*="
        r"\s*\{(.*?)^\s*\};", text, re.MULTILINE | re.DOTALL)
    if table_match is None:
        raise ValueError("src/ecmascript/es_prop.c: fnlist_prop[] not found")
    entries = {
        name: int(nargs)
        for name, nargs in NATIVE_ENTRY_RE.findall(table_match.group(1))
    }
    if not entries:
        raise ValueError("src/ecmascript/es_prop.c: fnlist_prop[] is empty")
    return entries


def _compare_name_sets(errors: list[str], module: str, label: str,
                       source: set[str], declared: set[str]) -> None:
    for name in sorted(source - declared):
        member = label + name if label.endswith(".") else label + " " + name
        errors.append("%s: %s missing declaration" % (module, member))
    for name in sorted(declared - source):
        member = label + name if label.endswith(".") else label + " " + name
        errors.append("%s: phantom declaration %s" % (module, member))


def _max_arity(signatures: list[Signature]) -> int:
    return max(signature.total for signature in signatures)


def check_source_shapes() -> list[str]:
    errors: list[str] = []
    native_prop = _native_prop_functions()

    for spec in MODULES:
        declaration = _read(spec.declaration)
        javascript = _read(spec.javascript)
        declared_names, declared_signatures, declared_kinds = \
            _declared_runtime(declaration)
        js_names, js_callables = _javascript_exports(javascript)
        runtime_names = set(js_names)
        if spec.name == "movian/prop":
            runtime_names.update(native_prop)
        _compare_name_sets(errors, spec.name, "export",
                           runtime_names, declared_names)

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

        if spec.name == "movian/prop":
            for name, nargs in sorted(native_prop.items()):
                if name == "global":
                    if nargs != 0:
                        errors.append(
                            "movian/prop: native global nargs=%d vs 0 wrapped"
                            % nargs)
                    if declared_kinds.get(name) != "value":
                        errors.append(
                            "movian/prop: global must declare the wrapped "
                            "np.global() value")
                    if not re.search(r"exports\.global\s*=.*np\.global\(\s*\)",
                                     javascript):
                        errors.append(
                            "movian/prop: global does not map fnlist_prop "
                            "global nargs=0 through np.global()")
                    continue
                signatures = declared_signatures.get(name)
                if signatures is None:
                    continue
                declared_nargs = _max_arity(signatures)
                if declared_nargs != nargs:
                    errors.append(
                        "movian/prop: native %s nargs=%d vs %d declared" %
                        (name, nargs, declared_nargs))

    return errors


def _tsc_command(tsc: str, fixture: Path) -> list[str]:
    inputs = [spec.declaration for spec in MODULES] + [fixture]
    return [
        tsc,
        "--noEmit",
        "--strict",
        "--target", "ES5",
        "--module", "commonjs",
        "--moduleResolution", "node",
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


def main() -> int:
    try:
        errors = check_source_shapes()
    except (OSError, ValueError) as error:
        print("reference-dts: %s" % error, file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print("reference-dts: %s" % error, file=sys.stderr)
        return 1
    print("reference-dts: source shapes OK (3 modules; native names+nargs)")

    tsc = shutil.which("tsc")
    if tsc is None:
        print("reference-dts: tsc unavailable; skipping TypeScript fixtures")
        return 0

    try:
        errors = check_typescript(tsc)
    except (OSError, ValueError) as error:
        print("reference-dts: TypeScript check failed to run: %s" % error,
              file=sys.stderr)
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
