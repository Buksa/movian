#!/usr/bin/env python3
"""Focused review and lifecycle regressions for movian-lsp."""

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
COMPLETION_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "completion"


def did_open(uri: str, text: str, version: int = 1,
             language_id: str = "glw") -> dict[str, Any]:
    return {
        "textDocument": {
            "uri": uri,
            "languageId": language_id,
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


def completion_labels(context: str, result: Any) -> list[str]:
    if isinstance(result, dict):
        result = result.get("items")
    if not isinstance(result, list):
        raise AssertionError("%s returned non-list completion: %s" %
                             (context, result))
    labels = []
    for item in result:
        if not isinstance(item, dict) or not isinstance(item.get("label"), str):
            raise AssertionError("%s returned invalid item: %s" %
                                 (context, item))
        labels.append(item["label"])
    return labels


def assert_completion(context: str, result: Any, *,
                      exact: list[str] | None = None,
                      contains: tuple[str, ...] = (),
                      excludes: tuple[str, ...] = ()) -> list[str]:
    labels = completion_labels(context, result)
    if exact is not None and labels != exact:
        raise AssertionError("%s list mismatch: expected=%s actual=%s" %
                             (context, exact, labels))
    missing = [label for label in contains if label not in labels]
    forbidden = [label for label in excludes if label in labels]
    if missing or forbidden:
        raise AssertionError("%s membership mismatch: missing=%s forbidden=%s "
                             "actual=%s" %
                             (context, missing, forbidden, labels))
    return labels


def fixture_text(name: str) -> str:
    return (COMPLETION_FIXTURES / name).read_text(encoding="utf-8")


def cursor_after(text: str, line: int, prefix: str) -> dict[str, int]:
    source_line = text.splitlines()[line]
    if not source_line.startswith(prefix):
        raise AssertionError("fixture line %d does not start with %r: %r" %
                             (line, prefix, source_line))
    return {"line": line, "character": len(prefix)}


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


def run_js_lifecycle_and_metadata_definition() -> None:
    """JS open/save diagnostics and metadata definitions share one document."""

    source = REPOSITORY_ROOT / "plugin_examples" / "async_page_load" / \
        "async_page_load.js"
    uri = source.as_uri()
    broken = "var valid = 1;\nvar identity = (x) => x;\n"
    clean = "var page = require('movian/page');\n// require('movian/page');\n"
    client = LspClient(SERVER, REPOSITORY_ROOT)
    try:
        initialized = client.request(
            31, "initialize", {"rootUri": REPOSITORY_ROOT.as_uri()})
        sync = initialized.get("capabilities", {}).get("textDocumentSync", {})
        if sync.get("save") != {"includeText": True}:
            raise AssertionError("didSave was not advertised: %s" % initialized)
        client.notify("initialized", {})
        client.notify("textDocument/didOpen",
                      did_open(uri, broken, language_id="javascript"))
        opened = client.wait_for_notification(
            "textDocument/publishDiagnostics",
            lambda params: params.get("uri") == uri
            and len(params.get("diagnostics", [])) == 1,
        )
        diagnostics = opened["diagnostics"]
        if diagnostics[0].get("source") != "duktape" \
                or diagnostics[0]["range"]["start"]["line"] != 1:
            raise AssertionError("wrong JS didOpen diagnostic: %s" % opened)

        client.notify("textDocument/didSave", {
            "textDocument": {"uri": uri},
            "text": clean,
        })
        client.wait_for_notification(
            "textDocument/publishDiagnostics",
            lambda params: params.get("uri") == uri
            and params.get("diagnostics") == [],
        )
        definition = client.request(32, "textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": {"line": 0, "character": clean.index("movian/page") + 2},
        })
        expected = REPOSITORY_ROOT / "res" / "ecmascript" / "modules" / \
            "movian" / "page.js"
        if not definition or definition[0].get("uri") != expected.as_uri():
            raise AssertionError("wrong metadata require definition: %s" % definition)
        outside_literal = client.request(33, "textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": {"line": 0, "character": clean.index("require") + 2},
        })
        if outside_literal is not None:
            raise AssertionError("definition escaped require literal: %s" %
                                 outside_literal)
        commented = client.request(34, "textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": {"line": 1,
                         "character": clean.splitlines()[1].index("movian/page") + 2},
        })
        if commented is not None:
            raise AssertionError("commented require returned a definition: %s" %
                                 commented)
        client.notify("textDocument/didClose", {"textDocument": {"uri": uri}})
        client.wait_for_notification(
            "textDocument/publishDiagnostics",
            lambda params: params.get("uri") == uri
            and params.get("diagnostics") == [],
        )
        shutdown(client, 35)
    except BaseException:
        abort(client)
        raise


