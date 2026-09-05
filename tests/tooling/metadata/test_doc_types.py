#!/usr/bin/env python3
"""The JSDoc types the source already carries (#231).

`gen.py` did not contain the string `@param` anywhere, while
`res/ecmascript/modules/**` carried 96 typed `@param` and 16 `@returns` in
TypeScript syntax -- so 205 parameters and 112 returns were emitted as `any`
over annotations that were already written.

The risk this file exists to bound: an annotation is the AUTHOR'S ASSERTION,
not a proof. Emitting it turns a comment into a compile-time constraint on
every plugin, and a wrong one invents errors that plugins do not have. Two
things keep that honest and both are pinned here:

* nothing is emitted unless the emitted file itself can resolve every named
  part of it, per MODULE -- `interface Item` lives inside `declare module
  'movian/page'` and is not a name `movian/http` may write;
* evidence beats assertion -- a `@returns` never overrides a return the scan
  proved from the body.

The witness that an annotation is CORRECT is not here. It is the reference-dts
compile in `--check`, which type-checks 20 core modules and every plugin
example against the generated declarations. It fired twice while this was
written, and both times the annotation was the thing that was wrong.
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

MODULES = REPO_ROOT / "res" / "ecmascript" / "modules"


class BlockReader(unittest.TestCase):
    """Finding the block, and reading what it claims."""

    def test_reads_the_forms_the_corpus_uses(self) -> None:
        cases = [
            ("a one-line block", "movian/xml.js", 70, {"str": "string"}),
            ("a multi-tag block", "movian/prop.js", 114, {
                "prop": "PropHandle",
                "callback": "(...args: any[]) => void",
                "ctrl": "import('native/prop').SubscribeOptions|null"}),
            # The brace-balanced read. A flat `\{([^}]+)\}` stops at the first
            # `}` and loses the outer type entirely.
            ("an object type across two lines", "movian/itemhook.js", 7, {
                "conf": "{itemtype?: string, title?: string, icon?: string,"
                        " handler?: Function}"}),
        ]
        for label, module, line, expected in cases:
            with self.subTest(label):
                self.assertEqual(
                    gen._jsdoc_types(MODULES / module, line).get("params"),
                    expected)

    def test_a_block_that_is_not_immediately_above_is_not_read(self) -> None:
        """Attribution, not proximity.

        A blank line or any code between the block and the declaration breaks
        it. A doc block that floats loose belongs to nothing, and guessing
        which callable it meant is how a comment types the wrong function.
        """
        # movian/xml.js:70 is `exports.parse`; 71 is inside its body.
        self.assertEqual(gen._jsdoc_types(MODULES / "movian/xml.js", 72), {})

    def test_the_optional_marker_is_not_part_of_the_name(self) -> None:
        types = gen._jsdoc_types(MODULES / "movian/prop.js", 114)
        self.assertIn("ctrl", types["params"])
        self.assertNotIn("[ctrl]", types["params"])


class TypeResolution(unittest.TestCase):
    """What may be emitted, and what must fall back to `any`."""

    VISIBLE = {"Item", "Node", "PropHandle"}
    BY_MODULE = {"native/prop": {"SubscribeOptions"}}

    def problem(self, text: str, position: str = "parameter"):
        return gen.doc_type_problem(
            text, self.VISIBLE, self.BY_MODULE, position)

    def test_admits_what_the_file_can_resolve(self) -> None:
        for text in [
                "string", "boolean", "string|Object", "Node[]", "Item|null",
                "PropHandle", "(...args: any[]) => void",
                "import('native/prop').SubscribeOptions",
                "{itemtype?: string, handler?: Function}",
                "{a: string, b?: Item}",
                "*",
        ]:
            with self.subTest(text):
                self.assertIsNone(self.problem(text))

    def test_refuses_a_name_the_module_cannot_write(self) -> None:
        for text, fragment in [
                ("HttpResponse", "not declared in this module"),
                ("Page|null", "not declared in this module"),
                ("Wat[]", "Wat"),
                ("import('native/prop').Nope", "declares no Nope"),
                ("import('native/nothing').X", "declares no X"),
                # Declared, but somewhere else. Resolving against one flat set
                # would emit a name this block cannot write, and the emitted
                # file would not compile where it landed.
                ("SubscribeOptions", "not declared in this module"),
                ("SubscribeOptions[]", "SubscribeOptions"),
        ]:
            with self.subTest(text):
                problem = self.problem(text)
                self.assertIsNotNone(problem)
                self.assertIn(fragment, problem)

    def test_unknown_is_admissible_on_a_parameter_and_not_on_a_return(
            self) -> None:
        """Not symmetric, and that is the whole point.

        `unknown` accepts every argument, so a parameter typed with it breaks
        no caller -- and `http.get` documents its callback that way on
        purpose: "accepted and NEVER invoked anywhere in this module". It
        accepts no USE, so on a return it breaks every caller `any` allowed.
        """
        self.assertIsNone(self.problem("unknown", "parameter"))
        problem = self.problem("unknown", "return")
        self.assertIsNotNone(problem)
        self.assertIn("breaks every caller", problem)

    def test_closure_star_becomes_any(self) -> None:
        self.assertEqual(gen.render_doc_type("*"), "any")
        self.assertEqual(gen.render_doc_type("string"), "string")

    def test_a_property_name_is_not_a_type_reference(self) -> None:
        """The `?:` half of this cost both object-literal annotations.

        `{itemtype?: string}` writes the optional marker between the name and
        the colon, so a skip rule that only looked for `:` read `itemtype` as
        a type reference and declared the whole annotation unresolvable.
        """
        self.assertIsNone(self.problem("{itemtype?: string, title?: string}"))
        self.assertIsNone(self.problem("(a?: string, b?: Item) => void"))


class NameMatching(unittest.TestCase):
    def record(self, params, line, module="movian/xml.js"):
        record = {"name": "x", "params": params,
                  "source": {"file": "res/ecmascript/modules/" + module,
                             "line": line}}
        gen._attach_doc_types(record, MODULES / module)
        return record

    def test_a_matching_name_is_recorded(self) -> None:
        self.assertEqual(self.record(["str"], 70).get("docParams"),
                         {"str": "string"})

    def test_a_name_that_is_not_a_parameter_is_never_emitted(self) -> None:
        """The author describing a signature that no longer exists.

        The corpus has 0 of these; the point is the day it has one. Emitting
        it would type a parameter the annotation never meant.
        """
        record = self.record(["renamed"], 70)
        self.assertNotIn("docParams", record)
        self.assertEqual(record.get("docParamsUnmatched"), ["str"])

    def test_a_file_outside_the_commonjs_corpus_is_never_read(self) -> None:
        """A `/**` above a `duk_function_list_entry` documents the C
        function, not the JavaScript signature.

        Written against a synthetic file on purpose. No `src/**.c` in the tree
        carries `@param` today, so pointing this at a real one would pass
        whether the guard exists or not -- a test the corpus makes vacuous.
        """
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "es_thing.c"
            outside.write_text(
                "/**\n * @param {string} a the C function's own argument\n */\n"
                "static int es_thing(duk_context *ctx) { return 0; }\n",
                encoding="utf-8")
            record = {"name": "x", "params": ["a"],
                      "source": {"file": "src/ecmascript/es_thing.c",
                                 "line": 4}}
            gen._attach_doc_types(record, outside)
        self.assertNotIn("docParams", record)
        self.assertNotIn("docParamsUnmatched", record)

    def test_the_same_block_inside_the_corpus_is_read(self) -> None:
        """The other half: without it, the guard could reject everything and
        the test above would still pass."""
        target = MODULES / "movian" / "_probe_tmp.js"
        target.write_text(
            "/**\n * @param {string} a\n */\n"
            "exports.f = function(a) {};\n", encoding="utf-8")
        try:
            record = {"name": "f", "params": ["a"],
                      "source": {"file": "res/ecmascript/modules/movian/"
                                         "_probe_tmp.js", "line": 4}}
            gen._attach_doc_types(record, target)
        finally:
            target.unlink()
            gen._RAW_LINES_CACHE.pop(target, None)
        self.assertEqual(record.get("docParams"), {"a": "string"})


class Census(unittest.TestCase):
    def artifact(self):
        path = REPO_ROOT / "generated" / "movian-metadata.json"
        if not path.is_file():
            self.skipTest("generated/movian-metadata.json is absent")
        return json.loads(path.read_text())

    def test_every_any_carries_a_reason(self) -> None:
        for site in gen._doc_type_census(self.artifact()):
            if site["status"] == "any":
                with self.subTest("%s.%s" % (site["member"], site["slot"])):
                    self.assertTrue(site.get("reason"))

    def test_the_committed_artifact_passes_and_prints_counts(self) -> None:
        ok, output = gen._check_doc_type_coverage(self.artifact())
        self.assertTrue(ok, output)
        self.assertIn("DOC TYPE COVERAGE OK", output)
        self.assertIn("why the remaining `any`", output)

    def test_the_counts_are_what_the_dts_shows(self) -> None:
        """A measurement, not a target -- but a census that drifts from the
        emitted file is describing nothing."""
        census = gen._doc_type_census(self.artifact())
        typed = sum(1 for s in census
                    if s["kind"] == "parameter" and s["status"] == "typed")
        self.assertEqual(typed, 96)

    def test_an_any_with_no_reason_fails(self) -> None:
        """Forced by injection. The reason path is total by construction,
        which is exactly why the guard must not be a silent pass."""
        real = gen._doc_type_census
        gen._doc_type_census = lambda artifact: [
            {"module": "m", "member": "f", "slot": "a", "kind": "parameter",
             "status": "any"}]
        try:
            ok, output = gen._check_doc_type_coverage({})
        finally:
            gen._doc_type_census = real
        self.assertFalse(ok)
        self.assertIn("`any` with no reason given", output)


class Rendering(unittest.TestCase):
    """What the emitted file actually says.

    The census can be right about every count while the renderer emits none
    of it -- the two are different code paths, and only this one is what a
    plugin author compiles against.
    """

    def render(self, *, doc_params=None, doc_returns=None, returns=None,
               shapes=("Item", "Node")):
        export = {"name": "f", "params": ["a"], "nargs": 1,
                  "source": {"file": "res/ecmascript/modules/movian/m.js",
                             "line": 1}}
        if doc_params is not None:
            export["docParams"] = doc_params
        if doc_returns is not None:
            export["docReturns"] = doc_returns
        if returns is not None:
            export["returns"] = returns
        return gen.render_dts({"js": {"modules": [{
            "name": "movian/m", "kind": "commonjs", "exports": [export],
            "shapes": [{"name": name, "kind": "prototype", "methods": []}
                       for name in shapes]}]}})

    def test_a_documented_parameter_reaches_the_signature(self) -> None:
        self.assertIn("f(a?: string)",
                      self.render(doc_params={"a": "string"}))

    def test_an_unresolvable_parameter_type_falls_back_to_any(self) -> None:
        self.assertIn("f(a?: any)",
                      self.render(doc_params={"a": "HttpResponse"}))

    def test_closure_star_reaches_the_signature_as_any(self) -> None:
        self.assertIn("f(a?: any)", self.render(doc_params={"a": "*"}))

    def test_a_documented_return_is_emitted_when_nothing_was_proved(
            self) -> None:
        self.assertIn("): Item;", self.render(doc_returns="Item"))

    def test_a_proved_return_wins_over_the_annotation(self) -> None:
        """A comment must not overrule a reading of the code, or the
        annotations become a second, unchecked type system. Rendered, not
        just counted: the census reporting the disagreement is worth nothing
        if the emitted file took the annotation anyway.
        """
        rendered = self.render(returns="Item", doc_returns="Node")
        self.assertIn("): Item;", rendered)
        self.assertNotIn("): Node;", rendered)

    def test_unknown_on_a_return_falls_back_to_any(self) -> None:
        self.assertIn("): any;", self.render(doc_returns="unknown"))
        self.assertIn("f(a?: unknown)",
                      self.render(doc_params={"a": "unknown"}))


class EvidenceBeatsAssertion(unittest.TestCase):

    def test_a_disagreement_is_reported_and_the_proof_wins(self) -> None:
        artifact = {"js": {"modules": [{
            "name": "movian/m", "kind": "commonjs",
            "exports": [{"name": "f", "params": [],
                         "returns": "Item", "docReturns": "Node"}],
            "shapes": [{"name": "Item", "methods": []},
                       {"name": "Node", "methods": []}]}]}}
        sites = [s for s in gen._doc_type_census(artifact)
                 if s.get("disagreement")]
        self.assertEqual(len(sites), 1)
        self.assertIn("the proof wins", sites[0]["disagreement"])
        self.assertEqual(sites[0]["type"], "proved from the source")

    def test_agreement_is_not_reported(self) -> None:
        """The noise cut. Nine sites annotate exactly what the scan proved,
        and printing those as findings buries the one that differs."""
        artifact = {"js": {"modules": [{
            "name": "movian/m", "kind": "commonjs",
            "exports": [{"name": "f", "params": [],
                         "returns": "Item", "docReturns": "Item"}],
            "shapes": [{"name": "Item", "methods": []}]}]}}
        self.assertEqual(
            [s for s in gen._doc_type_census(artifact)
             if s.get("disagreement")], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
