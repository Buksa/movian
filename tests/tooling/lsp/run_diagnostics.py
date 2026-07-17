#!/usr/bin/env python3
"""Check LSP fixture diagnostics against the exact movian-analyze contract."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from lsp_client import LspClient


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SERVER = REPOSITORY_ROOT / "support" / "devtools" / "movian-lsp"
ANALYZER = REPOSITORY_ROOT / "build.debug" / "movian-analyze"
FIXTURES = REPOSITORY_ROOT / "tests" / "tooling" / "glw" / "fixtures"


def analyzer_result(fixture: Path) -> dict:
    completed = subprocess.run(
        [str(ANALYZER), "--check", str(fixture.relative_to(REPOSITORY_ROOT))],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        timeout=2.0,
        check=False,
    )
    return json.loads(completed.stdout.decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("server", nargs="?", type=Path, default=DEFAULT_SERVER)
    args = parser.parse_args()
    client = LspClient(args.server, REPOSITORY_ROOT)
    checked = 0
    try:
        client.request(1, "initialize", {"rootUri": REPOSITORY_ROOT.as_uri()})
        client.notify("initialized", {})
        for fixture in sorted(FIXTURES.glob("*.view")):
            if fixture.name == "self-include.view":
                continue  # --check intentionally has no recursion depth knob.
            exact = analyzer_result(fixture)
            if exact.get("ok"):
                continue
            if not {"file", "line", "error"} <= exact.keys():
                raise AssertionError("invalid analyzer fixture result: %s" % exact)
            uri = fixture.as_uri()
            client.notify("textDocument/didOpen", {"textDocument": {
                "uri": uri,
                "languageId": "glw",
                "version": 1,
                "text": fixture.read_text(encoding="utf-8"),
            }})
            raw_path = Path(exact["file"])
            expected_path = raw_path if raw_path.is_absolute() \
                else REPOSITORY_ROOT / raw_path
            expected_uri = expected_path.resolve().as_uri()
            params = client.wait_for_notification(
                "textDocument/publishDiagnostics",
                lambda published: published.get("uri") == expected_uri
                and len(published.get("diagnostics", [])) == 1,
            )
            diagnostic = params["diagnostics"][0]
            if diagnostic.get("source") != "movian-glw" \
                    or diagnostic.get("message") != exact["error"] \
                    or diagnostic["range"]["start"]["line"] != max(0, exact["line"] - 1):
                raise AssertionError("fixture mismatch %s: analyzer=%s lsp=%s" %
                                     (fixture, exact, diagnostic))
            checked += 1
            client.notify("textDocument/didClose", {"textDocument": {"uri": uri}})
            client.wait_for_notification(
                "textDocument/publishDiagnostics",
                lambda published: published.get("uri") == uri
                and published.get("diagnostics") == [],
            )
        if client.request(2, "shutdown", {}) is not None:
            raise AssertionError("shutdown must return null")
        client.notify("exit", {})
        exit_code, stderr = client.close_after_exit()
        if exit_code != 0 or stderr:
            raise AssertionError("server exit=%s stderr=%r" % (exit_code, stderr))
        print(json.dumps({"fixtures": checked, "status": "LSP DIAGNOSTICS OK"},
                         sort_keys=True))
        return 0
    except BaseException:
        if client.process.poll() is None:
            client.process.kill()
            client.process.wait()
        raise


if __name__ == "__main__":
    sys.exit(main())
