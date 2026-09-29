#!/usr/bin/env python3
"""A local object built by `Object.defineProperties` is a named shape
(ADR-0006, movian#260).

`movian/settings`' `createSetting` builds `var item = {}`, attaches its whole
public surface with `Object.defineProperties(item, {...})` and returns it, and
`sp.createBool`, `createString`, `createInt` and `createAction` hand that object
to the plugin. The generator read no such target, printed a warning on every
run, and declared the four results `any`.

The scan half is pinned on synthetic modules, because every refusal below is a
form the corpus does not contain today and could tomorrow. The corpus half is
pinned on the real tree.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
GEN_PY = REPO_ROOT / "support" / "devtools" / "metadata" / "gen.py"

_spec = importlib.util.spec_from_file_location("movian_metadata_gen", GEN_PY)
assert _spec is not None and _spec.loader is not None
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

MODULES = REPO_ROOT / "res" / "ecmascript" / "modules"
PROBE = MODULES / "movian" / "_local_shape_probe.js"


def scan(source: str) -> tuple[list[dict], str]:
    """`(shapes, stderr)` of `source` scanned as a core module."""
    PROBE.write_text(source, encoding="utf-8")
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr):
            shapes = gen.scan_commonjs_shapes(PROBE)
    finally:
        PROBE.unlink()
        gen._RAW_LINES_CACHE.pop(PROBE, None)
    return shapes, stderr.getvalue()


def by_name(shapes: list[dict]) -> dict[str, dict]:
    return {shape["name"]: shape for shape in shapes}


# The corpus form, cut down: `settings.js:5-42` and `:71-91`.
FACTORY = (
    "var sp = {};\n"
    "function createSetting(group, id) {\n"
    "  var model = group.nodes[id];\n"
    "  var item = {};\n"
    "  Object.defineProperties(item, {\n"
    "    model: {\n"
    "      value: model\n"
    "    },\n"
    "    value: {\n"
    "      get: function() { return model.value; },\n"
    "      set: function(v) { model.value = v; }\n"
    "    }\n"
    "  });\n"
    "  return item;\n"
    "}\n"
    "function S() { this.__proto__ = sp; }\n"
    "sp.createBool = function(id) {\n"
    "  var item = createSetting(this, id);\n"
    "  item.model.value = true;\n"
    "  return item;\n"
    "}\n"
)


class TheScanReadsALocalTarget(unittest.TestCase):
    def test_the_defined_members_become_a_shape_named_after_the_local(
            self) -> None:
        shapes, _ = scan(FACTORY)
        item = by_name(shapes).get("item")
        self.assertIsNotNone(item, shapes)
        self.assertEqual(item["kind"], "local")
        self.assertEqual(item["factory"], "createSetting")
        self.assertEqual(
            [(prop["name"], prop["kind"], prop["source"]["line"])
             for prop in item["properties"]],
            [("model", "value", 6), ("value", "accessor", 9)])
        self.assertEqual(item["methods"], [])

    def test_the_factory_is_recorded_by_its_own_name(self) -> None:
        shapes, _ = scan(FACTORY.replace("createSetting", "makeItem"))
        self.assertEqual(by_name(shapes)["item"]["factory"], "makeItem")
        (method,) = by_name(shapes)["sp"]["methods"]
        self.assertEqual(method.get("returns"), "item")

    def test_a_declined_namesake_does_not_decline_the_other(self) -> None:
        """Only locals the rule accepts compete for the name; one another
        function builds and the rule declines is no declaration at all."""
        shapes, stderr = scan(
            FACTORY + "function createOther() {\n"
            "  var item = {};\n"
            "  Object.defineProperties(item, { other: { value: 1 } });\n"
            "  item.extra = 1;\n"
            "  return item;\n"
            "}\n")
        self.assertEqual(by_name(shapes)["item"]["factory"], "createSetting")
        self.assertIn("target item: " + USED, stderr)

    def test_a_named_function_expression_is_not_a_factory(self) -> None:
        """`exports.make = function createSetting(...) {...}` binds the name
        only inside itself, so `createSetting(...)` elsewhere in the module
        is not a call of it."""
        shapes, stderr = scan(FACTORY.replace(
            "function createSetting(group, id) {",
            "exports.make = function createSetting(group, id) {"))
        self.assertNotIn("item", by_name(shapes))
        (method,) = by_name(shapes)["sp"]["methods"]
        self.assertIsNone(method.get("returns"))
        # No reader takes it, so the census does, at the call's line.
        self.assertIn(
            ":5: warning: ignored unsupported Object.defineProperties call",
            stderr)

    def test_the_read_call_is_not_reported_as_unsupported(self) -> None:
        _, stderr = scan(FACTORY)
        self.assertNotIn("Object.defineProperties", stderr)

    def test_a_method_returning_the_factory_result_returns_the_shape(
            self) -> None:
        """`var item = createSetting(...); return item;` is how every
        `sp.create*` hands the object back (`settings.js:71-91`)."""
        shapes, _ = scan(FACTORY)
        (method,) = by_name(shapes)["sp"]["methods"]
        self.assertEqual(method.get("returns"), "item")


def variant(*edits: tuple[str, str]) -> str:
    """`FACTORY` with `(old, new)` edits for ONE cause, each old text found
    exactly once -- an edit that matched nothing would test `FACTORY`."""
    source = FACTORY
    for old, new in edits:
        assert source.count(old) == 1, old
        source = source.replace(old, new)
    return source


CALL_OPEN = "  Object.defineProperties(item, {\n"
CALL_CLOSE = "  });\n  return item;"


RETURNS = "does not return the local on every path"
NOT_A_STATEMENT = "is not a statement of the function's own body"
NOT_DECLARED = "the local is not declared in the function's own body"
USED = ("the local is used other than by its declaration, "
        "Object.defineProperties(V, {...}) and `return V`")
UNREADABLE_KEY = "a key the scan cannot read"
UNREADABLE = "the function holds text the scan cannot read"
THIS = "the function uses `this`"
NOT_IDENTIFIER = "a key that is not a plain identifier"
BACKSLASH = chr(92)
COLLIDES = "the name is already declared in this module"
TWICE = "the name is built by more than one function"
EMPTY = "no member the scan can read is defined"
REBOUND = ("the function's name is used in the module other than to declare "
           "it and call it")
INITIALIZER = "the local's initializer is not exactly `{}`"
CONSTRUCTED = "the name is also constructed with `new` in this module"
DECLARED_TWICE = "the function is declared more than once in the module"
NOT_INLINE = ("a descriptor that is not an object literal of value, get and "
              "set written in place")
NOT_WHOLE = ("the map is not the whole second argument, or the call not a "
             "whole statement")

# Each one leaves the object's members NOT fully stated by the call the scan
# reads, so a shape built from that call alone would be missing a member a
# plugin can reach, or would promise one that is not always there. Every one
# must decline the shape AND keep the warning, with its reason -- a refusal
# nothing reports is the silence #229 closed, and one reported for the wrong
# reason passes while the check it names is gone.
REFUSED = [
    ("another path returns something else",
     variant(("  var item = {};\n",
              "  if (!group) return null;\n  var item = {};\n")),
     RETURNS),
    ("the function can fall through to undefined",
     variant(("  return item;\n}\nfunction S",
              "  if (id) { return item; }\n}\nfunction S")),
     RETURNS),
    ("the function returns something other than the local",
     variant(("  return item;\n}\nfunction S",
              "  return model;\n}\nfunction S")),
     RETURNS),
    # The first return is the local; the last one, the one always reached,
    # is not.
    ("an early return of the local, then something else",
     variant(("  return item;\n}\nfunction S",
              "  if (id) return item;\n  return model;\n}\nfunction S")),
     RETURNS),
    ("the call is inside a conditional block",
     variant((CALL_OPEN, "  if (id) {\n" + CALL_OPEN),
             (CALL_CLOSE, "  });\n  }\n  return item;")),
     NOT_A_STATEMENT),
    # Same depth as the unconditional call -- only the statement boundary
    # before it tells them apart.
    ("the call is the body of an unbraced conditional",
     variant((CALL_OPEN, "  if (id) Object.defineProperties(item, {\n")),
     NOT_A_STATEMENT),
    ("the call is an operand, not a statement",
     variant((CALL_OPEN, "  id && Object.defineProperties(item, {\n")),
     NOT_A_STATEMENT),
    ("the call is inside a nested function",
     variant((CALL_OPEN, "  later(function() {\n" + CALL_OPEN),
             (CALL_CLOSE, "  });\n  });\n  return item;")),
     NOT_A_STATEMENT),
    # `{} && x` holds `x`.
    ("the initializer is more than `{}`",
     variant(("  var item = {};\n", "  var item = {} && group.other;\n")),
     INITIALIZER),
    ("the local is declared inside a nested function",
     variant(("  var item = {};\n",
              "  later(function() {\n    var item = {};\n  });\n"
              "  var item = group.make();\n")),
     NOT_DECLARED),
    # A WHITELIST, not a list of write forms. The first cut enumerated writes
    # -- reassignment, `V.x =`, `V[k] =`, compound assignment,
    # Object.defineProperty -- and review found four more it passed silently:
    # an alias, an escape into a callee, Object.setPrototypeOf, and a second
    # defineProperties whose map is not a literal. Any use of the local other
    # than the three the rule reads now declines it, whatever it does.
    ("the local is reassigned",
     variant((CALL_CLOSE, "  });\n  item = group.other;\n  return item;")),
     USED),
    ("a member is assigned directly",
     variant((CALL_CLOSE, "  });\n  item.extra = 1;\n  return item;")),
     USED),
    ("a member is assigned through a computed key",
     variant((CALL_CLOSE, "  });\n  item[id] = 1;\n  return item;")),
     USED),
    ("a member is created by a compound assignment",
     variant((CALL_CLOSE, "  });\n  item.count += 1;\n  return item;")),
     USED),
    ("a member is assigned later, by a nested function",
     variant((CALL_CLOSE,
              "  });\n  later(function() { item.extra = 1; });\n"
              "  return item;")),
     USED),
    ("Object.defineProperty adds a member the scan does not read",
     variant((CALL_CLOSE,
              "  });\n  Object.defineProperty(item, 'extra', { value: 1 });\n"
              "  return item;")),
     USED),
    ("a second defineProperties whose map is not a literal",
     variant((CALL_CLOSE,
              "  });\n  Object.defineProperties(item, group.extra);\n"
              "  return item;")),
     USED),
    ("an alias writes a member",
     variant((CALL_CLOSE,
              "  });\n  var self = item;\n  self.extra = 1;\n"
              "  return item;")),
     USED),
    ("the local is passed to a function that may add members",
     variant((CALL_CLOSE, "  });\n  group.decorate(item);\n  return item;")),
     USED),
    # Duktape 1.8 has it (ext/duktape/duktape.c), and the new prototype's
    # members become the object's.
    ("the prototype is replaced",
     variant((CALL_CLOSE,
              "  });\n  Object.setPrototypeOf(item, group);\n"
              "  return item;")),
     USED),
    # ES5 7.6: an identifier may be spelled with a Unicode escape, so this
    # writes `item.extra` while the text never says `item`. The same
    # whitelist of readable characters ADR-0005's scan uses refuses it.
    ("an identifier escape spells the local",
     variant((CALL_CLOSE,
              "  });\n  " + BACKSLASH + "u0069tem.extra = 1;\n  return item;")),
     UNREADABLE),
    # ES5.1 7.9.1: a line terminator after `return` ends the statement, so
    # this returns undefined. Collapsing whitespace before comparing hid it.
    ("a line terminator between `return` and the local",
     variant(("  return item;\n}\nfunction S",
              "  return\n  item;\n}\nfunction S")),
     RETURNS),
    # An accessor's `this` is the object, so a setter can add a member the
    # text never names. No core module's factory uses `this` at all.
    ("a descriptor writes a member through `this`",
     variant(("      set: function(v) { model.value = v; }\n",
              "      set: function(v) { this.extra = v; }\n")),
     THIS),
    # An accessor written elsewhere runs with the object as `this` too, and
    # the `this` check can only read one written in place.
    ("an accessor written elsewhere",
     variant(("      set: function(v) { model.value = v; }\n",
              "      set: group.mutate\n")),
     NOT_INLINE),
    ("a descriptor that is not an object literal",
     variant(("    model: {\n      value: model\n    },\n",
              "    model: group.descriptor,\n")),
     NOT_INLINE),
    # `{...} && d` passes `d` at runtime; the scan read the literal.
    ("the map is only part of the second argument",
     variant((CALL_CLOSE, "  } && group.more);\n  return item;")),
     NOT_WHOLE),
    ("the call's result is written to",
     variant((CALL_CLOSE, "  }).extra = 1;\n  return item;")),
     NOT_WHOLE),
    # The key was matched where strings are masked, so a quoted one never
    # matched and its descriptor went unchecked.
    ("a quoted key whose accessor is written elsewhere",
     variant(("    value: {\n", "    'value': {\n"),
             ("      set: function(v) { model.value = v; }\n",
              "      set: group.mutate\n")),
     NOT_INLINE),
    # A refusal of the first call is not undone by a second that reads.
    ("the first of two calls fails",
     variant((CALL_CLOSE,
              "  } && group.more);\n"
              "  Object.defineProperties(item, { extra: { value: 1 } });\n"
              "  return item;")),
     NOT_WHOLE),
    ("a quoted key TypeScript cannot declare unquoted",
     variant(("    model: {\n", "    'foo-bar': { value: 1 },\n    model: {\n")),
     NOT_IDENTIFIER),
    # The runtime key is `xa`; the text says something else.
    ("a quoted key spelled with an escape",
     variant(("    model: {\n",
              "    'x" + BACKSLASH + "u0061': { value: 1 },\n    model: {\n")),
     NOT_IDENTIFIER),
    # Reads decline too. The rule has no use for them, and telling a read
    # from a write is the enumeration the whitelist replaced.
    ("a read through a member",
     variant((CALL_CLOSE, "  });\n  item.model.value = 1;\n  return item;")),
     USED),
    ("a comparison",
     variant((CALL_CLOSE,
              "  });\n  if (item != group) {}\n  return item;")),
     USED),
    # `_property_names` skips a key it cannot read and says so. For a shape
    # that claims its whole member set, skipping it would drop a member.
    ("a key the scan cannot read",
     variant(("    model: {\n", "    0: { value: 2 },\n    model: {\n")),
     UNREADABLE_KEY),
    # The shape is named after the local, and a module block holds one
    # interface per name: TypeScript would MERGE a second `interface item`
    # into the first, and a return type naming it would mean both.
    ("a top-level function has the same name",
     FACTORY + "function item() {}\n",
     COLLIDES),
    ("a top-level variable has the same name",
     FACTORY + "var item = {};\n",
     COLLIDES),
    ("a top-level const has the same name",
     FACTORY + "const item = 1;\n",
     COLLIDES),
    ("an export has the same name",
     FACTORY + "exports.item = function() {};\n",
     COLLIDES),
    # A prototype shape is emitted under its receiver's name whether or not
    # the constructor is declared. Here it is an implicit global, and both
    # `interface item` declarations merged: the factory's callers were
    # promised `actual`.
    ("a prototype shape has the same name",
     FACTORY + "item = function () {};\n"
     "item.prototype.actual = function () {};\n",
     COLLIDES),
    # A receiver that only aliases a method still carries a shape, here
    # with the member the prototype map defines.
    ("a prototype shape of the same name, through an alias",
     FACTORY + "item = function () {};\n"
     "item.prototype.a = item.prototype.b;\n"
     "Object.defineProperties(item.prototype, { actual: { value: 1 } });\n",
     COLLIDES),
    # The export scanner reads this spelling too, and an export that mutates
    # its receiver is emitted as `interface item extends sp` -- which would
    # merge with the local's.
    ("an export has the same name, in bracket form",
     FACTORY + "exports['item'] = function() { this.__proto__ = sp; };\n",
     COLLIDES),
    # A call of F is a call of whatever F holds when it runs.
    ("the factory's name is reassigned in the module",
     FACTORY + "createSetting = function(group, id) { return {}; };\n",
     REBOUND),
    # The later declaration is the one a call reaches.
    ("the factory is declared again",
     FACTORY + "function createSetting() { return { actual: 1 }; }\n",
     DECLARED_TWICE),
    # Not an assignment operator after the name, and still an assignment.
    ("a parameter of the factory's name",
     variant(("sp.createBool = function(id) {",
              "sp.createBool = function(id, createSetting) {")),
     REBOUND),
    ("a parenthesized assignment rebinds the factory",
     FACTORY + "(createSetting) = function() { return {}; };\n",
     REBOUND),
    ("a method reassigns the factory's name",
     variant(("  var item = createSetting(this, id);\n",
              "  createSetting = this.make;\n"
              "  var item = createSetting(this, id);\n")),
     REBOUND),
    # `_returned_shape` reads `new item()` as the shape `item`, and a
    # module block has one type of that name.
    ("a constructor of the same name elsewhere in the module",
     FACTORY + "function other() {\n"
     "  function item() { this.actual = 1; }\n"
     "  return new item();\n"
     "}\n",
     CONSTRUCTED),
    ("two functions build a local of the same name",
     FACTORY + "function createOther() {\n"
     "  var item = {};\n"
     "  Object.defineProperties(item, { other: { value: 1 } });\n"
     "  return item;\n"
     "}\n",
     TWICE),
    # An empty interface would be a claim too -- that the object has no
    # members -- and nothing here read one either way.
    ("the call defines nothing the scan can read",
     "var sp = {};\n"
     "function createSetting(group, id) {\n"
     "  var item = {};\n"
     "  Object.defineProperties(item, {});\n"
     "  return item;\n"
     "}\n"
     "function S() { this.__proto__ = sp; }\n"
     "sp.createBool = function(id) {\n"
     "  var item = createSetting(this, id);\n"
     "  return item;\n"
     "}\n",
     EMPTY),
]

# The whitelist's edges: text that names the local and is not a use of it.
STILL_ANSWERED = [
    ("another object's member of the same name",
     variant((CALL_CLOSE, "  });\n  var other = group.item;\n  return item;"))),
    ("the name inside a string and a comment",
     variant((CALL_CLOSE,
              "  });\n  log('item.extra = 1'); // item.extra = 1\n"
              "  return item;"))),
    # The map is balanced in text whose strings are blank. Balanced in the
    # raw text, the `}` inside the getter's string closed the map after
    # `label` and the other two members were never read.
    ("a brace inside a string in a descriptor",
     variant(("    model: {\n",
              "    label: {\n"
              "      get: function() { return model.value + '}'; }\n"
              "    },\n"
              "    model: {\n")),
     ["label", "model", "value"]),
    ("a trailing comma after the last descriptor",
     variant(("    }\n  });\n  return item;",
              "    },\n  });\n  return item;"))),
]


class TheScanDeclinesAnIncompleteLocal(unittest.TestCase):
    def test_declines_the_shape_and_the_return(self) -> None:
        for label, source, _ in REFUSED:
            with self.subTest(label):
                shapes, _ = scan(source)
                # A prototype shape of that name may stay: it is what the
                # local collided with.
                self.assertNotEqual(
                    by_name(shapes).get("item", {}).get("kind"), "local")
                (method,) = by_name(shapes)["sp"]["methods"]
                self.assertIsNone(method.get("returns"))

    def test_keeps_the_warning_and_says_why(self) -> None:
        for label, source, reason in REFUSED:
            with self.subTest(label):
                _, stderr = scan(source)
                self.assertIn(
                    "ignored unsupported Object.defineProperties target "
                    "item: ", stderr)
                self.assertIn(reason, stderr)

    def test_text_that_is_not_a_use_does_not_decline(self) -> None:
        for label, source, *members in STILL_ANSWERED:
            with self.subTest(label):
                shapes, stderr = scan(source)
                self.assertEqual(
                    [prop["name"] for prop in
                     by_name(shapes).get("item", {}).get("properties", [])],
                    members[0] if members else ["model", "value"], stderr)

    def test_declines_a_name_typescript_reserves(self) -> None:
        """`var object = {}` is legal JavaScript, and `interface object` is
        TS2427 under the pinned tsc 5.7.3 -- as are `any`, `unknown`,
        `never`, `undefined`, `string`, `number`, `boolean`, `symbol` and
        `bigint`, the other predefined type names a `var` may take."""
        shapes, stderr = scan(FACTORY.replace("item", "object"))
        self.assertNotIn("object", by_name(shapes))
        self.assertIn("target object: the name is a type TypeScript "
                      "predefines", stderr)


# `var item = createSetting(...)` holds the factory's object only when the
# call IS the initializer.
CALLER_DECL = "  var item = createSetting(this, id);\n"
CALLER_RETURN = "  item.model.value = true;\n  return item;\n}\n"

# The caller side is a whitelist too, for the same reason as the factory's:
# each point guard added here was followed by another hole. In the method,
# the local may occur only as an unconditional `var x = F(...)` that is the
# whole initializer, as `x.<a member of the shape>`, and in one `return x`,
# and F must be the module's function.
CALLER_REFUSED = [
    ("a member of the result is what the local holds",
     variant((CALLER_DECL + CALLER_RETURN,
              "  var item = createSetting(this, id).model;\n"
              "  return item;\n}\n"))),
    ("the call is an operand",
     variant((CALLER_DECL, "  var item = createSetting(this, id) || null;\n"))),
    ("an early return of something else",
     variant((CALLER_DECL, "  if (id) return 5;\n" + CALLER_DECL))),
    ("the declaration is conditional",
     variant((CALLER_DECL, "  if (id) var item = createSetting(this, id);\n"))),
    ("a line terminator between `return` and the local",
     variant((CALLER_RETURN,
              "  item.model.value = true;\n  return\n  item;\n}\n"))),
    ("the method declares its own function of the factory's name",
     variant((CALLER_DECL,
              "  function createSetting() { return {}; }\n" + CALLER_DECL))),
    # Declarations hoist, so one written after the call still shadows it.
    ("the method declares that function after the call",
     variant((CALLER_DECL,
              CALLER_DECL + "  function createSetting() { return {}; }\n"))),
    ("the local is reassigned",
     variant((CALLER_RETURN,
              "  item = this.other;\n  return item;\n}\n"))),
    ("a callback reassigns the local",
     variant((CALLER_RETURN,
              "  later(function() { item = null; });\n  return item;\n}\n"))),
    # `item.value` names a member the shape has, and `delete` removes it --
    # for good when the descriptor says `configurable: true`.
    ("a member of the shape is deleted",
     variant((CALLER_RETURN, "  delete item.value;\n  return item;\n}\n"))),
    ("a callback deletes a member",
     variant((CALLER_RETURN,
              "  later(function() { delete item.model; });\n"
              "  return item;\n}\n"))),
    # A function held in a value member runs with the object as `this`.
    ("a member of the shape is called",
     variant((CALLER_RETURN, "  item.model();\n  return item;\n}\n"))),
    # A parenthesized reference keeps its base (ES5.1 11.1.6), so `this` is
    # still the object.
    ("a member of the shape is called through parentheses",
     variant((CALLER_RETURN, "  (item.model)();\n  return item;\n}\n"))),
    ("a member of the shape is called through two pairs of parentheses",
     variant((CALLER_RETURN,
              "  ((item.model))\n  ();\n  return item;\n}\n"))),
    ("a member the shape does not have is added",
     variant((CALLER_RETURN, "  item.extra = 1;\n  return item;\n}\n"))),
    ("the local is handed to a function",
     variant((CALLER_RETURN, "  register(item);\n  return item;\n}\n"))),
    ("an identifier escape spells the local",
     variant((CALLER_RETURN,
              "  " + BACKSLASH + "u0069tem.extra = 1;\n  return item;\n}\n"))),
]


class TheCallerHoldsTheFactoryResult(unittest.TestCase):
    def test_what_the_rule_allows_still_holds_it(self) -> None:
        for label, source in [
                ("a const declaration",
                 variant((CALLER_DECL,
                          "  const item = createSetting(this, id);\n"))),
                ("one declarator of several",
                 variant((CALLER_DECL,
                          "  var item = createSetting(this, id), n = 1;\n"))),
                ("members of the shape, read and written",
                 variant((CALLER_RETURN,
                          "  item . model.min = 1;\n  item.value = true;\n"
                          "  prop.subscribe(item.model.eventSink);\n"
                          "  return item;\n}\n")))]:
            with self.subTest(label):
                shapes, _ = scan(source)
                (method,) = by_name(shapes)["sp"]["methods"]
                self.assertEqual(method.get("returns"), "item")

    def test_anything_after_the_call_declines_the_return(self) -> None:
        for label, source in CALLER_REFUSED:
            with self.subTest(label):
                shapes, _ = scan(source)
                self.assertIn("item", by_name(shapes))
                (method,) = by_name(shapes)["sp"]["methods"]
                self.assertIsNone(method.get("returns"))


class TheCorpus(unittest.TestCase):
    """`movian/settings` itself, read from the tree."""

    @classmethod
    def setUpClass(cls) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            cls.modules = {module["name"]: module
                           for module in gen.build_commonjs_modules()}
        cls.stderr = stderr.getvalue()

    def test_settings_declares_the_item_createSetting_builds(self) -> None:
        shapes = by_name(self.modules["movian/settings"]["shapes"])
        item = shapes["item"]
        self.assertEqual(item["factory"], "createSetting")
        self.assertEqual(
            [(prop["name"], prop["kind"], prop["source"]["line"])
             for prop in item["properties"]],
            [("enabled", "accessor", 31), ("model", "value", 17),
             ("value", "accessor", 21)])

    def test_the_four_methods_that_return_it_say_so(self) -> None:
        methods = {method["name"]: method.get("returns") for method in
                   by_name(self.modules["movian/settings"]["shapes"])
                   ["sp"]["methods"]}
        self.assertEqual(
            {name: returned for name, returned in methods.items()
             if returned is not None},
            {"createAction": "item", "createBool": "item",
             "createInt": "item", "createString": "item"})

    def test_no_defineProperties_target_is_left_unread(self) -> None:
        """The warning #260 opened on, which printed on every run -- and
        the census's, which would name a spelling no reader takes."""
        self.assertNotIn("Object.defineProperties", self.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