def run_js_stale_save_and_close() -> None:
    """Superseded and closed JS analysis must never publish its error."""

    with tempfile.TemporaryDirectory(prefix="movian-lsp-js-race-") as directory:
        source = Path(directory) / "race.js"
        source.write_text("var clean = 1;\n", encoding="utf-8")
        output = io.BytesIO()
        server = LspServer(input_stream=io.BytesIO(), output_stream=output,
                           debounce_ms=1)
        server.workspace_root = source.parent
        first_started = threading.Event()
        release_first = threading.Event()
        third_started = threading.Event()
        release_third = threading.Event()
        publish_attempted = threading.Event()
        calls = 0
        original_publish = server._publish_diagnostics

        def controlled_analyzer(mode: str,
                                temporary_path: Path) -> tuple[Any, str | None]:
            nonlocal calls
            if mode != "--js":
                raise AssertionError("JS lifecycle invoked %s" % mode)
            calls += 1
            if calls == 1:
                first_started.set()
                if not release_first.wait(timeout=2.0):
                    raise AssertionError("test did not release first JS analysis")
                return {"file": str(temporary_path), "line": 1,
                        "error": "stale error"}, None
            if calls == 3:
                third_started.set()
                if not release_third.wait(timeout=2.0):
                    raise AssertionError("test did not release closed JS analysis")
                return {"file": str(temporary_path), "line": 1,
                        "error": "closed error"}, None
            return {"ok": True}, None

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
                "jsonrpc": "2.0", "method": "textDocument/didOpen",
                "params": did_open(uri, "var broken = ;\n",
                                   language_id="javascript"),
            })
            if not first_started.wait(timeout=2.0):
                raise AssertionError("first JS analysis did not start")
            server._dispatch({
                "jsonrpc": "2.0", "method": "textDocument/didSave",
                "params": {"textDocument": {"uri": uri},
                           "text": "var clean = 1;\n"},
            })
            release_first.set()
            if not publish_attempted.wait(timeout=2.0):
                raise AssertionError("saved JS analysis did not publish")
            messages = framed_messages(output.getvalue())
            if len(messages) != 1 \
                    or messages[0]["params"].get("diagnostics") != []:
                raise AssertionError("stale JS diagnostics published: %s" % messages)

            publish_attempted.clear()
            server._dispatch({
                "jsonrpc": "2.0", "method": "textDocument/didSave",
                "params": {"textDocument": {"uri": uri},
                           "text": "var broken = ;\n"},
            })
            if not third_started.wait(timeout=2.0):
                raise AssertionError("third JS analysis did not start")
            server._dispatch({
                "jsonrpc": "2.0", "method": "textDocument/didClose",
                "params": {"textDocument": {"uri": uri}},
            })
            close_messages = framed_messages(output.getvalue())
            release_third.set()
            if not publish_attempted.wait(timeout=2.0):
                raise AssertionError("closed JS analysis missed publish guard")
            if framed_messages(output.getvalue()) != close_messages:
                raise AssertionError("JS analysis published after didClose")
        finally:
            release_first.set()
            release_third.set()
            server._cancel_all_timers()


