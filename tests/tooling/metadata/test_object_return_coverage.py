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
