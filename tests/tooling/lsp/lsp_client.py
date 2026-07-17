"""Tiny framed-JSON-RPC client shared by the movian-lsp smoke scripts."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable


class LspClient:
    """Run one stdio LSP server and retain its literal stdout frames."""

    _EOF = object()

    def __init__(self, server_path: Path, repository_root: Path) -> None:
        environment = dict(os.environ)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        self.process = subprocess.Popen(
            [sys.executable, str(server_path), "--stdio"],
            cwd=repository_root,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        self._messages: queue.Queue[tuple[bytes, dict[str, Any]] | object] = \
            queue.Queue()
        self._pending: list[dict[str, Any]] = []
        self.frames: list[bytes] = []
        self.reader_error: BaseException | None = None
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()

    def _read_stdout(self) -> None:
        try:
            while True:
                header = bytearray()
                headers: dict[str, str] = {}
                while True:
                    line = self.process.stdout.readline()
                    if line == b"":
                        self._messages.put(self._EOF)
                        return
                    header.extend(line)
                    if line in (b"\r\n", b"\n"):
                        break
                    key, value = line.decode("ascii").split(":", 1)
                    headers[key.strip().lower()] = value.strip()
                length = int(headers["content-length"])
                body = bytearray()
                while len(body) < length:
                    chunk = self.process.stdout.read(length - len(body))
                    if chunk == b"":
                        raise EOFError("short JSON-RPC body")
                    body.extend(chunk)
                raw = bytes(header) + bytes(body)
                message = json.loads(bytes(body).decode("utf-8"))
                self._messages.put((raw, message))
        except BaseException as exc:  # surfaced by wait_for(), never hidden
            self.reader_error = exc
            self._messages.put(self._EOF)

    def send(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message, ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8")
        self.process.stdin.write(
            ("Content-Length: %d\r\n\r\n" % len(payload)).encode("ascii") +
            payload)
        self.process.stdin.flush()

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self.send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, request_id: int, method: str,
                params: dict[str, Any]) -> Any:
        self.send({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        })
        response = self.wait_for(lambda message: message.get("id") == request_id)
        if "error" in response:
            raise AssertionError("LSP error response: %s" % response["error"])
        return response.get("result")

    def wait_for(self, predicate: Callable[[dict[str, Any]], bool],
                 timeout: float = 5.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            for index, pending in enumerate(self._pending):
                if predicate(pending):
                    return self._pending.pop(index)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for matching LSP message")
            try:
                item = self._messages.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError("timed out waiting for LSP message") from exc
            if item is self._EOF:
                stderr = self.process.stderr.read().decode("utf-8", "replace") \
                    if self.process.stderr is not None else ""
                if self.reader_error is not None:
                    raise RuntimeError("LSP stdout reader failed: %s" %
                                       self.reader_error)
                raise RuntimeError("LSP exited before its response: %s" % stderr)
            raw, message = item
            self.frames.append(raw)
            if predicate(message):
                return message
            self._pending.append(message)

    def wait_for_notification(self, method: str,
                              predicate: Callable[[dict[str, Any]], bool],
                              timeout: float = 5.0) -> dict[str, Any]:
        message = self.wait_for(
            lambda message: message.get("method") == method
            and isinstance(message.get("params"), dict)
            and predicate(message["params"]),
            timeout,
        )
        return message["params"]

    def close_after_exit(self, timeout: float = 5.0) -> tuple[int, str]:
        assert self.process.stdin is not None
        self.process.stdin.close()
        exit_code = self.process.wait(timeout=timeout)
        stderr = self.process.stderr.read().decode("utf-8", "replace") \
            if self.process.stderr is not None else ""
        return exit_code, stderr
