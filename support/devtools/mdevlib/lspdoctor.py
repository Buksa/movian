"""Preflight checks for the repository's stdio movian-lsp server."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MINIMUM_PYTHON = (3, 10)


def _one_line(value: object) -> str:
    """Keep doctor failures useful in terminals and scripts alike."""
    return " ".join(str(value).strip().split()) or "unknown failure"


def _fail(check: str, reason: str) -> int:
    print("FAIL %s: %s" % (check, _one_line(reason)))
    return 1


def _check_python() -> tuple[bool, str]:
    version = sys.version_info
    if version[:2] < MINIMUM_PYTHON:
        return False, ("requires Python %d.%d or newer (movian-lsp uses "
                       "PEP 604 type syntax)" % MINIMUM_PYTHON)
    return True, "%d.%d.%d" % version[:3]


def _check_analyzer() -> tuple[bool, str]:
    analyzer = REPOSITORY_ROOT / "build.debug" / "movian-analyze"
    if not analyzer.is_file() or not os.access(analyzer, os.X_OK):
        return False, ("build.debug/movian-analyze is not an executable "
                       "file; run ./support/configure-linux-debug.sh && "
                       "make BUILD=debug -j$(nproc) movian-analyze")
    try:
        completed = subprocess.run(
            ["make", "BUILD=debug", "-q", "movian-analyze"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, ("make BUILD=debug -q movian-analyze could not run; "
                       "run ./support/configure-linux-debug.sh")
    if completed.returncode == 1:
        return False, ("build.debug/movian-analyze is stale; run make "
                       "BUILD=debug -j$(nproc) movian-analyze")
    if completed.returncode:
        return False, ("make BUILD=debug -q movian-analyze failed; run "
                       "./support/configure-linux-debug.sh")
    return True, "build.debug/movian-analyze is executable and up to date"


def _check_metadata() -> tuple[bool, str]:
    generator = REPOSITORY_ROOT / "support" / "devtools" / "metadata" / "gen.py"
    try:
        completed = subprocess.run(
            [sys.executable, str(generator), "--check"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, "gen.py --check could not run: %s" % exc
    if completed.returncode:
        combined = completed.stdout + completed.stderr
        if "METADATA DRIFT" in combined:
            return False, ("generated/movian-metadata.json is stale; run "
                           "python3 support/devtools/metadata/gen.py")
        return False, ("gen.py --check failed; run python3 "
                       "support/devtools/metadata/gen.py --check")
    return True, "generated/movian-metadata.json is fresh"


def _load_lsp_client() -> type[Any]:
    client_path = REPOSITORY_ROOT / "tests" / "tooling" / "lsp" / "lsp_client.py"
    spec = importlib.util.spec_from_file_location("movian_lsp_doctor_client",
                                                   client_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load %s" % client_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.LspClient


def _check_lsp_initialize() -> tuple[bool, str]:
    server = REPOSITORY_ROOT / "support" / "devtools" / "movian-lsp"
    if not server.is_file():
        return False, "support/devtools/movian-lsp is missing"

    client: Any | None = None
    try:
        client = _load_lsp_client()(server, REPOSITORY_ROOT)
        initialized = client.request(
            1,
            "initialize",
            {"rootUri": REPOSITORY_ROOT.as_uri()},
        )
        if not isinstance(initialized, dict):
            raise RuntimeError("initialize returned a non-object result")
        server_info = initialized.get("serverInfo")
        if not isinstance(server_info, dict) or server_info.get("name") != "movian-lsp":
            raise RuntimeError("initialize did not identify movian-lsp")
        if not client.frames or not client.frames[0].startswith(b"Content-Length:"):
            raise RuntimeError("initialize reply was not Content-Length framed")

        client.notify("initialized", {})
        if client.request(2, "shutdown", {}) is not None:
            raise RuntimeError("shutdown returned a non-null result")
        client.notify("exit", {})
        exit_code, stderr = client.close_after_exit()
        if exit_code:
            raise RuntimeError("server exited with status %d" % exit_code)
        if stderr.strip():
            raise RuntimeError("server wrote stderr: %s" % stderr)
        version = server_info.get("version", "unknown")
        return True, ("Content-Length JSON-RPC initialize reply from "
                      "movian-lsp %s" % version)
    except Exception as exc:
        return False, _one_line(exc)
    finally:
        if client is not None and client.process.poll() is None:
            client.process.kill()
            try:
                client.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


def run() -> int:
    """Run dependency-ordered checks, stopping before derivative failures."""
    for name, check in (
        ("python", _check_python),
        ("movian-analyze", _check_analyzer),
        ("metadata", _check_metadata),
        ("lsp-initialize", _check_lsp_initialize),
    ):
        ok, detail = check()
        if not ok:
            return _fail(name, detail)
        print("OK %s: %s" % (name, detail))
    return 0
