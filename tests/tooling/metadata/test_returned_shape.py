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

        members = sorted(
            (m["name"], m.get("source", {}).get("file", "?"),
             "native" if "impl" in m else "commonjs")
            for m in typed(json.loads(artifact.read_text())))
        counted = collections.Counter(kind for _, _, kind in members)
        # 72 natives when the return scan read a body with one push and
        # refused everything else; 91 once it also resolves the class behind
        # `es_resource_push`/`es_push_native_obj` and reads a container filled
        # through `duk_put_prop_*`. Both numbers are measurements, not targets
        # -- if this fails, re-measure before adjusting it.
        self.assertEqual(dict(counted), {"commonjs": 11, "native": 91},
                         members)


if __name__ == "__main__":
    unittest.main(verbosity=2)
