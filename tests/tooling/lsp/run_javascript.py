#!/usr/bin/env python3
"""Exercise metadata-backed JavaScript completion contexts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from lsp_client import LspClient


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SERVER = REPOSITORY_ROOT / "support" / "devtools" / "movian-lsp"
FIXTURE = Path(__file__).with_name("fixtures") / "javascript-completion.js"


def labels(result: Any) -> list[str]:
    if isinstance(result, dict):
        result = result.get("items")
    if not isinstance(result, list) \
            or any(not isinstance(item, dict)
                   or not isinstance(item.get("label"), str)
                   for item in result):
        raise AssertionError("malformed completion result: %s" % result)
    return [item["label"] for item in result]


def position(text: str, line_text: str, character: int | None = None) -> dict[str, int]:
    for line_number, line in enumerate(text.splitlines()):
        if line == line_text:
            return {"line": line_number,
                    "character": len(line) if character is None else character}
    raise AssertionError("line not found: %r" % line_text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("server", nargs="?", type=Path, default=DEFAULT_SERVER)
    args = parser.parse_args()
    artifact = json.loads(
        (REPOSITORY_ROOT / "generated" / "movian-metadata.json").read_text(
            encoding="utf-8"))
    js = artifact["js"]
    objects = {record["name"]: record for record in js["globals"]["objects"]}
    legacy = {record["name"]: record for record in js["legacyGlobals"]}
    modules = {record["name"]: record for record in js["modules"]}
    text = FIXTURE.read_text(encoding="utf-8")
    uri = FIXTURE.as_uri()
    client = LspClient(args.server, REPOSITORY_ROOT)
    request_id = 1
    try:
        client.request(request_id, "initialize", {
            "rootUri": REPOSITORY_ROOT.as_uri(),
        })
        client.notify("initialized", {})
        client.notify("textDocument/didOpen", {"textDocument": {
            "uri": uri,
            "languageId": "javascript",
            "version": 1,
            "text": text,
        }})

        def complete(line_text: str, character: int | None = None) -> list[str]:
            nonlocal request_id
            request_id += 1
            return labels(client.request(request_id, "textDocument/completion", {
                "textDocument": {"uri": uri},
                "position": position(text, line_text, character),
            }))

        expected_plugin = sorted(
            [record["name"] for record in objects["Plugin"]["properties"]])
        if complete("Plugin.") != expected_plugin:
            raise AssertionError("Plugin completion mismatch")
        if complete("Plugin.", 2) != ["Plugin"]:
            raise AssertionError("root prefix completion mismatch")

        expected_core = sorted(
            [record["name"] for record in objects["Core"]["functions"] +
             objects["Core"]["properties"] if record["name"].startswith("sl")])
        if complete("Core.sl") != expected_core:
            raise AssertionError("Core completion mismatch")

        expected_showtime = sorted(
            [record["name"] for record in legacy["showtime"]["members"]
             if record["name"].startswith("http")])
        if complete("showtime.http") != expected_showtime:
            raise AssertionError("showtime completion mismatch")

        expected_plugin_legacy = sorted(
            [record["name"] for record in legacy["plugin"]["members"]
             if record["name"].startswith("add")])
        if complete("plugin.add") != expected_plugin_legacy:
            raise AssertionError("plugin completion mismatch")

        fs_exports = modules["fs"]["exports"]
        if complete("fs.re") != sorted(
                (record["name"] for record in fs_exports
                 if record["name"].startswith("re")), key=str.casefold):
            raise AssertionError("fs completion mismatch")

        native_functions = modules["native/fs"]["functions"]
        if complete("nativeFs.re") != sorted(
                record["name"] for record in native_functions
                if record["name"].startswith("re")):
            raise AssertionError("native/fs completion mismatch")

        settings_export = next(record for record in modules["movian/settings"]["exports"]
                               if record["name"] == "globalSettings")
        expected_settings = sorted(
            record["name"] for record in settings_export["receiverMembers"]
            if record["name"].startswith("ge"))
        if complete("settings.ge") != expected_settings:
            raise AssertionError("settings receiver completion mismatch")

        page_route_shape = next(shape for shape in modules["movian/page"]["shapes"]
                                if shape["name"] == "Route")
        expected_route = sorted(
            record["name"] for record in page_route_shape["methods"]
            if record["name"].startswith("de"))
        if complete("route.de") != expected_route:
            raise AssertionError("page Route prototype completion mismatch")

        request_shape = next(shape for shape in modules["http"]["shapes"]
                             if shape["name"] == "Request")
        expected_request = sorted(
            record["name"] for record in request_shape["methods"] +
            request_shape["properties"] if record["name"].startswith("on"))
        if complete("response.on") != expected_request:
            raise AssertionError("http Request prototype completion mismatch")

        expected_modules = sorted(
            name for name in modules if name.startswith("native/"))
        if complete("require('native/") != expected_modules:
            raise AssertionError("native module path completion mismatch")

        forbidden = {"EXPECTED_DYNAMIC", "private", "_private"}
        for result in (complete("Plugin."), complete("nativeFs.re")):
            if any(label in forbidden or label.startswith("__") for label in result):
                raise AssertionError("private/dynamic fact leaked: %s" % result)

        client.notify("textDocument/didClose", {"textDocument": {"uri": uri}})
        client.wait_for_notification(
            "textDocument/publishDiagnostics",
            lambda params: params.get("uri") == uri
            and params.get("diagnostics") == [],
        )
        request_id += 1
        if client.request(request_id, "shutdown", {}) is not None:
            raise AssertionError("shutdown must return null")
        client.notify("exit", {})
        exit_code, stderr = client.close_after_exit()
        if exit_code != 0 or stderr:
            raise AssertionError("server exit=%s stderr=%r" % (exit_code, stderr))
        print(json.dumps({
            "contexts": 11,
            "status": "LSP JAVASCRIPT COMPLETION OK",
        }, sort_keys=True))
        return 0
    except BaseException:
        if client.process.poll() is None:
            client.process.kill()
            client.process.wait()
        raise


if __name__ == "__main__":
    sys.exit(main())
