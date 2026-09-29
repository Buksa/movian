#!/usr/bin/env python3
"""One reading of an `Object.defineProperties` map, for every target
(movian#272).

The generator reads `Object.defineProperties(<target>, {...})` for three
targets: `this` in a constructor, `X.prototype`, and a local object a module
function builds (ADR-0006). Each had its own reading, and only the local one
checked the map, so the other two recorded `a` from `{ a: {...} } && d`,
which passes `d`. All three read a member's kind from text whose strings were
intact.

A call spelled other than `Object.defineProperties(` was neither read nor
reported.

Every probe is a form the corpus does not contain today and could tomorrow;
`gen.py --check` pins the six calls it does contain, kinds included.
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
PROBE = MODULES / "movian" / "_descriptor_map_probe.js"


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


def members(shapes: list[dict], name: str) -> list[tuple[str, str]]:
    """`[(member, kind)]` of the shape `name`, or `[]` without one."""
    for shape in shapes:
        if shape["name"] == name:
            return [(prop["name"], prop["kind"])
                    for prop in shape.get("properties", [])]
    return []


# A statement `{t}` holds the target in. Each module puts it where its reader
# looks: a constructor's own body, the module's top level, a factory's own
# body. `d` is a parameter or a free name, whichever the place has.
def in_constructor(statement: str) -> str:
    return ("function C(d) {\n  %s\n}\n"
            "C.prototype.m = function() {};\n" % statement.replace("{t}", "this"))


def on_prototype(statement: str) -> str:
    return ("function C() {}\n"
            "C.prototype.m = function() {};\n%s\n"
            % statement.replace("{t}", "C.prototype"))


def in_factory(statement: str) -> str:
    return ("var sp = {};\n"
            "function make(d) {\n  var item = {};\n  %s\n  return item;\n}\n"
            "function S() { this.__proto__ = sp; }\n"
            "sp.create = function(d) {\n"
            "  var item = make(d);\n  return item;\n}\n"
            % statement.replace("{t}", "item"))


# `(module builder, shape name, the target as the warning spells it)`.
TARGETS = {
    "this": (in_constructor, "C", "this"),
    "X.prototype": (on_prototype, "C", "C.prototype"),
    "a local": (in_factory, "item", "item"),
}

NOT_WHOLE = ("the map is not the whole second argument, or the call not a "
             "whole statement")
NOT_INLINE = ("a descriptor that is not an object literal of value, get and "
              "set written in place")
NOT_IDENTIFIER = "a key that is not a plain identifier"
UNREADABLE_KEY = "a key the scan cannot read"

# Each call defines a member set the map text does not state, so recording
# what it names would claim members the object may not have, or leave out
# one it has. A refusal records NO member from the call -- not only the one
# at fault -- and says why. The local reader refused all of these already
# (test_local_object_shape.py); the other two recorded them.
#
# The local target is left out here only where its own rule refuses first:
# a call that does not begin a statement of the factory's own body is
# `NOT_A_STATEMENT` there, pinned in that file.
REFUSED = [
    # movian#272, as filed: at runtime the call receives `d`.
    ("the map is only part of the second argument",
     "Object.defineProperties({t}, { a: { value: 1 } } && d);",
     NOT_WHOLE, TARGETS),
    ("a third argument follows the map",
     "Object.defineProperties({t}, { a: { value: 1 } }, d);",
     NOT_WHOLE, TARGETS),
    ("the call's result is written to",
     "Object.defineProperties({t}, { a: { value: 1 } }).b = 1;",
     NOT_WHOLE, TARGETS),
    # The two a constructor or the top level can hold at the reader's
    # depth: members defined on one path only.
    ("the call is an operand",
     "d && Object.defineProperties({t}, { a: { value: 1 } });",
     NOT_WHOLE, ("this", "X.prototype")),
    ("the call is the body of an unbraced conditional",
     "if (d) Object.defineProperties({t}, { a: { value: 1 } });",
     NOT_WHOLE, ("this", "X.prototype")),
    ("a descriptor that is not an object literal",
     "Object.defineProperties({t}, { a: d.descriptor });",
     NOT_INLINE, TARGETS),
    ("an accessor written elsewhere",
     "Object.defineProperties({t}, { a: { get: d.read } });",
     NOT_INLINE, TARGETS),
    ("a quoted key whose accessor is written elsewhere",
     "Object.defineProperties({t}, { 'a': { get: d.read } });",
     NOT_INLINE, TARGETS),
    ("a key that is not a descriptor key",
     "Object.defineProperties({t}, { a: { value: 1, extra: 2 } });",
     NOT_INLINE, TARGETS),
    # `foo-bar: any;` is not a TypeScript member.
    ("a quoted key TypeScript cannot declare unquoted",
     "Object.defineProperties({t}, { b: { value: 2 }, "
     "'foo-bar': { value: 1 } });",
     NOT_IDENTIFIER, TARGETS),
    # The readable `b` is not recorded either: the call defines a member
    # the scan cannot name, and the rest would be a partial set.
    ("a key the scan cannot read",
     "Object.defineProperties({t}, { b: { value: 2 }, 0: { value: 1 } });",
     UNREADABLE_KEY, TARGETS),
]

# The map's edges: text around the members that must not stop them being
# read, with what each target records.
STILL_READ = [
    ("value and accessor descriptors",
     "Object.defineProperties({t}, {\n"
     "    a: { value: 1, writable: true },\n"
     "    b: { get: function() { return 1; },"
     " set: function(v) { d = v; } }\n"
     "  });",
     [("a", "value"), ("b", "accessor")]),
    ("a trailing comma after the last descriptor",
     "Object.defineProperties({t}, { a: { value: 1, }, });",
     [("a", "value")]),
    ("a quoted key spelled as an identifier",
     "Object.defineProperties({t}, { 'a': { value: 1 } });",
     [("a", "value")]),
    # Balanced where strings are masked. Balanced in the raw text, the `}`
    # in the string closed the map before `b`.
    ("a brace inside a string in a descriptor",
     "Object.defineProperties({t}, { a: { get: function() "
     "{ return '}'; } }, b: { value: 1 } });",
     [("a", "accessor"), ("b", "value")]),
    # Found where strings are masked. Counted in the raw text, the `{` put
    # the call one block deep, and neither `this` nor `X.prototype` read it.
    ("a brace inside a string before the call",
     "var s = '{';\n  Object.defineProperties({t}, { a: { value: 1 } });",
     [("a", "value")]),
    ("a comma inside a string in a descriptor",
     "Object.defineProperties({t}, { a: { value: 'x, b: y' } });",
     [("a", "value")]),
    ("a comment between descriptors",
     "Object.defineProperties({t}, { a: { value: 1 }, /* b: {} */ "
     "c: { value: 2 } });",
     [("a", "value"), ("c", "value")]),
]

# The kind is the descriptor's own: `get` or `set` among its keys. It was read
# by searching the descriptor's text, strings included, for `get:`.
KINDS = [
    # movian#272, as filed.
    ("`get:` inside a string",
     "Object.defineProperties({t}, { a: { value: 'get: x' } });",
     [("a", "value")]),
    ("`set:` inside a string",
     "Object.defineProperties({t}, { a: { value: \"set: x\" } });",
     [("a", "value")]),
    # A key of the value, not of the descriptor.
    ("`get` as a key of the value",
     "Object.defineProperties({t}, { a: { value: { get: 1 } } });",
     [("a", "value")]),
    ("a getter",
     "Object.defineProperties({t}, { a: { get: function() { return 1; } } "
     "});",
     [("a", "accessor")]),
    ("a setter alone",
     "Object.defineProperties({t}, { a: { set: function(v) { d = v; } } "
     "});",
     [("a", "accessor")]),
]


class OneReadingForEveryTarget(unittest.TestCase):
    def test_a_refused_call_records_no_member(self) -> None:
        for label, statement, _, targets in REFUSED:
            for target in targets:
                build, shape, _ = TARGETS[target]
                with self.subTest(label, target=target):
                    shapes, stderr = scan(build(statement))
                    self.assertEqual(members(shapes, shape), [], stderr)

    def test_a_refused_call_is_reported_with_its_reason(self) -> None:
        for label, statement, reason, targets in REFUSED:
            for target in targets:
                build, _, spelled = TARGETS[target]
                with self.subTest(label, target=target):
                    _, stderr = scan(build(statement))
                    self.assertIn(
                        "ignored unsupported Object.defineProperties target "
                        "%s: %s" % (spelled, reason), stderr)

    def test_the_edges_of_a_map_still_read(self) -> None:
        for label, statement, expected in STILL_READ:
            for target, (build, shape, _) in TARGETS.items():
                with self.subTest(label, target=target):
                    shapes, stderr = scan(build(statement))
                    self.assertEqual(members(shapes, shape), expected, stderr)
                    self.assertNotIn("Object.defineProperties", stderr)

    def test_a_kind_is_the_descriptors_own(self) -> None:
        for label, statement, expected in KINDS:
            for target, (build, shape, _) in TARGETS.items():
                with self.subTest(label, target=target):
                    shapes, stderr = scan(build(statement))
                    self.assertEqual(members(shapes, shape), expected, stderr)


# `(label, constructor body, the line that spells defineProperties)`. The
# body sits in `function C(d) {` on line 1, so its own first line is line 2.
UNREAD = [
    # movian#272, as filed.
    ("computed member access",
     "Object['defineProperties'](this, { a: { value: 1 } });", 2),
    ("a space before the argument list",
     "Object.defineProperties (this, { a: { value: 1 } });", 2),
    ("computed member access, double-quoted",
     "Object[\"defineProperties\"](this, { a: { value: 1 } });", 2),
    ("a line break before the argument list",
     "Object.defineProperties\n  (this, { a: { value: 1 } });", 2),
    ("a line break before the member name",
     "Object\n    .defineProperties(this, { a: { value: 1 } });", 3),
    ("an alias of the method",
     "var define = Object.defineProperties;\n"
     "  define(this, { a: { value: 1 } });", 2),
    # Literal, but at a depth the constructor's reader does not take.
    ("a call inside a block",
     "if (d) {\n    Object.defineProperties(this, { a: { value: 1 } });\n"
     "  }", 3),
    ("a call inside a callback",
     "later(function() {\n"
     "    Object.defineProperties(this, { a: { value: 1 } });\n  });", 3),
]


class EveryCallIsReadOrReported(unittest.TestCase):
    def test_a_call_no_reader_takes_is_reported_at_its_line(self) -> None:
        for label, body, line in UNREAD:
            with self.subTest(label):
                shapes, stderr = scan(in_constructor(body))
                self.assertEqual(members(shapes, "C"), [], stderr)
                self.assertIn(
                    "_descriptor_map_probe.js:%d: warning: ignored "
                    "unsupported Object.defineProperties call" % line, stderr)

    def test_a_call_a_reader_takes_is_not_also_reported(self) -> None:
        for target, (build, shape, _) in TARGETS.items():
            with self.subTest(target=target):
                shapes, stderr = scan(build(
                    "Object.defineProperties({t}, { a: { value: 1 } });"))
                self.assertEqual(members(shapes, shape), [("a", "value")])
                self.assertEqual(stderr, "")

    def test_a_call_a_reader_refuses_is_reported_once(self) -> None:
        for target, (build, _, _) in TARGETS.items():
            with self.subTest(target=target):
                _, stderr = scan(build(
                    "Object.defineProperties({t}, { a: { value: 1 } } && d);"))
                self.assertEqual(stderr.count("warning"), 1, stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
