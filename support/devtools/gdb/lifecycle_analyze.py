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
    created_tids = [event.get("thread", {}).get("osTid") for event in creates]
    exited_tids = [event.get("thread", {}).get("osTid") for event in exits]
    if len(created_tids) != len(exited_tids) \
            or Counter(created_tids) != Counter(exited_tids):
        complete = any(event.get("event") == "inferior-exited"
                       for event in event_list)
        return _result("thread-lifecycle", STATUS_FAIL if complete else STATUS_UNKNOWN,
                       OBSERVED if complete else UNKNOWN, expected,
                       [str(value) for value in created_tids],
                       created=len(created_tids), exited=len(exited_tids),
                       exitedThreads=exited_tids)
    return _result("thread-lifecycle", STATUS_PASS, INFERRED, expected,
                   [str(value) for value in created_tids],
                   created=len(created_tids), exited=len(exited_tids))


def derive_resource_balance(inventory: dict[str, Any] | str | Path,
                            events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    data = _load_inventory(inventory)
    event_list = list(events)
    creates = Counter(event.get("symbol") for event in event_list
                      if event.get("event") == "create")
    destroys = Counter(event.get("symbol") for event in event_list
                       if event.get("event") == "destroy")
    pairs: dict[str, str] = {}
    for entry in data.get("entries", []):
        pair = entry.get("pairedWith")
        if pair:
            pairs[entry["symbol"]] = pair
    observations: list[dict[str, Any]] = []
    imbalance = False
    for create_symbol, count in sorted(creates.items()):
        if not create_symbol:
            continue
        destroy_symbol = pairs.get(create_symbol)
        destroyed = destroys.get(destroy_symbol, 0) if destroy_symbol else 0
        observations.append({"create": create_symbol, "destroy": destroy_symbol,
                             "created": count, "destroyed": destroyed})
        imbalance |= count != destroyed
    for destroy_symbol, count in sorted(destroys.items()):
        if destroy_symbol not in pairs.values():
            observations.append({"create": None, "destroy": destroy_symbol,
                                 "created": 0, "destroyed": count})
            imbalance = True
    if not observations:
        return _result("resource-balance", STATUS_UNKNOWN, NOT_INSTRUMENTED,
                       [], [], resources=[])
    complete = any(event.get("event") == "inferior-exited"
                   for event in event_list)
    if imbalance:
        status = STATUS_FAIL if complete else STATUS_UNKNOWN
        evidence = OBSERVED if complete else UNKNOWN
    else:
        status = STATUS_PASS
        evidence = INFERRED
    return _result("resource-balance", status, evidence,
                   sorted(creates), sorted(destroys), resources=observations,
                   complete=complete)


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
