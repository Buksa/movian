#!/usr/bin/env python3
"""Capture a structured Movian HTTP diagnostics snapshot."""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

DEFAULT_PROPS = [
    "global/navigators/current/currentpage/url",
    "global/navigators/current/currentpage/model/loading",
    "global/navigators/current/currentpage/model/metadata/title",
]


def fetch_text(base_url: str, path: str, timeout: float) -> str:
    url = base_url.rstrip("/") + path
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as error:
        raise RuntimeError(f"GET {path} failed: {error}") from error


def parse_prop(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    result: dict[str, Any] = {
        "name": None,
        "refcount": None,
        "xref": None,
        "value": None,
        "children": [],
        "value_subscribers": [],
        "canonical_subscribers": [],
    }
    if not lines:
        return result

    match = re.match(
        r"^(.*?) \(ref:(\d+) xref:(\d+)\) is a(?:\s(.*))?$", lines[0]
    )
    if match:
        result["name"] = match.group(1)
        result["refcount"] = int(match.group(2))
        result["xref"] = int(match.group(3))
        result["value"] = match.group(4) or ""

    section = "children"
    for line in lines[1:]:
        if line == "Value Subscribers:":
            section = "value_subscribers"
            continue
        if line == "Canonical Subscribers:":
            section = "canonical_subscribers"
            continue
        if not line.strip():
            continue
        if section == "children":
            result["children"].append(line.strip())
        else:
            result[section].append(line.strip())
    return result


def parse_stats(text: str, include_paths: bool) -> dict[str, Any]:
    contexts: dict[str, Any] = {}
    current_id: str | None = None

    for raw_line in text.splitlines():
        header = re.match(r"^---\s+(.+?)\s+-{3,}\s*$", raw_line)
        if header:
            current_id = header.group(1).strip()
            contexts[current_id] = {
                "memory_current": None,
                "memory_peak": None,
                "rooted_objects": None,
                "native_instances": {},
                "permanent_resource_count": 0,
                "permanent_resource_types": {},
            }
            continue
        if current_id is None:
            continue

        context = contexts[current_id]
        stripped = raw_line.strip()

        loaded = re.match(r"Loaded from\s+(.+)$", stripped)
        if loaded and include_paths:
            context["loaded_from"] = loaded.group(1)
            continue

        memory = re.match(
            r"Memory usage, current:\s*(\d+) bytes, peak:\s*(\d+)", stripped
        )
        if memory:
            context["memory_current"] = int(memory.group(1))
            context["memory_peak"] = int(memory.group(2))
            continue

        rooted = re.match(r"Rooted Ecmascript objects:\s*(\d+)", stripped)
        if rooted:
            context["rooted_objects"] = int(rooted.group(1))
            continue

        native = re.match(r"([A-Za-z0-9_-]+):\s*(\d+) active$", stripped)
        if native:
            context["native_instances"][native.group(1)] = int(native.group(2))
            continue

        if raw_line.startswith("\t") and stripped:
            kind = stripped.split(":", 1)[0].split(None, 1)[0]
            context["permanent_resource_count"] += 1
            counts = Counter(context["permanent_resource_types"])
            counts[kind] += 1
            context["permanent_resource_types"] = dict(sorted(counts.items()))

    return contexts


def diff_stats(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for context_id in sorted(set(before) | set(after)):
        left = before.get(context_id)
        right = after.get(context_id)
        if left is None or right is None:
            result[context_id] = {
                "state": "added" if left is None else "removed"
            }
            continue

        native_keys = set(left["native_instances"]) | set(right["native_instances"])
        result[context_id] = {
            "memory_current_delta": (
                right["memory_current"] - left["memory_current"]
                if left["memory_current"] is not None
                and right["memory_current"] is not None
                else None
            ),
            "memory_peak_delta": (
                right["memory_peak"] - left["memory_peak"]
                if left["memory_peak"] is not None and right["memory_peak"] is not None
                else None
            ),
            "rooted_objects_delta": (
                right["rooted_objects"] - left["rooted_objects"]
                if left["rooted_objects"] is not None
                and right["rooted_objects"] is not None
                else None
            ),
            "permanent_resource_delta": (
                right["permanent_resource_count"]
                - left["permanent_resource_count"]
            ),
            "native_instance_deltas": {
                key: right["native_instances"].get(key, 0)
                - left["native_instances"].get(key, 0)
                for key in sorted(native_keys)
            },
        }
    return result


def collect(args: argparse.Namespace) -> dict[str, Any]:
    diag = fetch_text(args.base_url, "/api/diag", args.timeout)
    version_match = re.search(
        r"<strong>movian</strong>\s+Version\s+([^<]+)", diag, re.IGNORECASE
    )

    props: dict[str, Any] = {}
    for prop_path in args.prop:
        encoded_path = urllib.parse.quote(prop_path, safe="/*")
        text = fetch_text(args.base_url, "/api/prop/" + encoded_path, args.timeout)
        props[prop_path] = parse_prop(text)

    before_text = fetch_text(args.base_url, "/api/ecmascript/stats", args.timeout)
    before = parse_stats(before_text, args.include_paths)

    output: dict[str, Any] = {
        "base_url": args.base_url.rstrip("/"),
        "movian_version": version_match.group(1).strip() if version_match else None,
        "props": props,
        "ecmascript": {"before": before},
    }

    if args.gc:
        gc_response = fetch_text(args.base_url, "/api/ecmascript/gc", args.timeout)
        after_text = fetch_text(args.base_url, "/api/ecmascript/stats", args.timeout)
        after = parse_stats(after_text, args.include_paths)
        output["ecmascript"]["gc_response"] = gc_response.strip()
        output["ecmascript"]["after"] = after
        output["ecmascript"]["delta"] = diff_stats(before, after)

    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture Movian page props and ECMAScript diagnostics as JSON."
    )
    parser.add_argument(
        "--base-url", default="http://127.0.0.1:42000", help="Movian HTTP base URL"
    )
    parser.add_argument(
        "--timeout", type=float, default=5.0, help="HTTP timeout in seconds"
    )
    parser.add_argument(
        "--prop",
        action="append",
        default=None,
        help="Slash-separated /api/prop path; repeat for multiple props",
    )
    parser.add_argument(
        "--gc",
        action="store_true",
        help="Run global ECMAScript GC and include before/after deltas",
    )
    parser.add_argument(
        "--include-paths",
        action="store_true",
        help="Include ECMAScript context load paths in the private snapshot",
    )
    parser.add_argument("--output", type=Path, help="Write JSON to this file")
    args = parser.parse_args()
    if args.prop is None:
        args.prop = list(DEFAULT_PROPS)
    return args


def main() -> int:
    args = parse_args()
    try:
        data = collect(args)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1

    rendered = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
