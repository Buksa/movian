#!/usr/bin/env python3
"""A compile tsc did not finish checking fails the gate (movian#270).

tsc 6.0.3 rejects the `--target ES5` and `--moduleResolution node` that the
plugin_examples and core-module compiles pass, exits 2, and checks nothing.
The example gate kept only lines naming an example, so it read that run as a
pass; the core-module gate saw every curated diagnostic vanish and advised
deleting the list.

CI pins 5.7.3, which cannot produce that output, so the gate half replays
what 6.0.3 printed. The other half compiles for real under the tsc on PATH:
a diagnostic against a declaration file must still not fail the example gate,
and an API break must still turn it red -- the check that goes green when
nothing compiles.

The supported band ends below 6.0 for the same reason, so `gen.py --check`
refuses 6.x before it runs any compile at all.

The coverage floor reads a failing compile as a covered member, so there a
refused compile covered everything it probed. The real tsc refuses one when
the fixture it names is missing.

A tsc that crashes reports no diagnostic at all, and the example gate and the
coverage probe read that as a pass too.
"""

from __future__ import annotations

import importlib.util
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[3]
CHECKER = (REPO_ROOT / "support" / "devtools" / "metadata"
           / "check_reference_dts.py")

_spec = importlib.util.spec_from_file_location(
    "movian_check_reference_dts", CHECKER)
assert _spec is not None and _spec.loader is not None
crd = importlib.util.module_from_spec(_spec)
# Registered before it runs: its dataclasses look their module up by name.
sys.modules[_spec.name] = crd
_spec.loader.exec_module(crd)

# `01-basic/03-all-settings-types/main.js:54` passes `null` as `icon`.
EXAMPLE = crd.EXAMPLES_DIR / "01-basic" / "03-all-settings-types"

# Captured verbatim from tsc 6.0.3, exit status 2, running
# `_check_one_example`'s command on EXAMPLE; `_core_module_tsc_command`
# produced the same bytes. Neither compile reported anything else.
TSC_6_0_3_OUTPUT = (
    "error TS5107: Option 'moduleResolution=node10' is deprecated and will "
    "stop functioning in TypeScript 7.0. Specify compilerOption "
    "'\"ignoreDeprecations\": \"6.0\"' to silence this error.\n"
    "  Visit https://aka.ms/ts6 for migration information.\n"
    "error TS5107: Option 'target=ES5' is deprecated and will stop "
    "functioning in TypeScript 7.0. Specify compilerOption "
    "'\"ignoreDeprecations\": \"6.0\"' to silence this error.\n")
TSC_6_0_3_ERRORS = [line for line in TSC_6_0_3_OUTPUT.splitlines()
                    if line.startswith("error TS")]


# Captured from tsc 5.7.3 running `_check_one_example`'s command on EXAMPLE
# with node's heap capped: at `NODE_OPTIONS=--max-old-space-size=8` node
# printed this and aborted, at `=4` it segfaulted printing nothing. Verbatim
# except the native stack trace, cut after two frames. The statuses are the
# ones the shell reported (SIGABRT, SIGSEGV); Python reads the same crashes
# as -6 and -11. The rule reads only that the status is non-zero and that no
# diagnostic was printed, and replaying the status spares a core dump.
TSC_OOM_OUTPUT = (
    "\n<--- Last few GCs --->\n\n"
    "[2371340:0x1ecd6000]      134 ms: Mark-Compact 12.4 (13.9) -> 11.7 "
    "(16.4) MB, pooled: 0 MB, 7.53 / 0.00 ms  (average mu = 0.895, current "
    "mu = 0.913) allocation failure; scavenge might not succeed\n\n"
    "FATAL ERROR: Reached heap limit Allocation failed - JavaScript heap out "
    "of memory\n"
    "----- Native stack trace -----\n\n"
    " 1: 0x73f8c4 node::OOMErrorHandler(char const*, v8::OOMDetails const&) "
    "[node]\n"
    " 2: 0xc06f90  [node]\n")
TSC_CRASHES = {
    "out of memory": (TSC_OOM_OUTPUT, 134),
    "segfault": ("", 139),
}


def scratch_directory(test: unittest.TestCase) -> Path:
    """A directory outside the working tree, removed after `test`."""
    scratch = tempfile.TemporaryDirectory(prefix="movian-270-")
    test.addCleanup(scratch.cleanup)
    return Path(scratch.name)


