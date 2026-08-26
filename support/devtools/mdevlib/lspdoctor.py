"""Preflight checks for the repository's stdio movian-lsp server."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MINIMUM_PYTHON = (3, 10)


def _one_line(value: object) -> str:
    """Keep doctor failures useful in terminals and scripts alike."""
    return " ".join(str(value).strip().split()) or "unknown failure"


def _fail(check: str, reason: str) -> int:
    print("FAIL %s: %s" % (check, _one_line(reason)))
    return 1


def _completion_labels(result: Any) -> list[str]:
    if not isinstance(result, list):
        raise RuntimeError("completion returned a non-list result")
    if any(not isinstance(item, dict)
           or not isinstance(item.get("label"), str)
           for item in result):
        raise RuntimeError("completion returned a malformed item")
    return [item["label"] for item in result]


def _check_python() -> tuple[bool, str]:
    version = sys.version_info
    if version[:2] < MINIMUM_PYTHON:
        return False, ("requires Python %d.%d or newer (movian-lsp uses "
                       "PEP 604 type syntax)" % MINIMUM_PYTHON)
    return True, "%d.%d.%d" % version[:3]


# The analyzer links twelve objects, and the Makefile is where that list
# lives. Reading it here rather than repeating it means the two cannot drift:
# a source added to the analyzer without this list noticing would make the
# check pass over an input it never looked at.
_ANALYZE_OBJ_VARS = ("MOVIAN_ANALYZE_CORE_OBJS", "MOVIAN_ANALYZE_JS_OBJS",
                     "MOVIAN_ANALYZE_OWN_OBJS")
_ANALYZE_ASSIGN_RE = re.compile(
    r"^\s*(%s)\s*(?:\+|::|:|\?|!)?=(.*)$" % "|".join(_ANALYZE_OBJ_VARS))
_ANALYZE_OBJ_RE = re.compile(r"[$][({](BUILDDIR|MOVIAN_ANALYZE_BUILDDIR)[)}]"
                             r"/([A-Za-z0-9_./-]+)\.o")
_ANALYZE_DIR = "support/devtools/analyze"
# `stubs-auto.c` is regenerated from the objects whenever this script changes
# (Makefile:1043-1049), and the result is linked in -- so editing it makes the
# binary stale with no `.c` and no header having moved.
_ANALYZE_GENERATOR_RE = re.compile(
    r"[$][({]C[)}]/[$][({]MOVIAN_ANALYZE_DIR[)}]/([A-Za-z0-9_.-]+\.sh)")


def analyzer_sources(makefile: str) -> list[str]:
    """The repo-relative `.c` behind every object the analyzer links.

    `${BUILDDIR}/x.o` is compiled from `x.c` at the repo root;
    `${MOVIAN_ANALYZE_BUILDDIR}/x.o` from `support/devtools/analyze/x.c`.
    Continuation lines are followed, because every one of these lists is
    written across them.
    """
    sources: list[str] = []
    inside = False
    for raw in makefile.split("\n"):
        line = re.sub(r"(?<!\\)#.*$", "", raw)
        if _ANALYZE_ASSIGN_RE.match(line):
            inside = True
        elif not inside:
            continue
        for where, stem in _ANALYZE_OBJ_RE.findall(line):
            source = ("%s/%s.c" % (_ANALYZE_DIR, stem)
                      if where == "MOVIAN_ANALYZE_BUILDDIR" else
                      "%s.c" % stem)
            if source not in sources:
                sources.append(source)
        if not line.rstrip().endswith("\\"):
            inside = False
    for name in _ANALYZE_GENERATOR_RE.findall(makefile):
        script = "%s/%s" % (_ANALYZE_DIR, name)
        if script not in sources:
            sources.append(script)
    return sources


def _depfile_prerequisites(depfile: Path, root: Path) -> list[str] | None:
    """Every in-repo file the compiler recorded this object as depending on.

    `-MD -MP` writes the real answer at compile time, headers included, so
    there is no second list here to drift from the first. None when the
    depfile is absent: the object was built without it or the build directory
    was cleaned, and "I could not tell" is not "nothing changed".

    Prerequisites outside the repository -- /usr/include and the like -- are
    left out on purpose. A libc upgrade does make `make` want to relink, but
    it is not what a developer edits, and a check that fires on it is the
    check nobody reads. The question here is whether YOUR sources moved under
    the binary.
    """
    try:
        text = depfile.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    found = []
    # `target: a b \\\n c` plus the `-MP` phony `header.h:` lines, which
    # carry no prerequisites and are skipped by taking only what follows the
    # first colon of the first rule.
    body = text.replace("\\\n", " ").split(":", 1)
    if len(body) != 2:
        return []
    for token in body[1].split():
        if token.endswith(":"):
            break
        candidate = Path(token)
        if not candidate.is_absolute():
            candidate = root / token
        try:
            relative = candidate.resolve().relative_to(root.resolve())
        except ValueError:
            continue
        name = relative.as_posix()
        if name not in found:
            found.append(name)
    return found


def _check_analyzer() -> tuple[bool, str]:
    analyzer = REPOSITORY_ROOT / "build.debug" / "movian-analyze"
    if not analyzer.is_file() or not os.access(analyzer, os.X_OK):
        return False, ("build.debug/movian-analyze is not an executable "
                       "file; run ./support/configure-linux-debug.sh && "
                       "make BUILD=debug -j$(nproc) movian-analyze")
    # `make -q` answers "would make do any work?", which is a far broader
    # question than "is this binary newer than what it was built from". Every
    # object depends on `Makefile`, so a checkout that merely touched that
    # file -- a branch switch does -- made make want to recompile all ten and
    # the doctor call a working analyzer stale. Measured in a checkout whose
    # twelve analyzer sources were all older than the binary: `make
    # BUILD=debug -q movian-analyze` still exited 1, make's own reason being
    # `Prerequisite 'Makefile' is newer than target .../pool.o`. The
    # parse-time EXTDEPS gate (Makefile:46-52) is a second way in, needing no
    # rebuild of anything the analyzer uses.
    try:
        makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
    except OSError as exc:
        return False, ("cannot read the Makefile to learn what the analyzer "
                       "is built from: %s" % exc)
    sources = analyzer_sources(makefile)
    if not sources:
        # A parser that stops matching finds nothing newer and passes -- green
        # because it looked at no inputs, which is the failure this check
        # exists to avoid.
        return False, ("the Makefile names no analyzer objects, so this "
                       "check has gone blind; expected %s"
                       % ", ".join(_ANALYZE_OBJ_VARS))
    built = analyzer.stat().st_mtime
    missing = [name for name in sources
               if not (REPOSITORY_ROOT / name).is_file()]
    if missing:
        return False, ("the Makefile names analyzer inputs that are not on "
                       "disk: %s" % ", ".join(missing))
    # The headers each object was actually compiled against, from the
    # depfiles `-MD -MP` wrote at compile time. Comparing only the `.c` files
    # would miss an edit to `glw_view.h`, which relinks the analyzer and
    # changes what it does.
    inputs = list(sources)
    unreadable = []
    for name in sources:
        if not name.endswith(".c"):
            continue
        depfile = REPOSITORY_ROOT / "build.debug" / (name[:-len(".c")] + ".d")
        prerequisites = _depfile_prerequisites(depfile, REPOSITORY_ROOT)
        if prerequisites is None:
            unreadable.append(depfile.relative_to(REPOSITORY_ROOT).as_posix())
            continue
        for prerequisite in prerequisites:
            if prerequisite not in inputs:
                inputs.append(prerequisite)
    if unreadable:
        # Silently comparing against the `.c` files alone would answer a
        # narrower question than the one asked, which is the defect this
        # check was rewritten to stop making.
        return False, ("cannot tell what the analyzer was compiled against: "
                       "no depfile at %s; rebuild with make BUILD=debug "
                       "-j$(nproc) movian-analyze" % ", ".join(unreadable))
    newer = [name for name in inputs
             if (REPOSITORY_ROOT / name).is_file()
             and (REPOSITORY_ROOT / name).stat().st_mtime > built]
    if newer:
        return False, ("build.debug/movian-analyze is older than %s; run "
                       "make BUILD=debug -j$(nproc) movian-analyze"
                       % ", ".join(sorted(newer)))
    return True, ("build.debug/movian-analyze is executable and newer than "
                  "all %d inputs it is built from" % len(inputs))


# `gen.py --check` grew: it now compiles the tsc positive and negative
# fixtures, the generated-dts fixtures, every plugin example against its own
# apiversion, and 20 core modules. The old 30-second bound was below its
# runtime on every machine here -- measured on bba50466b, 36 s in WSL and
# 85 s on the Debian stand, both exiting 0 -- so this line was permanently
# red, and red with "could not run", which is the one thing it must not say
# about a check that ran and passed.
#
# 360 s is roughly four times the slowest of those two, chosen so a machine
# well slower than the stand still gets a verdict rather than a timeout. The
# elapsed time is reported on success too: that is how the next person sees
# the bound tightening before it starts failing again.
METADATA_CHECK_TIMEOUT = 360.0


def _check_metadata() -> tuple[bool, str]:
    generator = REPOSITORY_ROOT / "support" / "devtools" / "metadata" / "gen.py"
    started = time.monotonic()
    try:
        completed = subprocess.run(
            [sys.executable, str(generator), "--check"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=METADATA_CHECK_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        # Deliberately not phrased as a verdict on the tree. Nothing was
        # learned about freshness here, and saying so is different from
        # saying the metadata is stale.
        return False, ("gen.py --check did not finish within %ds, so "
                       "freshness is UNKNOWN -- this says nothing about the "
                       "tree; run python3 "
                       "support/devtools/metadata/gen.py --check by hand"
                       % METADATA_CHECK_TIMEOUT)
    except OSError as exc:
        return False, "gen.py --check could not be started: %s" % exc
    elapsed = time.monotonic() - started
    if completed.returncode:
        combined = completed.stdout + completed.stderr
        if "METADATA DRIFT" in combined:
            return False, ("generated/movian-metadata.json is stale; run "
                           "python3 support/devtools/metadata/gen.py")
        return False, ("gen.py --check failed in %.0fs; run python3 "
                       "support/devtools/metadata/gen.py --check"
                       % elapsed)
    return True, ("generated/movian-metadata.json is fresh (checked in "
                  "%.0fs of %ds allowed)" % (elapsed,
                                             METADATA_CHECK_TIMEOUT))


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


def _check_lsp_completion() -> tuple[bool, str]:
    """Probe artifact-exact widget completion using this checkout's server."""

    server = REPOSITORY_ROOT / "support" / "devtools" / "movian-lsp"
    fixture = REPOSITORY_ROOT / "tests" / "tooling" / "lsp" / "fixtures" / \
        "completion" / "widget.view"
    metadata = REPOSITORY_ROOT / "generated" / "movian-metadata.json"
    client: Any | None = None
    try:
        resolved_server = server.resolve(strict=True)
        resolved_server.relative_to(REPOSITORY_ROOT.resolve(strict=True))
        if not fixture.is_file():
            raise RuntimeError("completion fixture is missing: %s" % fixture)
        artifact = json.loads(metadata.read_text(encoding="utf-8"))
        expected = sorted(
            {name for record in artifact["glw"]["widgets"]
             if record.get("registered") is True
             for name in [record["name"], *record.get("aliases", [])]})
        text = fixture.read_text(encoding="utf-8")
        client = _load_lsp_client()(resolved_server, REPOSITORY_ROOT)
        initialized = client.request(
            21, "initialize", {"rootUri": REPOSITORY_ROOT.as_uri()})
        triggers = initialized.get("capabilities", {}).get(
            "completionProvider", {}).get("triggerCharacters")
        if triggers != [".", "$", "/"]:
            raise RuntimeError("initialize advertised wrong completion triggers")
        client.notify("initialized", {})
        client.notify("textDocument/didOpen", {"textDocument": {
            "uri": fixture.as_uri(),
            "languageId": "glw",
            "version": 1,
            "text": text,
        }})
        result = client.request(22, "textDocument/completion", {
            "textDocument": {"uri": fixture.as_uri()},
            "position": {"line": 0, "character": len("widget(")},
        })
        actual = _completion_labels(result)
        if actual != expected:
            raise RuntimeError("widget completion list mismatch")
        client.notify("textDocument/didClose", {
            "textDocument": {"uri": fixture.as_uri()},
        })
        if client.request(23, "shutdown", {}) is not None:
            raise RuntimeError("shutdown returned a non-null result")
        client.notify("exit", {})
        exit_code, stderr = client.close_after_exit()
        if exit_code or stderr.strip():
            raise RuntimeError("server exit=%d stderr=%s" % (exit_code, stderr))
        command = "%s %s --stdio" % (sys.executable, resolved_server)
        return True, ("widget completion fixture via server command %s" %
                      command)
    except Exception as exc:
        return False, _one_line(exc)
    finally:
        if client is not None and client.process.poll() is None:
            client.process.kill()
            try:
                client.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


