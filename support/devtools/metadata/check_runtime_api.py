#!/usr/bin/env python3
"""Compare source metadata with one disposable runtime API capture.

The checker keeps differences explicit instead of silently treating every
runtime key as a public static declaration.  It exits non-zero for
MISSING_STATIC, MISSING_RUNTIME, or BUG findings; EXPECTED_DYNAMIC and
VERSION_SPECIFIC findings are reported but do not fail the gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
METADATA_PATH = REPO_ROOT / "generated" / "movian-metadata.json"
ORACLE_PATH = (REPO_ROOT / "support" / "devtools" / "api-introspector"
               / "runtime-api.json")
FAIL_CLASSES = {"MISSING_STATIC", "MISSING_RUNTIME", "BUG"}


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError(f"cannot read {path}: {error}") from error


def finding(result: dict[str, list[dict[str, str]]],
            category: str, surface: str, detail: str) -> None:
    result[category].append({"surface": surface, "detail": detail})


def static_module_entries(
        name: str,
        modules: dict[str, dict[str, Any]],
        seen: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    seen = set() if seen is None else seen
    if name in seen or name not in modules:
        return {}
    seen.add(name)
    record = modules[name]
    entries = {
        entry["name"]: entry
        for entry in (record.get("exports") or record.get("functions") or [])
    }
    inherited = record.get("inherits")
    if inherited:
        inherited_entries = static_module_entries(inherited, modules, seen)
        inherited_entries.update(entries)
        entries = inherited_entries
    return entries


def static_member_names(
        name: str, modules: dict[str, dict[str, Any]]
) -> set[str]:
    return set(static_module_entries(name, modules))


def static_module_map(metadata: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        record["name"]: record
        for record in metadata.get("js", {}).get("modules", [])
    }


def runtime_own_names(record: dict[str, Any]) -> set[str]:
    return set((record.get("keys") or {}).keys())


def runtime_proto_names(record: dict[str, Any]) -> set[str]:
    return set(((record.get("prototype") or {}).get("keys") or {}).keys())


def compare_modules(metadata: dict[str, Any], oracle: dict[str, Any],
                    result: dict[str, list[dict[str, str]]]) -> None:
    static = static_module_map(metadata)
    before = oracle.get("before") or {}
    canonical = {
        name: record for name, record in before.items()
        if not name.startswith("showtime/")
    }

    for name in sorted(set(static) - set(canonical)):
        finding(result, "MISSING_RUNTIME", f"module:{name}",
                "declared by source metadata but not required by the oracle")
    for name in sorted(set(canonical) - set(static)):
        finding(result, "MISSING_STATIC", f"module:{name}",
                "required successfully by the runtime but absent from metadata")

    for alias, record in sorted(before.items()):
        if not alias.startswith("showtime/"):
            continue
        movian_name = "movian/" + alias.split("/", 1)[1]
        if movian_name in static:
            finding(result, "VERSION_SPECIFIC", f"module:{alias}",
                    f"showtime compatibility alias resolves to {movian_name}")

    for name in sorted(set(static) & set(canonical)):
        entries = static_module_entries(name, static)
        expected = set(entries)
        actual_own = runtime_own_names(canonical[name])
        actual_proto = runtime_proto_names(canonical[name])
        actual = actual_own | actual_proto
        for member in sorted(expected - actual):
            finding(result, "MISSING_RUNTIME", f"module:{name}.{member}",
                    "source metadata member is absent from runtime key surface")
        for member in sorted(actual_own - expected):
            finding(result, "MISSING_STATIC", f"module:{name}.{member}",
                    "runtime own key is absent from source metadata")
        for member in sorted(actual_proto - expected):
            finding(result, "MISSING_STATIC", f"module:{name}.__proto__.{member}",
                    "runtime prototype member has no static shape record")

        for member, runtime_shape in (canonical[name].get("keys") or {}).items():
            if runtime_shape.get("type") != "function":
                continue
            static_entry = entries.get(member)
            if static_entry is None:
                continue
            runtime_length = runtime_shape.get("length")
            static_arity = static_entry.get("nargs")
            if runtime_length is None or static_arity is None:
                continue
            if runtime_length != static_arity:
                source_file = static_entry.get("source", {}).get("file", "")
                category = ("VERSION_SPECIFIC"
                            if source_file.startswith("src/ecmascript/")
                            else "BUG")
                finding(
                    result,
                    category,
                    f"module:{name}.{member}",
                    f"source nargs={static_arity}, runtime length={runtime_length}",
                )


def compare_settings(metadata: dict[str, Any], oracle: dict[str, Any],
                     result: dict[str, list[dict[str, str]]]) -> None:
    static_settings = metadata.get("js", {}).get("settings", {})
    static_names = set(static_settings.get("members", []))
    after = oracle.get("afterGlobalSettings") or {}
    settings = after.get("movian/settings") or {}
    for member in sorted(runtime_own_names(settings) |
                         runtime_proto_names(settings)):
        if member not in static_names:
            finding(result, "MISSING_STATIC", f"settings.{member}",
                    "globalSettings() mutates the settings receiver surface")


def compare_globals(metadata: dict[str, Any], oracle: dict[str, Any],
                    result: dict[str, list[dict[str, str]]]) -> None:
    static_globals = metadata.get("js", {}).get("globals", {})
    static_objects = {
        record["name"]: record
        for record in static_globals.get("objects", [])
    }
    for name, observed in sorted((oracle.get("globals") or {}).items()):
        if observed.get("status") == "missing":
            finding(result, "EXPECTED_DYNAMIC", f"global:{name}",
                    "global is absent in this plugin runtime")
            continue
        if observed.get("status") == "failed":
            finding(result, "BUG", f"global:{name}", observed.get("error", "failed"))
            continue
        record = static_objects.get(name)
        if record is None:
            category = ("VERSION_SPECIFIC"
                        if name in {"showtime", "plugin"}
                        else "EXPECTED_DYNAMIC")
            finding(result, category, f"global:{name}",
                    "runtime global is not a source-derived public declaration")
            continue
        expected = {entry["name"] for entry in record.get("functions", [])}
        expected |= {entry["name"] for entry in record.get("properties", [])}
        actual = runtime_own_names(observed.get("value") or {})
        for member in sorted(actual - expected):
            finding(result, "MISSING_STATIC", f"global:{name}.{member}",
                    "runtime global member is absent from source metadata")
        for member in sorted(expected - actual):
            finding(result, "MISSING_RUNTIME", f"global:{name}.{member}",
                    "source global member is absent from runtime")


def compare_health(oracle: dict[str, Any],
                   result: dict[str, list[dict[str, str]]]) -> None:
    if oracle.get("tier3PageOpened") is not True:
        finding(result, "BUG", "tier3.page", "complete route payload was not captured")
    if oracle.get("loadErrors"):
        finding(result, "BUG", "require", "module load failures are present")
    if oracle.get("globalSettingsError") is not None:
        finding(result, "BUG", "settings.globalSettings",
                str(oracle["globalSettingsError"]))
    tier3 = oracle.get("tier3") or {}
    for surface in ("route", "page", "websocket"):
        record = tier3.get(surface) or {}
        if record.get("status") == "failed":
            finding(result, "BUG", f"tier3.{surface}",
                    str(record.get("error", "construction failed")))
    for name, record in (tier3.get("items") or {}).items():
        if record.get("status") == "failed":
            finding(result, "BUG", f"tier3.items.{name}",
                    str(record.get("error", "construction failed")))


def run(metadata: dict[str, Any], oracle: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, list[dict[str, str]]] = {
        "EXPECTED_DYNAMIC": [],
        "VERSION_SPECIFIC": [],
        "MISSING_STATIC": [],
        "MISSING_RUNTIME": [],
        "BUG": [],
    }
    compare_modules(metadata, oracle, result)
    compare_settings(metadata, oracle, result)
    compare_globals(metadata, oracle, result)
    compare_health(oracle, result)
    counts = {key: len(value) for key, value in result.items()}
    return {"status": "ok" if not any(counts[key] for key in FAIL_CLASSES)
            else "fail", "counts": counts, "findings": result}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, default=METADATA_PATH)
    parser.add_argument("--oracle", type=Path, default=ORACLE_PATH)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run(load_json(args.metadata), load_json(args.oracle))
    except RuntimeError as error:
        print(f"runtime-check: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("RUNTIME API %s" % result["status"].upper())
        print("counts: %s" % ", ".join(
            f"{key}={value}" for key, value in result["counts"].items()))
        for category, findings in result["findings"].items():
            for item in findings:
                print(f"{category}: {item['surface']}: {item['detail']}")
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