def replaying_tsc(directory: Path, version: str,
                  output: str = "", status: int = 0) -> Path:
    """A `tsc` that answers `--version` with `version` and any compile with
    `output` and `status`, recording each compile in `compiles.log`."""
    stub = directory / "tsc"
    stub.write_text(
        "#!%s\n"
        "import sys\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print('Version %s')\n"
        "    sys.exit(0)\n"
        "with open(%r, 'a') as log:\n"
        "    log.write(' '.join(sys.argv[1:]) + '\\n')\n"
        "sys.stdout.write(%r)\n"
        "sys.exit(%d)\n"
        % (sys.executable, version, str(directory / "compiles.log"),
           output, status),
        encoding="utf-8")
    stub.chmod(0o755)
    return stub


class AFilelessDiagnosticFailsTheGate(unittest.TestCase):
    def setUp(self) -> None:
        self.tsc = str(replaying_tsc(
            scratch_directory(self), "6.0.3", TSC_6_0_3_OUTPUT, 2))

    def test_the_example_gate_quotes_what_tsc_rejected(self) -> None:
        error = crd._check_one_example(self.tsc, EXAMPLE)
        self.assertIsNotNone(error, "a compile that checked nothing passed")
        assert error is not None
        for line in TSC_6_0_3_ERRORS:
            self.assertIn(line, error)

    def test_the_core_module_gate_quotes_it_instead_of_blaming_the_list(
            self) -> None:
        errors, _counts = crd.check_core_modules(self.tsc)
        report = "\n".join(errors)
        for line in TSC_6_0_3_ERRORS:
            self.assertIn(line, report)
        # The curated list is fine; telling the reader to empty it is wrong.
        self.assertNotIn("no longer fires", report)


class ACrashedCompileFailsTheGate(unittest.TestCase):
    def crashing_tsc(self, crash: str) -> str:
        output, status = TSC_CRASHES[crash]
        return str(replaying_tsc(
            scratch_directory(self), "5.7.3", output, status))

    def test_the_example_gate_reports_the_crash(self) -> None:
        for crash, (_output, status) in TSC_CRASHES.items():
            with self.subTest(crash):
                error = crd._check_one_example(
                    self.crashing_tsc(crash), EXAMPLE)
                self.assertIsNotNone(error, "a crashed compile passed")
                self.assertIn("exited %d" % status, error or "")
        error = crd._check_one_example(
            self.crashing_tsc("out of memory"), EXAMPLE)
        self.assertIn("JavaScript heap out of memory", error or "")

    def test_the_core_module_gate_reports_it_instead_of_blaming_the_list(
            self) -> None:
        for crash, (_output, status) in TSC_CRASHES.items():
            with self.subTest(crash):
                errors, _counts = crd.check_core_modules(
                    self.crashing_tsc(crash))
                report = "\n".join(errors)
                self.assertIn("exited %d" % status, report)
                self.assertNotIn("no longer fires", report)

    def test_the_coverage_probe_does_not_count_it_as_coverage(self) -> None:
        entry = crd.GENERATED_COVERAGE_FLOOR[0]
        artifacts = {str(path): crd._read(path) for path
                     in (crd.GENERATED_DTS, crd.GENERATED_V1_DTS)}
        for crash, (_output, status) in TSC_CRASHES.items():
            with self.subTest(crash):
                error = crd._coverage_probe(
                    self.crashing_tsc(crash), entry,
                    scratch_directory(self), artifacts)
                self.assertIsNotNone(
                    error, "a crashed compile counted as coverage")
                self.assertIn("exited %d" % status, error or "")


class TheBandEndsBelowSix(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = scratch_directory(self)

    def test_tsc_6_is_refused_before_anything_compiles(self) -> None:
        replaying_tsc(self.directory, "6.0.3")
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {"PATH": str(self.directory)}), \
                mock.patch.object(sys, "argv", [str(CHECKER)]), \
                redirect_stdout(io.StringIO()), redirect_stderr(stderr):
            status = crd.main()
        self.assertEqual(status, 1)
        self.assertIn("is outside the supported range >=5.3,<6.0",
                      stderr.getvalue())
        compiles = self.directory / "compiles.log"
        if compiles.exists():
            self.fail("tsc compiled before the band refused it:\n"
                      + compiles.read_text())

    def test_the_last_measured_5_x_is_still_admitted(self) -> None:
        # The other side of the edge, so a check refusing everything
        # cannot pass the test above.
        self.assertIsNone(crd._check_tsc_version(
            str(replaying_tsc(self.directory, "5.9.3"))))


