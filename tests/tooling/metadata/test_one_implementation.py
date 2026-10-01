#!/usr/bin/env python3
"""A name with more than one implementation carries no claim taken from any
one body (movian#267).

Two scans kept one record per name while the runtime may hold a different
function under it. An export assigned twice was described by its FIRST
body, and the last one wins at runtime. A prototype alias copied the record
of the LAST assignment to its target, and it holds what the target held at
the alias statement. Every field followed that choice -- `params`,
`variadic`, `returns`, `docParams` and, sharpest, `accessors`, which made a
declaration require a callable where the installed function stores anything.

The owner's decision at triage: refuse, do not model runtime order. Each
case is scanned in both orders -- the body that invokes its callback first,
and the one that stores it first -- because which order a missing refusal
gets wrong IS runtime order. Each body is also scanned alone, so the claims
the refusal withholds are shown to be there to withhold: a probe whose
bodies yield nothing would pass the rest for free.

The declaration still accepts every call one body's declaration accepted,
compiled for real under the tsc on PATH (CI pins 5.7.3).
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Iterator

REPO_ROOT = Path(__file__).resolve().parents[3]
GEN_PY = REPO_ROOT / "support" / "devtools" / "metadata" / "gen.py"

_spec = importlib.util.spec_from_file_location("movian_metadata_gen", GEN_PY)
assert _spec is not None and _spec.loader is not None
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

PROBE = "movian/_probe"
PROBE_FILE = "res/ecmascript/modules/movian/_probe.js"

# The claims a body yields, every one of which the refusal withholds.
CLAIMS = ("params", "nargs", "variadic", "accessors", "contested",
          "returns", "docParams", "docReturns", "constructor")

ITEM = ("function Item() {}\n"
        "Item.prototype.x = function() {};\n")
DOC = ("/**\n"
       " * @param {Function} cb\n"
       " * @returns {Item}\n"
       " */\n")
# The two functions of the issue: one invokes its callback, one stores it.
INVOKES = ("function(cb) {\n"
           "  cb();\n"
           "  return new Item();\n"
           "};\n")
STORES = ("function(cb) {\n"
          "  this.cb = cb;\n"
          "};\n")


@contextlib.contextmanager
def probe_tree(source: str) -> Iterator[Path]:
    """A corpus holding only `source`, as `movian/_probe`.

    `COMMONJS_DIR` and `REPO_ROOT` are both redirected, as the census tests
    redirect them, so no probe is written into the working tree.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / PROBE_FILE
        path.parent.mkdir(parents=True)
        path.write_text(source, encoding="utf-8")
        saved = (gen.REPO_ROOT, gen.COMMONJS_DIR)
        gen.REPO_ROOT = root
        gen.COMMONJS_DIR = root / "res" / "ecmascript" / "modules"
        try:
            yield path
        finally:
            gen.REPO_ROOT, gen.COMMONJS_DIR = saved


def build(source: str) -> tuple[dict, str]:
    """The `movian/_probe` module record, and what the generator warned."""
    stderr = io.StringIO()
    with probe_tree(source), contextlib.redirect_stderr(stderr):
        modules = gen.build_commonjs_modules()
    module = next(m for m in modules if m["name"] == PROBE)
    return module, stderr.getvalue()


def exports_of(module: dict) -> dict:
    return {record["name"]: record for record in module["exports"]}


def methods_of(module: dict) -> dict:
    return {"%s.%s" % (shape["name"], method["name"]): method
            for shape in module["shapes"]
            for method in shape["methods"]}


def lines_of(source: str, head: str) -> list[int]:
    """The 1-based lines of `source` that begin with `head`."""
    return [number for number, line in enumerate(source.splitlines(), 1)
            if line.startswith(head)]


class WithheldRecord(unittest.TestCase):
    def assertWithheld(self, record: dict, name: str, line: int,
                       implementations: list[int], **rest) -> None:
        for claim in CLAIMS:
            self.assertNotIn(claim, record)
        self.assertEqual(record, {
            "name": name, "source": {"file": PROBE_FILE, "line": line},
            "implementations": implementations, **rest})


