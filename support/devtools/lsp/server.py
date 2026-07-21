"""A small, stdlib-only stdio LSP server for Movian GLW and JavaScript files.

The semantic authority stays in ``movian-analyze``.  This module only owns
the editor-facing pieces the analyzer intentionally does not know about:
buffer snapshots, UTF-16 ranges, JSON-RPC framing, metadata lookups, and the
last-good GLW lexer-token fallback needed while a document is being edited.
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
JAVASCRIPT_DIAGNOSTIC_SOURCE = "duktape"

REQUIRE_LITERAL = re.compile(
    r"(?<![A-Za-z0-9_$\.])require[ \t]*\([ \t]*"
    r"(?P<quote>['\"])(?P<target>[^'\"\\\r\n]*)(?P=quote)[ \t]*\)")

# LSP SymbolKind values.  Keeping the numeric protocol values local avoids a
# dependency on a Python LSP package.
SYMBOL_FILE = 1
SYMBOL_NAMESPACE = 3
SYMBOL_CLASS = 5
SYMBOL_PROPERTY = 7
SYMBOL_FUNCTION = 12

# LSP CompletionItemKind values.
COMPLETION_FUNCTION = 3
COMPLETION_VARIABLE = 6
COMPLETION_CLASS = 7
COMPLETION_PROPERTY = 10
COMPLETION_VALUE = 12
COMPLETION_FILE = 17
COMPLETION_FOLDER = 19


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


def codepoint_index_at_utf16_position(line: str, character: int) -> int | None:
    """Translate one LSP UTF-16 offset to a Python string index."""

    if character < 0:
        return None
    units = 0
    for index, char in enumerate(line):
        width = 2 if ord(char) > 0xFFFF else 1
        if character < units + width:
            return index
        units += width
        if character == units:
            return index + 1
    return len(line) if character == units else None


def javascript_offset_is_code(text: str, offset: int) -> bool:
    """Reject regex matches inside JS strings and comments without an AST."""

    state = "code"
    index = 0
    while index < offset:
        char = text[index]
        following = text[index + 1] if index + 1 < offset else ""
        if state == "code":
            if char == "/" and following == "/":
                state = "line-comment"
                index += 2
                continue
            if char == "/" and following == "*":
                state = "block-comment"
                index += 2
                continue
            if char in ("'", '"', "`"):
                state = char
        elif state == "line-comment":
            if char == "\n":
                state = "code"
        elif state == "block-comment":
            if char == "*" and following == "/":
                state = "code"
                index += 2
                continue
        elif char == "\\":
            index += 2
            continue
        elif char == state:
            state = "code"
        index += 1
    return state == "code"


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
        self.scopes = {
            record["name"]: record for record in glw.get("scopes", [])
        }
        self.registered_widgets: dict[str, JSON] = {}
        self.widgets: dict[str, JSON] = {}
        for record in glw.get("widgets", []):
            name = record["name"]
            self.widgets[name] = record
            if record.get("registered") is True:
                self.registered_widgets[name] = record
            for alias in record.get("aliases", []):
                self.widgets[alias] = record
                if record.get("registered") is True:
                    self.registered_widgets[alias] = record
        js = artifact.get("js", {})
        self.modules = {
            record["name"]: record for record in js.get("modules", [])
            if isinstance(record, dict) and isinstance(record.get("name"), str)
        }

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
    """Own editor state while delegating parsing to ``movian-analyze``."""

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
        self.analyzer_semaphore = threading.Semaphore(
            max(2, min(os.cpu_count() or 2, 4)))
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
            elif method == "textDocument/didSave":
                self._did_save(message.get("params", {}))
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
            elif method == "textDocument/completion":
                result = self._completion(message.get("params", {}))
            elif method == "textDocument/signatureHelp":
                result = self._signature_help(message.get("params", {}))
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
                "textDocumentSync": {
                    "openClose": True,
                    "change": 1,
                    "save": {"includeText": True},
                },
                "documentSymbolProvider": True,
                "hoverProvider": True,
                "definitionProvider": True,
                "completionProvider": {
                    "triggerCharacters": [".", "$", "/"],
                },
                "signatureHelpProvider": {
                    "triggerCharacters": ["(", ","],
                },
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
        if not self._path_has_javascript_suffix(document.path):
            self._schedule_analysis(document)

    def _did_save(self, params: JSON) -> None:
        text_document = params["textDocument"]
        uri = text_document["uri"]
        document = self._document(uri)
        if document is None:
            return
        text = params.get("text")
        if text is not None and not isinstance(text, str):
            raise ValueError("text must be a string when provided")
        self._cancel_timer(document)
        with document.lock:
            if text is not None:
                document.text = normalize_text(text)
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
            with document.lock:
                uris = set(document.diagnostic_uris)
            claimed_by_open_documents: set[str] = set()
            for other in self.documents.values():
                with other.lock:
                    claimed_by_open_documents.update(other.diagnostic_uris)
            uris.add(document.uri)
            uris.difference_update(claimed_by_open_documents)
            # Keep membership locked through the clears. An in-flight analysis
            # either publishes before close reaches this point, or observes the
            # removed document and drops its result.
            for diagnostic_uri in sorted(uris):
                self._notify("textDocument/publishDiagnostics", {
                    "uri": diagnostic_uri,
                    "diagnostics": [],
                })
        self._cancel_timer(document)

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
                analyzed_version = document.version
            if self._path_has_javascript_suffix(document.path):
                if self._is_javascript_document(document):
                    self._analyze_javascript(
                        document, expected_generation, analyzed_version, text)
                else:
                    with document.lock:
                        if document.generation != expected_generation:
                            return
                        document.analyzed_generation = expected_generation
                    self._publish_diagnostics(
                        document, {document.uri: []}, expected_generation,
                        analyzed_version)
                return
            temporary_path: Path | None = None
            try:
                temporary_path = self._write_buffer_snapshot(document, text)
                with self.analyzer_semaphore:
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
            self._publish_diagnostics(document, diagnostics,
                                      expected_generation, analyzed_version)

    def _analyze_javascript(self, document: Document, expected_generation: int,
                            analyzed_version: int | None, text: str) -> None:
        """Run Duktape's single-error compile check for one JS snapshot."""

        temporary_path: Path | None = None
        try:
            temporary_path = self._write_buffer_snapshot(document, text)
            with self.analyzer_semaphore:
                check, failure = self._run_analyzer("--js", temporary_path)
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass
        diagnostics = self._diagnostics_from_check(
            document, temporary_path, check, failure,
            source=JAVASCRIPT_DIAGNOSTIC_SOURCE)
        with document.lock:
            if document.generation != expected_generation:
                return
            document.analyzed_generation = expected_generation
        self._publish_diagnostics(document, diagnostics, expected_generation,
                                  analyzed_version)

    def _write_buffer_snapshot(self, document: Document, text: str) -> Path:
        """Write a short-lived source clone, preferring the source directory.

        Keeping the clone beside an on-disk document preserves relative
        #include/#import resolution.  The clone is always removed after the
        analyzer pass or passes, so analysis never reads stale on-disk source.
        """

        directory: str | None = None
        if document.path is not None and document.path.parent.is_dir():
            directory = str(document.path.parent)
        try:
            descriptor, name = tempfile.mkstemp(
                prefix=".movian-lsp-", suffix=document.path.suffix
                if document.path is not None else ".view", dir=directory)
        except OSError:
            descriptor, name = tempfile.mkstemp(
                prefix=".movian-lsp-", suffix=document.path.suffix
                if document.path is not None else ".view")
        with os.fdopen(descriptor, "wb") as snapshot:
            snapshot.write(text.encode("utf-8"))
        return Path(name)

    def _analyzer_command(self, mode: str, temporary_path: Path) -> list[str]:
        """Build one analyzer invocation for a live-buffer snapshot."""

        # The analyzer's error text deliberately preserves the spelling of a
        # relative source path.  When the snapshot lives under this checkout,
        # pass it relative to the analyzer's cwd so an include failure keeps
        # the same file/message class as a normal in-tree ``--check`` call.
        try:
            analyzer_path = str(temporary_path.relative_to(self.repo_root))
        except ValueError:
            analyzer_path = str(temporary_path)
        return [
            str(self.analyzer),
            mode,
            "--root",
            str(self.workspace_root),
            "--skin",
            str(self.skin_root),
            analyzer_path,
        ]

    def _run_analyzer(self, mode: str, temporary_path: Path) -> tuple[JSON | None, str | None]:
        command = self._analyzer_command(mode, temporary_path)
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
                                failure: str | None, *,
                                source: str = DIAGNOSTIC_SOURCE
                                ) -> dict[str, list[JSON]]:
        if failure is not None:
            return {
                document.uri: [{
                    "range": range_for_line(document.text, 0),
                    "severity": 1,
                    "source": source,
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
                    "source": source,
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
                "source": source,
                "message": error,
            }]
        }

    def _publish_diagnostics(self, document: Document,
                             diagnostics: dict[str, list[JSON]],
                             expected_generation: int,
                             analyzed_version: int | None) -> None:
        """Publish only results still owned by the same document revision."""

        with self.documents_lock:
            # A debounce cancel cannot stop an analysis that already started.
            # Do not let such a detached document re-publish after didClose.
            if self.documents.get(document.uri) is not document:
                return
            with document.lock:
                # Pair diagnostics with the snapshot's version, not whatever
                # didChange may have installed while the analyzer was running.
                if document.generation != expected_generation:
                    return
                previous_uris = set(document.diagnostic_uris)
                document.diagnostic_uris = set(diagnostics)
                target_uris = previous_uris | set(diagnostics) | {document.uri}
                # Hold both locks while writing the frames: didClose and
                # didChange then cannot interleave a stale publish after this
                # membership/generation check.
                for uri in sorted(target_uris):
                    payload: JSON = {
                        "uri": uri,
                        "diagnostics": diagnostics.get(uri, []),
                    }
                    if analyzed_version is not None and uri == document.uri:
                        payload["version"] = analyzed_version
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
        if self._path_has_javascript_suffix(document.path):
            if not self._is_javascript_document(document):
                return None
            return self._javascript_definition(document, position)
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

    def _completion(self, params: JSON) -> list[JSON]:
        """Return only completions justified by GLW metadata or local tokens."""

        uri = params["textDocument"]["uri"]
        position = params["position"]
        document = self._document(uri)
        if document is None or self._path_has_javascript_suffix(document.path):
            return []
        self._ensure_analysis(document)
        cursor = self._completion_cursor(document, position)
        if cursor is None:
            return []
        line_number, line_prefix, text_before_cursor = cursor
        with document.lock:
            tokens = list(document.tokens)
        local_tokens = [token for token in tokens
                        if self._token_is_from_document(token, document)]

        path_match = re.match(
            r"^[ \t]*#[ \t]*(?:include|import)[ \t]+(?P<quote>['\"])(?P<path>[^'\"]*)$",
            line_prefix)
        if path_match is not None:
            return [
                self._completion_item(
                    candidate,
                    COMPLETION_FOLDER if candidate.endswith("/")
                    else COMPLETION_FILE,
                    "GLW include/import target")
                for candidate in self._include_completion_candidates(
                    document, path_match.group("path"))
            ]

        root_match = re.search(r"\$[A-Za-z_][A-Za-z0-9_]*$|\$$", line_prefix)
        if root_match is not None:
            return [
                self._completion_item(name, COMPLETION_VARIABLE,
                                      record.get("meaning", "GLW scope root"))
                for name, record in sorted(self.metadata.scopes.items())
            ]
        # Metadata has no child schema for any scope root.  A dot after a root
        # is therefore an intentionally empty completion context.
        if re.search(r"\$[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z0-9_]*$", line_prefix):
            return []

        enum_match = re.match(
            r"^[ \t]*(?P<attribute>[A-Za-z_][A-Za-z0-9_]*)[ \t]*:"
            r"[ \t]*[A-Za-z_][A-Za-z0-9_]*$|"
            r"^[ \t]*(?P<empty_attribute>[A-Za-z_][A-Za-z0-9_]*)[ \t]*:"
            r"[ \t]*$", line_prefix)
        if enum_match is not None:
            attribute_name = (enum_match.group("attribute") or
                              enum_match.group("empty_attribute"))
            attribute = self.metadata.attributes.get(attribute_name)
            enum_values = attribute.get("enumValues") if attribute else None
            if isinstance(enum_values, list):
                return [
                    self._completion_item(value, COMPLETION_VALUE,
                                          "%s enum value" % attribute_name)
                    for value in enum_values if isinstance(value, str)
                ]

        if re.search(r"\bwidget[ \t]*\([ \t]*[A-Za-z_][A-Za-z0-9_]*$|"
                     r"\bwidget[ \t]*\([ \t]*$", line_prefix):
            return [
                self._completion_item(name, COMPLETION_CLASS,
                                      "registered GLW widget")
                for name in sorted(self.metadata.registered_widgets)
            ]

        block_depth = self._completion_block_depth(
            local_tokens, line_number, line_prefix, text_before_cursor)
        context_prefix = line_prefix
        if block_depth > 0 and "{" in line_prefix:
            context_prefix = line_prefix.rsplit("{", 1)[-1]

        macros = self._local_macro_names(local_tokens, line_number + 1)
        identifier_match = re.match(
            r"^[ \t]*(?P<prefix>[A-Za-z_][A-Za-z0-9_]*)$",
            context_prefix)
        if identifier_match is not None:
            prefix = identifier_match.group("prefix")
            matching_macros = [name for name in macros
                               if name.startswith(prefix)]
            if matching_macros:
                return [
                    self._completion_item(name, COMPLETION_FUNCTION,
                                          "macro from current document")
                    for name in matching_macros
                ]

        if block_depth > 0 \
                and re.match(r"^[ \t]*(?:[A-Za-z_][A-Za-z0-9_]*)?$",
                             context_prefix):
            return [
                self._completion_item(
                    name, COMPLETION_PROPERTY,
                    "%s; confidence: %s" % (
                        record.get("valueType", "unknown"),
                        record.get("confidence", "unknown")))
                for name, record in sorted(self.metadata.attributes.items())
            ]


        if re.search(r"(?:[:=,(]|\breturn\b)[ \t]*"
                     r"(?:[A-Za-z_][A-Za-z0-9_]*)?$", line_prefix):
            return [
                self._completion_item(name, COMPLETION_FUNCTION,
                                      self._function_arity_detail(record))
                for name, record in sorted(self.metadata.functions.items())
            ]
        return []

    def _signature_help(self, params: JSON) -> JSON | None:
        uri = params["textDocument"]["uri"]
        document = self._document(uri)
        if document is None or self._path_has_javascript_suffix(document.path):
            return None
        cursor = self._completion_cursor(document, params["position"])
        if cursor is None:
            return None
        _line_number, line_prefix, _text_before_cursor = cursor
        open_parentheses: list[int] = []
        quote: str | None = None
        escaped = False
        for index, character in enumerate(line_prefix):
            if quote is not None:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == quote:
                    quote = None
                continue
            if character in ("'", '"'):
                quote = character
            elif character == "(":
                open_parentheses.append(index)
            elif character == ")" and open_parentheses:
                open_parentheses.pop()
        if not open_parentheses:
            return None
        name_match = re.search(
            r"([A-Za-z_][A-Za-z0-9_]*)[ \t]*$",
            line_prefix[:open_parentheses[-1]])
        call_name = name_match.group(1) if name_match is not None else None
        if call_name is None:
            return None
        record = self.metadata.functions.get(call_name)
        if record is None:
            return None
        name = record["name"]
        if record.get("variadic") is True:
            label = "%s(...) [variadic]" % name
            documentation = "Variadic GLW function from Movian metadata."
        else:
            nargs = record.get("nargs")
            if not isinstance(nargs, int) or nargs < 0:
                return None
            label = "%s() [%d argument%s]" % (
                name, nargs, "" if nargs == 1 else "s")
            documentation = "Fixed arity from Movian metadata: %d." % nargs
        return {
            "signatures": [{
                "label": label,
                "documentation": documentation,
            }],
            "activeSignature": 0,
        }

    @staticmethod
    def _completion_item(label: str, kind: int, detail: object) -> JSON:
        return {"label": label, "kind": kind, "detail": str(detail)}

    @staticmethod
    def _function_arity_detail(record: JSON) -> str:
        if record.get("variadic") is True:
            return "variadic GLW function"
        return "GLW function; nargs: %s" % record.get("nargs", "unknown")

    @staticmethod
    def _completion_cursor(document: Document,
                           position: JSON) -> tuple[int, str, str] | None:
        line_number = position["line"]
        character = position["character"]
        with document.lock:
            text = document.text
        lines = text.split("\n")
        if not isinstance(line_number, int) or not isinstance(character, int) \
                or line_number < 0 or line_number >= len(lines):
            return None
        column = codepoint_index_at_utf16_position(lines[line_number], character)
        if column is None:
            return None
        line_prefix = lines[line_number][:column]
        text_before_cursor = "\n".join(lines[:line_number] + [line_prefix])
        return line_number, line_prefix, text_before_cursor

    @staticmethod
    def _local_macro_names(tokens: list[JSON], before_line: int) -> list[str]:
        names: set[str] = set()
        for index, token in enumerate(tokens[:-2]):
            directive = tokens[index + 1]
            name = tokens[index + 2]
            token_line = token.get("line")
            if not isinstance(token_line, int) or token_line >= before_line \
                    or directive.get("line") != token_line \
                    or name.get("line") != token_line:
                continue
            if token.get("type") == "HASH" \
                    and directive.get("type") == "IDENTIFIER" \
                    and directive.get("value") == "define" \
                    and name.get("type") == "IDENTIFIER" \
                    and isinstance(name.get("value"), str):
                names.add(name["value"])
        return sorted(names)

    @staticmethod
    def _completion_block_depth(tokens: list[JSON], line_number: int,
                                line_prefix: str,
                                text_before_cursor: str) -> int:
        depth = 0
        for token in tokens:
            token_line = token.get("line")
            if not isinstance(token_line, int) or token_line >= line_number + 1:
                continue
            if token.get("type") == "BLOCK_OPEN":
                depth += 1
            elif token.get("type") == "BLOCK_CLOSE":
                depth = max(depth - 1, 0)
        if tokens:
            depth += line_prefix.count("{") - line_prefix.count("}")
            return max(depth, 0)
        # On the first invalid snapshot there is no last-good stream yet.  The
        # lexer punctuation has a one-to-one spelling, so brace balance is the
        # bounded fallback that keeps an unclosed block useful.
        return LspServer._raw_block_depth(text_before_cursor)

    @staticmethod
    def _raw_block_depth(text: str) -> int:
        depth = 0
        state = "code"
        index = 0
        while index < len(text):
            char = text[index]
            following = text[index + 1] if index + 1 < len(text) else ""
            if state == "code":
                if char == "/" and following == "/":
                    state = "line-comment"
                    index += 2
                    continue
                if char == "/" and following == "*":
                    state = "block-comment"
                    index += 2
                    continue
                if char in ("'", '"'):
                    state = char
                elif char == "{":
                    depth += 1
                elif char == "}":
                    depth = max(depth - 1, 0)
            elif state == "line-comment":
                if char == "\n":
                    state = "code"
            elif state == "block-comment":
                if char == "*" and following == "/":
                    state = "code"
                    index += 2
                    continue
            elif char == "\\":
                index += 2
                continue
            elif char == state:
                state = "code"
            index += 1
        return depth

    def _javascript_definition(self, document: Document,
                               position: JSON) -> list[JSON] | None:
        line_number = position["line"]
        character = position["character"]
        with document.lock:
            text = document.text
        lines = text.split("\n")
        if not isinstance(line_number, int) or not isinstance(character, int) \
                or line_number < 0 or line_number >= len(lines):
            return None
        column = codepoint_index_at_utf16_position(lines[line_number], character)
        if column is None:
            return None
        absolute_offset = sum(len(line) + 1 for line in lines[:line_number]) + column
        for match in REQUIRE_LITERAL.finditer(text):
            literal_start = match.start("target") - 1
            literal_end = match.end("target") + 1
            if not literal_start <= absolute_offset <= literal_end:
                continue
            if not javascript_offset_is_code(text, match.start()):
                return None
            resolved = self._resolve_javascript_require(
                document, match.group("target"))
            if resolved is None:
                return None
            target, line = resolved
            return [{
                "uri": path_to_uri(target),
                "range": {
                    "start": {"line": line, "character": 0},
                    "end": {"line": line, "character": 0},
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
            if candidate.name.startswith(".") or not candidate.is_file() \
                    or needle not in candidate.name.casefold():
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
            if candidate.is_file() and self._is_confined_definition_target(
                    candidate):
                return candidate.resolve()
        return None

    def _include_completion_candidates(self, document: Document,
                                       prefix: str) -> list[str]:
        """List one directory and validate every result through definition."""

        roots: list[tuple[Path, str]] = []
        relative_prefix = prefix
        if prefix.startswith("skin://"):
            relative_prefix = prefix[len("skin://"):]
            roots.extend([
                (self.workspace_root / "glwskins" / "flat", "skin://"),
                (self.skin_root, "skin://"),
            ])
        elif prefix.startswith("dataroot://"):
            relative_prefix = prefix[len("dataroot://"):]
            roots.append((self.workspace_root, "dataroot://"))
        elif prefix.startswith("file://"):
            candidate = uri_to_path(prefix)
            if candidate is None:
                return []
            directory = candidate if prefix.endswith("/") else candidate.parent
            fragment = "" if prefix.endswith("/") else candidate.name
            if not directory.is_dir() \
                    or not self._is_confined_definition_target(directory):
                return []
            labels: set[str] = set()
            try:
                for child in directory.iterdir():
                    if not child.name.startswith(fragment):
                        continue
                    label = path_to_uri(child)
                    if child.is_dir() \
                            and self._is_confined_definition_target(child):
                        labels.add(label + "/")
                    elif child.is_file() \
                            and self._resolve_include(document, label) is not None:
                        labels.add(label)
            except OSError:
                return []
            return sorted(labels, key=str.casefold)
        elif Path(prefix).is_absolute():
            roots.append((Path("/"), "/"))
            relative_prefix = prefix[1:]
        elif document.path is not None:
            roots.append((document.path.parent, ""))
        else:
            roots.append((self.workspace_root, ""))

        directory_part, separator, fragment = relative_prefix.rpartition("/")
        relative_directory = directory_part if separator else ""
        fragment = fragment if separator else relative_prefix
        labels: set[str] = set()
        for root, scheme in roots:
            directory = root / relative_directory
            if not directory.is_dir() \
                    or not self._is_confined_definition_target(directory):
                continue
            try:
                children = list(directory.iterdir())
            except OSError:
                continue
            for child in children:
                if not child.name.startswith(fragment):
                    continue
                relative_label = ((relative_directory + "/")
                                  if separator else "") + child.name
                label = scheme + relative_label
                if child.is_dir() \
                        and self._is_confined_definition_target(child):
                    labels.add(label + "/")
                elif child.is_file() \
                        and self._resolve_include(document, label) is not None:
                    labels.add(label)
        return sorted(labels, key=str.casefold)

    def _resolve_javascript_require(self, document: Document,
                                    target: str) -> tuple[Path, int] | None:
        module = self.metadata.modules.get(target)
        if module is not None:
            source = module.get("source")
            raw_file = source.get("file") if isinstance(source, dict) else None
            raw_line = source.get("line") if isinstance(source, dict) else None
            if not isinstance(raw_file, str) or not isinstance(raw_line, int):
                return None
            relative = Path(raw_file)
            if relative.is_absolute() or ".." in relative.parts:
                return None
            candidate = self.repo_root / relative
            try:
                candidate.resolve(strict=False).relative_to(
                    self.repo_root.resolve(strict=False))
            except (OSError, ValueError):
                return None
            if candidate.is_file():
                return candidate.resolve(), max(raw_line - 1, 0)
            return None

        if not target.startswith(("./", "../")) or document.path is None:
            return None
        relative = Path(target)
        if relative.is_absolute():
            return None
        # Match es_modsearch(): the requested ID is joined to the plugin's
        # load directory and then receives exactly one ".js" suffix.
        candidate = Path(str(document.path.parent / relative) + ".js")
        if candidate.is_file() \
                and self._is_confined_definition_target(candidate):
            return candidate.resolve(), 0
        return None

    @staticmethod
    def _path_has_javascript_suffix(path: Path | None) -> bool:
        return path is not None and path.suffix.casefold() == ".js"

    def _is_javascript_document(self, document: Document) -> bool:
        return self._path_has_javascript_suffix(document.path) \
            and document.path is not None \
            and self._is_confined_definition_target(document.path)

    def _is_confined_definition_target(self, candidate: Path) -> bool:
        """Match the analyzer's root/skin confinement for definitions."""

        try:
            resolved_candidate = Path(os.path.realpath(candidate))
            allowed_roots = (
                Path(os.path.realpath(self.workspace_root)),
                Path(os.path.realpath(self.skin_root)),
            )
        except OSError:
            return False
        for root in allowed_roots:
            try:
                resolved_candidate.relative_to(root)
                return True
            except ValueError:
                continue
        return False
