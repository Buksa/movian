"""A small, stdlib-only stdio LSP server for Movian GLW view files.

The semantic authority stays in ``movian-analyze``.  This module only owns
the editor-facing pieces the analyzer intentionally does not know about:
buffer snapshots, UTF-16 ranges, JSON-RPC framing, metadata hover text, and
the last-good lexer-token fallback needed while a document is being edited.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Iterable
from urllib.parse import unquote, urlparse


JSON = dict[str, Any]

DEFAULT_DEBOUNCE_MS = 150
ANALYZER_TIMEOUT_SECONDS = 2.0
DIAGNOSTIC_SOURCE = "movian-glw"

# LSP SymbolKind values.  Keeping the numeric protocol values local avoids a
# dependency on a Python LSP package.
SYMBOL_FILE = 1
SYMBOL_NAMESPACE = 3
SYMBOL_CLASS = 5
SYMBOL_PROPERTY = 7
SYMBOL_FUNCTION = 12


def normalize_text(text: str) -> str:
    """Apply LSP full-text-sync newline normalization."""

    return text.replace("\r\n", "\n").replace("\r", "\n")


def utf16_length(text: str) -> int:
    """Return the number of UTF-16 code units in *text*.

    LSP positions use UTF-16 code units, not Python's Unicode code points.
    Tabs are intentionally left as one unit; visual tab expansion belongs to
    the editor, not the protocol position model.
    """

    return sum(2 if ord(char) > 0xFFFF else 1 for char in text)


def range_for_line(text: str, line: int) -> JSON:
    """Make the issue-defined full-line LSP range for a zero-based line."""

    lines = text.split("\n")
    if line < 0 or line >= len(lines):
        return {
            "start": {"line": max(line, 0), "character": 0},
            "end": {"line": max(line, 0), "character": 0},
        }
    return {
        "start": {"line": line, "character": 0},
        "end": {"line": line, "character": utf16_length(lines[line])},
    }


def uri_to_path(uri: str) -> Path | None:
    """Translate a file URI to a local path without accepting other schemes."""

    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    path = unquote(parsed.path)
    if parsed.netloc and parsed.netloc not in ("", "localhost"):
        path = "//%s%s" % (parsed.netloc, path)
    return Path(path)


def path_to_uri(path: Path) -> str:
    return path.resolve(strict=False).as_uri()


def same_path(left: Path | None, right: Path | None) -> bool:
    if left is None or right is None:
        return False
    try:
        return left.resolve(strict=False) == right.resolve(strict=False)
    except OSError:
        return os.path.abspath(str(left)) == os.path.abspath(str(right))


def word_at_utf16_position(line: str, character: int) -> str | None:
    """Return the GLW-like identifier under a UTF-16 LSP character offset."""

    units = 0
    index = len(line)
    for candidate, char in enumerate(line):
        width = 2 if ord(char) > 0xFFFF else 1
        if character < units + width:
            index = candidate
            break
        if character == units + width:
            index = candidate + 1
            break
        units += width

    for match in re.finditer(r"[A-Za-z_][A-Za-z0-9_]*", line):
        if match.start() <= index < match.end():
            return match.group(0)
    return None


@dataclass
class Document:
    uri: str
    path: Path | None
    text: str
    version: int | None
    generation: int = 0
    analyzed_generation: int = -1
    timer: threading.Timer | None = None
    tokens: list[JSON] = field(default_factory=list)
    token_text: str = ""
    last_good_tokens: list[JSON] = field(default_factory=list)
    last_good_text: str = ""
    using_token_fallback: bool = False
    diagnostic_uris: set[str] = field(default_factory=set)
    lock: threading.RLock = field(default_factory=threading.RLock,
                                  repr=False)
    analysis_lock: threading.Lock = field(default_factory=threading.Lock,
                                          repr=False)


class Metadata:
    """Lookup indexes over the committed ``movian-metadata`` artifact."""

    def __init__(self, artifact_path: Path) -> None:
        with artifact_path.open(encoding="utf-8") as artifact_file:
            artifact = json.load(artifact_file)
        glw = artifact.get("glw", {})
        self.functions = {
            record["name"]: record for record in glw.get("functions", [])
        }
        self.attributes = {
            record["name"]: record for record in glw.get("attributes", [])
        }
        self.widgets: dict[str, JSON] = {}
        for record in glw.get("widgets", []):
            self.widgets[record["name"]] = record
            for alias in record.get("aliases", []):
                self.widgets[alias] = record

    def hover(self, word: str) -> str | None:
        if word in self.functions:
            record = self.functions[word]
            if record.get("variadic"):
                signature = "%s(...args)" % record["name"]
            else:
                signature = "%s(%s)" % (
                    record["name"],
                    ", ".join("arg%d" % (index + 1)
                              for index in range(record.get("nargs", 0))),
                )
            lines = [
                "```glw",
                signature,
                "```",
                "",
                "GLW function from Movian metadata.",
            ]
            self._append_source(lines, record)
            self._append_condition(lines, record)
            return "\n".join(lines)

        if word in self.attributes:
            record = self.attributes[word]
            lines = [
                "```glw",
                "%s: %s" % (record["name"], record["valueType"]),
                "```",
                "",
                "GLW attribute from Movian metadata.",
            ]
            self._append_source(lines, record)
            self._append_condition(lines, record)
            return "\n".join(lines)

        if word in self.widgets:
            record = self.widgets[word]
            lines = [
                "```glw",
                record["name"],
                "```",
                "",
                "GLW widget from Movian metadata.",
                "Registered: %s." % ("yes" if record.get("registered")
                                      else "no"),
            ]
            aliases = record.get("aliases", [])
            if aliases:
                lines.append("Aliases: %s." % ", ".join("`%s`" % alias
                                                       for alias in aliases))
            self._append_source(lines, record)
            self._append_condition(lines, record)
            return "\n".join(lines)
        return None

    @staticmethod
    def _append_source(lines: list[str], record: JSON) -> None:
        source = record.get("source", {})
        if source.get("file") and source.get("line"):
            lines.append("Defined in `%s:%s`." %
                         (source["file"], source["line"]))

    @staticmethod
    def _append_condition(lines: list[str], record: JSON) -> None:
        condition = record.get("condition")
        if condition:
            lines.append("Available under `%s`." % condition)


class LspServer:
    """Own GLW LSP state while delegating parsing to ``movian-analyze``."""

    def __init__(self, input_stream: BinaryIO | None = None,
                 output_stream: BinaryIO | None = None,
                 *, debounce_ms: int = DEFAULT_DEBOUNCE_MS) -> None:
        self.repo_root = Path(__file__).resolve().parents[3]
        self.workspace_root = self.repo_root
        self.analyzer = self.repo_root / "build.debug" / "movian-analyze"
        self.skin_root = self.repo_root / "glwskins" / "flat"
        self.metadata = Metadata(self.repo_root / "generated" /
                                 "movian-metadata.json")
        self.debounce_seconds = debounce_ms / 1000.0
        self.input = input_stream if input_stream is not None else sys.stdin.buffer
        self.output = output_stream if output_stream is not None else sys.stdout.buffer
        self.documents: dict[str, Document] = {}
        self.documents_lock = threading.RLock()
        self.output_lock = threading.Lock()
        self.shutdown_requested = False
        self.exiting = False

    # ------------------------------------------------------------------
    # JSON-RPC framing and dispatch
    # ------------------------------------------------------------------

    def run(self) -> int:
        while not self.exiting:
            try:
                message = self._read_message()
            except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
                self._send_error(None, -32700, "Parse error: %s" % exc)
                continue
            if message is None:
                break
            if not isinstance(message, dict):
                self._send_error(None, -32600, "Invalid Request")
                continue
            self._dispatch(message)
        self._cancel_all_timers()
        return 0 if self.shutdown_requested else 1

    def _read_message(self) -> JSON | None:
        headers: dict[str, str] = {}
        saw_header = False
        while True:
            line = self.input.readline()
            if line == b"":
                if not saw_header:
                    return None
                raise ValueError("unexpected EOF in JSON-RPC headers")
            saw_header = True
            if line in (b"\r\n", b"\n"):
                break
            try:
                key, value = line.decode("ascii").split(":", 1)
            except ValueError as exc:
                raise ValueError("malformed JSON-RPC header") from exc
            headers[key.strip().lower()] = value.strip()
        if "content-length" not in headers:
            raise ValueError("missing Content-Length header")
        length = int(headers["content-length"])
        if length < 0:
            raise ValueError("negative Content-Length")
        payload = self.input.read(length)
        if len(payload) != length:
            raise ValueError("unexpected EOF in JSON-RPC payload")
        return json.loads(payload.decode("utf-8"))

    def _send(self, message: JSON) -> None:
        payload = json.dumps(message, ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8")
        frame = ("Content-Length: %d\r\n\r\n" % len(payload)).encode("ascii")
        with self.output_lock:
            self.output.write(frame)
            self.output.write(payload)
            self.output.flush()

    def _send_response(self, request_id: Any, result: Any) -> None:
        self._send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _send_error(self, request_id: Any, code: int, message: str) -> None:
        self._send({
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        })

    def _notify(self, method: str, params: JSON) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _dispatch(self, message: JSON) -> None:
        method = message.get("method")
        request_id = message.get("id")
        is_request = "id" in message
        if not isinstance(method, str):
            if is_request:
                self._send_error(request_id, -32600, "Invalid Request")
            return

        try:
            if method == "initialize":
                result = self._initialize(message.get("params", {}))
            elif method == "initialized":
                result = None
            elif method == "shutdown":
                self.shutdown_requested = True
                self._cancel_all_timers()
                result = None
            elif method == "exit":
                self.exiting = True
                return
            elif method == "textDocument/didOpen":
                self._did_open(message.get("params", {}))
                result = None
            elif method == "textDocument/didChange":
                self._did_change(message.get("params", {}))
                result = None
            elif method == "textDocument/didClose":
                self._did_close(message.get("params", {}))
                result = None
            elif method == "textDocument/documentSymbol":
                result = self._document_symbols(message.get("params", {}))
            elif method == "textDocument/hover":
                result = self._hover(message.get("params", {}))
            elif method == "textDocument/definition":
                result = self._definition(message.get("params", {}))
            elif method == "workspace/symbol":
                result = self._workspace_symbols(message.get("params", {}))
            else:
                if is_request:
                    self._send_error(request_id, -32601,
                                     "Method not found: %s" % method)
                return
        except (KeyError, TypeError, ValueError) as exc:
            if is_request:
                self._send_error(request_id, -32602,
                                 "Invalid params: %s" % exc)
            return
        except Exception as exc:  # never leak an exception through stdout
            print("movian-lsp: %s" % exc, file=sys.stderr)
            if is_request:
                self._send_error(request_id, -32603, "Internal error")
            return

        if is_request:
            self._send_response(request_id, result)

    def _initialize(self, params: JSON) -> JSON:
        workspace = self._workspace_from_initialize(params)
        if workspace is not None:
            self.workspace_root = workspace
        return {
            "capabilities": {
                "textDocumentSync": 1,
                "documentSymbolProvider": True,
                "hoverProvider": True,
                "definitionProvider": True,
                "workspaceSymbolProvider": True,
            },
            "serverInfo": {"name": "movian-lsp", "version": "0.1.0"},
        }

    @staticmethod
    def _workspace_from_initialize(params: JSON) -> Path | None:
        candidates: list[str] = []
        root_uri = params.get("rootUri")
        if isinstance(root_uri, str):
            candidates.append(root_uri)
        for folder in params.get("workspaceFolders") or []:
            if isinstance(folder, dict) and isinstance(folder.get("uri"), str):
                candidates.append(folder["uri"])
        root_path = params.get("rootPath")
        if isinstance(root_path, str):
            candidates.append(path_to_uri(Path(root_path)))
        for candidate in candidates:
            path = uri_to_path(candidate)
            if path is not None:
                return path.resolve(strict=False)
        return None

    # ------------------------------------------------------------------
    # Text-document lifecycle and analyzer bridge
    # ------------------------------------------------------------------

    def _did_open(self, params: JSON) -> None:
        text_document = params["textDocument"]
        uri = text_document["uri"]
        if not isinstance(uri, str):
            raise ValueError("textDocument.uri must be a string")
        path = uri_to_path(uri)
        if path is not None:
            path = path.resolve(strict=False)
        document = Document(
            uri=uri,
            path=path,
            text=normalize_text(text_document.get("text", "")),
            version=text_document.get("version"),
        )
        with self.documents_lock:
            previous = self.documents.get(uri)
            self.documents[uri] = document
        if previous is not None:
            self._cancel_timer(previous)
        self._schedule_analysis(document)

    def _did_change(self, params: JSON) -> None:
        text_document = params["textDocument"]
        uri = text_document["uri"]
        document = self._document(uri)
        if document is None:
            return
        changes = params.get("contentChanges") or []
        if not changes or not isinstance(changes[-1], dict):
            raise ValueError("full-text contentChanges is required")
        text = changes[-1].get("text")
        if not isinstance(text, str):
            raise ValueError("contentChanges[-1].text must be a string")
        self._cancel_timer(document)
        with document.lock:
            document.text = normalize_text(text)
            document.version = text_document.get("version", document.version)
            document.generation += 1
            document.analyzed_generation = -1
        self._schedule_analysis(document)

    def _did_close(self, params: JSON) -> None:
        text_document = params["textDocument"]
        uri = text_document["uri"]
        with self.documents_lock:
            document = self.documents.pop(uri, None)
        if document is None:
            return
        self._cancel_timer(document)
        with document.lock:
            uris = set(document.diagnostic_uris)
        uris.add(document.uri)
        for diagnostic_uri in sorted(uris):
            self._notify("textDocument/publishDiagnostics", {
                "uri": diagnostic_uri,
                "diagnostics": [],
            })

    def _document(self, uri: str) -> Document | None:
        with self.documents_lock:
            return self.documents.get(uri)

    def _schedule_analysis(self, document: Document) -> None:
        with document.lock:
            generation = document.generation
            timer = threading.Timer(
                self.debounce_seconds,
                self._run_scheduled_analysis,
                args=(document.uri, generation),
            )
            timer.daemon = True
            document.timer = timer
        timer.start()

    def _cancel_timer(self, document: Document) -> None:
        with document.lock:
            timer = document.timer
            document.timer = None
        if timer is not None:
            timer.cancel()

    def _cancel_all_timers(self) -> None:
        with self.documents_lock:
            documents = list(self.documents.values())
        for document in documents:
            self._cancel_timer(document)

    def _run_scheduled_analysis(self, uri: str, generation: int) -> None:
        document = self._document(uri)
        if document is not None:
            self._analyze(document, generation)

    def _ensure_analysis(self, document: Document) -> None:
        with document.lock:
            generation = document.generation
            done = document.analyzed_generation == generation
        if done:
            return
        self._cancel_timer(document)
        self._analyze(document, generation)

    def _analyze(self, document: Document, expected_generation: int) -> None:
        """Run exact and tolerant analyzer modes against one buffer snapshot."""

        with document.analysis_lock:
            with document.lock:
                if document.generation != expected_generation:
                    return
                text = document.text
            temporary_path: Path | None = None
            try:
                temporary_path = self._write_buffer_snapshot(document, text)
                check, check_failure = self._run_analyzer(
                    "--check", temporary_path)
                tokens, _tokens_failure = self._run_analyzer(
                    "--tokens", temporary_path)
            finally:
                if temporary_path is not None:
                    try:
                        temporary_path.unlink()
                    except FileNotFoundError:
                        pass

            diagnostics = self._diagnostics_from_check(
                document, temporary_path, check, check_failure)
            if isinstance(tokens, dict) and isinstance(tokens.get("tokens"), list):
                remapped_tokens = self._remap_tokens(
                    tokens["tokens"], temporary_path, document)
                token_text = text
                token_fallback = False
            else:
                remapped_tokens = None
                token_text = ""
                token_fallback = True

            with document.lock:
                if document.generation != expected_generation:
                    return
                document.analyzed_generation = expected_generation
                if remapped_tokens is not None:
                    document.tokens = remapped_tokens
                    document.token_text = token_text
                    document.last_good_tokens = remapped_tokens
                    document.last_good_text = token_text
                    document.using_token_fallback = False
                else:
                    document.tokens = document.last_good_tokens
                    document.token_text = document.last_good_text
                    document.using_token_fallback = token_fallback
            self._publish_diagnostics(document, diagnostics)

    def _write_buffer_snapshot(self, document: Document, text: str) -> Path:
        """Write a short-lived ``.view`` clone, preferring the source directory.

        Keeping the clone beside an on-disk document preserves relative
        #include/#import resolution.  The clone is always removed after both
        analyzer passes, so the analyzer never reads stale on-disk source.
        """

        directory: str | None = None
        if document.path is not None and document.path.parent.is_dir():
            directory = str(document.path.parent)
        try:
            descriptor, name = tempfile.mkstemp(
                prefix=".movian-lsp-", suffix=".view", dir=directory)
        except OSError:
            descriptor, name = tempfile.mkstemp(
                prefix=".movian-lsp-", suffix=".view")
        with os.fdopen(descriptor, "wb") as snapshot:
            snapshot.write(text.encode("utf-8"))
        return Path(name)

    def _run_analyzer(self, mode: str, temporary_path: Path) -> tuple[JSON | None, str | None]:
        # The analyzer's error text deliberately preserves the spelling of a
        # relative source path.  When the snapshot lives under this checkout,
        # pass it relative to the analyzer's cwd so an include failure keeps
        # the same file/message class as a normal in-tree ``--check`` call.
        try:
            analyzer_path = str(temporary_path.relative_to(self.repo_root))
        except ValueError:
            analyzer_path = str(temporary_path)
        command = [
            str(self.analyzer),
            mode,
            "--root",
            str(self.workspace_root),
            analyzer_path,
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=self.repo_root,
                capture_output=True,
                timeout=ANALYZER_TIMEOUT_SECONDS,
                check=False,
            )
        except FileNotFoundError:
            return None, "movian-analyze is not built at %s" % self.analyzer
        except subprocess.TimeoutExpired:
            return None, "movian-analyze timed out after 2 seconds"
        except OSError as exc:
            return None, "unable to run movian-analyze: %s" % exc

        try:
            payload = json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return None, "movian-analyze emitted invalid JSON: %s" % exc
        if not isinstance(payload, dict):
            return None, "movian-analyze emitted a non-object result"
        return payload, None

    def _remap_tokens(self, tokens: Iterable[JSON], temporary_path: Path,
                      document: Document) -> list[JSON]:
        remapped: list[JSON] = []
        for token in tokens:
            if not isinstance(token, dict):
                continue
            copy = dict(token)
            raw_file = copy.get("file")
            if isinstance(raw_file, str) and self._is_temporary_file(
                    raw_file, temporary_path):
                copy["file"] = (str(document.path) if document.path is not None
                                else document.uri)
            remapped.append(copy)
        return remapped

    def _is_temporary_file(self, raw_file: str,
                           temporary_path: Path | None) -> bool:
        if temporary_path is None:
            return False
        candidate = Path(raw_file)
        if not candidate.is_absolute():
            candidate = self.repo_root / candidate
        return same_path(candidate, temporary_path)

    def _diagnostics_from_check(self, document: Document,
                                temporary_path: Path | None,
                                check: JSON | None,
                                failure: str | None) -> dict[str, list[JSON]]:
        if failure is not None:
            return {
                document.uri: [{
                    "range": range_for_line(document.text, 0),
                    "severity": 1,
                    "source": DIAGNOSTIC_SOURCE,
                    "message": failure,
                }]
            }
        if check is None or check.get("ok") is True:
            return {document.uri: []}
        raw_file = check.get("file")
        raw_line = check.get("line")
        error = check.get("error")
        if not isinstance(raw_file, str) or not isinstance(raw_line, int) \
                or not isinstance(error, str):
            return {
                document.uri: [{
                    "range": range_for_line(document.text, 0),
                    "severity": 1,
                    "source": DIAGNOSTIC_SOURCE,
                    "message": "movian-analyze emitted an invalid diagnostic",
                }]
            }
        path = self._analyzer_file_path(raw_file, temporary_path, document)
        diagnostic_uri = path_to_uri(path) if path is not None else document.uri
        diagnostic_text = self._text_for_path(document, path)
        line = raw_line - 1 if raw_line > 0 else 0
        return {
            diagnostic_uri: [{
                "range": range_for_line(diagnostic_text, line),
                "severity": 1,
                "source": DIAGNOSTIC_SOURCE,
                "message": error,
            }]
        }

    def _publish_diagnostics(self, document: Document,
                             diagnostics: dict[str, list[JSON]]) -> None:
        with document.lock:
            previous_uris = set(document.diagnostic_uris)
            document.diagnostic_uris = set(diagnostics)
            version = document.version
        target_uris = previous_uris | set(diagnostics) | {document.uri}
        for uri in sorted(target_uris):
            payload: JSON = {
                "uri": uri,
                "diagnostics": diagnostics.get(uri, []),
            }
            if version is not None and uri == document.uri:
                payload["version"] = version
            self._notify("textDocument/publishDiagnostics", payload)

    # ------------------------------------------------------------------
    # LSP feature handlers
    # ------------------------------------------------------------------

    def _document_symbols(self, params: JSON) -> list[JSON]:
        uri = params["textDocument"]["uri"]
        document = self._document(uri)
        if document is None:
            return []
        self._ensure_analysis(document)
        with document.lock:
            tokens = list(document.tokens)
            text = document.token_text or document.text
        local_tokens = [token for token in tokens
                        if self._token_is_from_document(token, document)]
        symbols: list[JSON] = []
        seen: set[tuple[str, int, int]] = set()

        def append(name: str, kind: int, token: JSON, detail: str) -> None:
            line = int(token.get("line", 1)) - 1
            key = (name, kind, line)
            if key in seen:
                return
            seen.add(key)
            symbol_range = range_for_line(text, line)
            symbols.append({
                "name": name,
                "kind": kind,
                "detail": detail,
                "range": symbol_range,
                "selectionRange": symbol_range,
            })

        for index, token in enumerate(local_tokens):
            token_type = token.get("type")
            if token_type == "HASH" and index + 2 < len(local_tokens):
                directive = local_tokens[index + 1]
                target = local_tokens[index + 2]
                if directive.get("type") == "IDENTIFIER" \
                        and directive.get("value") in ("include", "import") \
                        and target.get("type") == "RSTRING":
                    append("#%s %s" % (directive["value"],
                                        target.get("value", "")),
                           SYMBOL_NAMESPACE, token, "GLW include")
            if token_type != "IDENTIFIER":
                continue
            value = token.get("value")
            if not isinstance(value, str):
                continue
            if value == "widget" and index + 2 < len(local_tokens):
                paren = local_tokens[index + 1]
                widget = local_tokens[index + 2]
                if paren.get("type") == "LEFT_PARENTHESIS" \
                        and widget.get("type") == "IDENTIFIER" \
                        and isinstance(widget.get("value"), str):
                    append(widget["value"], SYMBOL_CLASS, widget, "GLW widget")
            elif index + 1 < len(local_tokens) \
                    and local_tokens[index + 1].get("type") == "COLON":
                append(value, SYMBOL_PROPERTY, token, "GLW attribute")
            elif value in self.metadata.functions and index + 1 < len(local_tokens) \
                    and local_tokens[index + 1].get("type") == "LEFT_PARENTHESIS":
                append(value, SYMBOL_FUNCTION, token, "GLW function")
        return symbols

    def _hover(self, params: JSON) -> JSON | None:
        uri = params["textDocument"]["uri"]
        position = params["position"]
        document = self._document(uri)
        if document is None:
            return None
        self._ensure_analysis(document)
        line_number = position["line"]
        character = position["character"]
        with document.lock:
            lines = document.text.split("\n")
        if not isinstance(line_number, int) or not isinstance(character, int) \
                or line_number < 0 or line_number >= len(lines):
            return None
        word = word_at_utf16_position(lines[line_number], character)
        if word is None:
            return None
        contents = self.metadata.hover(word)
        if contents is None:
            return None
        return {
            "contents": {"kind": "markdown", "value": contents},
            "range": range_for_line(document.text, line_number),
        }

    def _definition(self, params: JSON) -> list[JSON] | None:
        uri = params["textDocument"]["uri"]
        position = params["position"]
        document = self._document(uri)
        if document is None:
            return None
        self._ensure_analysis(document)
        requested_line = position["line"] + 1
        with document.lock:
            tokens = list(document.tokens)
        local_tokens = [token for token in tokens
                        if self._token_is_from_document(token, document)]
        for index, token in enumerate(local_tokens):
            if token.get("type") != "HASH" or token.get("line") != requested_line:
                continue
            if index + 2 >= len(local_tokens):
                continue
            directive = local_tokens[index + 1]
            target = local_tokens[index + 2]
            if directive.get("type") != "IDENTIFIER" \
                    or directive.get("value") not in ("include", "import") \
                    or target.get("type") != "RSTRING" \
                    or not isinstance(target.get("value"), str):
                continue
            resolved = self._resolve_include(document, target["value"])
            if resolved is None:
                return None
            return [{
                "uri": path_to_uri(resolved),
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 0, "character": 0},
                },
            }]
        return None

    def _workspace_symbols(self, params: JSON) -> list[JSON]:
        query = params.get("query", "")
        if not isinstance(query, str):
            raise ValueError("query must be a string")
        needle = query.casefold()
        symbols: list[JSON] = []
        if not self.workspace_root.is_dir():
            return symbols
        for candidate in sorted(self.workspace_root.rglob("*.view"),
                                key=lambda path: path.as_posix().casefold()):
            if not candidate.is_file() or needle not in candidate.name.casefold():
                continue
            try:
                relative = candidate.relative_to(self.workspace_root).as_posix()
            except ValueError:
                relative = candidate.name
            symbols.append({
                "name": candidate.name,
                "kind": SYMBOL_FILE,
                "containerName": str(Path(relative).parent),
                "location": {
                    "uri": path_to_uri(candidate),
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 0, "character": 0},
                    },
                },
            })
        return symbols

    # ------------------------------------------------------------------
    # Provenance and include resolution
    # ------------------------------------------------------------------

    def _token_is_from_document(self, token: JSON, document: Document) -> bool:
        raw_file = token.get("file")
        if not isinstance(raw_file, str):
            return False
        if document.path is None:
            return raw_file == document.uri
        return same_path(Path(raw_file), document.path)

    def _analyzer_file_path(self, raw_file: str, temporary_path: Path | None,
                            document: Document) -> Path | None:
        if self._is_temporary_file(raw_file, temporary_path):
            return document.path
        if raw_file.startswith("skin://"):
            return self.skin_root / raw_file[len("skin://"):]
        if raw_file.startswith("dataroot://"):
            return self.workspace_root / raw_file[len("dataroot://"):]
        if raw_file.startswith("file://"):
            return uri_to_path(raw_file)
        candidate = Path(raw_file)
        if candidate.is_absolute():
            return candidate
        for base in (self.repo_root, document.path.parent if document.path else None,
                     self.workspace_root):
            if base is not None and (base / candidate).exists():
                return base / candidate
        return candidate

    def _text_for_path(self, document: Document, path: Path | None) -> str:
        if same_path(path, document.path):
            return document.text
        if path is not None:
            with self.documents_lock:
                other_documents = list(self.documents.values())
            for other in other_documents:
                if same_path(path, other.path):
                    with other.lock:
                        return other.text
            try:
                return normalize_text(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError):
                pass
        return document.text

    def _resolve_include(self, document: Document, target: str) -> Path | None:
        candidates: list[Path] = []
        if target.startswith("skin://"):
            suffix = target[len("skin://"):]
            candidates.extend([
                self.workspace_root / "glwskins" / "flat" / suffix,
                self.skin_root / suffix,
            ])
        elif target.startswith("dataroot://"):
            candidates.append(self.workspace_root / target[len("dataroot://"):])
        elif target.startswith("file://"):
            path = uri_to_path(target)
            if path is not None:
                candidates.append(path)
        else:
            candidate = Path(target)
            if candidate.is_absolute():
                candidates.append(candidate)
            elif document.path is not None:
                candidates.append(document.path.parent / candidate)
            else:
                candidates.append(self.workspace_root / candidate)
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
        return None