class TheRepeatedExport(WithheldRecord):
    """`exports.f = ...` twice: the scan described the first body."""

    def source(self, first: str, second: str) -> str:
        return (ITEM + DOC + "exports.f = " + first
                + DOC + "exports.f = " + second)

    def test_each_body_alone_makes_claims(self) -> None:
        invoking = exports_of(build(ITEM + DOC + "exports.f = " + INVOKES)[0])
        self.assertEqual(invoking["f"]["accessors"], {"cb": [8]})
        self.assertEqual(invoking["f"]["returns"], "Item")
        self.assertEqual(invoking["f"]["docParams"], {"cb": "Function"})
        storing = exports_of(build(ITEM + DOC + "exports.f = " + STORES)[0])
        self.assertEqual(storing["f"]["params"], ["cb"])
        self.assertEqual(storing["f"]["docReturns"], "Item")
        self.assertNotIn("accessors", storing["f"])

    def test_invoking_body_first(self) -> None:
        source = self.source(INVOKES, STORES)
        module, warnings = build(source)
        lines = lines_of(source, "exports.f =")
        self.assertWithheld(exports_of(module)["f"], "f", lines[0], lines,
                            callable=True)
        self.assertIn(
            "%s:%d: warning: exports.f: assigned more than once (lines "
            "%d, %d)" % (PROBE_FILE, lines[0], *lines), warnings)

    def test_storing_body_first(self) -> None:
        source = self.source(STORES, INVOKES)
        module, warnings = build(source)
        lines = lines_of(source, "exports.f =")
        self.assertWithheld(exports_of(module)["f"], "f", lines[0], lines,
                            callable=True)
        self.assertIn("exports.f: assigned more than once (lines %d, %d)"
                      % tuple(lines), warnings)

    def test_a_value_first_stays_a_value(self) -> None:
        """`const g: any` is what the declaration has been. A callable form
        would reject `var n: number = probe.g` (TS2322), and `any` an
        unannotated callback (TS7006), so the form follows the first
        assignment, as it always has."""
        source = "exports.g = null;\nexports.g = " + INVOKES
        module, _ = build(source)
        self.assertWithheld(exports_of(module)["g"], "g", 1, [1, 2])

    def test_what_the_first_body_installs_stays_declared(self) -> None:
        """`this.__proto__ = sp` hoists what the body assigns to `this` onto
        the module and names an instance interface after the export. Those
        declare other names, and dropping them would narrow the module."""
        source = ("var sp = {};\n"
                  "sp.x = function() {};\n"
                  "exports.init = function(cb) {\n"
                  "  this.__proto__ = sp;\n"
                  "  this.helper = function(a) { a(); };\n"
                  "  cb();\n"
                  "};\n"
                  "exports.init = function(cb) {\n"
                  "  this.__proto__ = sp;\n"
                  "  this.stored = cb;\n"
                  "};\n")
        module, _ = build(source)
        record = dict(exports_of(module)["init"])
        helper = record.pop("receiverMembers")
        self.assertWithheld(record, "init", 3, [3, 8], callable=True)
        self.assertEqual([member["name"] for member in helper], ["helper"])
        self.assertEqual(
            [member["name"] for member in module["receiverMembers"]],
            ["helper"])
        lines = [line.strip() for line in gen.render_dts(
            {"js": {"modules": [module]}}).splitlines()]
        self.assertIn("interface init extends sp {", lines)
        self.assertIn("function helper(a?: Function | "
                      "((...args: any[]) => void)): any;", lines)

    def test_one_assignment_is_unaffected(self) -> None:
        module, warnings = build(ITEM + "exports.f = " + INVOKES
                                 + "exports.h = " + STORES)
        self.assertEqual(exports_of(module)["f"]["accessors"], {"cb": [4]})
        self.assertNotIn("implementations", exports_of(module)["h"])
        self.assertNotIn("more than once", warnings)