def _check_lsp_javascript() -> tuple[bool, str]:
    """Probe JS diagnostics, save handling, and metadata-backed definition."""

    server = REPOSITORY_ROOT / "support" / "devtools" / "movian-lsp"
    source = REPOSITORY_ROOT / "plugin_examples" / "async_page_load" / \
        "async_page_load.js"
    definition_target = REPOSITORY_ROOT / "res" / "ecmascript" / "modules" / \
        "movian" / "page.js"
    client: Any | None = None
    try:
        client = _load_lsp_client()(server, REPOSITORY_ROOT)
        initialized = client.request(
            11, "initialize", {"rootUri": REPOSITORY_ROOT.as_uri()})
        sync = initialized.get("capabilities", {}).get("textDocumentSync", {})
        if not isinstance(sync, dict) \
                or sync.get("save") != {"includeText": True}:
            raise RuntimeError("initialize did not advertise didSave includeText")
        client.notify("initialized", {})
        uri = source.as_uri()
        client.notify("textDocument/didOpen", {"textDocument": {
            "uri": uri,
            "languageId": "javascript",
            "version": 1,
            "text": "var valid = 1;\nvar identity = (x) => x;\n",
        }})
        opened = client.wait_for_notification(
            "textDocument/publishDiagnostics",
            lambda params: params.get("uri") == uri
            and len(params.get("diagnostics", [])) == 1,
        )
        diagnostic = opened["diagnostics"][0]
        if diagnostic.get("source") != "duktape" \
                or diagnostic.get("range", {}).get("start", {}).get("line") != 1:
            raise RuntimeError("unexpected Duktape diagnostic: %s" % diagnostic)

        clean = "var page = require('movian/page');\n"
        client.notify("textDocument/didSave", {
            "textDocument": {"uri": uri},
            "text": clean,
        })
        client.wait_for_notification(
            "textDocument/publishDiagnostics",
            lambda params: params.get("uri") == uri
            and params.get("diagnostics") == [],
        )
        definition = client.request(12, "textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": {"line": 0,
                         "character": clean.index("movian/page") + 2},
        })
        if not definition \
                or definition[0].get("uri") != definition_target.as_uri():
            raise RuntimeError("movian/page definition probe failed: %s" %
                               definition)
        client.notify("textDocument/didClose", {
            "textDocument": {"uri": uri},
        })
        if client.request(13, "shutdown", {}) is not None:
            raise RuntimeError("shutdown returned a non-null result")
        client.notify("exit", {})
        exit_code, stderr = client.close_after_exit()
        if exit_code or stderr.strip():
            raise RuntimeError("server exit=%d stderr=%s" % (exit_code, stderr))
        return True, ("didOpen/didSave Duktape diagnostic and movian/page "
                      "definition")
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
        ("lsp-completion", _check_lsp_completion),
        ("lsp-javascript", _check_lsp_javascript),
    ):
        ok, detail = check()
        if not ok:
            return _fail(name, detail)
        print("OK %s: %s" % (name, detail))
    return 0
