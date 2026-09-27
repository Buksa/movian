#!/usr/bin/env python3
"""A local object built by `Object.defineProperties` is a named shape
(ADR-0006, movian#260).

`movian/settings`' `createSetting` builds `var item = {}`, attaches its whole
public surface with `Object.defineProperties(item, {...})` and returns it, and
every `sp.create*` hands that object to the plugin. The generator read no such
target, printed a warning on every run, and declared the four results `any`.

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

    def test_the_read_call_is_not_reported_as_unsupported(self) -> None:
        _, stderr = scan(FACTORY)
        self.assertNotIn("defineProperties target item", stderr)

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
WRITTEN = "a member of the local is written outside Object.defineProperties"
REASSIGNED = "the local is reassigned"
SINGULAR = "Object.defineProperty on the local is not read"
COLLIDES = "the name is already declared in this module"
TWICE = "the name is built by more than one function"
EMPTY = "no member the scan can read is defined"

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
    ("the call is inside a conditional block",
     variant((CALL_OPEN, "  if (id) {\n" + CALL_OPEN),
             (CALL_CLOSE, "  });\n  }\n  return item;")),
     NOT_A_STATEMENT),
    # Same depth as the unconditional call -- only the statement boundary
    # before it tells them apart.
    ("the call is the body of an unbraced conditional",
     variant((CALL_OPEN, "  if (id) Object.defineProperties(item, {\n")),
     NOT_A_STATEMENT),
    ("the call is inside a nested function",
     variant((CALL_OPEN, "  later(function() {\n" + CALL_OPEN),
             (CALL_CLOSE, "  });\n  });\n  return item;")),
     NOT_A_STATEMENT),
    ("the local is declared inside a nested function",
     variant(("  var item = {};\n",
              "  later(function() {\n    var item = {};\n  });\n"
              "  var item = group.make();\n")),
     NOT_DECLARED),
    ("the local is reassigned",
     variant((CALL_CLOSE, "  });\n  item = group.other;\n  return item;")),
     REASSIGNED),
    ("a member is assigned directly",
     variant((CALL_CLOSE, "  });\n  item.extra = 1;\n  return item;")),
     WRITTEN),
    ("a member is assigned through a computed key",
     variant((CALL_CLOSE, "  });\n  item[id] = 1;\n  return item;")),
     WRITTEN),
    ("a member is created by a compound assignment",
     variant((CALL_CLOSE, "  });\n  item.count += 1;\n  return item;")),
     WRITTEN),
    ("a member is assigned later, by a nested function",
     variant((CALL_CLOSE,
              "  });\n  later(function() { item.extra = 1; });\n"
              "  return item;")),
     WRITTEN),
    ("Object.defineProperty adds a member the scan does not read",
     variant((CALL_CLOSE,
              "  });\n  Object.defineProperty(item, 'extra', { value: 1 });\n"
              "  return item;")),
     SINGULAR),
    # The shape is named after the local, and a module block holds one
    # interface per name: TypeScript would MERGE a second `interface item`
    # into the first, and a return type naming it would mean both.
    ("a top-level function has the same name",
     FACTORY + "function item() {}\n",
     COLLIDES),
    ("a top-level variable has the same name",
     FACTORY + "var item = {};\n",
     COLLIDES),
    ("an export has the same name",
     FACTORY + "exports.item = function() {};\n",
     COLLIDES),
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

# Writes that do NOT add a member of the local, and must not decline it.
STILL_ANSWERED = [
    ("a write through a member reaches the member's own object",
     variant((CALL_CLOSE, "  });\n  item.model.value = 1;\n  return item;"))),
    ("a comparison is not an assignment",
     variant((CALL_CLOSE,
              "  });\n  if (item.model == null || item != group) {}\n"
              "  return item;"))),
]


class TheScanDeclinesAnIncompleteLocal(unittest.TestCase):
    def test_declines_the_shape_and_the_return(self) -> None:
        for label, source, _ in REFUSED:
            with self.subTest(label):
                shapes, _ = scan(source)
                self.assertNotIn("item", by_name(shapes))
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

    def test_a_write_that_adds_no_member_does_not_decline(self) -> None:
        for label, source in STILL_ANSWERED:
            with self.subTest(label):
                shapes, stderr = scan(source)
                self.assertIn("item", by_name(shapes), stderr)


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
        """The warning #260 opened on, which printed on every run."""
        self.assertNotIn("defineProperties target", self.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
