#!/usr/bin/env python3
"""What `mdev lsp doctor` is allowed to claim.

Both cases here are the same defect wearing different clothes: a check that
answers a question nobody asked, and reports the answer as if it were the one
that was. One asked make whether anything at all needed rebuilding; the other
gave a check less time than it takes and called the result "could not run".
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from support.devtools.mdevlib import lspdoctor  # noqa: E402


REAL_MAKEFILE = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")


class AnalyzerSourceList(unittest.TestCase):
    """The list of inputs is read from the Makefile, not repeated here."""

    def test_this_makefile_names_twelve_sources_and_the_generator(self):
        sources = lspdoctor.analyzer_sources(REAL_MAKEFILE)
        self.assertEqual(len([n for n in sources if n.endswith(".c")]), 12,
                         sources)
        self.assertEqual(len(sources), 13, sources)
        self.assertIn("src/ui/glw/glw_view_eval.c", sources)
        self.assertIn("src/misc/pool.c", sources)
        self.assertIn("ext/duktape/duktape.c", sources)
        self.assertIn("support/devtools/analyze/shim.c", sources)
        self.assertIn("support/devtools/analyze/gen-abort-stubs.sh", sources)

    def test_every_named_source_exists(self):
        for name in lspdoctor.analyzer_sources(REAL_MAKEFILE):
            self.assertTrue((REPO_ROOT / name).is_file(), name)

    def test_the_two_build_directories_are_told_apart(self):
        # `${BUILDDIR}/x.o` is compiled from `x.c` at the repo root;
        # `${MOVIAN_ANALYZE_BUILDDIR}/x.o` from support/devtools/analyze.
        sources = lspdoctor.analyzer_sources(
            "MOVIAN_ANALYZE_CORE_OBJS = \\\n"
            "\t${BUILDDIR}/src/misc/pool.o\n"
            "MOVIAN_ANALYZE_OWN_OBJS = \\\n"
            "\t${MOVIAN_ANALYZE_BUILDDIR}/shim.o\n")
        self.assertEqual(sources, ["src/misc/pool.c",
                                   "support/devtools/analyze/shim.c"])

    def test_objects_outside_the_analyzer_lists_are_not_read(self):
        # `$(OBJS)` names hundreds of objects; only the analyzer's three
        # lists describe what it links.
        sources = lspdoctor.analyzer_sources(
            "SRCS += src/main.c\n"
            "OBJS = ${BUILDDIR}/src/main.o\n"
            "MOVIAN_ANALYZE_JS_OBJS = ${BUILDDIR}/ext/duktape/duktape.o\n")
        self.assertEqual(sources, ["ext/duktape/duktape.c"])

    def test_a_commented_out_object_is_not_an_input(self):
        sources = lspdoctor.analyzer_sources(
            "MOVIAN_ANALYZE_CORE_OBJS = \\\n"
            "\t${BUILDDIR}/src/misc/pool.o \\\n"
            "#\t${BUILDDIR}/src/misc/rstr.o\n")
        self.assertEqual(sources, ["src/misc/pool.c"])


class AnalyzerFreshness(unittest.TestCase):
    """`make -q` answers "would make do any work?", which is not the
    question. Every object depends on `Makefile`, so a checkout that merely
    touched that file made make want to recompile all ten and the doctor call
    a working analyzer stale."""

    HEADER = "src/ui/glw/glw_view.h"

    def _tree(self, stack) -> Path:
        root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        outside = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self._outside = outside / "system-header.h"
        self._outside.write_text("/* not in the repository */\n",
                                 encoding="utf-8")
        (root / "Makefile").write_text(REAL_MAKEFILE, encoding="utf-8")
        header = root / self.HEADER
        header.parent.mkdir(parents=True, exist_ok=True)
        header.write_text("/* stand-in */\n", encoding="utf-8")
        for name in lspdoctor.analyzer_sources(REAL_MAKEFILE):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("/* stand-in */\n", encoding="utf-8")
            if not name.endswith(".c"):
                continue
            # What `-MD -MP` writes: the object, its source, one in-repo
            # header, and one outside the repository that must not be picked
            # up. The outside one is a real file this test can age, because
            # /usr/include cannot be touched and a stand-in that is merely
            # old proves nothing.
            depfile = root / "build.debug" / (name[:-len(".c")] + ".d")
            depfile.parent.mkdir(parents=True, exist_ok=True)
            depfile.write_text(
                "%s.o: \\\n %s \\\n %s \\\n %s\n\n"
                "%s:\n" % (name[:-len(".c")], root / name, header,
                            self._outside, header),
                encoding="utf-8")
        binary = root / "build.debug" / "movian-analyze"
        binary.parent.mkdir(parents=True, exist_ok=True)
        # A real build links the selection in; the fixture does the same, or
        # every case below would exercise the no-signature branch instead of
        # the one it is named for.
        binary.write_bytes(
            b"#!/bin/sh\n"
            + lspdoctor.selection_marker(
                lspdoctor.analyzer_selection(REAL_MAKEFILE)))
        binary.chmod(0o755)
        self._touch(binary, newest=True)
        stack.enter_context(
            mock.patch.object(lspdoctor, "REPOSITORY_ROOT", root))
        return root

    def _add_built_object(self, root: Path, name: str) -> None:
        """Give the tree a source and depfile, as an already-built
        object has -- so every timestamp check passes and only the
        signature can object."""
        import os
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("/* stand-in */\n", encoding="utf-8")
        depfile = root / "build.debug" / (name[:-len(".c")] + ".d")
        depfile.parent.mkdir(parents=True, exist_ok=True)
        depfile.write_text("%s.o: %s\n" % (name[:-len(".c")], path),
                           encoding="utf-8")
        for each in (path, depfile):
            os.utime(each, (1_000_000_000, 1_000_000_000))

    @staticmethod
    def _touch(path: Path, newest: bool) -> None:
        import os
        stamp = 2_000_000_000 if newest else 1_000_000_000
        os.utime(path, (stamp, stamp))

    def test_a_binary_newer_than_its_sources_is_fresh(self):
        import contextlib
        with contextlib.ExitStack() as stack:
            self._tree(stack)
            ok, message = lspdoctor._check_analyzer()
            self.assertTrue(ok, message)
            self.assertIn("newer than all", message)

    def test_touching_any_one_input_flips_it_and_names_that_file(self):
        # One from each of the three lists the Makefile keeps.
        import contextlib
        for name in ("src/ui/glw/glw_view_eval.c", "src/misc/pool.c",
                     "ext/duktape/duktape.c",
                     "support/devtools/analyze/shim.c"):
            with contextlib.ExitStack() as stack:
                root = self._tree(stack)
                import os
                os.utime(root / name, (2_100_000_000, 2_100_000_000))
                ok, message = lspdoctor._check_analyzer()
                self.assertFalse(ok, (name, message))
                self.assertIn(name, message)
                self.assertIn("older than", message)
                # Restoring it makes the check pass again.
                os.utime(root / name, (1_000_000_000, 1_000_000_000))
                self.assertTrue(lspdoctor._check_analyzer()[0], name)

    def test_a_missing_binary_is_a_different_finding_from_a_stale_one(self):
        import contextlib
        with contextlib.ExitStack() as stack:
            root = self._tree(stack)
            (root / "build.debug" / "movian-analyze").unlink()
            ok, message = lspdoctor._check_analyzer()
            self.assertFalse(ok)
            self.assertIn("is not an executable file", message)
            self.assertNotIn("older than", message)

    def test_a_source_the_makefile_names_and_disk_lacks_is_reported(self):
        import contextlib
        with contextlib.ExitStack() as stack:
            root = self._tree(stack)
            (root / "src" / "misc" / "pool.c").unlink()
            ok, message = lspdoctor._check_analyzer()
            self.assertFalse(ok)
            self.assertIn("not on disk", message)
            self.assertIn("src/misc/pool.c", message)

    def test_touching_a_header_flips_it_and_names_that_header(self):
        # The `.c` files are not the whole input set. Editing `glw_view.h`
        # relinks the analyzer and changes what it does, and comparing only
        # sources would answer a narrower question than the one asked.
        import contextlib, os
        with contextlib.ExitStack() as stack:
            root = self._tree(stack)
            os.utime(root / self.HEADER, (2_100_000_000, 2_100_000_000))
            ok, message = lspdoctor._check_analyzer()
            self.assertFalse(ok, message)
            self.assertIn(self.HEADER, message)

    def test_touching_the_stub_generator_flips_it(self):
        # It has no `.o` and no depfile: `stubs-auto.c` is regenerated from
        # it and linked in, so it is an input with no compile step.
        import contextlib, os
        with contextlib.ExitStack() as stack:
            root = self._tree(stack)
            script = "support/devtools/analyze/gen-abort-stubs.sh"
            self.assertIn(script, lspdoctor.analyzer_sources(REAL_MAKEFILE))
            os.utime(root / script, (2_100_000_000, 2_100_000_000))
            ok, message = lspdoctor._check_analyzer()
            self.assertFalse(ok, message)
            self.assertIn(script, message)

    def test_a_header_outside_the_repository_is_not_an_input(self):
        # /usr/include moves when the distribution says so, and a check that
        # fires on that is the check nobody reads. Aged past the binary on
        # purpose: a stand-in that is merely old would pass either way.
        import contextlib, os
        with contextlib.ExitStack() as stack:
            self._tree(stack)
            os.utime(self._outside, (2_100_000_000, 2_100_000_000))
            ok, message = lspdoctor._check_analyzer()
            self.assertTrue(ok, message)

    def test_a_missing_depfile_is_not_read_as_nothing_changed(self):
        import contextlib
        with contextlib.ExitStack() as stack:
            root = self._tree(stack)
            (root / "build.debug" / "src" / "misc" / "pool.d").unlink()
            ok, message = lspdoctor._check_analyzer()
            self.assertFalse(ok, message)
            self.assertIn("cannot tell", message)
            self.assertIn("src/misc/pool.d", message)

    def test_an_empty_depfile_is_not_read_as_no_dependencies(self):
        # A compile killed part-way writes one. "No prerequisites" and "I
        # could not read it" are different answers.
        import contextlib
        with contextlib.ExitStack() as stack:
            root = self._tree(stack)
            (root / "build.debug" / "src" / "misc" / "pool.d").write_text(
                "", encoding="utf-8")
            ok, message = lspdoctor._check_analyzer()
            self.assertFalse(ok, message)
            self.assertIn("cannot tell", message)

    def test_a_rule_with_no_prerequisites_is_unreadable_too(self):
        # A truncation that keeps the colon and loses the list. A real
        # depfile always names at least the source, so an empty list is not
        # an answer -- and it is the shape an empty-file guard alone misses.
        import contextlib
        with contextlib.ExitStack() as stack:
            root = self._tree(stack)
            (root / "build.debug" / "src" / "misc" / "pool.d").write_text(
                "src/misc/pool.o:\n", encoding="utf-8")
            ok, message = lspdoctor._check_analyzer()
            self.assertFalse(ok, message)
            self.assertIn("cannot tell", message)

    def test_a_recorded_input_that_vanished_is_not_skipped(self):
        # The compiler says the binary depends on it and the tree no longer
        # has it, so the binary was built from something that is gone.
        import contextlib
        with contextlib.ExitStack() as stack:
            root = self._tree(stack)
            (root / self.HEADER).unlink()
            ok, message = lspdoctor._check_analyzer()
            self.assertFalse(ok, message)
            self.assertIn("no longer in the tree", message)
            self.assertIn(self.HEADER, message)

    def test_success_claims_exactly_what_was_checked(self):
        # movian#224 withheld "built from" because reading the current object
        # list proved nothing about the binary. The binary records its own
        # selection now (movian#225), so the claim is earned -- and the
        # message says both halves: the timestamps AND the recorded objects.
        import contextlib
        with contextlib.ExitStack() as stack:
            self._tree(stack)
            ok, message = lspdoctor._check_analyzer()
            self.assertTrue(ok, message)
            self.assertIn("newer than all", message)
            self.assertIn("records the", message)
            self.assertIn("built from", message)

    def test_an_object_added_to_the_recipe_flips_the_verdict(self):
        # The measured case from movian#225: an object that already exists
        # and is older than the binary. No timestamp moves, no depfile moves,
        # and the binary provably cannot contain it.
        import contextlib
        with contextlib.ExitStack() as stack:
            root = self._tree(stack)
            # "an object that already exists and is older than the binary" --
            # so the source and its depfile are present, and every timestamp
            # check below passes. Only the signature can see this.
            self._add_built_object(root, "src/misc/callout.c")
            makefile = (root / "Makefile").read_text(encoding="utf-8")
            (root / "Makefile").write_text(
                makefile.replace("\t${BUILDDIR}/src/misc/buf.o\n",
                                 "\t${BUILDDIR}/src/misc/buf.o \\\n"
                                 "\t${BUILDDIR}/src/misc/callout.o\n", 1),
                encoding="utf-8")
            ok, message = lspdoctor._check_analyzer()
            self.assertFalse(ok, message)
            self.assertIn("src/misc/callout.o", message)

    def test_an_object_dropped_from_the_recipe_flips_it_too(self):
        # The other direction: the binary contains something the recipe no
        # longer names.
        import contextlib
        with contextlib.ExitStack() as stack:
            root = self._tree(stack)
            makefile = (root / "Makefile").read_text(encoding="utf-8")
            (root / "Makefile").write_text(
                makefile.replace("\t${BUILDDIR}/src/misc/buf.o\n", "", 1),
                encoding="utf-8")
            ok, message = lspdoctor._check_analyzer()
            self.assertFalse(ok, message)
            self.assertIn("src/misc/buf.o", message)

    def test_a_binary_without_a_signature_is_unknown_not_fresh(self):
        # An older build.debug predates the marker. That is neither stale nor
        # fresh, and saying either would be a claim nothing supports.
        import contextlib
        with contextlib.ExitStack() as stack:
            root = self._tree(stack)
            binary = root / "build.debug" / "movian-analyze"
            binary.write_bytes(b"#!/bin/sh\n")
            binary.chmod(0o755)
            ok, message = lspdoctor._check_analyzer()
            self.assertFalse(ok, message)
            self.assertIn("UNKNOWN", message)
            self.assertIn("make BUILD=debug", message)
            self.assertNotIn("older than", message)

    def test_a_blind_parser_fails_instead_of_passing(self):
        # Nothing newer than the binary is found when nothing is looked at,
        # so an extractor that stops matching would score perfectly.
        import contextlib
        with contextlib.ExitStack() as stack:
            root = self._tree(stack)
            (root / "Makefile").write_text("all:\n\techo hi\n",
                                           encoding="utf-8")
            ok, message = lspdoctor._check_analyzer()
            self.assertFalse(ok)
            self.assertIn("gone blind", message)

    def test_the_check_does_not_shell_out_to_make(self):
        # The whole point: it no longer asks a question about the tree it
        # does not need answered, and so cannot be wrong about it.
        import contextlib
        with contextlib.ExitStack() as stack:
            self._tree(stack)
            run = stack.enter_context(
                mock.patch.object(subprocess, "run",
                                  side_effect=AssertionError("ran make")))
            self.assertTrue(lspdoctor._check_analyzer()[0])
            self.assertFalse(run.called)


class AnalyzerSelectionSignature(unittest.TestCase):
    """Reading the object list the recipe names NOW is not evidence that it
    is the list that produced the binary.

    Adding an object that already exists and is older than the binary moves
    no timestamp and no depfile, so the mtime comparison cannot see it -- and
    the binary provably cannot contain it, having been linked before the line
    existed. The build therefore records its own selection INSIDE the binary,
    where a hand-copied binary carries it along rather than inheriting a
    stale file left beside it.
    """

    def test_the_recipe_names_twelve_objects(self):
        selection = lspdoctor.analyzer_selection(REAL_MAKEFILE)
        self.assertEqual(len(selection), 12, selection)
        self.assertIn("src/misc/pool.o", selection)
        self.assertIn("ext/duktape/duktape.o", selection)
        self.assertIn("support/devtools/analyze/shim.o", selection)
        self.assertEqual(selection, sorted(selection))

    def test_the_recorded_selection_is_read_out_of_the_binary(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "movian-analyze"
            binary.write_bytes(b"\x7fELF junk"
                               + lspdoctor.selection_marker(["b.o", "a.o"])
                               + b"more junk")
            self.assertEqual(lspdoctor.recorded_selection(binary),
                             ["a.o", "b.o"])

    def test_the_makefile_writes_the_marker_this_module_reads(self):
        # The writer is a printf in the Makefile and the reader is a regex
        # here. Nothing but this test stops them drifting apart, and the
        # drift would be silent: an unreadable marker looks exactly like a
        # binary that carries none.
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        line = [row for row in makefile.split("\n")
                if "MOVIAN-ANALYZE-SELECTION" in row and "printf" in row]
        self.assertEqual(len(line), 1, line)
        # Render the recipe the way make would, with a known selection.
        import re as _re
        fmt = _re.search(r"printf '(.*)' \"\$\{MOVIAN_ANALYZE_SELECTION\}\"",
                         line[0])
        self.assertIsNotNone(fmt, line[0])
        rendered = (fmt.group(1)
                    .replace("\\n", "\n")
                    .replace("%s", "b.o a.o"))
        recorded = lspdoctor._SELECTION_RE.search(rendered.encode("ascii"))
        self.assertIsNotNone(recorded, rendered)
        self.assertEqual(sorted(recorded.group(1).decode().split()),
                         ["a.o", "b.o"])

    def test_the_signature_object_is_actually_linked(self):
        # Generating it is not linking it. Drop it from the link line and
        # every binary carries no marker, so every check answers UNKNOWN --
        # loud, but the mechanism is off and only this notices.
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        rule = makefile.split("${MOVIAN_ANALYZE_BIN}: ")[1].split("\n\n")[0]
        self.assertIn("${MOVIAN_ANALYZE_SELECTION_O}", rule, rule)

    def test_the_recorded_selection_is_built_from_all_three_lists(self):
        # What the signature describes: the three object variables the
        # checker also reads. NOT the whole link line -- that additionally
        # carries `stubs-auto.o` and `selection-auto.o`, both generated from
        # the selection rather than members of it, so recording them would
        # make the signature describe itself.
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        assignment = makefile.split("MOVIAN_ANALYZE_SELECTION = ")[1]
        assignment = assignment.split("\n\n")[0]
        self.assertTrue(assignment.startswith("$(sort $(patsubst "
                                              "${BUILDDIR}/%,%,"), assignment)
        for var in lspdoctor._ANALYZE_OBJ_VARS:
            self.assertIn("${%s}" % var, assignment)

    def test_a_binary_with_no_marker_records_nothing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "movian-analyze"
            binary.write_bytes(b"\x7fELF with no marker at all")
            self.assertIsNone(lspdoctor.recorded_selection(binary))


class MetadataTimeout(unittest.TestCase):
    """Measured on bba50466b: `gen.py --check` exits 0 in 36 s in WSL and
    85 s on the stand. The bound was 30 s, so this check reported "could not
    run" on a healthy tree, everywhere, always."""

    def test_the_bound_is_above_every_measured_runtime(self):
        self.assertGreaterEqual(lspdoctor.METADATA_CHECK_TIMEOUT, 4 * 85)

    def _run(self, **kwargs):
        return mock.patch.object(subprocess, "run", **kwargs)

    def test_a_timeout_claims_nothing_about_the_tree(self):
        expired = subprocess.TimeoutExpired(cmd="gen.py", timeout=360)
        with self._run(side_effect=expired):
            ok, message = lspdoctor._check_metadata()
        self.assertFalse(ok)
        self.assertIn("UNKNOWN", message)
        self.assertNotIn("stale", message)
        self.assertNotIn("failed", message)

    def test_a_real_failure_still_reads_as_a_failure(self):
        done = subprocess.CompletedProcess(["gen.py"], 1, "", "")
        with self._run(return_value=done):
            ok, message = lspdoctor._check_metadata()
        self.assertFalse(ok)
        self.assertIn("failed", message)
        self.assertNotIn("UNKNOWN", message)

    def test_drift_is_named_as_drift(self):
        done = subprocess.CompletedProcess(
            ["gen.py"], 1, "METADATA DRIFT: something moved", "")
        with self._run(return_value=done):
            ok, message = lspdoctor._check_metadata()
        self.assertFalse(ok)
        self.assertIn("stale", message)

    def test_success_reports_how_close_the_bound_is(self):
        # How the next person sees the bound tightening before it starts
        # failing again, which is what nobody could see the first time.
        done = subprocess.CompletedProcess(["gen.py"], 0, "", "")
        with self._run(return_value=done):
            ok, message = lspdoctor._check_metadata()
        self.assertTrue(ok)
        self.assertIn("checked in", message)
        self.assertIn("allowed", message)

    def test_a_missing_interpreter_is_not_a_timeout(self):
        with self._run(side_effect=OSError("no such file")):
            ok, message = lspdoctor._check_metadata()
        self.assertFalse(ok)
        self.assertIn("could not be started", message)
        self.assertNotIn("UNKNOWN", message)


if __name__ == "__main__":
    unittest.main()
