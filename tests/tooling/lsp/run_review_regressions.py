#!/usr/bin/env python3
"""Focused regressions for the PR #108 review findings on movian-lsp."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any


sys.dont_write_bytecode = True

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEVTOOLS_ROOT = REPOSITORY_ROOT / "support" / "devtools"
if str(DEVTOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(DEVTOOLS_ROOT))

from lsp.server import LspServer  # noqa: E402
from lsp_client import LspClient  # noqa: E402


SERVER = REPOSITORY_ROOT / "support" / "devtools" / "movian-lsp"


def did_open(uri: str, text: str, version: int = 1) -> dict[str, Any]:
    return {
        "textDocument": {
            "uri": uri,
            "languageId": "glw",
            "version": version,
            "text": text,
        }
    }


def shutdown(client: LspClient, request_id: int) -> None:
    if client.request(request_id, "shutdown", {}) is not None:
        raise AssertionError("shutdown must return null")
    client.notify("exit", {})
    exit_code, stderr = client.close_after_exit()
    if exit_code != 0 or stderr:
        raise AssertionError("server exit=%s stderr=%r" % (exit_code, stderr))


def abort(client: LspClient) -> None:
    if client.process.poll() is None:
        client.process.kill()
        client.process.wait()


def assert_no_empty_diagnostics(client: LspClient, uri: str,
                                timeout: float = 0.35) -> None:
    try:
        params = client.wait_for_notification(
            "textDocument/publishDiagnostics",
            lambda candidate: candidate.get("uri") == uri
            and candidate.get("diagnostics") == [],
            timeout=timeout,
        )
    except TimeoutError:
        return
    raise AssertionError("unexpected diagnostics clear for %s: %s" %
                         (uri, params))


def framed_messages(raw: bytes) -> list[dict[str, Any]]:
    """Decode only the JSON-RPC frames emitted by an in-process server."""

    messages: list[dict[str, Any]] = []
    offset = 0
    while offset < len(raw):
        header_end = raw.find(b"\r\n\r\n", offset)
        if header_end < 0:
            raise AssertionError("unterminated JSON-RPC header: %r" % raw[offset:])
        headers = raw[offset:header_end].decode("ascii").split("\r\n")
        content_length = None
        for header in headers:
            key, value = header.split(":", 1)
            if key.lower() == "content-length":
                content_length = int(value.strip())
                break
        if content_length is None:
            raise AssertionError("missing Content-Length: %r" % headers)
        body_start = header_end + 4
        body_end = body_start + content_length
        if body_end > len(raw):
            raise AssertionError("short JSON-RPC body")
        messages.append(json.loads(raw[body_start:body_end].decode("utf-8")))
        offset = body_end
    return messages


def run_f1_command_builder() -> None:
    """The analyzer command must carry its explicit skin root."""

    server = LspServer(input_stream=io.BytesIO(), output_stream=io.BytesIO())
    server.workspace_root = REPOSITORY_ROOT / "glwskins" / "flat"
    command = server._analyzer_command(
        "--check", REPOSITORY_ROOT / "glwskins" / "flat" / "log.view")
    try:
        skin_index = command.index("--skin")
    except ValueError as exc:
        raise AssertionError("analyzer command omitted --skin: %s" % command) from exc
    if command[skin_index + 1] != str(server.skin_root):
        raise AssertionError("wrong --skin value: %s" % command)


def run_f2_shared_import_cleanup() -> None:
    """Closing one claimant must not clear a shared imported diagnostic URI."""

    with tempfile.TemporaryDirectory(prefix="movian-lsp-f2-") as directory:
        root = Path(directory)
        target = root / "shared-broken.view"
        first = root / "first.view"
        second = root / "second.view"
        target.write_text("`\n", encoding="utf-8")
        import_text = '#import "shared-broken.view"\n'
        first.write_text(import_text, encoding="utf-8")
        second.write_text(import_text, encoding="utf-8")
        target_uri = target.as_uri()
        first_uri = first.as_uri()
        second_uri = second.as_uri()
        client = LspClient(SERVER, REPOSITORY_ROOT)
        try:
            client.request(1, "initialize", {"rootUri": root.as_uri()})
            client.notify("initialized", {})
            client.notify("textDocument/didOpen", did_open(first_uri, import_text))
            first_diagnostics = client.wait_for_notification(
                "textDocument/publishDiagnostics",
                lambda params: params.get("uri") == target_uri
                and len(params.get("diagnostics", [])) == 1,
            )
            if first_diagnostics["diagnostics"][0].get("source") != "movian-glw":
                raise AssertionError("unexpected shared diagnostic: %s" %
                                     first_diagnostics)

            client.notify("textDocument/didOpen", did_open(second_uri, import_text))
            client.wait_for_notification(
                "textDocument/publishDiagnostics",
                lambda params: params.get("uri") == target_uri
                and len(params.get("diagnostics", [])) == 1,
            )

            client.notify("textDocument/didClose", {
                "textDocument": {"uri": first_uri},
            })
            client.wait_for_notification(
                "textDocument/publishDiagnostics",
                lambda params: params.get("uri") == first_uri
                and params.get("diagnostics") == [],
            )
            assert_no_empty_diagnostics(client, target_uri)

            client.notify("textDocument/didClose", {
                "textDocument": {"uri": second_uri},
            })
            client.wait_for_notification(
                "textDocument/publishDiagnostics",
                lambda params: params.get("uri") == target_uri
                and params.get("diagnostics") == [],
            )
            shutdown(client, 2)
        except BaseException:
            abort(client)
            raise


def run_f3_close_race() -> None:
    """A running debounced analysis must not publish after didClose."""

    with tempfile.TemporaryDirectory(prefix="movian-lsp-f3-") as directory:
        source = Path(directory) / "race.view"
        source.write_text("widget(label, {})\n", encoding="utf-8")
        output = io.BytesIO()
        server = LspServer(input_stream=io.BytesIO(), output_stream=output,
                           debounce_ms=1)
        server.workspace_root = source.parent
        analyzer_started = threading.Event()
        release_analyzer = threading.Event()
        publish_attempted = threading.Event()
        original_publish = server._publish_diagnostics

        def controlled_analyzer(mode: str,
                                _temporary_path: Path) -> tuple[Any, str | None]:
            if mode == "--check":
                analyzer_started.set()
                if not release_analyzer.wait(timeout=2.0):
                    raise AssertionError("test did not release analyzer")
                return {"ok": True}, None
            return {"tokens": []}, None

        def tracked_publish(*args: Any, **kwargs: Any) -> None:
            try:
                original_publish(*args, **kwargs)
            finally:
                publish_attempted.set()

        server._run_analyzer = controlled_analyzer  # type: ignore[method-assign]
        server._publish_diagnostics = tracked_publish  # type: ignore[method-assign]
        uri = source.as_uri()
        try:
            server._dispatch({
                "jsonrpc": "2.0",
                "method": "textDocument/didOpen",
                "params": did_open(uri, source.read_text(encoding="utf-8")),
            })
            if not analyzer_started.wait(timeout=2.0):
                raise AssertionError("scheduled analysis did not start")

            server._dispatch({
                "jsonrpc": "2.0",
                "method": "textDocument/didClose",
                "params": {"textDocument": {"uri": uri}},
            })
            close_messages = framed_messages(output.getvalue())
            expected_close = [{
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {"uri": uri, "diagnostics": []},
            }]
            if close_messages != expected_close:
                raise AssertionError("unexpected didClose frames: %s" %
                                     close_messages)

            release_analyzer.set()
            if not publish_attempted.wait(timeout=2.0):
                raise AssertionError("analysis did not reach publish guard")
            deadline = time.monotonic() + 0.25
            while time.monotonic() < deadline:
                if framed_messages(output.getvalue()) != close_messages:
                    raise AssertionError("analysis published after didClose")
                time.sleep(0.025)
        finally:
            release_analyzer.set()
            server._cancel_all_timers()


def run_f5_definition_confinement() -> None:
    """Definition must reject an absolute import that escapes both roots."""

    with tempfile.TemporaryDirectory(prefix="movian-lsp-f5-root-") as root_dir, \
            tempfile.TemporaryDirectory(prefix="movian-lsp-f5-outside-") as outside_dir:
        root = Path(root_dir)
        outside_target = Path(outside_dir) / "outside.view"
        source = root / "escape.view"
        outside_target.write_text("widget(label, {})\n", encoding="utf-8")
        source_text = '#import "%s"\n' % outside_target
        source.write_text(source_text, encoding="utf-8")
        client = LspClient(SERVER, REPOSITORY_ROOT)
        try:
            client.request(11, "initialize", {"rootUri": root.as_uri()})
            client.notify("initialized", {})
            client.notify("textDocument/didOpen", did_open(source.as_uri(),
                                                              source_text))
            result = client.request(12, "textDocument/definition", {
                "textDocument": {"uri": source.as_uri()},
                "position": {"line": 0, "character": 1},
            })
            if result is not None:
                raise AssertionError("escaped definition was returned: %s" % result)
            client.notify("textDocument/didClose", {
                "textDocument": {"uri": source.as_uri()},
            })
            shutdown(client, 13)
        except BaseException:
            abort(client)
            raise


def run_f7_hidden_workspace_snapshot() -> None:
    """Workspace symbols must ignore transient dot-prefixed snapshots."""

    fixture_directory = REPOSITORY_ROOT / "tests" / "tooling" / "glw" / "fixtures"
    snapshot = fixture_directory / ".movian-lsp-tmp.view"
    if snapshot.exists():
        raise AssertionError("refusing to overwrite existing fixture %s" % snapshot)
    snapshot.write_text("widget(label, {})\n", encoding="utf-8")
    client = LspClient(SERVER, REPOSITORY_ROOT)
    try:
        client.request(21, "initialize", {"rootUri": REPOSITORY_ROOT.as_uri()})
        client.notify("initialized", {})
        symbols = client.request(22, "workspace/symbol", {
            "query": "movian-lsp-tmp",
        })
        if any(symbol.get("location", {}).get("uri") == snapshot.as_uri()
               for symbol in symbols):
            raise AssertionError("workspace/symbol returned snapshot: %s" % symbols)
        shutdown(client, 23)
    except BaseException:
        abort(client)
        raise
    finally:
        snapshot.unlink(missing_ok=True)


def main() -> int:
    run_f1_command_builder()
    run_f2_shared_import_cleanup()
    run_f3_close_race()
    run_f5_definition_confinement()
    run_f7_hidden_workspace_snapshot()
    print(json.dumps({
        "checks": 5,
        "status": "LSP REVIEW REGRESSIONS OK",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
