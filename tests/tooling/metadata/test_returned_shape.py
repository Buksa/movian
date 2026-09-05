#!/usr/bin/env python3
"""Behavior tests for `gen.py`'s return-type inference.

Why a unit test and not a fixture. The declaration fixtures observe the emitted
artifact, and this rule's whole point is that the artifact does NOT change:
every CommonJS member that carries a return type already ends its body with an
unconditional `return`, so tightening the rule costs nothing and shows nothing.
The only place the change is visible is at the rule itself.

That makes the fixtures the wrong gate here and this file the right one -- but
only while something runs it. It is wired into `.github/workflows/gates.yml`
beside `gen.py --check` for exactly that reason: a test nobody runs answers
every question with silence (movian#187).

What it pins is the pair, never one half:

* REFUSED -- a function that can fall out of its body has no single return
  type, no matter how well its explicit returns agree (movian#190). Drop the
  `_always_returns` guard and these go red.
* ANSWERED -- the shapes the corpus actually uses. Drop the inference entirely
  and these go red instead. A test that only pinned refusals would be
  satisfied by a rule that answers `None` to everything.
"""

from __future__ import annotations

import importlib.util
import sys
import collections
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
GEN_PY = REPO_ROOT / "support" / "devtools" / "metadata" / "gen.py"

_spec = importlib.util.spec_from_file_location("movian_metadata_gen", GEN_PY)
assert _spec is not None and _spec.loader is not None
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


# Each region is what the scanner sees: text starting at the `=` of an
# assignment, exactly as `_shape_method` and the export scan pass it.
ANSWERED = [
    ("the real movian/html selector",
     "= function(t){ return gumbo.findByTagName(this._gumboNode, t)"
     ".map(function(n){ return new Node(n); }); }",
     {"kind": "array", "element": "Node"}),
    ("filter().map() -- only the last map is read",
     "= function(t){ return xs.filter(function(n){ return n.ok; })"
     ".map(function(n){ return new Node(n); }); }",
     {"kind": "array", "element": "Node"}),
    ("construct-then-return, the movian/page form (#177)",
     "= function(u){ var item = new Item(this); this.items.push(item);"
     " return item; }",
     "Item"),
    ("a direct construction as the last statement",
     "= function(u){ return new Request(u); }",
     "Request"),
    ("a conditional BLOCK before an unconditional return",
     "= function(u){ var i = new Item(this); if(u){ i.x = 1; }"
     " prop.setParent(i, n); return i; }",
     "Item"),
    # movian/http's `request`: the callback form exits through a bare `return;`
    # inside `if(callback)`, and the synchronous form returns the shape. The
    # split is modelled separately as `voidWhen` (#178); what matters here is
    # that an early return in a branch does not by itself make the function
    # fall through, because the LAST statement is still an unconditional return.
    ("an early bare return in a branch, shape returned at the end",
     "= function(u,c,cb){ if(cb){ io.httpReq(u, c, function(e,r){}); return; }"
     " var res = io.httpReq(u,c); return new HttpResponse(res); }",
     "HttpResponse"),
    # The paren-skipping backward scan must not swallow an ordinary call that
    # happens to sit before the return: `foo(a, b);` ends in `)` too, and a
    # scan that stepped over it would keep walking into whatever precedes it.
    ("a plain call before the final return",
     "= function(u){ var i = new Item(this); foo(a, b); return i; }",
     "Item"),
]

REFUSED = [
    ("a map callback that can fall through",
     "= function(t){ return xs.map(function(n){ if (n) return new Node(n); }); }"),
    ("a conditional return with no braces",
     "= function(t){ if (t) return new Item(this); }"),
    ("a conditional return inside a block",
     "= function(t){ if (t) { return new Item(this); } }"),
    ("a return reached only through else",
     "= function(t){ if (t) { x(); } else return new Item(this); }"),
    ("a return inside a loop",
     "= function(t){ for(var i=0;i<n;i++){ return new Item(this); } }"),
    ("a return inside an unbraced loop -- the header carries its own `;`",
     "= function(t){ for (var i = 0; i < n; i++) return new Item(this); }"),
    ("the same for `while`",
     "= function(t){ while (n--) return new Item(this); }"),
    ("a bare `return;` as the final statement",
     "= function(t){ if (t) return new Item(this); return; }"),
    ("a property named `return`, which is not a return statement",
     "= function(t){ if (t) return new Item(this); iterator.return(); }"),
    ("a body that constructs but never returns",
     "= function(t){ var i = new Item(this); }"),
    # Sound, and refused anyway. The approximation errs toward `any`, which is
    # what every other rule here does when the evidence is not plain; nothing in
    # res/ecmascript/modules/** is written this way. Pinned so that a later
    # reader sees the cost was known and chosen, not overlooked.
    ("both branches returning the same shape -- sound, refused by design",
     "= function(t){ if (t) return new Item(this); else return new Item(this); }"),
]