def run_js_relative_definition_confinement() -> None:
    """Relative JS definitions resolve locally but cannot leave either root."""

    with tempfile.TemporaryDirectory(prefix="movian-lsp-js-root-") as root_dir, \
            tempfile.TemporaryDirectory(prefix="movian-lsp-js-outside-") as outside_dir:
        root = Path(root_dir)
        outside = Path(outside_dir) / "outside.js"
        local = root / "local.js"
        source = root / "main.js"
        local.write_text("exports.ok = true;\n", encoding="utf-8")
        outside.write_text("var broken = ;\n", encoding="utf-8")
        relative_outside = Path("..") / Path(outside_dir).name / "outside"
        source_text = "var local = require('./local');\nvar nope = require('%s');\n" % \
            relative_outside.as_posix()
        source.write_text(source_text, encoding="utf-8")
        client = LspClient(SERVER, REPOSITORY_ROOT)
        try:
            client.request(41, "initialize", {"rootUri": root.as_uri()})
            client.notify("initialized", {})
            client.notify("textDocument/didOpen",
                          did_open(source.as_uri(), source_text,
                                   language_id="javascript"))
            client.wait_for_notification(
                "textDocument/publishDiagnostics",
                lambda params: params.get("uri") == source.as_uri()
                and params.get("diagnostics") == [],
            )
            local_definition = client.request(42, "textDocument/definition", {
                "textDocument": {"uri": source.as_uri()},
                "position": {"line": 0, "character": source_text.index("./local") + 2},
            })
            if not local_definition \
                    or local_definition[0].get("uri") != local.as_uri():
                raise AssertionError("relative JS definition failed: %s" %
                                     local_definition)
            escaped = client.request(43, "textDocument/definition", {
                "textDocument": {"uri": source.as_uri()},
                "position": {
                    "line": 1,
                    "character": source_text.splitlines()[1].index(
                        relative_outside.as_posix()) + 2,
                },
            })
            if escaped is not None:
                raise AssertionError("relative JS definition escaped root: %s" %
                                     escaped)

            client.notify("textDocument/didOpen",
                          did_open(outside.as_uri(), outside.read_text(encoding="utf-8"),
                                   language_id="javascript"))
            ignored = client.wait_for_notification(
                "textDocument/publishDiagnostics",
                lambda params: params.get("uri") == outside.as_uri(),
            )
            if ignored.get("diagnostics") != []:
                raise AssertionError("outside JS document was analyzed: %s" % ignored)
            client.notify("textDocument/didClose", {
                "textDocument": {"uri": outside.as_uri()},
            })
            client.notify("textDocument/didClose", {
                "textDocument": {"uri": source.as_uri()},
            })
            shutdown(client, 44)
        except BaseException:
            abort(client)
            raise


