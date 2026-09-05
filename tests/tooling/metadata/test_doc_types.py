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


class ReaderLexing(unittest.TestCase):
    """The reader is a lexer, and every shortcut in it was a real misreading.

    All five cases below came out of the cross-vendor review round, and every
    one produced either a wrong type in the emitted file or an annotation
    attributed to the wrong function.
    """

    def probe(self, source: str, line: int):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.js"
            path.write_text(source, encoding="utf-8")
            try:
                return gen._jsdoc_types(path, line)
            finally:
                gen._RAW_LINES_CACHE.pop(path, None)

    def test_a_brace_inside_a_string_type_is_not_a_delimiter(self) -> None:
        """`@returns {"}"}` read as `{"`, and the emitted declaration was an
        unterminated string literal -- tsc TS1002."""
        self.assertEqual(gen._braced('{"}"} rest', 0), ('"}"', 5))
        self.assertEqual(
            self.probe('/** @returns {"}"} */\nexports.f = function() {};\n',
                       2).get("returns"),
            '"}"')

    def test_whitespace_inside_a_string_type_is_preserved(self) -> None:
        """Layout whitespace is collapsed so a multi-line object type reads;
        whitespace INSIDE a literal is part of the value, and collapsing it
        made the signature reject the call the annotation described."""
        self.assertEqual(
            self.probe('/** @param {"true  false"} x */\n'
                       'exports.f = function(x) {};\n', 2)["params"]["x"],
            '"true  false"')
        self.assertEqual(
            self.probe('/**\n * @param {{a?: string,\n'
                       ' *          b?: Item}} c\n */\n'
                       'exports.f = function(c) {};\n', 5)["params"]["c"],
            "{a?: string, b?: Item}")

    def test_a_trailing_comment_does_not_transfer_the_block(self) -> None:
        """Attribution needs comment lexical state, not proximity.

        The walk started at the ORDINARY comment's `*/`, crossed executable
        code, and reached the block belonging to `first` -- typing `f` with
        an annotation written for a different function.
        """
        self.assertEqual(
            self.probe('/** @param {string} x */\n'
                       'exports.first = function(x) {}; /* ordinary */\n'
                       'exports.f = function(x) {};\n', 3), {})

    def test_a_fake_opener_inside_an_ordinary_comment_is_not_a_block(
            self) -> None:
        self.assertEqual(
            self.probe('/* ordinary comment\n'
                       ' /** @param {string} x\n'
                       '*/\n'
                       'exports.f = function(x) {};\n', 4), {})

    def test_a_dotted_param_documents_a_property_not_the_parameter(
            self) -> None:
        """`@param {string} options.name` matched the `options` prefix and
        overwrote the parameter's own type, rendering `options?: string` and
        rejecting every real call."""
        self.assertEqual(
            self.probe('/**\n * @param {{name: string}} options\n'
                       ' * @param {string} options.name\n */\n'
                       'exports.f = function(options) {};\n',
                       5)["params"],
            {"options": "{name: string}"})

    def test_a_multiline_ordinary_comment_does_not_transfer_the_block(
            self) -> None:
        """The second half of the walk guard.

        A trailing `/* ... */` on the same line as code is caught by the
        closing-line rule. An ordinary comment whose `*/` sits on its own line
        passes that rule and is only stopped by the walk refusing to cross a
        second comment's boundary.
        """
        self.assertEqual(
            self.probe('/** @param {string} x */\n'
                       'exports.first = function(x) {};\n'
                       '/* an ordinary note\n'
                       '   spanning lines\n'
                       '*/\n'
                       'exports.f = function(x) {};\n', 6), {})

    def test_a_reachable_comment_boundary_stops_the_walk(self) -> None:
        """This guard was deleted once as unreachable, on the evidence that
        no mutation could kill it. That was a fact about the mutation set.

        The input is valid JavaScript, the closing line is ` */` alone, and
        the walk reaches an ordinary comment's opener with executable code
        already behind it.
        """
        self.assertEqual(
            self.probe('/** @param {string} x\n'
                       ' * */ var y = 1; /* ordinary\n'
                       ' */\n'
                       'exports.f = function(x) {};\n', 4), {})

    def test_a_continuation_without_a_leading_star_still_reads(self) -> None:
        """A line between `/**` and `*/` is comment text by definition.

        Requiring the conventional ` * ` refused valid JSDoc for no safety:
        the reachable bad case is stopped by the comment-boundary check, and
        that was verified by running the counterexample with this rule
        removed -- not inferred from a mutation nothing killed.
        """
        self.assertEqual(
            self.probe('/**\n@param {string} x\n*/\n'
                       'exports.f = function(x) {};\n', 4),
            {"params": {"x": "string"}})

    def test_the_ordinary_forms_still_read(self) -> None:
        """The other half. A reader tightened until it reads nothing passes
        every refusal test above."""
        self.assertEqual(
            self.probe('/** @param {string} s */\n'
                       'exports.f = function(s) {};\n', 2),
            {"params": {"s": "string"}})
        self.assertEqual(
            self.probe('/**\n * @param {string} a\n * @returns {Item}\n'
                       ' */\nexports.f = function(a) {};\n', 5),
            {"params": {"a": "string"}, "returns": "Item"})


