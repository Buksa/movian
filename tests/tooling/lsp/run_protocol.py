#!/usr/bin/env python3
"""Exercise stdio JSON-RPC errors and document lifecycle transitions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from lsp_client import LspClient


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SERVER = REPOSITORY_ROOT / "support" / "devtools" / "movian-lsp"
FIXTURE = Path(__file__).with_name("fixtures") / "diagnostics" / "valid.view"


def send_raw(client: LspClient, payload: bytes) -> None:
    assert client.process.stdin is not None
    client.process.stdin.write(
        ("Content-Length: %d\r\n\r\n" % len(payload)).encode("ascii") +
        payload)
    client.process.stdin.flush()


def wait_diagnostics(client: LspClient, uri: str,
                     version: int | None = None) -> dict:
    return client.wait_for_notification(
        "textDocument/publishDiagnostics",
        lambda params: params.get("uri") == uri
        and (version is None or params.get("version") == version),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("server", nargs="?", type=Path, default=DEFAULT_SERVER)
    args = parser.parse_args()
    client = LspClient(args.server, REPOSITORY_ROOT)
    uri = FIXTURE.as_uri()
    text = FIXTURE.read_text(encoding="utf-8")
    try:
        initialized = client.request(
            1, "initialize", {"rootUri": REPOSITORY_ROOT.as_uri()})
        if initialized.get("serverInfo", {}).get("name") != "movian-lsp":
            raise AssertionError("initialize did not identify movian-lsp")
        client.notify("initialized", {})

        client.send({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "unknown/method",
            "params": {},
        })
        unknown = client.wait_for(lambda message: message.get("id") == 2)
        if unknown.get("error", {}).get("code") != -32601:
            raise AssertionError("unknown method response: %s" % unknown)

        send_raw(client, b"not-json")
        parse_error = client.wait_for(
            lambda message: message.get("error", {}).get("code") == -32700)
        if parse_error.get("id") is not None:
            raise AssertionError("parse error must have a null id")

        client.notify("textDocument/didOpen", {"textDocument": {
            "uri": uri,
            "languageId": "glw",
            "version": 1,
            "text": text,
        }})
        opened = wait_diagnostics(client, uri, 1)
        if opened.get("diagnostics") != []:
            raise AssertionError("valid open produced diagnostics: %s" % opened)

        changed_text = text + "`\n"
        client.notify("textDocument/didChange", {
            "textDocument": {"uri": uri, "version": 2},
            "contentChanges": [{"text": changed_text}],
        })
        changed = wait_diagnostics(client, uri, 2)
        if len(changed.get("diagnostics", [])) != 1:
            raise AssertionError("change diagnostic missing: %s" % changed)

        client.notify("textDocument/didClose", {"textDocument": {"uri": uri}})
        closed = wait_diagnostics(client, uri)
        if closed.get("diagnostics") != []:
            raise AssertionError("close did not clear diagnostics: %s" % closed)

        if client.request(3, "shutdown", {}) is not None:
            raise AssertionError("shutdown must return null")
        client.notify("exit", {})
        exit_code, stderr = client.close_after_exit()
        if exit_code != 0 or stderr:
            raise AssertionError("server exit=%s stderr=%r" % (exit_code, stderr))
        print(json.dumps({
            "frames": len(client.frames),
            "status": "LSP PROTOCOL OK",
        }, sort_keys=True))
        return 0
    except BaseException:
        if client.process.poll() is None:
            client.process.kill()
            client.process.wait()
        raise


if __name__ == "__main__":
    sys.exit(main())
