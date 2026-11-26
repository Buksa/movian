#!/usr/bin/env python3
"""`mdev key` posts actions in order; `mdev run --language` seeds i18n.

The language value is what i18n.c stores: a lang/ file name, or "none"
for English (the first option in the SETTING_MULTIOPT list, i18n.c).
"""

from __future__ import annotations

import http.server
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from support.devtools.mdevlib import cli  # noqa: E402
from support.devtools.mdevlib import harness  # noqa: E402
from support.devtools.mdevlib.harness import MdevError  # noqa: E402


class KeyTest(unittest.TestCase):
    def test_actions_are_posted_in_order(self) -> None:
        seen: list[tuple[str, str]] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                seen.append((self.command, self.path))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args) -> None:
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:%d" % server.server_port
        try:
            with mock.patch.object(harness.Instance, "base_url",
                                   return_value=base), \
                 mock.patch.object(cli, "KEY_SETTLE", 0):
                rc = cli.main(["key", "--name", "t", "Activate", "Right",
                               "Right"])
        finally:
            server.shutdown()
        self.assertEqual(rc, 0)
        self.assertEqual(seen, [("POST", "/api/input/action/Activate"),
                                ("POST", "/api/input/action/Right"),
                                ("POST", "/api/input/action/Right")])

    def test_a_failed_post_is_an_error(self) -> None:
        with mock.patch.object(harness.Instance, "base_url",
                               return_value="http://127.0.0.1:9"):
            rc = cli.main(["key", "--name", "t", "Up"])
        self.assertEqual(rc, 1)


class LanguageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.persistent = Path(self.tmp.name)
        self.store = self.persistent / "settings" / "i18n"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_a_lang_file_name_is_seeded(self) -> None:
        harness.seed_language(self.persistent, "it_IT")
        self.assertEqual(json.loads(self.store.read_text()),
                         {"language": "it_IT"})

    def test_english_is_what_the_core_stores_for_it(self) -> None:
        harness.seed_language(self.persistent, "en")
        self.assertEqual(json.loads(self.store.read_text()),
                         {"language": "none"})

    def test_other_i18n_settings_are_kept(self) -> None:
        self.store.parent.mkdir(parents=True)
        self.store.write_text('{"language":"de_DE","timeformat":1}')
        harness.seed_language(self.persistent, "sp_SP")
        self.assertEqual(json.loads(self.store.read_text()),
                         {"language": "sp_SP", "timeformat": 1})

    def test_an_unknown_language_is_refused(self) -> None:
        for bad in ("es_ES", "it", "../lang/it_IT", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(MdevError):
                    harness.seed_language(self.persistent, bad)
        self.assertFalse(self.store.exists())

    def test_run_refuses_before_any_side_effect(self) -> None:
        # Refused ahead of the instance check, so a --force restart is
        # never spent on a request that cannot run.
        with mock.patch.object(harness.Instance, "live_pid") as live_pid:
            rc = cli.main(["run", "--name", "t", "--language", "xx_XX"])
        self.assertEqual(rc, 1)
        live_pid.assert_not_called()


if __name__ == "__main__":
    unittest.main()
