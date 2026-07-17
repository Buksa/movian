#!/usr/bin/env python3
"""Run the issue #99 1000-didChange latency/survival smoke."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True

from lsp_client import LspClient


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SERVER = REPOSITORY_ROOT / "support" / "devtools" / "movian-lsp"


def diagnostics_for(client: LspClient, uri: str, version: int) -> dict:
    return client.wait_for_notification(
        "textDocument/publishDiagnostics",
        lambda params: params.get("uri") == uri and params.get("version") == version,
        timeout=3.0,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("server", nargs="?", type=Path, default=DEFAULT_SERVER)
    parser.add_argument("--changes", type=int, default=1000)
    args = parser.parse_args()
    if args.changes < 1:
        parser.error("--changes must be positive")

    universe = REPOSITORY_ROOT / "glwskins" / "flat" / "universe.view"
    uri = universe.as_uri()
    text = universe.read_text(encoding="utf-8")
    client = LspClient(args.server, REPOSITORY_ROOT)
    try:
        client.request(1, "initialize", {"rootUri": REPOSITORY_ROOT.as_uri()})
        client.notify("initialized", {})
        client.notify("textDocument/didOpen", {"textDocument": {
            "uri": uri,
            "languageId": "glw",
            "version": 1,
            "text": text,
        }})
        opened = diagnostics_for(client, uri, 1)
        if opened.get("diagnostics") != []:
            raise AssertionError("universe.view must open clean: %s" % opened)

        latencies_ms: list[float] = []
        for change in range(1, args.changes + 1):
            version = change + 1
            started = time.monotonic()
            client.notify("textDocument/didChange", {
                "textDocument": {"uri": uri, "version": version},
                "contentChanges": [{"text": text + "` %d\n" % change}],
            })
            params = diagnostics_for(client, uri, version)
            latencies_ms.append((time.monotonic() - started) * 1000.0)
            diagnostics = params.get("diagnostics", [])
            if len(diagnostics) != 1 or diagnostics[0].get("source") != "movian-glw":
                raise AssertionError("change %d diagnostic mismatch: %s" %
                                     (change, params))

        ordered = sorted(latencies_ms)
        p95 = ordered[math.ceil(0.95 * len(ordered)) - 1]
        result = {
            "changes": args.changes,
            "max_ms": round(max(latencies_ms), 3),
            "p50_ms": round(statistics.median(latencies_ms), 3),
            "p95_ms": round(p95, 3),
        }
        if p95 >= 300.0:
            raise AssertionError("p95 must stay below 300 ms: %s" % result)

        client.notify("textDocument/didClose", {"textDocument": {"uri": uri}})
        client.wait_for_notification(
            "textDocument/publishDiagnostics",
            lambda params: params.get("uri") == uri
            and params.get("diagnostics") == [],
        )
        if client.request(2, "shutdown", {}) is not None:
            raise AssertionError("shutdown must return null")
        client.notify("exit", {})
        exit_code, stderr = client.close_after_exit()
        if exit_code != 0 or stderr:
            raise AssertionError("server exit=%s stderr=%r" % (exit_code, stderr))
        print(json.dumps(result, sort_keys=True))
        return 0
    except BaseException:
        if client.process.poll() is None:
            client.process.kill()
            client.process.wait()
        raise


if __name__ == "__main__":
    sys.exit(main())