# `_anonymous_return_shape` sees the text of one export region, exactly as the
# export scan passes it. Each case is the whole region so the brace walk that
# finds the direct `return {` has the same input it has in production.
OBJECT_ANSWERED = [
    ("the real movian/html form -- constructor fields, unchanged by #229",
     "= function(h){ var gdoc = gumbo.parse(h);"
     " return { document: new Node(gdoc.document),"
     " root: new Node(gdoc.root) }; }",
     {"kind": "object", "fields": [
         {"name": "document", "type": "Node"},
         {"name": "root", "type": "Node"}]}),
    ("the real movian/itemhook handle -- a function field that returns nothing",
     "= function(conf){ var node = prop.createRoot();"
     " return { destroy: function() { prop.destroy(node); } }; }",
     {"kind": "object", "fields": [
         {"name": "destroy", "kind": "function", "params": [],
          "returns": {"kind": "void"}}]}),
    ("a function field with parameters",
     "= function(){ return { seek: function(where, how) { io.seek(where); } }; }",
     {"kind": "object", "fields": [
         {"name": "seek", "kind": "function", "params": ["where", "how"],
          "returns": {"kind": "void"}}]}),
    ("every own return valueless still proves void",
     "= function(){ return { stop: function(x) { if (x) { return; } return; } }; }",
     {"kind": "object", "fields": [
         {"name": "stop", "kind": "function", "params": ["x"],
          "returns": {"kind": "void"}}]}),
    ("a nested callback's return is not the field's own",
     "= function(){ return { run: function() {"
     " xs.forEach(function(n){ return n.v; }); } }; }",
     {"kind": "object", "fields": [
         {"name": "run", "kind": "function", "params": [],
          "returns": {"kind": "void"}}]}),
    ("a function field whose own return has a provable shape",
     "= function(){ return { open: function(u) { return new Request(u); } }; }",
     {"kind": "object", "fields": [
         {"name": "open", "kind": "function", "params": ["u"],
          "returns": "Request"}]}),
    ("constructor and function fields together",
     "= function(){ return { root: new Node(x),"
     " destroy: function() { prop.destroy(x); } }; }",
     {"kind": "object", "fields": [
         {"name": "root", "type": "Node"},
         {"name": "destroy", "kind": "function", "params": [],
          "returns": {"kind": "void"}}]}),
]

# The all-or-nothing contract from movian#160: a value form still not
# understood declines the WHOLE shape rather than emitting a partial `any`.
# #229 recognises one more form; it does not relax the contract.
OBJECT_REFUSED = [
    ("a plain value field",
     "= function(){ return { n: 1 }; }"),
    ("a bare identifier field",
     "= function(){ return { node: node }; }"),
    ("a call-valued field",
     "= function(){ return { doc: gumbo.parse(h) }; }"),
    ("one unknown field poisons the known ones",
     "= function(){ return { root: new Node(x), n: 1 }; }"),
    ("a function field whose parameter list does not parse (ES5 has no defaults)",
     "= function(){ return { go: function(a = 1) { x(); } }; }"),
    ("a function field that returns a value with no provable shape",
     "= function(){ return { pick: function() { return xs[0]; } }; }"),
    ("a function field that can fall through -- undefined on the other path",
     "= function(){ return { pick: function(n) { if (n) return new Node(n); } }; }"),
    ("a nested object field is one field, and an unrecognised one",
     "= function(){ return { pos: { x: 1, y: 2 } }; }"),
    # Reachability, the guard movian#190 gave the scalar path and this one
    # never had. An unbraced `if` puts the return at depth 1, so the direct
    # filter accepts it and it is the body's only own return -- only
    # `_always_returns` sees that `!t` falls through to `undefined`.
    ("an unbraced conditional return -- undefined on the other path",
     "= function(t){ if (t) return { a: new Node(x) }; }"),
    ("a return-object inside a loop",
     "= function(t){ while (n--) return { a: new Node(x) }; }"),
    # Not the last statement: the body carries on and yields undefined.
    ("a return-object followed by more of the body",
     "= function(t){ if (t) { return { a: new Node(x) }; } cleanup(); }"),
    ("two direct return-objects: no single shape",
     "= function(t){ if (t) { return { a: new Node(x) }; }"
     " return { b: new Node(y) }; }"),
]


