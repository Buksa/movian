#!/usr/bin/env python3
"""Agent-friendly Movian HTTP/STPP inspection and control CLI.

Run as a module: python3 -m mdevlib.movian_agent (from support/devtools/).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import socket
import struct
import sys
import time
import urllib.parse
from collections import Counter
from pathlib import Path
from typing import Any

from . import harness
from . import movian_diag_snapshot as diag
from . import stpp_probe as ws


PAGE_PROPS = [
    "global/app/systemname",
    "global/navigators/current/currentpage/url",
    "global/navigators/current/currentpage/model/type",
    "global/navigators/current/currentpage/model/loading",
    "global/navigators/current/currentpage/model/metadata/title",
    "global/navigators/current/canGoBack",
    "global/navigators/current/canGoForward",
    "global/navigators/current/canGoHome",
    "global/userinterfaces/ui/keyboard",
    "global/stpp/remoteControlled",
    "global/media/current/url",
    "global/media/current/playstatus",
]

NODE_FIELDS = [
    "type",
    "metadata/title",
    "metadata/subtitle",
    "metadata/icon",
    "url",
    "enabled",
    "value",
]

SAFE_HTTP_PROBES = [
    ("/api/diag", "read", "diagnostics"),
    ("/api/done", "read", "no-op OK"),
    ("/api/open", "read", "HTML form; no url means no navigation"),
    ("/api/openparameterized", "read", "missing scheme should be 404"),
    ("/api/input/action", "read", "missing action should be 404"),
    ("/api/input/utf8", "read", "missing str should be 400"),
    ("/api/notifyuser", "read", "missing msg should be 400"),
    ("/api/image", "active-read", "missing url should be 404 without loader I/O"),
    ("/api/logfile", "read", "missing index should be 400"),
    ("/api/static/index.html", "read", "core dataroot static asset"),
    ("/favicon.ico", "read", "core dataroot icon"),
    ("/api/ws/echo", "read", "normal HTTP should be 405; WebSocket tested separately"),
    ("/api/stpp", "read", "normal HTTP should be 405; WebSocket tested separately"),
]

BLOCKED_HTTP_PROBES = [
    ("/api/restart", "destructive", "terminates Movian"),
    ("/api/replace", "destructive", "replaces executable and restarts"),
    ("/api/logfile/0?mode=pastebin", "exfiltration", "uploads private log"),
    ("/api/screenshot", "external-write", "upload mode may contact imgur"),
]

SIGNALS = {
    # Canonical error-line set lives in harness.ERROR_SIGNALS; keep the
    # two tools' idea of "an error line" from drifting apart.
    "errors": harness.ERROR_SIGNALS,
    "http_ready": re.compile(r"http-server: Listening on port"),
    "plugin_ready": re.compile(r"Loaded dev plugin|Route .* added"),
    "navigation": re.compile(r"navigator.*Opening"),
    "glw_focus": re.compile(r"GLW.*Focus set|Event .* route start"),
    "stpp": re.compile(r"\bSTPP\b"),
    "prop_debug": re.compile(r"prop.*debug|PROP_DEBUG", re.IGNORECASE),
}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def request(
    base_url: str,
    path: str,
    timeout: float,
    method: str = "GET",
    form: dict[str, str] | None = None,
) -> dict[str, Any]:
    """harness.http_request plus a "bytes" convenience key. Delegating
    also inherits its guarded body read: a connection reset mid-read on
    one probe becomes {"ok": False, ...} for that probe instead of an
    OSError that aborts a whole snapshot run."""
    result = harness.http_request(base_url, path, timeout, method, form)
    if "body" in result:
        result["bytes"] = len(result["body"])
    return result


def public_http_result(result: dict[str, Any], preview: int = 240) -> dict[str, Any]:
    body = result.get("body", b"")
    output = {key: value for key, value in result.items() if key != "body"}
    if body:
        output["sha256"] = hashlib.sha256(body).hexdigest()
        content_type = result.get("content_type") or ""
        if content_type.startswith("text/") or "html" in content_type:
            output["preview"] = redact(body.decode("utf-8", "replace")[:preview])
    return output


def redact(text: str) -> str:
    text = re.sub(
        r"(?i)(cookie|authorization|password|passwd|token|secret|session|auth)"
        r"(\s*(?::|=)\s*)([^\s,;]+)",
        r"\1\2<redacted>",
        text,
    )
    text = re.sub(r"(?i)(set-cookie\s*:\s*)[^\r\n]+", r"\1<redacted>", text)
    return text


def prop_result(base_url: str, path: str, timeout: float) -> dict[str, Any]:
    encoded = urllib.parse.quote(path, safe="/*")
    response = request(base_url, "/api/prop/" + encoded, timeout)
    output = public_http_result(response)
    if "body" not in response:
        return output
    text = response["body"].decode("utf-8", "replace")
    output["parsed"] = diag.parse_prop(text)
    return output


def prop_value(base_url: str, path: str, timeout: float) -> Any:
    result = prop_result(base_url, path, timeout)
    return result.get("parsed", {}).get("value")


def page_state(base_url: str, timeout: float) -> dict[str, Any]:
    return {
        path: prop_result(base_url, path, timeout)
        for path in PAGE_PROPS
    }


def child_refs(parsed: dict[str, Any]) -> list[str]:
    """Addressable ref per child, in order. /api/prop's plain-text output
    (src/prop/prop_http.c, non-html branch) prints each child's real name
    or the literal "<unnamed>" -- never the html-only "*N" form -- so
    unnamed children are addressed by their POSITION among all children
    (same scheme as cli.collect_props)."""
    return [
        "*%d" % index if name == "<unnamed>" else name
        for index, name in enumerate(parsed.get("children", []))
    ]


def enumerate_children(
    base_url: str,
    parent: str,
    fields: list[str],
    limit: int,
    timeout: float,
) -> dict[str, Any]:
    root = prop_result(base_url, parent, timeout)
    refs = child_refs(root.get("parsed", {}))
    rows = []
    for ref in refs[:limit]:
        prefix = f"{parent}/{ref}"
        row = {
            "ref": ref,
            "root": prop_result(base_url, prefix, timeout),
            "fields": {
                field: prop_result(base_url, f"{prefix}/{field}", timeout)
                for field in fields
            },
        }
        rows.append(row)
    return {
        "parent": root,
        "child_count": len(refs),
        "truncated": len(refs) > limit,
        "children": rows,
    }


def http_surface(base_url: str, timeout: float) -> dict[str, Any]:
    tested = {}
    for path, safety, expectation in SAFE_HTTP_PROBES:
        result = public_http_result(request(base_url, path, timeout))
        result["safety"] = safety
        result["expectation"] = expectation
        tested[path] = result
    log_head = public_http_result(
        request(base_url, "/api/logfile/0", timeout, method="HEAD")
    )
    log_head["safety"] = "sensitive-read"
    log_head["expectation"] = "HEAD verifies capability without returning log body"
    tested["/api/logfile/0 (HEAD)"] = log_head
    blocked = {
        path: {
            "status": "not-called",
            "safety": safety,
            "reason": reason,
        }
        for path, safety, reason in BLOCKED_HTTP_PROBES
    }
    return {"tested": tested, "blocked_by_policy": blocked}


def screenshot(base_url: str, output: Path, timeout: float) -> dict[str, Any]:
    raw_response = request(base_url, "/api/screenshot/raw", timeout)
    raw_body = raw_response.pop("body", b"")
    query_response = request(base_url, "/api/screenshot?raw=1", timeout)
    query_body = query_response.pop("body", b"")
    raw = public_http_result({**raw_response, "body": raw_body})
    query = public_http_result({**query_response, "body": query_body})
    result = {
        "raw": raw,
        "query_raw": query,
        "same_sha256": (
            bool(raw_body)
            and bool(query_body)
            and hashlib.sha256(raw_body).digest()
            == hashlib.sha256(query_body).digest()
        ),
    }
    if raw_response.get("status") == 200 and raw_body:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(raw_body)
        raw["output"] = str(output)
        if raw_body.startswith(b"\x89PNG\r\n\x1a\n") and len(raw_body) >= 24:
            raw["png_width"], raw["png_height"] = struct.unpack(
                "!II", raw_body[16:24]
            )
    return result


def log_summary(path: Path, tail: int) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    counts = {
        name: sum(1 for line in lines if pattern.search(line))
        for name, pattern in SIGNALS.items()
    }
    selected = [
        redact(line)
        for line in lines
        if any(pattern.search(line) for pattern in SIGNALS.values())
    ]
    return {
        "path": str(path),
        "line_count": len(lines),
        "signal_counts": counts,
        "signal_tail": selected[-tail:],
    }


def websocket_message(opcode: int, data: bytes) -> dict[str, Any]:
    result: dict[str, Any] = {
        "opcode": opcode,
        "bytes": len(data),
    }
    if opcode == 1:
        raw = data.decode("utf-8", "replace")
        result["raw"] = raw[:1024]
        try:
            result["message"] = json.loads(raw)
            result["json_valid"] = True
        except ValueError as error:
            result["json_valid"] = False
            result["json_error"] = str(error)
    else:
        result["hex"] = data[:128].hex()
    return result


def recv_messages(sock: socket.socket, seconds: float, maximum: int) -> list[dict[str, Any]]:
    deadline = time.monotonic() + seconds
    messages = []
    while len(messages) < maximum and time.monotonic() < deadline:
        sock.settimeout(max(0.05, min(0.5, deadline - time.monotonic())))
        try:
            opcode, data = ws.recv_frame(sock)
        except socket.timeout:
            continue
        if opcode == 9:
            ws.send_frame(sock, 10, data)
            continue
        messages.append(websocket_message(opcode, data))
    return messages


def stpp_audit(base_url: str, timeout: float) -> dict[str, Any]:
    parsed_url = urllib.parse.urlsplit(base_url)
    host = parsed_url.hostname or "127.0.0.1"
    port = parsed_url.port or 80
    result: dict[str, Any] = {
        "remote_controlled_before": prop_value(
            base_url, "global/stpp/remoteControlled", timeout
        )
    }

    json_sock = ws.connect(host, port)
    try:
        result["remote_controlled_during_json"] = prop_value(
            base_url, "global/stpp/remoteControlled", timeout
        )
        ws.send_text(json_sock, json.dumps([1, 101, 0, "app.systemname"]))
        ws.send_text(json_sock, json.dumps([1, 102, 0, "services"]))
        ws.send_text(
            json_sock,
            json.dumps([1, 103, 0, "navigators.current.currentpage.model.nodes"]),
        )
        result["json_messages"] = recv_messages(json_sock, 2.0, 96)
        for sid in (101, 102, 103):
            ws.send_text(json_sock, json.dumps([2, sid]))
    finally:
        ws.close(json_sock)

    time.sleep(0.1)
    result["remote_controlled_after_json"] = prop_value(
        base_url, "global/stpp/remoteControlled", timeout
    )
    result["json_invalid_frames"] = sum(
        1
        for message in result.get("json_messages", [])
        if message.get("json_valid") is False
    )

    binary_sock = ws.connect(host, port)
    try:
        ws.send_binary(binary_sock, bytes((0, 3, 0)))
        opcode, data = ws.recv_frame(binary_sock)
        result["binary_hello"] = {
            "opcode": opcode,
            "length": len(data),
            "command": data[0] if data else None,
            "version": data[1] if len(data) > 1 else None,
            "running_instance": data[2:18].hex() if len(data) >= 18 else None,
            "flags": data[18] if len(data) > 18 else None,
            "valid_v3_reply": opcode == 2
            and len(data) == 19
            and data[:2] == b"\x00\x03",
        }
    finally:
        ws.close(binary_sock)

    echo_sock = ws.connect(host, port, "/api/ws/echo")
    try:
        token = "movian-agent-echo"
        ws.send_text(echo_sock, token)
        opcode, data = ws.recv_frame(echo_sock)
        result["websocket_echo"] = {
            "opcode": opcode,
            "value": data.decode("utf-8", "replace"),
            "matches": opcode == 1 and data.decode("utf-8", "replace") == token,
        }
    finally:
        ws.close(echo_sock)

    time.sleep(0.1)
    result["remote_controlled_final"] = prop_value(
        base_url, "global/stpp/remoteControlled", timeout
    )
    return result


def ecmascript_snapshot(base_url: str, timeout: float, include_paths: bool) -> dict[str, Any]:
    response = request(base_url, "/api/ecmascript/stats", timeout)
    if "body" not in response:
        return public_http_result(response)
    text = response["body"].decode("utf-8", "replace")
    return {
        "http": public_http_result(response),
        "contexts": diag.parse_stats(text, include_paths),
    }


def collect(args: argparse.Namespace) -> dict[str, Any]:
    diag_response = request(args.base_url, "/api/diag", args.timeout)
    diag_text = diag_response.get("body", b"").decode("utf-8", "replace")
    version = re.search(
        r"<strong>movian</strong>\s+Version\s+([^<]+)", diag_text, re.IGNORECASE
    )
    output: dict[str, Any] = {
        "schema_version": 1,
        "captured_at": now(),
        "target": {
            "base_url": args.base_url.rstrip("/"),
            "movian_version": version.group(1).strip() if version else None,
            "diag": public_http_result(diag_response),
        },
        "page": page_state(args.base_url, args.timeout),
        "nodes": enumerate_children(
            args.base_url,
            "global/navigators/current/currentpage/model/nodes",
            NODE_FIELDS,
            args.node_limit,
            args.timeout,
        ),
        "popups": enumerate_children(
            args.base_url,
            "global/popups",
            ["type", "message", "title", "username", "domain"],
            args.popup_limit,
            args.timeout,
        ),
        "ecmascript": {
            "before": ecmascript_snapshot(
                args.base_url, args.timeout, args.include_paths
            )
        },
    }
    for path in args.prop:
        output.setdefault("extra_props", {})[path] = prop_result(
            args.base_url, path, args.timeout
        )
    if args.http_surface or args.full:
        output["http_surface"] = http_surface(args.base_url, args.timeout)
    if args.stpp or args.full:
        output["stpp"] = stpp_audit(args.base_url, args.timeout)
    if args.screenshot:
        output["screenshot"] = screenshot(
            args.base_url, args.screenshot, max(args.timeout, 15)
        )
    if args.log:
        output["log"] = log_summary(args.log, args.log_tail)
    if args.gc:
        output["ecmascript"]["gc"] = public_http_result(
            request(args.base_url, "/api/ecmascript/gc", args.timeout)
        )
        output["ecmascript"]["after"] = ecmascript_snapshot(
            args.base_url, args.timeout, args.include_paths
        )
        before = output["ecmascript"]["before"].get("contexts", {})
        after = output["ecmascript"]["after"].get("contexts", {})
        output["ecmascript"]["delta"] = diag.diff_stats(before, after)
    return output


def emit(data: Any, output: Path | None = None) -> None:
    rendered = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)


def page_summary(base_url: str, timeout: float) -> dict[str, Any]:
    keys = PAGE_PROPS[1:5]
    return {path: prop_value(base_url, path, timeout) for path in keys}


def run_open(args: argparse.Namespace) -> int:
    before = page_summary(args.base_url, args.timeout)
    path = "/api/open?" + urllib.parse.urlencode({"url": args.url})
    response = public_http_result(request(args.base_url, path, args.timeout))
    deadline = time.monotonic() + args.wait
    after = page_summary(args.base_url, args.timeout)
    while time.monotonic() < deadline:
        after = page_summary(args.base_url, args.timeout)
        loading = after.get(PAGE_PROPS[3])
        current_url = after.get(PAGE_PROPS[1])
        if loading != "1" and (not args.expect_url or current_url == args.expect_url):
            break
        time.sleep(0.2)
    emit({"before": before, "request": response, "after": after})
    return 0 if response.get("ok") else 1


def run_action(args: argparse.Namespace) -> int:
    before = page_summary(args.base_url, args.timeout)
    path = "/api/input/action/" + urllib.parse.quote(args.action, safe="")
    response = public_http_result(request(args.base_url, path, args.timeout))
    time.sleep(args.settle)
    after = page_summary(args.base_url, args.timeout)
    emit({"before": before, "request": response, "after": after})
    return 0 if response.get("ok") else 1


def run_prop_debug(args: argparse.Namespace) -> int:
    encoded = urllib.parse.quote(args.path, safe="/*")
    response = request(
        args.base_url,
        "/api/prop/" + encoded,
        args.timeout,
        method="POST",
        form={"debug": args.mode},
    )
    emit(
        {
            "mutation": "PROP_DEBUG_THIS",
            "warning": (
                "HTTP OK proves only flag mutation; verify that the target "
                "branch has a PROP_DEBUG_THIS consumer before expecting logs"
            ),
            "path": args.path,
            "mode": args.mode,
            "request": public_http_result(response),
            "prop": prop_result(args.base_url, args.path, args.timeout),
        }
    )
    return 0 if response.get("ok") else 1


def run_watch(args: argparse.Namespace) -> int:
    parsed_url = urllib.parse.urlsplit(args.base_url)
    sock = ws.connect(parsed_url.hostname or "127.0.0.1", parsed_url.port or 80)
    subscriptions = list(enumerate(args.paths, 1))
    try:
        for sid, path in subscriptions:
            ws.send_text(sock, json.dumps([1, sid, 0, path]))
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            sock.settimeout(max(0.05, min(0.5, deadline - time.monotonic())))
            try:
                opcode, data = ws.recv_frame(sock)
            except socket.timeout:
                continue
            if opcode == 9:
                ws.send_frame(sock, 10, data)
                continue
            event = websocket_message(opcode, data)
            event["received_at"] = now()
            print(json.dumps(event, ensure_ascii=False, sort_keys=True), flush=True)
    finally:
        for sid, _path in subscriptions:
            ws.send_text(sock, json.dumps([2, sid]))
        ws.close(sock)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def connection_options(target: argparse.ArgumentParser) -> None:
        target.add_argument("--base-url", default="http://127.0.0.1:42000")
        target.add_argument("--timeout", type=float, default=5.0)

    snap = subparsers.add_parser("snapshot", help="Capture structured diagnostics")
    connection_options(snap)
    snap.add_argument("--node-limit", type=int, default=20)
    snap.add_argument("--popup-limit", type=int, default=4)
    snap.add_argument("--prop", action="append", default=[])
    snap.add_argument(
        "--full",
        action="store_true",
        help="Include the safe HTTP matrix and STPP/echo lifecycle audit",
    )
    snap.add_argument("--http-surface", action="store_true")
    snap.add_argument("--stpp", action="store_true")
    snap.add_argument("--gc", action="store_true")
    snap.add_argument("--include-paths", action="store_true")
    snap.add_argument("--screenshot", type=Path)
    snap.add_argument("--log", type=Path)
    snap.add_argument("--log-tail", type=int, default=120)
    snap.add_argument("--output", type=Path)

    watch = subparsers.add_parser("watch", help="Stream STPP events as JSONL")
    connection_options(watch)
    watch.add_argument("--seconds", type=float, default=10.0)
    watch.add_argument("paths", nargs="+")

    open_parser = subparsers.add_parser("open", help="Open a URL and show before/after")
    connection_options(open_parser)
    open_parser.add_argument("url")
    open_parser.add_argument("--expect-url")
    open_parser.add_argument("--wait", type=float, default=15.0)

    action = subparsers.add_parser("action", help="Send a UI action and show before/after")
    connection_options(action)
    action.add_argument("action")
    action.add_argument("--settle", type=float, default=0.5)

    prop = subparsers.add_parser("prop", help="Read and parse one prop")
    connection_options(prop)
    prop.add_argument("path")

    prop_debug = subparsers.add_parser(
        "prop-debug", help="Explicitly enable or disable PROP_DEBUG_THIS"
    )
    connection_options(prop_debug)
    prop_debug.add_argument("path")
    prop_debug.add_argument("mode", choices=("on", "off"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "snapshot":
            emit(collect(args), args.output)
            return 0
        if args.command == "watch":
            return run_watch(args)
        if args.command == "open":
            return run_open(args)
        if args.command == "action":
            return run_action(args)
        if args.command == "prop":
            emit(prop_result(args.base_url, args.path, args.timeout))
            return 0
        if args.command == "prop-debug":
            return run_prop_debug(args)
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
