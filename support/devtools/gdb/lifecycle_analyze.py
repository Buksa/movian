#!/usr/bin/env python3
"""Analyze lifecycle JSONL without treating missing probes as success."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_WEDGE = "WEDGE"
STATUS_TIMEOUT = "TIMEOUT"
STATUS_COLLECTOR_ERROR = "COLLECTOR_ERROR"
STATUS_INFRA_ERROR = "INFRA_ERROR"
STATUS_UNKNOWN = "UNKNOWN"

OBSERVED = "OBSERVED"
INFERRED = "INFERRED"
NOT_INSTRUMENTED = "NOT_INSTRUMENTED"
NOT_REACHED = "NOT_REACHED"
UNKNOWN = "UNKNOWN"

SPECIAL_EVENTS = {
    "collector-final", "collector-error", "probe-error", "inferior-exited",
    "thread-exit", "rate-limited", "wedge", "timeout",
}
REQUIRED_EVENT_FIELDS = {
    "seq", "monotonicNs", "category", "event", "symbol", "thread",
    "arguments", "objects", "stack",
}


def _iter_lines(source: str | Path | Iterable[str]) -> Iterable[tuple[int, str]]:
    if isinstance(source, Path):
        with source.open(encoding="utf-8") as stream:
            yield from enumerate(stream, 1)
        return
    if isinstance(source, str):
        yield from enumerate(source.splitlines(True), 1)
        return
    yield from enumerate(source, 1)


def parse_jsonl(source: str | Path | Iterable[str]) -> dict[str, Any]:
    lines = list(_iter_lines(source))
    events: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    previous_seq = 0
    previous_time = -1
    line_count = lines[-1][0] if lines else 0
    nonempty_lines = [line_number for line_number, raw_line in lines
                      if raw_line.strip()]
    last_nonempty_line = nonempty_lines[-1] if nonempty_lines else 0
    for line_number, raw_line in lines:
        text = raw_line.strip()
        if not text:
            continue
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            errors.append({
                "line": line_number,
                "kind": "truncated-json" if line_number == last_nonempty_line
                else "malformed-json",
                "message": str(exc),
            })
            continue
        if not isinstance(value, dict):
            errors.append({"line": line_number, "kind": "event-not-object"})
            continue
        missing = sorted(REQUIRED_EVENT_FIELDS - set(value))
        if missing:
            errors.append({"line": line_number, "kind": "missing-fields",
                           "fields": missing})
            continue
        seq = value.get("seq")
        timestamp = value.get("monotonicNs")
        if not isinstance(seq, int) or seq <= previous_seq:
            errors.append({"line": line_number, "kind": "sequence-order"})
        if not isinstance(timestamp, int) or timestamp < previous_time:
            errors.append({"line": line_number, "kind": "timestamp-order"})
        if not isinstance(value.get("thread"), dict):
            errors.append({"line": line_number, "kind": "thread-context"})
        events.append(value)
        if isinstance(seq, int):
            previous_seq = max(previous_seq, seq)
        if isinstance(timestamp, int):
            previous_time = max(previous_time, timestamp)
    return {
        "events": events,
        "errors": errors,
        "lineCount": line_count,
        "truncated": any(error["kind"] == "truncated-json"
                          for error in errors),
    }


def _load_inventory(inventory: dict[str, Any] | str | Path) -> dict[str, Any]:
    if isinstance(inventory, dict):
        return inventory
    return json.loads(Path(inventory).read_text(encoding="utf-8"))


def _result(name: str, status: str, evidence: str,
            expected: list[str], observed: list[str], **extra: Any) -> dict[str, Any]:
    result = {
        "name": name,
        "status": status,
        "evidence": evidence,
        "expected": expected,
        "observed": observed,
    }
    result.update(extra)
    return result


def _unique_symbols(events: Iterable[dict[str, Any]], allowed: set[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for event in events:
        symbol = event.get("symbol")
        if symbol in allowed and symbol not in seen:
            result.append(symbol)
            seen.add(symbol)
    return result


def _order_result(name: str, entries: list[dict[str, Any]],
                  events: list[dict[str, Any]], reverse: bool = False) -> dict[str, Any]:
    ordered_entries = sorted(
        entries, key=lambda item: item.get("contractOrder", 0), reverse=reverse)
    expected = [entry["symbol"] for entry in ordered_entries]
    observed = _unique_symbols(events, set(expected))
    if not expected:
        return _result(name, STATUS_UNKNOWN, NOT_INSTRUMENTED, expected, observed)
    missing = [symbol for symbol in expected if symbol not in observed]
    if missing:
        evidence = NOT_REACHED if not observed else UNKNOWN
        return _result(name, STATUS_UNKNOWN, evidence, expected, observed,
                       missing=missing)
    positions = [observed.index(symbol) for symbol in expected]
    status = STATUS_PASS if positions == sorted(positions) else STATUS_FAIL
    return _result(name, status, OBSERVED, expected, observed)


def derive_init_order(inventory: dict[str, Any] | str | Path,
                      events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    data = _load_inventory(inventory)
    entries = [entry for entry in data.get("entries", [])
               if entry.get("phase") == "startup"]
    event_list = [event for event in events
                  if event.get("event") not in SPECIAL_EVENTS]
    return _order_result("init-order", entries, event_list)


def derive_fini_order(inventory: dict[str, Any] | str | Path,
                      events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    data = _load_inventory(inventory)
    entries = [entry for entry in data.get("entries", [])
               if entry.get("phase") == "shutdown"]
    event_list = [event for event in events
                  if event.get("event") not in SPECIAL_EVENTS]
    return _order_result("fini-order", entries, event_list, reverse=True)


def derive_thread_lifecycle(inventory: dict[str, Any] | str | Path,
                            events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    data = _load_inventory(inventory)
    event_list = list(events)
    expected = [entry["symbol"] for entry in data.get("entries", [])
                if entry.get("category") == "thread-create"]
    creates = [event for event in event_list
               if event.get("category") == "thread-create"
               and event.get("event") == "create"]
    exits = [event for event in event_list if event.get("event") == "thread-exit"]
    if not expected:
        return _result("thread-lifecycle", STATUS_UNKNOWN,
                       NOT_INSTRUMENTED, [], [], created=0, exited=0)
    if not creates and not exits:
        return _result("thread-lifecycle", STATUS_UNKNOWN,
                       NOT_REACHED, expected, [], created=0, exited=0)
    created_count = len(creates)
    exited_count = len(exits)
    complete = any(event.get("event") == "inferior-exited"
                   for event in event_list)
    # A create breakpoint runs in the creator, not the new thread.  The
    # creator TID therefore cannot be compared with thread-exit TIDs.
    if created_count != exited_count:
        return _result("thread-lifecycle", STATUS_UNKNOWN, UNKNOWN, expected,
                       [], created=created_count, exited=exited_count,
                       identityCorrelation="not-available",
                       exitedThreads=[
                           event.get("thread", {}).get("osTid")
                           for event in exits],
                       complete=complete)
    return _result("thread-lifecycle", STATUS_PASS, INFERRED, expected, [],
                   created=created_count, exited=exited_count,
                   identityCorrelation="count-only", complete=complete)


RELOAD_RESOURCE_PAIRS = {
    "es-context": ({"es_context_create"}, {"es_context_end"}),
    "es-resource": ({"es_resource_link"}, {"es_resource_unlink"}),
    "service": ({"service_create"}, {"service_destroy"}),
    "es-plugin": ({"ecmascript_plugin_load"}, {"ecmascript_plugin_unload"}),
    "plugin": ({"plugin_load", "plugins_reload_dev_plugin"},
               {"plugin_unload"}),
}


def _pointer_value(event: dict[str, Any], keys: Iterable[str]) -> str | None:
    arguments = event.get("arguments") or {}
    objects = event.get("objects") or {}
    for key in keys:
        value = objects.get(key, arguments.get(key))
        if isinstance(value, str) and value.startswith("0x") and value != "0x0":
            return value
    return None


def _reload_resource_window(events: list[dict[str, Any]],
                            start: int, end: int) -> dict[str, Any]:
    window = events[start:end]
    counts: dict[str, dict[str, int]] = {
        kind: {"created": 0, "destroyed": 0}
        for kind in RELOAD_RESOURCE_PAIRS
    }
    seen_operations: set[tuple[str, str]] = set()
    pointer_missing = False
    for event in window:
        symbol = event.get("symbol")
        if symbol == "es_context_create":
            counts["es-context"]["created"] = 1
            continue
        if symbol == "es_context_end":
            counts["es-context"]["destroyed"] = 1
            continue
        for kind, (creates, destroys) in RELOAD_RESOURCE_PAIRS.items():
            if symbol in creates or symbol in destroys:
                operation = "create" if symbol in creates else "destroy"
                if kind == "es-resource":
                    pointer = _pointer_value(event, ("er", "resource"))
                    if pointer is None:
                        pointer_missing = True
                    else:
                        key = (operation, pointer)
                        if key in seen_operations:
                            continue
                        seen_operations.add(key)
                counts[kind]["created" if operation == "create"
                              else "destroyed"] += 1
                break
    imbalances = []
    indeterminate = []
    balance: dict[str, dict[str, Any]] = {}
    for kind, values in counts.items():
        created = values["created"]
        destroyed = values["destroyed"]
        if created == 0 and destroyed == 0:
            kind_status = "not-observed"
        elif kind == "es-resource" and pointer_missing:
            kind_status = "indeterminate"
            indeterminate.append({
                "kind": kind, "reason": "resource-pointer-not-captured",
            })
        elif created != destroyed:
            kind_status = "imbalanced"
            imbalances.append({
                "kind": kind, "created": created,
                "destroyed": destroyed, "delta": created - destroyed,
            })
        else:
            kind_status = "balanced"
        balance[kind] = {
            "created": created, "destroyed": destroyed,
            "delta": created - destroyed, "status": kind_status,
        }
    if indeterminate:
        status, evidence = STATUS_UNKNOWN, UNKNOWN
    elif imbalances:
        status, evidence = STATUS_FAIL, OBSERVED
    else:
        status, evidence = STATUS_PASS, OBSERVED
    return {
        "status": status, "evidence": evidence, "balance": balance,
        "imbalances": imbalances, "indeterminateReasons": indeterminate,
        "startSeq": window[0].get("seq") if window else None,
        "endSeq": window[-1].get("seq") if window else None,
        "eventCount": len(window),
    }


def _generic_resource_balance(data: dict[str, Any],
                              event_list: list[dict[str, Any]]) -> dict[str, Any]:
    pairs = {
        entry["symbol"]: entry["pairedWith"]
        for entry in data.get("entries", [])
        if entry.get("event") in {"create", "destroy"}
        and entry.get("pairedWith")
    }
    creates = Counter(event.get("symbol") for event in event_list
                      if event.get("event") == "create"
                      and event.get("symbol") in pairs)
    destroys = Counter(event.get("symbol") for event in event_list
                       if event.get("event") == "destroy"
                       and event.get("symbol") in pairs.values())
    observations: list[dict[str, Any]] = []
    for create_symbol, count in sorted(creates.items()):
        destroy_symbol = pairs[create_symbol]
        observations.append({
            "create": create_symbol, "destroy": destroy_symbol,
            "created": count, "destroyed": destroys.get(destroy_symbol, 0),
        })
    for destroy_symbol, count in sorted(destroys.items()):
        if destroy_symbol not in pairs.values():
            observations.append({
                "create": None, "destroy": destroy_symbol,
                "created": 0, "destroyed": count,
            })
    if not observations:
        return _result("resource-balance", STATUS_UNKNOWN, NOT_INSTRUMENTED,
                       [], [], resources=[], complete=False)
    complete = any(event.get("event") == "inferior-exited"
                   for event in event_list)
    balanced = all(item["created"] == item["destroyed"]
                   for item in observations)
    status, evidence = ((STATUS_PASS, INFERRED) if balanced
                        else (STATUS_UNKNOWN, UNKNOWN))
    return _result(
        "resource-balance", status, evidence, sorted(creates), sorted(destroys),
        resources=observations, complete=complete,
        indeterminateReasons=[] if balanced else [
            "object-identity-not-captured-for-mismatched-pairs"])


def derive_resource_balance(inventory: dict[str, Any] | str | Path,
                            events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    data = _load_inventory(inventory)
    event_list = list(events)
    reload_indexes = [
        index for index, event in enumerate(event_list)
        if event.get("symbol") == "plugins_reload_dev_plugin"
    ]
    if not reload_indexes:
        return _generic_resource_balance(data, event_list)
    windows = []
    for number, start in enumerate(reload_indexes):
        end = reload_indexes[number + 1] if number + 1 < len(reload_indexes) \
            else len(event_list)
        for index in range(start + 1, end):
            event = event_list[index]
            if event.get("symbol") == "fini_group" \
                    or event.get("event") == "inferior-exited":
                end = index
                break
        windows.append(_reload_resource_window(event_list, start, end))
    statuses = {window["status"] for window in windows}
    if STATUS_FAIL in statuses:
        status, evidence = STATUS_FAIL, OBSERVED
    elif STATUS_UNKNOWN in statuses:
        status, evidence = STATUS_UNKNOWN, UNKNOWN
    else:
        status, evidence = STATUS_PASS, OBSERVED
    return _result(
        "resource-balance", status, evidence,
        sorted(RELOAD_RESOURCE_PAIRS), sorted(RELOAD_RESOURCE_PAIRS),
        resources=windows, reloadWindows=windows,
        complete=any(event.get("event") == "inferior-exited"
                     for event in event_list))


def _overall_status(results: list[dict[str, Any]], stream: dict[str, Any]) -> str:
    if stream["errors"]:
        return STATUS_COLLECTOR_ERROR
    statuses = {result["status"] for result in results}
    if STATUS_FAIL in statuses:
        return STATUS_FAIL
    if STATUS_WEDGE in statuses:
        return STATUS_WEDGE
    if STATUS_TIMEOUT in statuses:
        return STATUS_TIMEOUT
    if STATUS_INFRA_ERROR in statuses:
        return STATUS_INFRA_ERROR
    if statuses and statuses <= {STATUS_PASS}:
        return STATUS_PASS
    return STATUS_UNKNOWN


def analyze_events(inventory: dict[str, Any] | str | Path,
                   source: str | Path | Iterable[str]) -> dict[str, Any]:
    stream = parse_jsonl(source)
    events = stream["events"]
    results = [
        derive_init_order(inventory, events),
        derive_fini_order(inventory, events),
        derive_thread_lifecycle(inventory, events),
        derive_resource_balance(inventory, events),
    ]
    collector_errors = list(stream["errors"])
    if any(event.get("event") == "probe-error" for event in events):
        collector_errors.append({"kind": "probe-error"})
    status = _overall_status(results, {**stream, "errors": collector_errors})
    return {
        "status": status,
        "stream": {
            "lineCount": stream["lineCount"],
            "eventCount": len(events),
            "truncated": stream["truncated"],
            "errors": collector_errors,
        },
        "results": results,
        "observability": {
            "observedEvents": len(events),
            "missingCandidates": len(_load_inventory(inventory).get(
                "missingCandidates", [])),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = analyze_events(args.inventory, args.events)
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0 if result["status"] in {STATUS_PASS, STATUS_UNKNOWN} else 1


if __name__ == "__main__":
    raise SystemExit(main())
