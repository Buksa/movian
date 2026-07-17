#!/usr/bin/env python3
"""Protocol and feature smoke for ``movian-lsp --stdio`` (issue #99).

The main session exercises each MVP method against the real flat skin.  It
also emits a literal-stdout-frame mode for the executor report, while the
normal mode compares behavior with the committed golden JSON contract.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from lsp_client import LspClient


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SERVER = REPOSITORY_ROOT / "support" / "devtools" / "movian-lsp"
GOLDEN = Path(__file__).with_name("golden") / "stdio-session.json"
UTF16_FIXTURE = Path(__file__).with_name("fixtures") / \
    "utf16-crlf-emoji-tabs.json"


def utf16_length(text: str) -> int:
    return sum(2 if ord(char) > 0xFFFF else 1 for char in text)


def position_of(text: str, word: str) -> dict[str, int]:
    for line_number, line in enumerate(text.splitlines()):
        column = line.find(word)
        if column >= 0:
            return {"line": line_number,
                    "character": utf16_length(line[:column])}
    raise AssertionError("%r not found" % word)


def did_open(uri: str, text: str, version: int = 1) -> dict[str, Any]:
    return {
        "textDocument": {
            "uri": uri,
            "languageId": "glw",
            "version": version,
            "text": text,
        }
    }


def diagnostics_for(client: LspClient, uri: str,
                    version: int | None = None) -> dict[str, Any]:
    return client.wait_for_notification(
        "textDocument/publishDiagnostics",
        lambda params: params.get("uri") == uri
        and (version is None or params.get("version") == version),
    )


def assert_empty_diagnostics(client: LspClient, uri: str,
                             version: int | None = None) -> None:
    params = diagnostics_for(client, uri, version)
    if params.get("diagnostics") != []:
        raise AssertionError("expected clean diagnostics for %s: %s" %
                             (uri, params))


def shutdown(client: LspClient, request_id: int) -> None:
    if client.request(request_id, "shutdown", {}) is not None:
        raise AssertionError("shutdown must return null")
    client.notify("exit", {})
    exit_code, stderr = client.close_after_exit()
    if exit_code != 0 or stderr:
        raise AssertionError("server exit=%s stderr=%r" % (exit_code, stderr))


def run_main_session(server: Path, golden: dict[str, Any]) -> list[bytes]:
    client = LspClient(server, REPOSITORY_ROOT)
    log_path = REPOSITORY_ROOT / "glwskins" / "flat" / "log.view"
    background_path = REPOSITORY_ROOT / "glwskins" / "flat" / "background.view"
    log_uri = log_path.as_uri()
    background_uri = background_path.as_uri()
    log_text = log_path.read_text(encoding="utf-8")
    background_text = background_path.read_text(encoding="utf-8")
    try:
        initialized = client.request(
            1, "initialize", {"rootUri": REPOSITORY_ROOT.as_uri()})
        if initialized != golden["initialize"]:
            raise AssertionError("initialize golden mismatch: %s" % initialized)
        client.notify("initialized", {})

        client.notify("textDocument/didOpen", did_open(log_uri, log_text))
        assert_empty_diagnostics(client, log_uri, 1)

        symbols = client.request(
            2, "textDocument/documentSymbol", {"textDocument": {"uri": log_uri}})
        names = {symbol.get("name") for symbol in symbols}
        missing_symbols = set(golden["symbolNames"]) - names
        if missing_symbols:
            raise AssertionError("documentSymbol missing %s" % missing_symbols)

        for request_id, word in ((3, "translate"), (4, "container_x")):
            result = client.request(request_id, "textDocument/hover", {
                "textDocument": {"uri": log_uri},
                "position": position_of(log_text, word),
            })
            value = result["contents"]["value"] if result else ""
            if golden["hoverNeedles"][word] not in value:
                raise AssertionError("hover for %s did not use metadata: %s" %
                                     (word, value))

        client.notify("textDocument/didOpen", did_open(background_uri,
                                                         background_text))
        assert_empty_diagnostics(client, background_uri, 1)
        source_hover = client.request(5, "textDocument/hover", {
            "textDocument": {"uri": background_uri},
            "position": position_of(background_text, "source"),
        })
        source_value = source_hover["contents"]["value"] if source_hover else ""
        if golden["hoverNeedles"]["source"] not in source_value:
            raise AssertionError("hover for source did not use metadata: %s" %
                                 source_value)

        definition = client.request(6, "textDocument/definition", {
            "textDocument": {"uri": log_uri},
            "position": {"line": 0, "character": 1},
        })
        if not definition:
            raise AssertionError("#import definition was empty")
        definition_path = Path(definition[0]["uri"].removeprefix("file://"))
        if definition_path.resolve() != (REPOSITORY_ROOT /
                                         golden["definition"]).resolve():
            raise AssertionError("wrong #import definition: %s" % definition)

        workspace = client.request(7, "workspace/symbol", {"query": "log.view"})
        if not any(symbol.get("name") == golden["workspaceSymbol"]
                   for symbol in workspace):
            raise AssertionError("workspace/symbol did not find log.view")

        client.notify("textDocument/didClose", {"textDocument": {
            "uri": background_uri,
        }})
        assert_empty_diagnostics(client, background_uri)

        bad_text = log_text + "`\n"
        client.notify("textDocument/didChange", {
            "textDocument": {"uri": log_uri, "version": 2},
            "contentChanges": [{"text": bad_text}],
        })
        changed = diagnostics_for(client, log_uri, 2)
        diagnostics = changed.get("diagnostics", [])
        if len(diagnostics) != 1:
            raise AssertionError("expected one exact changed diagnostic: %s" %
                                 changed)
        diagnostic = diagnostics[0]
        expected = golden["changedDiagnostic"]
        if diagnostic.get("source") != expected["source"] \
                or diagnostic.get("message") != expected["message"] \
                or diagnostic["range"]["start"]["line"] != expected["line"]:
            raise AssertionError("changed diagnostic mismatch: %s" % diagnostic)

        client.notify("textDocument/didClose", {"textDocument": {
            "uri": log_uri,
        }})
        assert_empty_diagnostics(client, log_uri)
        shutdown(client, 8)
        return client.frames
    except BaseException:
        if client.process.poll() is None:
            client.process.kill()
            client.process.wait()
        raise


def run_fallback_session(server: Path) -> list[bytes]:
    """Prove lexer failure retains the previous successful token stream."""

    client = LspClient(server, REPOSITORY_ROOT)
    fixture = REPOSITORY_ROOT / "tests" / "tooling" / "glw" / "fixtures" / \
        "invalid-char.view"
    uri = fixture.as_uri()
    broken = fixture.read_text(encoding="utf-8")
    clean = broken.replace("`", "0")
    try:
        client.request(11, "initialize", {"rootUri": REPOSITORY_ROOT.as_uri()})
        client.notify("initialized", {})
        client.notify("textDocument/didOpen", did_open(uri, clean))
        assert_empty_diagnostics(client, uri, 1)

        client.notify("textDocument/didChange", {
            "textDocument": {"uri": uri, "version": 2},
            "contentChanges": [{"text": broken}],
        })
        changed = diagnostics_for(client, uri, 2)
        diagnostics = changed.get("diagnostics", [])
        if len(diagnostics) != 1 or diagnostics[0].get("message") != "Invalid char '`'":
            raise AssertionError("expected fixture lexer diagnostic: %s" % changed)

        symbols = client.request(12, "textDocument/documentSymbol", {
            "textDocument": {"uri": uri},
        })
        if not symbols or not any(symbol.get("name") == "label" for symbol in symbols):
            raise AssertionError("last-good token fallback did not serve symbols: %s" %
                                 symbols)
        client.notify("textDocument/didClose", {"textDocument": {"uri": uri}})
        assert_empty_diagnostics(client, uri)
        shutdown(client, 13)
        return client.frames
    except BaseException:
        if client.process.poll() is None:
            client.process.kill()
            client.process.wait()
        raise


def run_utf16_session(server: Path) -> list[bytes]:
    fixture = json.loads(UTF16_FIXTURE.read_text(encoding="utf-8"))
    client = LspClient(server, REPOSITORY_ROOT)
    try:
        client.request(21, "initialize", {"rootUri": REPOSITORY_ROOT.as_uri()})
        client.notify("initialized", {})
        client.notify("textDocument/didOpen", did_open(fixture["uri"],
                                                         fixture["text"]))
        params = diagnostics_for(client, fixture["uri"], 1)
        diagnostics = params.get("diagnostics", [])
        if len(diagnostics) != 1:
            raise AssertionError("UTF-16 fixture expected one diagnostic: %s" %
                                 params)
        diagnostic = diagnostics[0]
        expected = fixture["expected"]
        if diagnostic.get("source") != expected["source"] \
                or diagnostic.get("message") != expected["message"] \
                or diagnostic["range"]["start"]["line"] != expected["line"] \
                or diagnostic["range"]["end"]["character"] != expected["endCharacter"]:
            raise AssertionError("UTF-16 range mismatch: %s" % diagnostic)
        client.notify("textDocument/didClose", {"textDocument": {
            "uri": fixture["uri"],
        }})
        assert_empty_diagnostics(client, fixture["uri"])
        shutdown(client, 22)
        return client.frames
    except BaseException:
        if client.process.poll() is None:
            client.process.kill()
            client.process.wait()
        raise


def print_transcript(label: str, frames: list[bytes]) -> None:
    stream = sys.stdout.buffer
    stream.write(("--- %s stdout frames (verbatim) ---\n" % label).encode())
    for frame in frames:
        stream.write(frame)
        stream.write(b"\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("server", nargs="?", type=Path, default=DEFAULT_SERVER)
    parser.add_argument("--transcript", action="store_true",
                        help="print every server stdout frame verbatim")
    args = parser.parse_args()
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    main_frames = run_main_session(args.server, golden)
    fallback_frames = run_fallback_session(args.server)
    utf16_frames = run_utf16_session(args.server)
    if args.transcript:
        print_transcript("main", main_frames)
        print_transcript("fallback", fallback_frames)
        print_transcript("utf16", utf16_frames)
    print(json.dumps({
        "fallbackFrames": len(fallback_frames),
        "mainFrames": len(main_frames),
        "status": "LSP SMOKE OK",
        "utf16Frames": len(utf16_frames),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
