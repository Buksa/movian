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
    # A line terminator ends the statement only where the next token cannot
    # continue the expression (ES5.1 7.9.1). Each of these continues it:
    # the first calls what the call returns with `d`.
    ("a line terminator, then `(`",
     "Object.defineProperties({t}, { a: { value: 1 } })\n  (d)();",
     NOT_WHOLE, TARGETS),
    ("a line terminator, then `[`",
     "Object.defineProperties({t}, { a: { value: 1 } })\n  [d].pop();",
     NOT_WHOLE, TARGETS),
    ("a line terminator, then `.`",
     "Object.defineProperties({t}, { a: { value: 1 } })\n  .b = 1;",
     NOT_WHOLE, TARGETS),
    ("a line terminator, then `instanceof`",
     "Object.defineProperties({t}, { a: { value: 1 } })\n  instanceof d;",
     NOT_WHOLE, TARGETS),
    # And before the call: the token ending the line takes an operand, so
    # the call is one. The local's own rule refuses both first.
    ("an operator, then a line terminator, then the call",
     "d &&\n  Object.defineProperties({t}, { a: { value: 1 } });",
     NOT_WHOLE, ("this", "X.prototype")),
    ("a control header, then a line terminator, then the call",
     "if (d)\n  Object.defineProperties({t}, { a: { value: 1 } });",
     NOT_WHOLE, ("this", "X.prototype")),
    # The readers find a call in the masked text, whose string mask works a
    # line at a time: it shows this string's second line as code. The
    # module's scanner knows it is a string, and there is no call to read.
    # The local's whitelist refuses the backslash first.
    ("the call inside a string continued onto the next line",
     "var s = 'x\\\n"
     "Object.defineProperties({t}, { a: { value: 1 } })';",
     "the module's scanner reads it as part of a literal or a comment",
     ("this", "X.prototype")),
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
    # ES5.1 7.9.1 inserts the `;` where the next token cannot continue the
    # expression. Before this branch, `this` and `X.prototype` read all of
    # these; its first commit required the `;` spelled, and recorded no
    # member (PR #277, Codex).
    ("a line terminator ends the call's statement",
     "Object.defineProperties({t}, { a: { value: 1 } })",
     [("a", "value")]),
    ("a comment, then a line terminator",
     "Object.defineProperties({t}, { a: { value: 1 } }) // a\n  d = 1;",
     [("a", "value")]),
    # ES5.1 7.4: a comment holding a line terminator counts as one.
    ("a block comment holding a line terminator",
     "Object.defineProperties({t}, { a: { value: 1 } }) /*\n  */ d = 1;",
     [("a", "value")]),
]

# Read on the module's scanner, `_js_spans`, where a regex literal is a
# literal, and a statement ends where ES5.1 7.9.1 ends it. The first commit
# of this branch read these on the per-line mask and refused each; the
# readers before it recorded `a`, and missed `b` beside a regex brace.
#
# `(label, statement, what each target records, targets)`. The local is left
# out where its own whitelist refuses first: `/` is text it cannot read, and
# the statement before its call must end in `;`, `{` or `}`.
READ_ON_THE_SCANNER = [
    # PR #277, Codex.
    ("a comma inside a regex literal in a descriptor",
     "Object.defineProperties({t}, { a: { value: /x,y/ } });",
     [("a", "value")], ("this", "X.prototype")),
    ("a brace inside a regex literal in a descriptor",
     "Object.defineProperties({t}, { a: { value: /}/ }, b: { value: 1 } });",
     [("a", "value"), ("b", "value")], ("this", "X.prototype")),
    ("a line terminator ends the statement before the call",
     "var x = d\n  Object.defineProperties({t}, { a: { value: 1 } });",
     [("a", "value")], ("this", "X.prototype")),
    ("a string ends the statement before the call",
     "var s = 'x'\n  Object.defineProperties({t}, { a: { value: 1 } });",
     [("a", "value")], ("this", "X.prototype")),
    ("a call ends the statement before the call",
     "d()\n  Object.defineProperties({t}, { a: { value: 1 } });",
     [("a", "value")], ("this", "X.prototype")),
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

    def test_literals_and_statements_are_the_scanners(self) -> None:
        for label, statement, expected, targets in READ_ON_THE_SCANNER:
            for target in targets:
                build, shape, _ = TARGETS[target]
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
    ("computed member access, spaced inside the brackets",
     "Object[ 'defineProperties' ](this, { a: { value: 1 } });", 2),
    ("computed member access after a parenthesis",
     "(0, Object)['defineProperties'](this, { a: { value: 1 } });", 2),
    ("computed member access after a bracket",
     "var o = [Object];\n"
     "  o[0]['defineProperties'](this, { a: { value: 1 } });", 3),
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
    # The readers' string mask misreads both lines: the regex's quote, and
    # the quote closing the continued string, each open a "string" that
    # hides the call. The census walks the module with its own scanner, so
    # it is not blind where they are (ADR-0004).
    ("a call after a regex literal holding a quote",
     "var r = /'/; Object.defineProperties (this, { a: { value: 1 } }); "
     "var q = '';", 2),
    ("a call after a string continued onto the next line",
     "var s = 'x\\\n"
     "  y'; Object.defineProperties (this, { a: { value: 1 } });", 3),
]

# `(label, constructor body, what C records)`. A string is not a call. The
# census read strings intact so that `Object['defineProperties']` is seen,
# and every other string that said the word was reported too, at its own
# line, beside a call the reader took or with no call at all (movian#272,
# both reviews).
MENTIONED = [
    ("the name alone in a string",
     "var s = 'defineProperties';", []),
    ("the name in a string beside a call the reader takes",
     "var s = 'defineProperties';\n"
     "  Object.defineProperties(this, { a: { value: 1 } });",
     [("a", "value")]),
    ("the call's spelling in a string",
     "var help = \"Object.defineProperties\";", []),
    # The computed member's spelling, but inside a string: the brackets are
    # not code.
    ("the computed member's spelling in a string",
     "var help = \"Object['defineProperties']\";", []),
    # ASI ends the first line, so a name is the code just before the
    # string's own bracket.
    ("the computed member's spelling in a string after a name",
     "var help = d\n  \"Object['defineProperties']\";", []),
    # The brackets are code but read no member: nothing precedes them that
    # a member could be read from.
    ("the name alone in an array literal",
     "var names = ['defineProperties'];", []),
    ("an array literal after a keyword that takes an operand",
     "if (d) {\n    return ['defineProperties'];\n  }", []),
    ("the name as a later element of an array literal",
     "var names = [d, 'defineProperties'];", []),
    # A member read, but its name is not `defineProperties`.
    ("a member keyed by a string that says more than the name",
     "d['Object.defineProperties'] = 1;", []),
    ("a member name built from the string",
     "var v = d['defineProperties' + d];", []),
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

    def test_a_string_that_only_mentions_the_name_is_not_reported(
            self) -> None:
        for label, body, expected in MENTIONED:
            with self.subTest(label):
                shapes, stderr = scan(in_constructor(body))
                self.assertEqual(members(shapes, "C"), expected, stderr)
                self.assertEqual(stderr, "")

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