class ThePrototypeAlias(WithheldRecord):
    """`C.prototype.alias = C.prototype.m` with `m` assigned twice: the alias
    copied the last body's record, and holds the one before the alias."""

    def source(self, first: str, second: str) -> str:
        return (ITEM + "function C() {}\n"
                + DOC + "C.prototype.m = " + first
                # The alias's own block: copying the target and reading it,
                # as an alias with one implementation does, keeps a claim.
                + "/** @returns {Item} */\n"
                + "C.prototype.alias = C.prototype.m;\n"
                + DOC + "C.prototype.m = " + second)

    def check(self, source: str) -> None:
        module, warnings = build(source)
        methods = methods_of(module)
        lines = lines_of(source, "C.prototype.m =")
        alias = lines_of(source, "C.prototype.alias =")[0]
        self.assertWithheld(methods["C.alias"], "alias", alias, lines,
                            aliasOf="m")
        self.assertWithheld(methods["C.m"], "m", lines[0], lines)
        self.assertIn(
            "%s:%d: warning: C.prototype.alias: an alias of m, which holds "
            "more than one implementation (lines %d, %d)"
            % (PROBE_FILE, alias, *lines), warnings)
        self.assertIn("C.prototype.m: assigned more than once (lines %d, %d)"
                      % tuple(lines), warnings)

    def test_storing_body_first(self) -> None:
        """The issue's probe: the scan recorded `alias` with `{cb}`."""
        self.check(self.source(STORES, INVOKES))

    def test_invoking_body_first(self) -> None:
        self.check(self.source(INVOKES, STORES))

    def test_an_alias_assigned_twice_is_withheld(self) -> None:
        source = (ITEM + "function C() {}\n"
                  "C.prototype.one = " + INVOKES
                  + "C.prototype.a = C.prototype.one;\n"
                  "C.prototype.a = " + STORES)
        methods = methods_of(build(source)[0])
        self.assertWithheld(methods["C.a"], "a", 8, [8, 9])
        self.assertEqual(methods["C.one"]["accessors"], {"cb": [5]})

    def test_an_alias_of_a_withheld_alias_is_withheld(self) -> None:
        source = (ITEM + "function C() {}\n"
                  "C.prototype.m = " + INVOKES
                  + "C.prototype.a = C.prototype.m;\n"
                  "C.prototype.b = C.prototype.a;\n"
                  "C.prototype.m = " + STORES)
        module, warnings = build(source)
        methods = methods_of(module)
        lines = lines_of(source, "C.prototype.m =")
        self.assertWithheld(methods["C.b"], "b",
                            lines_of(source, "C.prototype.b =")[0], lines,
                            aliasOf="a")
        self.assertIn("C.prototype.b: an alias of a, which holds more than "
                      "one implementation (lines %d, %d)" % tuple(lines),
                      warnings)

    def test_an_assignment_the_scan_does_not_read_counts(self) -> None:
        """`= helper` installs a function the function scan never sees."""
        source = (ITEM + "function C() {}\n"
                  "C.prototype.m = " + INVOKES
                  + "C.prototype.m = helper;\n")
        self.assertWithheld(methods_of(build(source)[0])["C.m"],
                            "m", 4, [4, 8])

    def test_a_conditional_assignment_counts(self) -> None:
        source = (ITEM + "function C() {}\n"
                  "C.prototype.m = " + INVOKES
                  + "if (legacy) {\n"
                  "  C.prototype.m = " + STORES + "}\n")
        self.assertWithheld(methods_of(build(source)[0])["C.m"],
                            "m", 4, [4, 9])

    def test_a_shared_object_member_assigned_twice_is_withheld(self) -> None:
        source = (ITEM + "var sp = {};\n"
                  "sp.m = " + INVOKES
                  + "sp.m = " + STORES
                  + "exports.init = function() {\n"
                  "  this.__proto__ = sp;\n"
                  "};\n")
        module, warnings = build(source)
        self.assertWithheld(methods_of(module)["sp.m"], "m", 4, [4, 8])
        self.assertIn("sp.m: assigned more than once (lines 4, 8)", warnings)

    def test_one_implementation_is_unaffected(self) -> None:
        source = (ITEM + "function C() {}\n"
                  "C.prototype.m = " + INVOKES
                  + "C.prototype.alias = C.prototype.m;\n")
        module, warnings = build(source)
        methods = methods_of(module)
        self.assertEqual(methods["C.alias"]["accessors"], {"cb": [5]})
        self.assertEqual(methods["C.alias"]["aliasOf"], "m")
        self.assertNotIn("implementations", methods["C.m"])
        self.assertNotIn("more than once", warnings)


