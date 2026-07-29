#!/usr/bin/env python3
"""End-to-end coverage for HTTP 401 request inspector behavior."""

from __future__ import annotations

import base64
import json
import os
import sys
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from support.devtools.mdevlib import harness  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "http401"
OUTCOME_PROP = "global/navigators/current/currentpage/model/metadata/outcome"
EXPECTED_AUTHORIZATION = "Basic dXNlcjpwYXNz"


class CountingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), HTTP401Handler)
        self.mode = "plain"
        self.request_count = 0
        self.authorization_headers: list[str | None] = []
        self.counter_lock = threading.Lock()

    def record_request(self, authorization: str | None) -> str:
        with self.counter_lock:
            self.request_count += 1
            self.authorization_headers.append(authorization)
            return self.mode

    def snapshot(self) -> tuple[int, list[str | None]]:
        with self.counter_lock:
            return self.request_count, list(self.authorization_headers)


class HTTP401Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        server: CountingHTTPServer = self.server  # type: ignore[assignment]
        authorization = self.headers.get("Authorization")
        mode = server.record_request(authorization)
        if mode == "plain_empty":
            self._respond(401, b"")
            return

        if mode == "realm" and authorization == EXPECTED_AUTHORIZATION:
            self._respond(200, b"authenticated")
            return

        headers = {}
        if mode == "realm":
            headers["WWW-Authenticate"] = 'Basic realm="testrealm"'
        self._respond(401, b"token expired", headers)

    def _respond(
        self, status: int, body: bytes, headers: dict[str, str] | None = None
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        pass


class HTTP401InspectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self._old_display = os.environ.get("DISPLAY")
        self._old_wayland_display = os.environ.get("WAYLAND_DISPLAY")
        os.environ["DISPLAY"] = ":0"
        os.environ["WAYLAND_DISPLAY"] = "wayland-0"
        self.addCleanup(self._restore_display_environment)

        self.server = CountingHTTPServer()
        self.server_thread = threading.Thread(
            target=self.server.serve_forever,
            name="http401-test-server",
            daemon=True,
        )
        self.server_thread.start()
        self.addCleanup(self._stop_server)

        suffix = uuid.uuid4().hex[:8]
        name = "t-149-%s-%s" % (self._testMethodName.replace("test_", ""), suffix)
        self.inst = harness.Instance(name)
        self.addCleanup(self._stop_instance)

    def _restore_display_environment(self) -> None:
        if self._old_display is None:
            os.environ.pop("DISPLAY", None)
        else:
            os.environ["DISPLAY"] = self._old_display
        if self._old_wayland_display is None:
            os.environ.pop("WAYLAND_DISPLAY", None)
        else:
            os.environ["WAYLAND_DISPLAY"] = self._old_wayland_display

    def _stop_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=5)

    def _stop_instance(self) -> None:
        pid = self.inst.live_pid()
        if pid is None:
            return
        outcome = harness.kill_owned_pid(self.inst, pid)
        if outcome == "still-alive":
            raise AssertionError("test Movian instance did not stop")

    def _launch(
        self, mode: str, *, inspector: bool, no_fail: bool = False,
        seed_keyring: bool = False
    ) -> tuple[str, dict[str, object]]:
        self.server.mode = mode
        url = "http://127.0.0.1:%d/target" % self.server.server_port

        if seed_keyring:
            settings = self.inst.persistent / "settings"
            settings.mkdir(parents=True, exist_ok=True)
            (settings / "keyring").write_text(
                json.dumps({
                    "testrealm @ 127.0.0.1": {
                        "username": "user",
                        "password": "pass",
                    }
                }),
                encoding="utf-8",
            )

        config = json.dumps({
            "url": url,
            "inspector": inspector,
            "noFail": no_fail,
        }, separators=(",", ":")).encode("utf-8")
        route = "http401test:" + base64.b64encode(config).decode("ascii")
        argv = harness.build_argv(
            self.inst, [str(FIXTURE)], None, False, route
        )
        harness.launch(self.inst, argv)

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            outcome = harness.prop_value(self.inst.base_url(), OUTCOME_PROP)
            if outcome not in (None, "", "(void)"):
                return url, json.loads(outcome)
            if self.inst.live_pid() is None:
                break
            time.sleep(0.1)

        log_tail = "\n".join(harness.read_log(self.inst).splitlines()[-80:])
        self.fail("plugin did not surface an outcome; log tail:\n" + log_tail)

    def _assert_debug_count(self, url: str, server_count: int) -> None:
        needle = "Sending request for %s" % url
        debug_count = harness.read_log(self.inst).count(needle)
        self.assertEqual(debug_count, server_count)

    def test_inspector_persistent_401_is_bounded(self) -> None:
        url, outcome = self._launch("plain", inspector=True)
        request_count, _ = self.server.snapshot()

        self.assertGreaterEqual(request_count, 1)
        self.assertLessEqual(request_count, 3)
        self.assertEqual(outcome, {
            "ok": False,
            "error": "Error: HTTP request failed %s -- "
                     "HTTP 401: authentication failed for %s" % (url, url),
        })
        self._assert_debug_count(url, request_count)

    def test_inspector_401_nofail_returns_body(self) -> None:
        url, outcome = self._launch("plain", inspector=True, no_fail=True)
        request_count, _ = self.server.snapshot()

        self.assertEqual(request_count, 1)
        self.assertEqual(outcome, {
            "ok": True,
            "status": 401,
            "body": "token expired",
        })
        self._assert_debug_count(url, request_count)

    def test_inspector_401_nofail_empty_body(self) -> None:
        url, outcome = self._launch("plain_empty", inspector=True, no_fail=True)
        request_count, _ = self.server.snapshot()

        self.assertEqual(request_count, 1)
        self.assertEqual(outcome, {
            "ok": True,
            "status": 401,
            "body": "",
        })
        self._assert_debug_count(url, request_count)

    def test_realm_401_authenticates_with_seeded_creds(self) -> None:
        url, outcome = self._launch(
            "realm", inspector=False, seed_keyring=True
        )
        request_count, authorization_headers = self.server.snapshot()

        self.assertGreaterEqual(request_count, 2)
        self.assertEqual(outcome, {
            "ok": True,
            "status": 200,
            "body": "authenticated",
        })
        self.assertIn(EXPECTED_AUTHORIZATION, authorization_headers)
        self._assert_debug_count(url, request_count)


if __name__ == "__main__":
    unittest.main()
