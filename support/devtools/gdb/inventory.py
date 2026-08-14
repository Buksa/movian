#!/usr/bin/env python3
"""Generate and validate a lifecycle inventory for one debug binary.

The inventory is a derived artifact, not a hand-maintained copy of the M7
inventory.  Contracts below name source-confirmed lifecycle probes; generation
keeps only symbols proved by ``nm`` in the selected binary and records the
address/type evidence used for that decision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1
CATEGORY_ORDER = (
    "core-init", "shutdown-hook", "init-system", "init-helper",
    "thread-create", "plugin", "es-plugin", "es-context", "es-resource",
    "navigator", "glw", "backend", "service", "prop-subscribe",
    "callout", "cache",
)
EVENTS = {"enter", "create", "destroy"}
FORBIDDEN_SYMBOL_PARTS = ("smb", "wsd")


def _contract(symbol: str, category: str, phase: str,
              paired_with: str | None = None, event: str = "enter",
              group: str | None = None, priority: int | None = None,
              init_fn: str | None = None, fini_fn: str | None = None) -> dict[str, Any]:
    return {
        "id": symbol,
        "symbol": symbol,
        "category": category,
        "phase": phase,
        "event": event,
        "pairedWith": paired_with,
        "group": group,
        "priority": priority,
        "initFn": init_fn,
        "finiFn": fini_fn,
    }


def _pair(symbol: str, category: str, phase: str, other: str,
          event: str) -> dict[str, Any]:
    return _contract(symbol, category, phase, other, event)


def _helper(symbol: str, phase: str, group: str, priority: int = 0,
            other: str | None = None) -> dict[str, Any]:
    if phase == "startup":
        init_fn, fini_fn, event = symbol, other, "enter"
    else:
        init_fn, fini_fn, event = None, symbol, "enter"
    return _contract(symbol, "init-helper", phase, other, event,
                     group, priority, init_fn, fini_fn)


# These are source-confirmed contracts, deliberately smaller than the M7
# inventory and free of SMB/WSD candidates.  They are filtered against the
# selected binary before becoming inventory entries.
CONTRACTS: tuple[dict[str, Any], ...] = (
    _contract("main_init", "core-init", "startup", "main_fini"),
    _contract("main_fini", "core-init", "shutdown", "main_init"),
    _contract("app_shutdown", "core-init", "shutdown"),
    _contract("shutdown_eject", "core-init", "shutdown"),
    _contract("arch_exit", "core-init", "shutdown"),
    _contract("do_shutdown", "core-init", "shutdown"),
    _pair("shutdown_hook_add", "shutdown-hook", "registration",
          "shutdown_hook_run", "enter"),
    _pair("shutdown_hook_run", "shutdown-hook", "shutdown",
          "shutdown_hook_add", "enter"),
    _contract("init_group", "init-system", "startup", "fini_group"),
    _contract("fini_group", "init-system", "shutdown", "init_group"),
    _contract("inithelper_register", "init-system", "registration"),

    _helper("asyncio_http_init", "startup", "INIT_GROUP_ASYNCIO"),
    _helper("ecmascript_init", "startup", "INIT_GROUP_API", other="ecmascript_fini"),
    _helper("ecmascript_fini", "shutdown", "INIT_GROUP_API", other="ecmascript_init"),
    _helper("fontstash_init", "startup", "INIT_GROUP_GRAPHICS"),
    _helper("freetype_init", "startup", "INIT_GROUP_GRAPHICS"),
    _helper("ftp_server_init", "startup", "INIT_GROUP_ASYNCIO"),
    _helper("http_server_init", "startup", "INIT_GROUP_ASYNCIO", other="http_server_fini"),
    _helper("http_server_fini", "shutdown", "INIT_GROUP_ASYNCIO", other="http_server_init"),
    _helper("httpcontrol_init", "startup", "INIT_GROUP_API"),
    _helper("net_refresh_network_status", "startup", "INIT_GROUP_NET"),
    _helper("nmb_init", "startup", "INIT_GROUP_ASYNCIO"),
    _helper("nmb_resolver_init", "startup", "INIT_GROUP_NET"),
    _helper("prop_http_init", "startup", "INIT_GROUP_API"),
    _helper("routesocket_init", "startup", "INIT_GROUP_ASYNCIO"),
    _helper("screenshot_init", "startup", "INIT_GROUP_API"),
    _helper("ssdp_shutdown", "shutdown", "INIT_GROUP_ASYNCIO", 10),
    _helper("stpp_discover_init", "startup", "INIT_GROUP_ASYNCIO", 10,
            "stpp_discover_fini"),
    _helper("stpp_discover_fini", "shutdown", "INIT_GROUP_ASYNCIO", 10,
            "stpp_discover_init"),
    _helper("usage_early_init", "startup", "INIT_GROUP_NET"),
    _helper("usage_init", "startup", "INIT_GROUP_API", other="usage_fini"),
    _helper("usage_fini", "shutdown", "INIT_GROUP_API", other="usage_init"),
    _helper("ws_init", "startup", "INIT_GROUP_API"),

    _contract("hts_thread_create_detached", "thread-create", "runtime",
              event="create"),
    _contract("hts_thread_create_joinable", "thread-create", "runtime",
              event="create"),
    _contract("plugins_init", "plugin", "startup"),
    _pair("plugin_load", "plugin", "runtime", "plugin_unload", "create"),
    _pair("plugin_unload", "plugin", "runtime", "plugin_load", "destroy"),
    _contract("plugins_reload_dev_plugin", "plugin", "runtime"),
    _pair("ecmascript_plugin_load", "es-plugin", "runtime",
          "ecmascript_plugin_unload", "create"),
    _pair("ecmascript_plugin_unload", "es-plugin", "runtime",
          "ecmascript_plugin_load", "destroy"),
    _pair("es_context_begin", "es-context", "runtime", "es_context_end", "enter"),
    _pair("es_context_end", "es-context", "runtime", "es_context_begin", "destroy"),
    _pair("es_context_create", "es-context", "runtime", "es_context_release", "create"),
    _pair("es_context_release", "es-context", "runtime", "es_context_create", "destroy"),
    _pair("es_resource_create", "es-resource", "runtime", "es_resource_destroy", "create"),
    _pair("es_resource_destroy", "es-resource", "runtime", "es_resource_create", "destroy"),
    _pair("es_resource_link", "es-resource", "runtime", "es_resource_unlink", "create"),
    _pair("es_resource_unlink", "es-resource", "runtime", "es_resource_link", "destroy"),
    _pair("nav_init", "navigator", "startup", "nav_fini", "enter"),
    _pair("nav_fini", "navigator", "shutdown", "nav_init", "enter"),
    _contract("nav_open0", "navigator", "runtime"),
    _contract("nav_reload_current", "navigator", "runtime"),
    _contract("glw_init", "glw", "startup"),
    _contract("glw_init2", "glw", "startup"),
    _contract("glw_settings_init", "glw", "startup"),
    _contract("glw_view_create", "glw", "runtime", event="create"),
    _contract("glw_flush", "glw", "shutdown"),
    _contract("glw_unload_universe", "glw", "shutdown"),
    _contract("glw_view_cache_flush", "glw", "shutdown"),
    _pair("backend_init", "backend", "startup", "backend_fini", "enter"),
    _pair("backend_fini", "backend", "shutdown", "backend_init", "enter"),
    _contract("backend_register", "backend", "registration"),
    _contract("service_init", "service", "startup"),
    _pair("service_create", "service", "runtime", "service_destroy", "create"),
    _pair("service_destroy", "service", "runtime", "service_create", "destroy"),
    _contract("prop_courier_create", "prop-subscribe", "runtime", event="create"),
    _pair("prop_subscribe", "prop-subscribe", "runtime", "prop_unsubscribe", "create"),
    _pair("prop_subscribe_ex", "prop-subscribe", "runtime", "prop_unsubscribe", "create"),
    _pair("prop_unsubscribe", "prop-subscribe", "runtime", "prop_subscribe", "destroy"),
    _contract("callout_init", "callout", "startup"),
    _pair("callout_arm0", "callout", "runtime", "callout_disarm", "create"),
    _pair("callout_arm_x", "callout", "runtime", "callout_disarm", "create"),
    _pair("callout_disarm", "callout", "runtime", "callout_arm0", "destroy"),
    _pair("blobcache_init", "cache", "startup", "blobcache_fini", "enter"),
    _pair("blobcache_fini", "cache", "shutdown", "blobcache_init", "enter"),
    _pair("kvstore_init", "cache", "startup", "kvstore_fini", "enter"),
    _pair("kvstore_fini", "cache", "shutdown", "kvstore_init", "enter"),
    _pair("metadb_init", "cache", "startup", "metadb_fini", "enter"),
    _pair("metadb_fini", "cache", "shutdown", "metadb_init", "enter"),
    _contract("app_flush_caches", "cache", "shutdown"),
    _contract("htsmsg_store_flush", "cache", "shutdown"),
)


def validate_contracts(contracts: Iterable[dict[str, Any]] = CONTRACTS) -> None:
    seen_ids: set[str] = set()
    seen_symbols: set[str] = set()
    for contract in contracts:
        ident = contract.get("id")
        symbol = contract.get("symbol")
        if not isinstance(ident, str) or not ident:
            raise ValueError("contract id must be a non-empty string")
        if ident in seen_ids:
            raise ValueError("duplicate contract id: %s" % ident)
        if not isinstance(symbol, str) or not symbol:
            raise ValueError("contract symbol must be a non-empty string")
        if symbol in seen_symbols:
            raise ValueError("duplicate contract symbol: %s" % symbol)
        if contract.get("category") not in CATEGORY_ORDER:
            raise ValueError("unknown contract category: %s" % contract.get("category"))
        if contract.get("event") not in EVENTS:
            raise ValueError("invalid contract event: %s" % contract.get("event"))
        seen_ids.add(ident)
        seen_symbols.add(symbol)


validate_contracts()


def parse_nm_output(output: str) -> dict[str, dict[str, Any]]:
    """Parse GNU nm POSIX records, retaining the first exact symbol record."""
    records: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(output.splitlines(), 1):
        fields = line.split()
        if len(fields) < 3:
            continue
        name, symbol_type, address = fields[:3]
        if not name or not re.fullmatch(r"[0-9A-Fa-f]+", address):
            continue
        if name in records:
            continue
        record: dict[str, Any] = {
            "symbol": name,
            "type": symbol_type,
            "address": "0x%x" % int(address, 16),
        }
        if len(fields) >= 4 and re.fullmatch(r"[0-9A-Fa-f]+", fields[3]):
            record["size"] = int(fields[3], 16)
        record["nmLine"] = line_number
        records[name] = record
    return records


def _run_nm(binary: Path, nm: str = "nm") -> dict[str, dict[str, Any]]:
    result = subprocess.run(
        [nm, "-an", "--defined-only", "--format=posix", str(binary)],
        check=False, capture_output=True, text=True,
    )
    if result.returncode:
        raise RuntimeError("nm failed for %s: %s" % (binary, result.stderr.strip()))
    return parse_nm_output(result.stdout)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _build_id(binary: Path) -> str | None:
    try:
        result = subprocess.run(
            ["readelf", "-n", str(binary)], check=False,
            capture_output=True, text=True,
        )
    except OSError:
        return None
    match = re.search(r"Build ID:\s*([0-9a-fA-F]+)", result.stdout)
    return match.group(1).lower() if match else None


def _evidence(record: dict[str, Any]) -> dict[str, Any]:
    return {key: record[key] for key in ("address", "type", "size")
            if key in record}


def generate_inventory(binary: Path, nm_records: dict[str, dict[str, Any]] | None = None,
                       nm: str = "nm") -> dict[str, Any]:
    binary = binary.resolve()
    if not binary.is_file():
        raise FileNotFoundError(binary)
    records = nm_records if nm_records is not None else _run_nm(binary, nm)
    entries: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for contract_order, contract in enumerate(CONTRACTS):
        symbol = contract["symbol"]
        record = records.get(symbol)
        if record is None:
            missing.append({
                "id": contract["id"], "symbol": symbol,
                "category": contract["category"],
                "reason": "not-defined-in-selected-binary",
            })
            continue
        if any(part in symbol.lower() for part in FORBIDDEN_SYMBOL_PARTS):
            raise ValueError("forbidden lifecycle symbol: %s" % symbol)
        entry = {
            "id": contract["id"],
            "symbol": symbol,
            "category": contract["category"],
            "phase": contract["phase"],
            "event": contract["event"],
            "pairedWith": contract["pairedWith"],
            "contractOrder": contract_order,
            "confidence": "binary-symbol-confirmed",
            "binaryEvidence": _evidence(record),
        }
        for key in ("group", "priority", "initFn", "finiFn"):
            if contract.get(key) is not None:
                entry[key] = contract[key]
        entries.append(entry)

    entries.sort(key=lambda item: item["contractOrder"])
    counts = Counter(entry["category"] for entry in entries)
    inventory = {
        "schemaVersion": SCHEMA_VERSION,
        "generator": "support/devtools/gdb/inventory.py",
        "description": (
            "Lifecycle contracts filtered and evidenced against one selected "
            "debug binary; missing symbols remain explicit and are never armed."),
        "binary": {
            "name": binary.name,
            "sha256": _sha256(binary),
            "buildId": _build_id(binary),
            "definedSymbolCount": len(records),
        },
        "categories": list(CATEGORY_ORDER),
        "categoryCounts": {category: counts.get(category, 0)
                           for category in CATEGORY_ORDER},
        "count": len(entries),
        "initmeUseCount": sum(1 for entry in entries
                              if entry.get("group") is not None),
        "missingCandidates": sorted(missing, key=lambda item: item["symbol"]),
        "entries": entries,
    }
    validate_inventory(inventory)
    return inventory


def validate_inventory(inventory: dict[str, Any]) -> None:
    if inventory.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("unsupported inventory schema")
    entries = inventory.get("entries")
    if not isinstance(entries, list):
        raise ValueError("inventory entries must be an array")
    if inventory.get("count") != len(entries):
        raise ValueError("inventory count mismatch")
    ids: set[str] = set()
    symbols: set[str] = set()
    categories = set(inventory.get("categories", []))
    order_values: set[int] = set()
    for entry in entries:
        ident = entry.get("id")
        symbol = entry.get("symbol")
        if not isinstance(ident, str) or ident in ids:
            raise ValueError("duplicate or invalid inventory id: %s" % ident)
        order = entry.get("contractOrder")
        if not isinstance(order, int) or order in order_values:
            raise ValueError("duplicate or invalid contract order: %s" % order)
        if entry.get("category") not in categories:
            raise ValueError("entry category not declared: %s" % entry.get("category"))
        if entry.get("event") not in EVENTS:
            raise ValueError("invalid inventory event: %s" % entry.get("event"))
        evidence = entry.get("binaryEvidence")
        if not isinstance(evidence, dict) or not evidence.get("address") \
                or not evidence.get("type"):
            raise ValueError("missing binary evidence for %s" % symbol)
        if any(part in symbol.lower() for part in FORBIDDEN_SYMBOL_PARTS):
            raise ValueError("forbidden inventory symbol: %s" % symbol)
        ids.add(ident)
        order_values.add(order)
    expected = {category: sum(entry["category"] == category
                              for entry in entries)
                for category in CATEGORY_ORDER}
    if inventory.get("categoryCounts") != expected:
        raise ValueError("inventory category count mismatch")


def canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _write(path: Path, inventory: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(inventory), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("scan", "check"):
        item = sub.add_parser(command)
        item.add_argument("--binary", required=True, type=Path)
        item.add_argument("--inventory", required=True, type=Path)
        item.add_argument("--nm", default="nm")
    args = parser.parse_args(argv)
    generated = generate_inventory(args.binary, nm=args.nm)
    if args.command == "scan":
        _write(args.inventory, generated)
        print(json.dumps({"status": "GENERATED", "count": generated["count"],
                          "binarySha256": generated["binary"]["sha256"]},
                         sort_keys=True))
        return 0
    try:
        actual = json.loads(args.inventory.read_text(encoding="utf-8"))
        validate_inventory(actual)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("inventory invalid: %s" % exc, file=sys.stderr)
        return 1
    if canonical_json(actual) != canonical_json(generated):
        print("inventory differs from selected binary; regenerate it",
              file=sys.stderr)
        return 1
    print(json.dumps({"status": "OK", "count": generated["count"],
                      "binarySha256": generated["binary"]["sha256"]},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