class TheCorpus(unittest.TestCase):
    def test_the_html_aliases_keep_their_targets_records(self) -> None:
        """`getElementsByClassName` and `getElementsByTagName`, the corpus's
        two aliases, whose targets are assigned once."""
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            shapes = gen.scan_commonjs_shapes(
                REPO_ROOT / "res" / "ecmascript" / "modules" / "movian"
                / "html.js")
        methods = {method["name"]: method for shape in shapes
                   for method in shape["methods"]}
        for alias, target in (
                ("getElementsByClassName", "getElementByClassName"),
                ("getElementsByTagName", "getElementByTagName")):
            with self.subTest(alias):
                self.assertEqual(methods[alias]["aliasOf"], target)
                self.assertEqual(methods[alias]["params"],
                                 methods[target]["params"])
                self.assertNotIn("implementations", methods[alias])
        self.assertNotIn("more than once", stderr.getvalue())


# Both cases and a method with one implementation, in one module.
MODULE = (ITEM + "function C() {}\n"
          "C.prototype.m = " + STORES
          + "C.prototype.alias = C.prototype.m;\n"
          "C.prototype.m = " + INVOKES
          + "C.prototype.one = " + INVOKES
          + "exports.f = " + INVOKES
          + "exports.f = " + STORES)

WITHHELD = ("...args: (Function | ((...args: any[]) => void) | {} | null "
            "| undefined)[]")


class TheDeclaration(unittest.TestCase):
    def setUp(self) -> None:
        module, _ = build(MODULE)
        self.dts = gen.render_dts({"js": {"modules": [module]}})
        self.lines = [line.strip() for line in self.dts.splitlines()]

    def test_a_method_takes_anything(self) -> None:
        self.assertIn("m(%s): any;" % WITHHELD, self.lines)
        self.assertIn("alias(%s): any;" % WITHHELD, self.lines)

    def test_an_export_is_called_or_constructed_with_anything(self) -> None:
        """Each body's own declaration is a call or a construct signature,
        as it uses `this` or not, so the refused one keeps both."""
        start = self.lines.index("const f: {")
        self.assertEqual(self.lines[start:start + 4], [
            "const f: {",
            "new (%s): any;" % WITHHELD,
            "(%s): any;" % WITHHELD,
            "};"])

    def test_tsc_accepts_every_call_a_body_declared(self) -> None:
        """`f(42)` and `f(1, 2, 3)` against a fixed formal list, and an
        unannotated callback against plain `any` (TS7006, ADR-0005), are
        each a call one body's declaration accepted and a narrower one
        rejects. `c.one(1, 2)` is the control: one implementation keeps its
        list, and an error proves the compile checked the file at all."""
        tsc = shutil.which("tsc")
        if tsc is None:
            self.fail("tsc not on PATH")
        usage = (
            "import probe = require('movian/_probe');\n"
            "probe.f(42);\n"
            "probe.f(1, 2, 3);\n"
            "probe.f(function (value) { return value; });\n"
            "probe.f();\n"
            "new probe.f(function (value) { return value; });\n"
            "declare const c: probe.C;\n"
            "c.m(42);\n"
            "c.m(1, 2, 3);\n"
            "c.m(function (value) { return value; });\n"
            "c.alias(42);\n"
            "c.alias(1, 2, 3);\n"
            "c.alias(function (value) { return value; });\n"
            "var item: number = c.alias();\n"
            "c.one(1, 2);\n")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "api.d.ts").write_text(self.dts, encoding="utf-8")
            (Path(tmp) / "use.ts").write_text(usage, encoding="utf-8")
            result = subprocess.run(
                [tsc, "--noEmit", "--strict", "--target", "ES2015",
                 "--lib", "ES2015", "--module", "commonjs",
                 "--pretty", "false", "api.d.ts", "use.ts"],
                cwd=tmp, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, check=False, timeout=300)
        errors = [line for line in result.stdout.splitlines()
                  if ": error TS" in line]
        self.assertEqual(len(errors), 1, result.stdout)
        self.assertIn("use.ts(15,", errors[0])
        self.assertIn("TS2554", errors[0])