class Aliases(unittest.TestCase):
    """A prototype alias copies the target's record.

    Inheriting the annotations is right -- the function is identical. Claiming
    the target's words came from the ALIAS's source line is not, and the
    alias's own block was never read at all.
    """

    def scan(self, source: str):
        path = MODULES / "movian" / "_alias_probe.js"
        path.write_text(source, encoding="utf-8")
        try:
            return {method["name"]: method
                    for shape in gen.scan_commonjs_shapes(path)
                    for method in shape["methods"]}
        finally:
            path.unlink()
            gen._RAW_LINES_CACHE.pop(path, None)

    SOURCE = ('/**\n * @param {string} x\n * @returns {string}\n */\n'
              'C.prototype.original = function(x) { return x; };\n'
              '/**\n * @param {Item} x\n * @returns {Item}\n */\n'
              'C.prototype.alias = C.prototype.original;\n'
              'C.prototype.bare = C.prototype.original;\n')

    def test_an_alias_reads_its_own_block(self) -> None:
        methods = self.scan(self.SOURCE)
        self.assertEqual(methods["alias"]["docParams"], {"x": "Item"})
        self.assertEqual(methods["alias"]["docReturns"], "Item")
        self.assertNotIn("docFrom", methods["alias"])

    def test_an_undocumented_alias_inherits_and_says_so(self) -> None:
        methods = self.scan(self.SOURCE)
        self.assertEqual(methods["bare"]["docParams"], {"x": "string"})
        self.assertEqual(methods["bare"]["docFrom"], "original")

    def test_an_alias_of_an_alias_does_not_keep_stale_provenance(self) -> None:
        """The record is COPIED, so `docFrom` came along with it. The
        annotation was correctly replaced and its provenance still named the
        original method."""
        methods = self.scan(
            '/**\n * @param {string} x\n */\n'
            'C.prototype.original = function(x) { return x; };\n'
            'C.prototype.bare = C.prototype.original;\n'
            '/**\n * @param {Item} x\n */\n'
            'C.prototype.own = C.prototype.bare;\n')
        self.assertEqual(methods["own"]["docParams"], {"x": "Item"})
        self.assertNotIn("docFrom", methods["own"])


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
                ("HttpResponse", "not a name this module declares"),
                ("Page|null", "not a name this module declares"),
                ("Wat[]", "Wat"),
                ("import('native/prop').Nope", "declares no Nope"),
                ("import('native/nothing').X", "declares no X"),
                # Declared, but somewhere else. Resolving against one flat set
                # would emit a name this block cannot write, and the emitted
                # file would not compile where it landed.
                ("SubscribeOptions", "not a name this module declares"),
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

    def test_a_qualified_name_is_refused_outright(self) -> None:
        """The old rule skipped an identifier "preceded by a dot".

        `[...Missing]` is a rest element, not a qualification, so `Missing`
        was skipped and emitted -- TS2304 in a file that is supposed to
        compile. `Item.Missing` was accepted too, and an interface is not a
        namespace: TS2702. Only `import('module').Member` is a dotted form
        this resolver can follow.
        """
        problem = self.problem("[...Missing]")
        self.assertIsNotNone(problem)
        self.assertIn("Missing", problem)
        problem = self.problem("Item.Missing")
        self.assertIsNotNone(problem)
        self.assertIn("qualified name", problem)

    def test_unknown_nested_in_a_type_is_refused_on_a_parameter(self) -> None:
        """The asymmetry does not survive nesting.

        `(value: unknown) => void` puts `unknown` in a CONTRAVARIANT position:
        the emitted callback must accept unknown, so
        `f((value: string) => ...)` stops compiling although it compiled
        against `any`. Bare `unknown` at the top level is still fine, and that
        is what the corpus actually writes.
        """
        problem = self.problem("(value: unknown) => void")
        self.assertIsNotNone(problem)
        self.assertIn("contravariant", problem)
        self.assertIsNone(self.problem("unknown"))

    def test_forms_the_resolver_refuses_rather_than_half_reads(self) -> None:
        """Second review round. Each of these passed and emitted a
        declaration that does not compile.

        A refusal costs coverage; a half-parse costs correctness, and the
        corpus writes none of these forms.
        """
        for text, fragment in [
            # One quote-state variable cannot represent a nested
            # interpolation: `` `outer${`}`}tail` `` truncated to `` `outer${``
            # and emitted an unterminated template (TS1160).
            ("`x${Missing}`", "template literal"),
            ("`outer${`}`}tail`", "template literal"),
            # `Missing` is followed by ` :`, so the property-name skip took
            # it and an undeclared name was emitted (TS2304).
            ("true extends true ? Missing : string", "`extends`"),
            ("{[K in T]: string}", "`in`"),
            ("typeof Item", "`typeof`"),
        ]:
            with self.subTest(text):
                problem = self.problem(text)
                self.assertIsNotNone(problem)
                self.assertIn(fragment, problem)

    def test_this_is_refused_because_its_legality_depends_on_the_site(
            self) -> None:
        """The same record is emitted twice -- hoisted as
        `function f(): this;` (TS2526) and inside the interface, where it is
        legal. A type whose validity depends on the emission site cannot be
        judged by a function that does not know the site."""
        for position in ("parameter", "return"):
            with self.subTest(position):
                problem = self.problem("this", position)
                self.assertIsNotNone(problem)
                self.assertIn("class or interface", problem)

    def test_a_dot_inside_a_string_is_not_a_qualified_name(self) -> None:
        """The qualified-name check read raw text, so `"a.b"` -- a
        string-literal type containing no reference -- was refused, and
        `"string.number"` went from accepted to refused."""
        self.assertIsNone(self.problem('"a.b"'))
        self.assertIsNone(self.problem('"string.number"'))
        self.assertIsNotNone(self.problem("Item.Missing"))

    def test_forms_the_resolver_reads_correctly(self) -> None:
        """Lexical bugs, each of which refused a valid annotation.

        A name inside a string is not a type reference, a method shorthand key
        is not a type reference, and a type operator is a keyword.
        """
        for text in ['{run(): void}', '{"property name": string}',
                     '"key: value"', 'keyof {a: string}']:
            with self.subTest(text):
                self.assertIsNone(self.problem(text))

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
        # 96 while `*` was counted as typed and the rest-parameter slots were
        # not counted at all -- a number that described the annotations, not
        # the emitted file. Both were corrected in the review round.
        self.assertEqual(typed, 90)  # noqa: kept, see comment above

    def test_a_closure_star_is_counted_as_any_not_typed(self) -> None:
        """It renders `any`. Counting it typed described the annotation and
        not the file, and excluded six real `any` slots from the reasons."""
        artifact = {"js": {"modules": [{
            "name": "movian/m", "kind": "commonjs", "shapes": [],
            "exports": [{"name": "f", "params": ["a"],
                         "docParams": {"a": "*"},
                         "source": {"file": "res/ecmascript/modules/"
                                            "movian/m.js", "line": 1}}]}]}}
        slot = [s for s in gen._doc_type_census(artifact)
                if s["slot"] == "a"][0]
        self.assertEqual(slot["status"], "any")
        self.assertIn("Closure's any", slot["reason"])

    def test_the_rest_parameter_slot_is_counted(self) -> None:
        """`params_signature(None)` emits `...args: any[]` -- a real `any` in
        the declaration that the census skipped, because it iterated a list
        that was None."""
        for record, fragment in [
            ({"name": "f", "params": None}, "did not parse"),
            ({"name": "f", "params": ["a"], "variadic": True}, "`arguments`"),
        ]:
            with self.subTest(fragment):
                record["source"] = {"file": "res/ecmascript/modules/"
                                            "movian/m.js", "line": 1}
                sites = gen._doc_type_census({"js": {"modules": [{
                    "name": "movian/m", "kind": "commonjs", "shapes": [],
                    "exports": [record]}]}})
                rest = [s for s in sites if s["slot"] == "...args"]
                self.assertEqual(len(rest), 1)
                self.assertIn(fragment, rest[0]["reason"])

    def test_a_value_member_gets_no_return_slot(self) -> None:
        """The emitter writes `name: any;`, not a call signature. Inventing a
        return for it added four results that do not exist."""
        sites = gen._doc_type_census({"js": {"modules": [{
            "name": "movian/m", "kind": "commonjs", "shapes": [],
            "exports": [{"name": "init", "params": [], "nargs": 0,
                         "source": {"file": "res/ecmascript/modules/"
                                            "movian/m.js", "line": 1},
                         "receiverMembers": [
                             {"name": "v", "kind": "value",
                              "source": {"file": "res/ecmascript/modules/"
                                                 "movian/m.js", "line": 2}}]}]
        }]}})
        self.assertEqual(
            [s for s in sites if s["member"] == "init.v"], [])

    def test_a_mismatched_name_survives_into_the_report(self) -> None:
        """The requirement is to print the name claimed and the name that
        exists. Folding it into "no annotation" erased both -- the one reason
        in the list that names somebody's mistake was the one the summary
        threw away."""
        artifact = {"js": {"modules": [{
            "name": "movian/m", "kind": "commonjs", "shapes": [],
            "exports": [{"name": "f", "params": ["x"],
                         "docParamsUnmatched": ["oldName"],
                         "source": {"file": "res/ecmascript/modules/"
                                            "movian/m.js", "line": 1}}]}]}}
        ok, output = gen._check_doc_type_coverage(artifact)
        self.assertTrue(ok, output)
        self.assertIn("oldName", output)
        self.assertIn("x", output)

    def test_a_structural_proof_can_disagree_too(self) -> None:
        """`isinstance(proved, str)` excluded every object, array and void
        proof from the comparison."""
        def sites(proved, claimed):
            return [s for s in gen._doc_type_census({"js": {"modules": [{
                "name": "movian/m", "kind": "commonjs", "shapes": [
                    {"name": "Node", "kind": "prototype", "methods": []}],
                "exports": [{"name": "f", "params": [], "returns": proved,
                             "docReturns": claimed,
                             "source": {"file": "res/ecmascript/modules/"
                                                "movian/m.js", "line": 1}}]}]}})
                    if s.get("disagreement")]

        self.assertEqual(len(sites({"kind": "object", "fields": []},
                                   "string")), 1)
        # And the noise cut: `Node[]` and {"kind":"array","element":"Node"}
        # are the same claim in two notations. Comparing raw forms reported
        # four agreements in the real corpus as contradictions.
        self.assertEqual(
            sites({"kind": "array", "element": "Node"}, "Node[]"), [])
        # And an OBJECT shape against the annotation that describes it
        # exactly. `{document: Node, root: Node}` and
        # `{ document: Node; root: Node; }` are the same type; the emitter
        # writes one and the comparison text the other, so a raw comparison
        # called both real object returns in the corpus contradictions.
        self.assertEqual(
            sites({"kind": "object", "fields": [
                {"name": "document", "type": "Node"},
                {"name": "root", "type": "Node"}]},
                "{ document: Node; root: Node; }"), [])
        self.assertEqual(
            sites({"kind": "object", "fields": [
                {"name": "destroy", "kind": "function", "params": [],
                 "returns": {"kind": "void"}}]},
                "{ destroy: () => void; }"), [])

    def test_the_report_names_the_population_it_counts(self) -> None:
        """A number nobody can check is not a measurement.

        The census counts declaration RECORDS. The emitted file writes a
        receiver member once as a hoisted `function` and again inside every
        interface it belongs to -- `movian/settings.getvalue` is three lines
        and one record -- so counting lines of the `.d.ts` and expecting the
        census to match is wrong in a way the old wording invited.
        """
        artifact = self.artifact()
        ok, output = gen._check_doc_type_coverage(artifact)
        self.assertTrue(ok, output)
        self.assertIn("%d CommonJS declarations"
                      % len(gen._commonjs_callables(artifact)), output)

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

    def test_a_method_parameter_reaches_the_signature(self) -> None:
        """The defect the review found first, and the worst of them.

        `member_signature` was shared by every emission site, but every method
        site called it without the record, so `params_signature` had nothing
        to read `docParams` off -- 42 documented method parameters emitted
        `any` while the census counted them typed. Being one function did not
        make the ARGUMENT the same.
        """
        rendered = gen.render_dts({"js": {"modules": [{
            "name": "movian/m", "kind": "commonjs", "exports": [],
            "shapes": [{"name": "Thing", "kind": "prototype", "methods": [
                {"name": "go", "params": ["url"], "nargs": 1,
                 "docParams": {"url": "string"},
                 "source": {"file": "res/ecmascript/modules/movian/m.js",
                            "line": 1}}]}]}]}})
        self.assertIn("go(url?: string)", rendered)

    def test_a_receiver_member_parameter_reaches_the_signature(self) -> None:
        """The same defect as the method sites, in the two receiver
        emitters."""
        member = {"name": "send", "kind": "function", "params": ["body"],
                  "nargs": 1, "docParams": {"body": "string"},
                  "source": {"file": "res/ecmascript/modules/movian/m.js",
                             "line": 2}}
        rendered = gen.render_dts({"js": {"modules": [{
            "name": "movian/m", "kind": "commonjs", "shapes": [],
            "receiverMembers": [member],
            "exports": [{
                "name": "init", "params": [], "nargs": 0,
                "receiverMutation": True,
                "source": {"file": "res/ecmascript/modules/movian/m.js",
                           "line": 1},
                "receiverMembers": [member]}]}]}})
        # The hoisted module-level declaration. The export's own interface is
        # only emitted alongside shared shapes, which this artifact has none
        # of; `test_a_method_return_goes_through_the_shared_rule` covers that
        # emitter.
        self.assertIn("send(body?: string)", rendered)

    def test_a_method_return_goes_through_the_shared_rule(self) -> None:
        """Three emitters wrote `: any;` outright, so a shared method could
        be `any` as a hoisted module member and `string` inside its own
        interface -- the same method, two answers."""
        rendered = gen.render_dts({"js": {"modules": [{
            "name": "movian/m", "kind": "commonjs",
            "exports": [{"name": "init", "params": [], "nargs": 0,
                         "receiverMutation": True,
                         "source": {"file": "res/ecmascript/modules/"
                                            "movian/m.js", "line": 1}}],
            "shapes": [{"name": "sp", "kind": "shared", "methods": [
                {"name": "f", "params": [], "nargs": 0,
                 "docReturns": "string",
                 "source": {"file": "res/ecmascript/modules/movian/m.js",
                            "line": 1}}]}]}]}})
        self.assertNotIn("function f(): any;", rendered)
        self.assertIn("function f(): string;", rendered)

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
