#!/usr/bin/env python3
"""Check flat-skin diagnostics, including the documented include fragment."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from lsp_client import LspClient


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SERVER = REPOSITORY_ROOT / "support" / "devtools" / "movian-lsp"
FLAT_SKIN = REPOSITORY_ROOT / "glwskins" / "flat"
KNOWN_INCLUDE_FRAGMENT = FLAT_SKIN / "menu" / "sidebar_common.view"
KNOWN_FRAGMENT_ERROR = "Unknown function: ListItemBevel"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("server", nargs="?", type=Path, default=DEFAULT_SERVER)
    args = parser.parse_args()
    client = LspClient(args.server, REPOSITORY_ROOT)
    views = sorted(FLAT_SKIN.rglob("*.view"))
    fragments = 0
    try:
        client.request(1, "initialize", {"rootUri": REPOSITORY_ROOT.as_uri()})
        client.notify("initialized", {})
        for index, view in enumerate(views, 1):
            uri = view.as_uri()
            client.notify("textDocument/didOpen", {"textDocument": {
                "uri": uri,
                "languageId": "glw",
                "version": index,
                "text": view.read_text(encoding="utf-8"),
            }})
            published = client.wait_for_notification(
                "textDocument/publishDiagnostics",
                lambda params: params.get("uri") == uri
                and params.get("version") == index,
                timeout=3.0,
            )
            diagnostics = published.get("diagnostics")
            if view == KNOWN_INCLUDE_FRAGMENT:
                fragments += 1
                if len(diagnostics) != 1:
                    raise AssertionError("fragment must have one diagnostic: %s" %
                                         published)
                diagnostic = diagnostics[0]
                if diagnostic.get("source") != "movian-glw" \
                        or diagnostic.get("message") != KNOWN_FRAGMENT_ERROR \
                        or diagnostic["range"]["start"]["line"] != 10:
                    raise AssertionError("fragment diagnostic mismatch: %s" %
                                         published)
            elif diagnostics != []:
                raise AssertionError("false diagnostic in %s: %s" %
                                     (view, published))
            client.notify("textDocument/didClose", {"textDocument": {"uri": uri}})
            client.wait_for_notification(
                "textDocument/publishDiagnostics",
                lambda params: params.get("uri") == uri
                and params.get("diagnostics") == [],
                timeout=3.0,
            )
        if client.request(2, "shutdown", {}) is not None:
            raise AssertionError("shutdown must return null")
        client.notify("exit", {})
        exit_code, stderr = client.close_after_exit()
        if exit_code != 0 or stderr:
            raise AssertionError("server exit=%s stderr=%r" % (exit_code, stderr))
        if fragments != 1:
            raise AssertionError("expected exactly one known include fragment, got %d" %
                                 fragments)
        print(json.dumps({
            "clean": len(views) - fragments,
            "files": len(views),
            "include_fragments": fragments,
            "status": "LSP CORPUS OK",
        }, sort_keys=True))
        return 0
    except BaseException:
        if client.process.poll() is None:
            client.process.kill()
            client.process.wait()
        raise


if __name__ == "__main__":
    sys.exit(main())
