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


def _analyzer_objects(makefile: str) -> list[str]:
    """Every object the analyzer links, BUILDDIR-relative and extensionless.

    `${BUILDDIR}/x.o` sits at the repo root; `${MOVIAN_ANALYZE_BUILDDIR}/x.o`
    under `support/devtools/analyze`. Continuation lines are followed,
    because every one of these lists is written across them.

    One reader for both callers: the source list and the recorded selection
    are the same objects seen twice, and two copies of this loop would drift
    into disagreeing about which they are.
    """
    stems: list[str] = []
    inside = False
    for raw in makefile.split("\n"):
        line = re.sub(r"(?<!\\)#.*$", "", raw)
        if _ANALYZE_ASSIGN_RE.match(line):
            inside = True
        elif not inside:
            continue
        for where, stem in _ANALYZE_OBJ_RE.findall(line):
            name = ("%s/%s" % (_ANALYZE_DIR, stem)
                    if where == "MOVIAN_ANALYZE_BUILDDIR" else stem)
            if name not in stems:
                stems.append(name)
        if not line.rstrip().endswith("\\"):
            inside = False
    return stems


def analyzer_sources(makefile: str) -> list[str]:
    """The repo-relative `.c` behind every object the analyzer links, plus
    the scripts that generate linked code."""
    sources = ["%s.c" % stem for stem in _analyzer_objects(makefile)]
    for name in _ANALYZE_GENERATOR_RE.findall(makefile):
        script = "%s/%s" % (_ANALYZE_DIR, name)
        if script not in sources:
            sources.append(script)
    return sources


# The build records its own object selection INSIDE the binary, and this is
# the marker it writes (Makefile: MOVIAN_ANALYZE_SELECTION_C). Inside, not
# beside: a signature in a separate file is orphaned the moment someone copies
# a binary in, and nothing notices. Linked in, it travels with whatever binary
# is actually there.
SELECTION_MARKER = "MOVIAN-ANALYZE-SELECTION-V1"
_SELECTION_RE = re.compile(
    (SELECTION_MARKER + r"\[([A-Za-z0-9_./ -]*)\]").encode("ascii"))


def selection_marker(objects: list[str]) -> bytes:
    """The exact bytes the build embeds for this selection.

    The writer is a `printf` in the Makefile, not this function -- nothing
    here can force them to agree, so
    `test_the_makefile_writes_the_marker_this_module_reads` renders that
    recipe and compares. This exists so the tests and the reader share one
    definition of the format rather than three.
    """
    return ("%s[%s]" % (SELECTION_MARKER,
                        " ".join(sorted(objects)))).encode("ascii")


def analyzer_selection(makefile: str) -> list[str]:
    """The objects the recipe links, relative to BUILDDIR, sorted.

    Objects rather than sources: this is what the build records, and
    comparing sources would not notice an object added from a source already
    in the list.
    """
    return sorted("%s.o" % stem for stem in _analyzer_objects(makefile))


def recorded_selection(binary: Path) -> list[str] | None:
    """The selection the binary carries, or None when it carries none.

    None is not an empty selection. A binary built before this marker
    existed, or one produced by another route, simply cannot answer -- and
    "cannot answer" must not read as "nothing changed".
    """
    try:
        blob = binary.read_bytes()
    except OSError:
        return None
    match = _SELECTION_RE.search(blob)
    if match is None:
        return None
    return sorted(match.group(1).decode("ascii").split())


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
        # An empty or truncated depfile -- a compile killed part-way writes
        # one -- has no rule to read. Returning "no prerequisites" would make
        # it mean "nothing this object depends on changed", which is the
        # silence this whole check exists to refuse.
        return None
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
    if not found:
        # A rule with a colon and no prerequisites is equally unreadable: the
        # source itself is always among them in a real depfile.
        return None
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
    # A prerequisite the compiler recorded and the tree no longer has means
    # the binary was built from something that is gone. Skipping it -- which
    # `is_file()` used to do quietly -- lets an analyzer built from a deleted
    # header report as fresh.
    vanished = [name for name in inputs
                if not (REPOSITORY_ROOT / name).exists()]
    if vanished:
        return False, ("the analyzer was compiled against %s, which is no "
                       "longer in the tree; run make BUILD=debug -j$(nproc) "
                       "movian-analyze" % ", ".join(sorted(vanished)))
    newer = [name for name in inputs
             if (REPOSITORY_ROOT / name).stat().st_mtime > built]
    if newer:
        return False, ("build.debug/movian-analyze is older than %s; run "
                       "make BUILD=debug -j$(nproc) movian-analyze"
                       % ", ".join(sorted(newer)))
    # "the recipe NOW names", not "it was built from". Reading the current
    # list does not prove it is the list that produced the binary: adding an
    # already-old object to MOVIAN_ANALYZE_CORE_OBJS changes what the
    # analyzer should contain while no timestamp moves, and mtimes cannot see
    # it. The signature below closes that (movian#225).
    # Everything above compares timestamps, and a timestamp cannot see the
    # recipe's composition change. Adding an object that already exists and
    # is older than the binary moves nothing -- yet the binary was linked
    # before that line existed and provably does not contain it. So the last
    # word belongs to what the BUILD recorded, not to what the recipe now
    # says (movian#225).
    recorded = recorded_selection(analyzer)
    selection = analyzer_selection(makefile)
    if recorded is None:
        return False, ("build.debug/movian-analyze carries no selection "
                       "signature, so which objects it was linked from is "
                       "UNKNOWN -- a build predating the signature does "
                       "this; run make BUILD=debug -j$(nproc) movian-analyze "
                       "to get one")
    added = [name for name in selection if name not in recorded]
    dropped = [name for name in recorded if name not in selection]
    if added or dropped:
        parts = []
        if added:
            parts.append("the recipe now links %s, which this binary was not "
                         "built from" % ", ".join(added))
        if dropped:
            parts.append("this binary was built from %s, which the recipe no "
                         "longer links" % ", ".join(dropped))
        return False, ("build.debug/movian-analyze does not match the "
                       "recipe: %s; run make BUILD=debug -j$(nproc) "
                       "movian-analyze" % "; and ".join(parts))
    return True, ("build.debug/movian-analyze is executable, newer than all "
                  "%d inputs, and records the %d objects it was built from"
                  % (len(inputs), len(selection)))


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
