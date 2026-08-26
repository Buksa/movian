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
            # header, and a system header that must not be picked up.
            depfile = root / "build.debug" / (name[:-len(".c")] + ".d")
            depfile.parent.mkdir(parents=True, exist_ok=True)
            depfile.write_text(
                "%s.o: \\\n %s \\\n %s \\\n /usr/include/stdio.h\n\n"
                "%s:\n" % (name[:-len(".c")], root / name, header, header),
                encoding="utf-8")
        binary = root / "build.debug" / "movian-analyze"
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(b"#!/bin/sh\n")
        binary.chmod(0o755)
        self._touch(binary, newest=True)
        stack.enter_context(
            mock.patch.object(lspdoctor, "REPOSITORY_ROOT", root))
        return root

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

    def test_a_system_header_is_not_an_input(self):
        # /usr/include moves when the distribution says so, and a check that
        # fires on that is the check nobody reads.
        import contextlib
        with contextlib.ExitStack() as stack:
            self._tree(stack)
            self.assertTrue(lspdoctor._check_analyzer()[0])

    def test_a_missing_depfile_is_not_read_as_nothing_changed(self):
        import contextlib
        with contextlib.ExitStack() as stack:
            root = self._tree(stack)
            (root / "build.debug" / "src" / "misc" / "pool.d").unlink()
            ok, message = lspdoctor._check_analyzer()
            self.assertFalse(ok, message)
            self.assertIn("cannot tell", message)
            self.assertIn("src/misc/pool.d", message)

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


if __name__ == "__main__":
    unittest.main()