def supported_tsc(test: unittest.TestCase) -> str:
    """The tsc `gen.py --check` would use.

    Failing, not skipping: the checker itself refuses to run without a
    supported tsc, and CI installs one before these tests.
    """
    tsc = shutil.which("tsc")
    if tsc is None:
        test.fail("tsc not on PATH")
    refusal = crd._check_tsc_version(tsc)
    if refusal is not None:
        test.fail(refusal)
    return tsc


def with_create_info_icon(icon_type: str) -> str:
    """The artifact with both declarations of `createInfo` taking `icon` as
    `icon_type`. Raises if they are not both there, so a reshaped artifact
    fails the test instead of mutating nothing."""
    text = crd.GENERATED_DTS.read_text(encoding="utf-8")
    mutated, count = re.subn(
        r"(\bcreateInfo\([^)]*\bicon\?: )[^,)]+", r"\g<1>" + icon_type, text)
    if count != 2:
        raise AssertionError(
            "expected 2 declarations of createInfo(..., icon?, ...) in %s, "
            "found %d" % (crd.GENERATED_DTS.name, count))
    return mutated


class TheExampleGateStillReadsARealCompile(unittest.TestCase):
    """Under the tsc `gen.py --check` would use, a real compile."""

    def setUp(self) -> None:
        self.tsc = supported_tsc(self)
        self.scratch = scratch_directory(self)

    def compile_against(self, declarations: str) -> tuple[str | None, str]:
        """The gate's verdict on EXAMPLE, and what tsc printed for it."""
        dts = self.scratch / "movian-api.d.ts"
        dts.write_text(declarations, encoding="utf-8")
        outputs: list[str] = []
        real_run = subprocess.run

        def recording_run(*args, **kwargs):
            result = real_run(*args, **kwargs)
            outputs.append(result.stdout)
            return result

        with mock.patch.object(crd, "GENERATED_DTS", dts), \
                mock.patch.object(crd.subprocess, "run", recording_run):
            error = crd._check_one_example(self.tsc, EXAMPLE)
        self.assertEqual(len(outputs), 1)
        return error, outputs[0]

    def test_an_api_break_turns_the_gate_red(self) -> None:
        error, _output = self.compile_against(with_create_info_icon("string"))
        self.assertIsNotNone(error)
        self.assertRegex(
            error or "",
            r"(?m)^plugin_examples/01-basic/03-all-settings-types/main\.js"
            r"\(54,23\): error TS2345:")

    def test_a_declaration_files_own_diagnostic_does_not(self) -> None:
        # An undeclared type name is what a generator slip would emit. tsc
        # reports it in the declaration file and resolves the slot to an
        # error type, which the example's `null` still fits.
        error, output = self.compile_against(
            with_create_info_icon("UndeclaredIconType"))
        in_declarations = [
            path for path, _line, _column, _code
            in crd.DIAGNOSTIC_RE.findall(output)
            if (REPO_ROOT / path).resolve()
            == (self.scratch / "movian-api.d.ts").resolve()]
        self.assertTrue(in_declarations,
                        "the probe raised nothing in the declarations:\n"
                        + output)
        self.assertIsNone(error)


class ARefusedCoverageProbeIsNotCoverage(unittest.TestCase):
    """43 of the floor's 75 members are v1, and only their probes compile
    the v1 fixture. With it deleted, tsc answered every one of them
    `error TS6053: File '...' not found.`, and the floor printed OK."""

    def setUp(self) -> None:
        self.tsc = supported_tsc(self)
        self.scratch = scratch_directory(self)
        self.entry = next(entry for entry in crd.GENERATED_COVERAGE_FLOOR
                          if entry[0] == crd.V1_SCOPE)
        self.artifacts = {str(path): crd._read(path) for path
                          in (crd.GENERATED_DTS, crd.GENERATED_V1_DTS)}

    def probe(self) -> str | None:
        return crd._coverage_probe(
            self.tsc, self.entry, self.scratch, self.artifacts)

    def test_a_member_the_fixture_uses_is_covered(self) -> None:
        # The control: the same probe, with the fixture there.
        self.assertIsNone(self.probe())

    def test_a_missing_fixture_covers_nothing(self) -> None:
        missing = self.scratch / crd.GENERATED_V1_FIXTURE.name
        with mock.patch.object(crd, "GENERATED_V1_FIXTURE", missing):
            error = self.probe()
        self.assertIsNotNone(
            error, "a compile that checked nothing counted as coverage")
        self.assertRegex(error or "", r"(?m)^error TS\d+: .*%s"
                         % re.escape(missing.name))


if __name__ == "__main__":
    unittest.main()