class TheCensuses(unittest.TestCase):
    """The census sees the regions the generator reads (movian#229), and a
    withheld member says why its slots are `any`."""

    # A shape the recogniser types: `{ n: 1 }` it declines on its own
    # (#160), and a probe built of those would pass with the refusal gone.
    BODY = ("function() {\n"
            "  return { close: function() { io.close(); } };\n"
            "};\n")
    SOURCE = "exports.make = " + BODY + "exports.make = " + BODY

    def census(self, source: str) -> tuple[list, dict, bool, str]:
        with probe_tree(source), contextlib.redirect_stderr(io.StringIO()):
            census = gen._object_return_census()
            modules = gen.build_commonjs_modules()
            ok, output = gen._check_object_return_coverage(
                {"js": {"modules": modules}})
        make = exports_of(next(m for m in modules if m["name"] == PROBE))
        return census, make["make"], ok, output

    def test_one_body_is_emitted(self) -> None:
        census, make, ok, _ = self.census("exports.make = " + self.BODY)
        self.assertEqual([site["status"] for site in census], ["emitted"])
        self.assertEqual(make["returns"]["kind"], "object")
        self.assertTrue(ok)

    def test_every_body_of_a_repeated_export_is_declined(self) -> None:
        """Before, the second body was `unattributed` and failed the gate,
        and the first was typed although the second is the one installed."""
        reason = ("assigned more than once (lines 1, 4); the declaration "
                  "takes no claim from any one body")
        census, make, ok, output = self.census(self.SOURCE)
        self.assertEqual(
            [(site["line"], site["status"], site.get("reason"))
             for site in census],
            [(2, "declined", reason), (5, "declined", reason)])
        self.assertTrue(ok, output)
        self.assertNotIn("returns", make)

    def test_the_doc_type_census_names_the_refusal(self) -> None:
        module, _ = build(MODULE)
        census = gen._doc_type_census({"js": {"modules": [module]}})
        reasons = {(site["member"], site["slot"]): site.get("reason")
                   for site in census}
        lines = ", ".join(map(str, lines_of(MODULE, "C.prototype.m =")))
        self.assertEqual(
            reasons[("C.alias", "...args")],
            "an alias of m, which holds more than one implementation "
            "(lines %s); the declaration takes no claim from any one body"
            % lines)
        self.assertEqual(reasons[("f", "(return)")],
                         reasons[("f", "...args")])
        self.assertIn("assigned more than once", reasons[("f", "...args")])

    def test_a_callable_export_is_still_a_function_to_the_oracle(
            self) -> None:
        """From the record, as `params` says it of a body. The fallback
        reads the assignment's own line, and `exports.f =` alone on it reads
        as a value where the oracle holds a function."""
        source = ("exports.f =\n"
                  "  function(cb) { cb(); };\n"
                  "exports.f = function(cb) { this.cb = cb; };\n")
        module, _ = build(source)
        with probe_tree(source):
            kind = gen._static_export_kind(
                PROBE, "f", {PROBE: module}, {}, set())
        self.assertEqual(kind, "function")


if __name__ == "__main__":
    unittest.main()
