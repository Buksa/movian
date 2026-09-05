#!/usr/bin/env python3
"""The census that makes a declined return shape impossible to miss (#229).

Why this file exists. Nothing compared an emitted `returns` against source.
`_check_commonjs_shape_coverage` compares member NAMES module by module, and
the runtime oracle observes MEMBERS, not return shapes. So a return shape the
generator declined to emit failed no gate and printed no diagnostic -- which is
how `movian/itemhook.create` stayed `any` from the day the rule was tightened
until somebody read the file.

The census is read from the SOURCE tree on purpose. A check whose two sides are
both the generator's own output cannot see the generator declining; that is the
"both sides blind in the same way" shape this program keeps removing.

What is pinned here is the pair, the same way `test_returned_shape.py` pins
its rule:

* the real corpus passes and every site in it is accounted for -- delete the
  census and this goes red;
* each of the three failure modes fails -- weaken any of them and the
  corresponding case goes red instead. A test that only pinned the pass would
  be satisfied by a gate that returns True to everything.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
GEN_PY = REPO_ROOT / "support" / "devtools" / "metadata" / "gen.py"

_spec = importlib.util.spec_from_file_location("movian_metadata_gen", GEN_PY)
assert _spec is not None and _spec.loader is not None
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


class CorpusCensus(unittest.TestCase):
    """Against the tree as it is."""

    def test_every_site_in_the_corpus_is_accounted_for(self) -> None:
        census = gen._object_return_census()
        self.assertTrue(census, "the census found no sites at all")
        for site in census:
            with self.subTest("%s:%d" % (site["file"], site["line"])):
                self.assertIn(site["status"], {"emitted", "declined"})
                if site["status"] == "declined":
                    self.assertTrue(site["reason"])

    def test_the_two_known_sites_are_the_census(self) -> None:
        """Both object-literal returns in `res/ecmascript/modules/**`.

        A measurement, not a target. If a third is written this fails, and
        that is the point: the new one has to be looked at, not absorbed.
        """
        self.assertEqual(
            [(s["file"], s["export"], s["status"]) for s in
             gen._object_return_census()],
            [("res/ecmascript/modules/movian/html.js", "parse", "emitted"),
             ("res/ecmascript/modules/movian/itemhook.js", "create",
              "emitted")])

    def test_the_committed_artifact_passes(self) -> None:
        artifact = REPO_ROOT / "generated" / "movian-metadata.json"
        if not artifact.is_file():
            self.skipTest("generated/movian-metadata.json is absent")
        ok, output = gen._check_object_return_coverage(
            json.loads(artifact.read_text()))
        self.assertTrue(ok, output)
        self.assertIn("OBJECT RETURN COVERAGE OK", output)


class SyntheticCorpus(unittest.TestCase):
    """Each failure mode, on a corpus written for it.

    `COMMONJS_DIR` and `REPO_ROOT` are both redirected: the census walks the
    first and `rel()` reports against the second, so a fixture outside the
    real tree still produces the paths the report prints.
    """

    def census_of(self, **modules: str):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "res" / "ecmascript" / "modules" / "movian").mkdir(
                parents=True)
            for name, text in modules.items():
                (root / "res" / "ecmascript" / "modules" / "movian"
                 / (name + ".js")).write_text(text, encoding="utf-8")
            saved = (gen.REPO_ROOT, gen.COMMONJS_DIR)
            gen.REPO_ROOT = root
            gen.COMMONJS_DIR = root / "res" / "ecmascript" / "modules"
            try:
                return gen._object_return_census()
            finally:
                gen.REPO_ROOT, gen.COMMONJS_DIR = saved

    def check_of(self, artifact, **modules: str):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "res" / "ecmascript" / "modules" / "movian").mkdir(
                parents=True)
            for name, text in modules.items():
                (root / "res" / "ecmascript" / "modules" / "movian"
                 / (name + ".js")).write_text(text, encoding="utf-8")
            saved = (gen.REPO_ROOT, gen.COMMONJS_DIR)
            gen.REPO_ROOT = root
            gen.COMMONJS_DIR = root / "res" / "ecmascript" / "modules"
            try:
                return gen._check_object_return_coverage(artifact)
            finally:
                gen.REPO_ROOT, gen.COMMONJS_DIR = saved

    def test_a_site_no_export_region_covers_is_unattributed(self) -> None:
        """The silence itself.

        A `return {` inside a helper that no export region reaches: the
        generator cannot emit it and cannot decline it, because it never read
        it. Nothing else in the build mentions such a site.
        """
        census = self.census_of(helper=(
            "function build() {\n"
            "  return { root: new Node(x) };\n"
            "}\n"
            "exports.use = function() { return build(); };\n"))
        self.assertEqual([s["status"] for s in census], ["unattributed"])
        ok, output = self.check_of({}, helper=(
            "function build() {\n"
            "  return { root: new Node(x) };\n"
            "}\n"
            "exports.use = function() { return build(); };\n"))
        self.assertFalse(ok)
        self.assertIn("OBJECT RETURN COVERAGE FAILED", output)
        self.assertIn("never read it", output)

    def test_a_decline_is_reported_and_is_not_a_failure(self) -> None:
        source = ("exports.make = function() {\n"
                  "  return { n: 1 };\n"
                  "};\n")
        census = self.census_of(plain=source)
        self.assertEqual([s["status"] for s in census], ["declined"])
        self.assertTrue(census[0]["reason"])
        ok, output = self.check_of({}, plain=source)
        self.assertTrue(ok, output)
        self.assertIn("OBJECT RETURN COVERAGE OK", output)
        # The whole reason the reasons exist: a passing run still names the
        # site, so it stops being emitted visibly rather than silently.
        self.assertIn("declined, with the reason", output)
        self.assertIn("make", output)

    def test_an_emitted_site_the_artifact_does_not_carry_fails(self) -> None:
        """The two sides disagreeing.

        The recogniser types `movian/handle.open`; the artifact declares no
        object return for it. Only a check that reads both can see this.
        """
        source = ("exports.open = function() {\n"
                  "  return { close: function() { io.close(); } };\n"
                  "};\n")
        ok, output = self.check_of(
            {"js": {"modules": [
                {"name": "movian/handle",
                 "exports": [{"name": "open"}]}]}},
            handle=source)
        self.assertFalse(ok)
        self.assertIn("the artifact declares no object return", output)

        ok, output = self.check_of(
            {"js": {"modules": [
                {"name": "movian/handle",
                 "exports": [{"name": "open",
                              "returns": {"kind": "object", "fields": []}}]}]}},
            handle=source)
        self.assertTrue(ok, output)

    def test_a_decline_with_no_reason_is_a_failure(self) -> None:
        """The gate's own contract, forced.

        No real source produces a reasonless decline -- every refusal in
        `_anonymous_return_shape_verbose` carries text, and
        `ReturnedShape.test_every_refusal_says_why` pins that. So the only way
        to exercise this branch is to inject one, and it is worth exercising:
        without it a future refusal that forgot its reason would print an
        empty line and pass, putting the site straight back into the silence
        this census replaces.
        """
        source = ("exports.make = function() {\n"
                  "  return { n: 1 };\n"
                  "};\n")
        real = gen._anonymous_return_shape_verbose
        gen._anonymous_return_shape_verbose = lambda region: (None, "")
        try:
            ok, output = self.check_of({}, plain=source)
        finally:
            gen._anonymous_return_shape_verbose = real
        self.assertFalse(ok)
        self.assertIn("declined with no reason given", output)

    def test_an_object_returned_by_another_route_is_reported(self) -> None:
        """The blind spot a census defined by the recogniser's own regex
        cannot rule out by construction.

        `return {` is the population AND the recogniser's trigger, so a module
        that returns an object any other way is invisible to both and the gate
        would print OK over an intact silence. These are notes, not failures:
        the recogniser does not type them and this change does not start.
        """
        for label, source, fragment in [
            ("parenthesised",
             "exports.f = function() { return ({a: new Node(x)}); };\n",
             "parenthesised"),
            ("ternary",
             "exports.f = function(t) { return t ? {a: 1} : {b: 2}; };\n",
             "ternary branches"),
            ("bound to a local",
             "exports.f = function() { var o = {a: new Node(x)};"
             " return o; };\n",
             "bound to a local"),
        ]:
            with self.subTest(label):
                census = self.census_of(m=source)
                self.assertEqual([s["status"] for s in census], ["uncovered"])
                ok, output = self.check_of({}, m=source)
                self.assertTrue(ok, output)
                self.assertIn("not covered", output)
                self.assertIn(fragment, output)

    def test_a_literal_that_is_not_the_returned_value_is_not_reported(
            self) -> None:
        """Noise control, pinned.

        A first cut tested `"{" in statement` and flagged all three of these
        -- `new Proxy({msg: x}, h)` returns a Proxy, and a callback's body
        brace is not a literal at all. Every one of them is in the real corpus
        (`movian/xml.js:72,77`, `movian/prop.js:112`), so the gate would have
        shipped three false notes on day one, and notes that are noise are
        notes nobody reads.
        """
        for label, source in [
            ("an object literal as a call argument",
             "exports.f = function(x) { return new Proxy({msg: x}, h); };\n"),
            ("a callback body brace",
             "exports.f = function(p, c) { return np.subscribe(p,"
             " function(t, v) { if (t) return; }); };\n"),
            ("an ordinary constructed return",
             "exports.f = function() { var i = new Item(this);"
             " return i; };\n"),
        ]:
            with self.subTest(label):
                self.assertEqual(self.census_of(m=source), [])

    def test_asi_makes_a_cross_line_return_brace_a_block(self) -> None:
        """ES5.1 7.9.1, and the reason the two scans had to be unified first.

        `RETURN_OBJECT_RE`'s `\\s*` crosses a newline. The file scan used to
        search line by line, so `return\\n{` was found in the region, missed
        by the file scan, and the mismatch was skipped -- the site vanished
        from the census entirely, not even as unattributed. Unifying the two
        coordinate systems made it visible, and visible turned out to mean
        EMITTED: a wrong type, because ASI makes this `return;` and the braces
        a block. Both halves are pinned here, since fixing only the first is
        worse than leaving it alone.
        """
        source = ("exports.f = function() {\n"
                  "  return\n"
                  "{ a: new Node(x) };\n"
                  "};\n")
        census = self.census_of(m=source)
        self.assertEqual([s["status"] for s in census], ["declined"])
        self.assertIn("ASI", census[0]["reason"])
        ok, output = self.check_of({}, m=source)
        self.assertTrue(ok, output)

    def test_a_site_the_two_scans_disagree_about_is_a_failure(self) -> None:
        """The skipped-mismatch path, forced.

        Unreachable while both scans read the same masked text by the same
        rule -- which is exactly why it must not be a silent `continue` again.
        """
        source = "exports.f = function() { return {a: new Node(x)}; };\n"
        artifact = {"js": {"modules": [
            {"name": "movian/m", "exports": [
                {"name": "f",
                 "returns": {"kind": "object", "fields": []}}]}]}}
        real = gen.RETURN_OBJECT_RE
        try:
            ok, output = self.check_of(artifact, m=source)
            self.assertTrue(ok, output)
            # Make the file scan blind while the region scan still sees it.
            state = {"file_scan": True}

            class Blinded:
                """First `finditer` is the file scan; blind only that one."""

                @staticmethod
                def match(text):
                    return real.match(text)

                @staticmethod
                def finditer(text):
                    if state["file_scan"]:
                        state["file_scan"] = False
                        return iter(())
                    return real.finditer(text)

            gen.RETURN_OBJECT_RE = Blinded
            ok, output = self.check_of(artifact, m=source)
        finally:
            gen.RETURN_OBJECT_RE = real
        self.assertFalse(ok)
        self.assertIn("not by the file scan", output)

    def test_a_return_inside_a_comment_is_not_a_site(self) -> None:
        """Masking, pinned. A commented-out return is not code, and reporting
        it as unattributed would make the gate cry wolf on every module that
        documents a shape it used to return."""
        census = self.census_of(commented=(
            "// return { root: new Node(x) };\n"
            "/* return { root: new Node(x) }; */\n"
            "exports.use = function() { return 1; };\n"))
        self.assertEqual(census, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