class AnonymousReturnShape(unittest.TestCase):
    """The object-literal return family (movian#229).

    Paired the same way as `ReturnedShape` above, and for the same reason. The
    ANSWERED half dies if the recogniser is gutted; the REFUSED half dies if it
    is loosened into `any`. Only one form was added -- a function-valued field
    -- because that is the one the corpus uses and cannot express today.
    """

    def test_answers_the_forms_the_corpus_uses(self) -> None:
        for label, region, expected in OBJECT_ANSWERED:
            with self.subTest(label):
                self.assertEqual(gen._anonymous_return_shape(region), expected)

    def test_declines_the_whole_shape_on_an_unknown_field(self) -> None:
        for label, region in OBJECT_REFUSED:
            with self.subTest(label):
                self.assertIsNone(gen._anonymous_return_shape(region))

    def test_every_refusal_says_why(self) -> None:
        """Totality of the reason path, pinned at the source.

        `_check_object_return_coverage` reports a declined site by printing
        the reason the recogniser gave, and treats a blank one as a failure.
        That treatment is only meaningful if a reason is genuinely always
        produced -- otherwise the gate's own guard is the thing keeping the
        silence, one level up.
        """
        for label, region in OBJECT_REFUSED:
            with self.subTest(label):
                shape, reason = gen._anonymous_return_shape_verbose(region)
                self.assertIsNone(shape)
                self.assertTrue(reason, "declined with no reason")

    def test_split_fields_does_not_split_inside_braces(self) -> None:
        """The hazard the issue named, pinned as a decision.

        `split_fields` tracked paren and bracket depth but not brace depth, so
        `{ pos: {x: 1, y: 2} }` split into `pos: {x: 1` and `y: 2}`. That was
        harmless only by luck -- both halves failed the `new Ctor(...)`
        fullmatch and the shape declined for the wrong reason. A function field
        body ends the luck: its `,` are inside braces too.
        """
        self.assertEqual(
            gen.split_fields("pos: {x: 1, y: 2}, root: new Node(n)"),
            ["pos: {x: 1, y: 2}", "root: new Node(n)"])
        self.assertEqual(
            gen.split_fields("f: function(a, b) { g(1, 2); }, n: 1"),
            ["f: function(a, b) { g(1, 2); }", "n: 1"])


class ReturnedShape(unittest.TestCase):
    def test_answers_the_shapes_the_corpus_uses(self) -> None:
        for label, region, expected in ANSWERED:
            with self.subTest(label):
                self.assertEqual(gen._returned_shape(region), expected)

    def test_refuses_a_body_that_can_fall_through(self) -> None:
        for label, region in REFUSED:
            with self.subTest(label):
                self.assertIsNone(gen._returned_shape(region))

    def test_every_typed_member_of_the_corpus_survives(self) -> None:
        """The measurement that made this change free, kept as a gate.

        Tightening a rule is supposed to cost nothing here. If a future edit to
        `res/ecmascript/modules/**` or to the rule drops one of these, that is a
        real loss of checking and it should be a decision, not a diff nobody
        reads.

        The two populations are counted apart because they are derived by
        different rules over different corpora, and a single total would let a
        loss on one side hide behind a gain on the other. `impl` is what tells
        them apart: only a native ES_MODULE export names the C function
        implementing it.

        Native return types were all 0 until movian#207 read them out of the C
        bodies; the 11 in this assertion used to be the whole population, which
        is why it was written as a bare total.
        """
        artifact = REPO_ROOT / "generated" / "movian-metadata.json"
        if not artifact.is_file():
            self.skipTest("generated/movian-metadata.json is absent")
        import json

        def typed(node: object):
            if isinstance(node, dict):
                if "returns" in node and "name" in node:
                    yield node
                for value in node.values():
                    yield from typed(value)
            elif isinstance(node, list):
                for value in node:
                    yield from typed(value)

        def population(member: dict) -> str:
            # A field of an object return shape carries `name` and `returns`
            # too, so the walker above finds it -- but it is not a member of
            # any module, it is part of one member's type. `source` is what
            # tells them apart: every real member records where it was
            # scanned from, a field records nothing (movian#229). Counting
            # them together would let a lost member hide behind a gained
            # field.
            if "source" not in member:
                return "returnField"
            return "native" if "impl" in member else "commonjs"

        members = sorted(
            (m["name"], m.get("source", {}).get("file", "?"), population(m))
            for m in typed(json.loads(artifact.read_text())))
        counted = collections.Counter(kind for _, _, kind in members)
        # 72 natives when the return scan read a body with one push and
        # refused everything else; 91 once it also resolves the class behind
        # `es_resource_push`/`es_push_native_obj` and reads a container filled
        # through `duk_put_prop_*`. 11 CommonJS until movian#229 taught the
        # object-return recogniser one more field form, which added
        # `movian/itemhook.create` and, with it, the first `returnField`.
        # All three are measurements, not targets -- if this fails,
        # re-measure before adjusting it.
        self.assertEqual(
            dict(counted),
            {"commonjs": 12, "native": 91, "returnField": 1},
            members)


if __name__ == "__main__":
    unittest.main(verbosity=2)