def run_completion_contexts() -> None:
    """Exercise all eight metadata-honest GLW completion contexts."""

    artifact = json.loads(
        (REPOSITORY_ROOT / "generated" / "movian-metadata.json").read_text(
            encoding="utf-8"))
    glw = artifact["glw"]
    widgets = sorted(record["name"] for record in glw["widgets"]
                     if record.get("registered") is True)
    attributes = sorted(record["name"] for record in glw["attributes"])
    attribute_records = {record["name"]: record
                         for record in glw["attributes"]}
    roots = sorted(record["name"] for record in glw["scopes"])
    align = next(record for record in glw["attributes"]
                 if record["name"] == "align")
    enum_values = align["enumValues"]
    functions = {record["name"]: record for record in glw["functions"]}
    if roots != sorted(("args", "clone", "core", "global", "nav", "parent",
                        "parentview", "self", "ui", "view")):
        raise AssertionError("completion/root artifact inventory changed: %s" %
                             roots)

    client = LspClient(SERVER, REPOSITORY_ROOT)
    opened: list[str] = []
    request_id = 100

    def open_fixture(name: str, *, uri: str | None = None) -> tuple[str, str]:
        nonlocal request_id
        text = fixture_text(name)
        target_uri = uri or (COMPLETION_FIXTURES / name).as_uri()
        client.notify("textDocument/didOpen", did_open(target_uri, text))
        opened.append(target_uri)
        request_id += 1
        return target_uri, text

    def complete(context: str, uri: str, position: dict[str, int],
                 trigger: str | None = None) -> Any:
        nonlocal request_id
        request_id += 1
        params: dict[str, Any] = {
            "textDocument": {"uri": uri},
            "position": position,
        }
        if trigger is not None:
            params["context"] = {"triggerKind": 2,
                                 "triggerCharacter": trigger}
        return client.request(request_id, "textDocument/completion", params)

    try:
        initialized = client.request(
            request_id, "initialize", {"rootUri": REPOSITORY_ROOT.as_uri()})
        triggers = initialized.get("capabilities", {}).get(
            "completionProvider", {}).get("triggerCharacters")
        if triggers != [".", "$", "/"]:
            raise AssertionError("completion/capabilities triggers=%s" % triggers)
        signature_triggers = initialized.get("capabilities", {}).get(
            "signatureHelpProvider", {}).get("triggerCharacters")
        if signature_triggers != ["(", ","]:
            raise AssertionError("completion/signature capabilities=%s" %
                                 signature_triggers)
        client.notify("initialized", {})

        uri, text = open_fixture("widget.view")
        assert_completion(
            "completion/widget",
            complete("completion/widget", uri,
                     cursor_after(text, 0, "widget(")),
            exact=widgets)

        uri, text = open_fixture("attribute.view")
        attribute_result = complete(
            "completion/attribute-label", uri,
            cursor_after(text, 1, "  al"))
        closed_catalog = assert_completion(
            "completion/attribute-label",
            attribute_result,
            exact=attributes)
        expected_details = {
            name: "%s; confidence: %s" %
            (record["valueType"], record["confidence"])
            for name, record in attribute_records.items()
        }
        actual_details = {item["label"]: item.get("detail")
                          for item in attribute_result}
        if actual_details != expected_details:
            raise AssertionError("completion/attribute detail mismatch")
        assert_completion(
            "completion/attribute-image",
            complete("completion/attribute-image", uri,
                     cursor_after(text, 4, "  so")),
            exact=attributes)

        uri, text = open_fixture("function.view")
        function_result = complete(
            "completion/function", uri,
            cursor_after(text, 1, "  alpha: cl"))
        assert_completion(
            "completion/function",
            function_result,
            contains=("clamp", "fmt"), excludes=("align", "self", "$event"))
        function_details = {item["label"]: item.get("detail")
                            for item in function_result}
        if function_details.get("clamp") != "GLW function; nargs: 3" \
                or function_details.get("fmt") != "variadic GLW function":
            raise AssertionError("completion/function arity detail mismatch: %s" %
                                 function_details)
        request_id += 1
        fixed = client.request(request_id, "textDocument/signatureHelp", {
            "textDocument": {"uri": uri},
            "position": cursor_after(text, 1, "  alpha: clamp("),
        })
        request_id += 1
        variadic = client.request(request_id, "textDocument/signatureHelp", {
            "textDocument": {"uri": uri},
            "position": cursor_after(text, 2, "  caption: fmt("),
        })
        expected_fixed = "clamp() [%d arguments]" % functions["clamp"]["nargs"]
        expected_variadic = "fmt(...) [variadic]"
        for context, result, expected in (
                ("signature/fixed", fixed, expected_fixed),
                ("signature/variadic", variadic, expected_variadic)):
            signatures = result.get("signatures") if isinstance(result, dict) \
                else None
            if not isinstance(signatures, list) or len(signatures) != 1 \
                    or signatures[0].get("label") != expected \
                    or "parameters" in signatures[0]:
                raise AssertionError("%s exposed unsupported signature: %s" %
                                     (context, result))

        uri, text = open_fixture("root.view")
        assert_completion(
            "completion/root",
            complete("completion/root", uri,
                     cursor_after(text, 1, "  caption: $"), "$"),
            exact=roots, excludes=("event",))
        assert_completion(
            "completion/root-children",
            complete("completion/root-children", uri,
                     cursor_after(text, 1, "  caption: $self."), "."),
            exact=[])

        uri, text = open_fixture("enum.view")
        assert_completion(
            "completion/enum-align",
            complete("completion/enum-align", uri,
                     cursor_after(text, 1, "  align: ")),
            exact=enum_values)

        uri, text = open_fixture("path.view")
        assert_completion(
            "completion/path-relative",
            complete("completion/path-relative", uri,
                     cursor_after(text, 0, '#include "./'), "/"),
            contains=("./widget.view",))
        assert_completion(
            "completion/path-traversal",
            complete("completion/path-traversal", uri,
                     cursor_after(text, 1, '#include "../../../../'), "/"),
            exact=[])
        moved_uri = (COMPLETION_FIXTURES / "moved" / "path.view").as_uri()
        moved, moved_text = open_fixture("path.view", uri=moved_uri)
        assert_completion(
            "completion/path-uri-relative",
            complete("completion/path-uri-relative", moved,
                     cursor_after(moved_text, 0, '#include "./'), "/"),
            exact=[])

        uri, text = open_fixture("local.view")
        assert_completion(
            "completion/local-macro",
            complete("completion/local-macro", uri,
                     cursor_after(text, 3, "Loc")),
            contains=("LocalCard",), excludes=("clamp", "label"))

        uri, text = open_fixture("incomplete.view")
        assert_completion(
            "completion/incomplete-block",
            complete("completion/incomplete-block", uri,
                     cursor_after(text, 1, "  al")),
            exact=closed_catalog)

        for opened_uri in opened:
            client.notify("textDocument/didClose", {
                "textDocument": {"uri": opened_uri},
            })
        shutdown(client, request_id + 1)
    except BaseException:
        abort(client)
        raise


def main() -> int:
    run_f1_command_builder()
    run_f2_shared_import_cleanup()
    run_f3_close_race()
    run_f5_definition_confinement()
    run_f7_hidden_workspace_snapshot()
    run_js_lifecycle_and_metadata_definition()
    run_js_stale_save_and_close()
    run_js_relative_definition_confinement()
    run_completion_contexts()
    print(json.dumps({
        "checks": 9,
        "status": "LSP REVIEW REGRESSIONS OK",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
