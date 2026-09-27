#!/usr/bin/env python3
"""An invocation in a core module is an accessor (ADR-0005, movian#262).

A core-module parameter the function uses at least once, every use an
invocation of the value as passed -- `p(...)`, `p.apply(...)`, `p.call(...)`
-- is typed callable, the way a C accessor types a native slot. Zero uses, or
any other use, and the rule types nothing.

The scan half is pinned here on synthetic modules, because the rule is a
statement about WHICH USES EXIST and every refusal below is a use the corpus
may not contain today. The emission half is pinned on `TypeScope`, and the
corpus half on the real tree, so a regression that returns every slot to
`any` and one that admits a contested slot both have a test that goes red.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
GEN_PY = REPO_ROOT / "support" / "devtools" / "metadata" / "gen.py"

_spec = importlib.util.spec_from_file_location("movian_metadata_gen", GEN_PY)
assert _spec is not None and _spec.loader is not None
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

MODULES = REPO_ROOT / "res" / "ecmascript" / "modules"


PROBE = MODULES / "movian" / "_accessor_probe.js"


def scan(source: str, scanner):
    PROBE.write_text(source, encoding="utf-8")
    try:
        return scanner(PROBE)
    finally:
        PROBE.unlink()
        gen._RAW_LINES_CACHE.pop(PROBE, None)


def scan_export(source: str) -> dict:
    """The record of the one export in `source`, scanned as a core module."""
    exports = scan(source, gen.scan_commonjs_exports)
    assert len(exports) == 1, exports
    return exports[0]


def scan_methods(source: str) -> dict:
    """`{receiver.name: record}` for the methods in `source`."""
    return {"%s.%s" % (shape["name"], method["name"]): method
            for shape in scan(source, gen.scan_commonjs_shapes)
            for method in shape["methods"]}


class TheScanRecordsAccessors(unittest.TestCase):
    def test_a_parameter_only_invoked_is_an_accessor(self) -> None:
        record = scan_export(
            "exports.f = function(cb) {\n"
            "  cb(1);\n"
            "}\n")
        self.assertEqual(record.get("accessors"), {"cb": [2]})

    def test_a_prototype_method_is_scanned_the_same_way(self) -> None:
        """`Item.addOptAction` and `Page.appendAction` are methods, not
        exports: a rule wired into the export scan alone missed them."""
        methods = scan_methods(
            "function C() {}\n"
            "C.prototype.m = function(title, func) {\n"
            "  func();\n"
            "}\n")
        self.assertEqual(methods["C.m"].get("accessors"), {"func": [3]})

    def test_a_shared_object_method_is_scanned_the_same_way(self) -> None:
        """The five `movian/settings` callbacks live on `sp`, a shared object
        installed as the prototype -- a third record builder."""
        methods = scan_methods(
            "var sp = {};\n"
            "function S() { this.__proto__ = sp; }\n"
            "sp.createAction = function(id, callback) {\n"
            "  sub(function(type) {\n"
            "    if(type == 'action')\n"
            "      callback();\n"
            "  });\n"
            "}\n")
        self.assertEqual(methods["sp.createAction"].get("accessors"),
                         {"callback": [6]})

    def test_a_receiver_member_is_scanned_the_same_way(self) -> None:
        record = scan_export(
            "exports.C = function() {\n"
            "  this.__proto__ = sp;\n"
            "  this.m = function(cb) {\n"
            "    cb();\n"
            "  };\n"
            "}\n")
        members = {m["name"]: m for m in record["receiverMembers"]}
        self.assertEqual(members["m"].get("accessors"), {"cb": [4]})


def body(*lines: str) -> dict:
    """`exports.f = function(cb) {` on line 1, then `lines` from line 2."""
    return scan_export("exports.f = function(cb) {\n%s\n}\n"
                       % "\n".join(lines))


class EveryUseIsAnInvocation(unittest.TestCase):
    """What admits: `p(...)`, `p.apply(...)`, `p.call(...)`, however deferred
    or conditional."""

    def test_apply_and_call_are_invocations(self) -> None:
        record = body("cb.apply(null, [1]);",
                      "cb . call(null, 1);")
        self.assertEqual(record.get("accessors"), {"cb": [2, 3]})

    def test_a_deferred_conditional_invocation_counts(self) -> None:
        record = body("sub(function(type) {",
                      "  if(type == 'action')",
                      "    cb();",
                      "});")
        self.assertEqual(record.get("accessors"), {"cb": [4]})

    def test_a_property_sharing_the_name_is_not_a_use(self) -> None:
        record = body("this.cb = 1;",
                      "o.cb();",
                      "cb();")
        self.assertEqual(record.get("accessors"), {"cb": [4]})

    def test_the_name_in_a_string_or_a_comment_is_not_a_use(self) -> None:
        record = body("log('cb');  // cb is invoked below",
                      "cb();")
        self.assertEqual(record.get("accessors"), {"cb": [3]})

    def test_a_nested_functions_own_arguments_do_not_block(self) -> None:
        record = body("sub(function() {",
                      "  log(arguments);",
                      "  cb();",
                      "});")
        self.assertEqual(record.get("accessors"), {"cb": [4]})


class AnyOtherUseBlocks(unittest.TestCase):
    """What refuses, each with its anchor recorded (ADR-0005, "How it is
    recorded"): the blocking line beside the invocations it outvotes."""

    def assertBlocked(self, record: dict, invocations: list[int],
                      blocking: list[int]) -> None:
        self.assertNotIn("accessors", record)
        self.assertEqual(record.get("contested"), {
            "cb": {"invocations": invocations, "blocking": blocking}})

    def test_zero_uses_is_not_an_accessor(self) -> None:
        """"Every use an invocation" holds vacuously and still types
        nothing: the root `http.request`'s callback is `unknown` on
        purpose."""
        record = body("return 1;")
        self.assertNotIn("accessors", record)
        self.assertNotIn("contested", record)

    def test_a_use_that_is_never_an_invocation_records_nothing(self) -> None:
        """`Request.on` stores `fn`; nothing here is a callback the rule
        could speak for, so neither field is written."""
        record = body("this.onData = cb;")
        self.assertNotIn("accessors", record)
        self.assertNotIn("contested", record)

    def test_a_truthiness_test(self) -> None:
        """`movian/http.js:110`, `if(callback)`."""
        self.assertBlocked(body("if(cb)", "  cb();"), [3], [2])

    def test_typeof(self) -> None:
        self.assertBlocked(
            body("if(typeof cb == 'function')", "  cb();"), [3], [2])

    def test_forwarding(self) -> None:
        self.assertBlocked(body("g(cb);", "cb();"), [3], [2])

    def test_storing(self) -> None:
        self.assertBlocked(body("this.handler = cb;", "cb();"), [3], [2])

    def test_reassignment(self) -> None:
        self.assertBlocked(body("cb = cb || noop;", "cb();"), [3], [2])

    def test_a_shadowing_inner_parameter(self) -> None:
        """The inner `cb` is another binding; its call is not the outer
        parameter's. Nothing resolves scope -- the declaring occurrence is
        what refuses."""
        self.assertBlocked(
            body("sub(function(cb) {", "  cb();", "});"), [3], [2])

    def test_a_shadowing_var_in_a_nested_function(self) -> None:
        """`var cb` rebinds the name for the whole closure; its `cb()` calls
        that, not the parameter. Found surviving the suite as a mutation
        (luna review on PR #265)."""
        self.assertBlocked(body("(function() {",
                                "  var cb = function() {};",
                                "  cb();",
                                "})();"), [4], [3])

    def test_a_shadowing_catch_parameter(self) -> None:
        """Inside `catch (cb)`, `cb` is the exception."""
        self.assertBlocked(body("try { x(); } catch (cb) {",
                                "  cb();",
                                "}"), [3], [2])

    def test_a_shadowing_function_declaration(self) -> None:
        """`function cb(` reads like a call and declares a binding."""
        self.assertBlocked(
            body("function cb() {}", "cb();"), [3], [2])

    def test_an_accessor_property_definition_is_not_a_call(self) -> None:
        """ES5 `{ get cb() {} }` reads like `cb(` and defines a property.
        Taken for a call, it would admit a slot on a use that never
        happened -- the one direction this rule must not err in."""
        record = body("return { get cb() { return 1; },",
                      "         set cb(v) { } };")
        self.assertNotIn("accessors", record)

    def test_construction_is_not_invocation(self) -> None:
        self.assertBlocked(body("new cb();", "cb();"), [3], [2])

    def test_the_functions_own_arguments(self) -> None:
        """`arguments` can reach every parameter."""
        self.assertBlocked(body("g.apply(null, arguments);", "cb();"),
                           [3], [2])

    def test_arguments_after_a_nested_function_keeps_its_line(self) -> None:
        """Found in the own body, whose nested functions are blanked rather
        than cut out -- cut out, the lines they held vanish and the anchor
        lands above the read it names."""
        self.assertBlocked(body("sub(function() {",
                                "  x();",
                                "});",
                                "g.apply(null, arguments);",
                                "cb();"), [6], [5])


class TheCallbackShapePosition(unittest.TestCase):
    """Which argument carries `new <shape>(...)`, read off the invocation.
    Counting `.call` as an invocation made it pick the callback without
    reading its arguments, and the shape fell back to position 0 (Codex on
    PR #265)."""

    SOURCE = ("function Item() {}\n"
              "exports.f = function(cb) {\n"
              "  %s\n"
              "}\n")

    def test_a_direct_call(self) -> None:
        record = scan_export(self.SOURCE % "cb(null, new Item());")
        self.assertEqual(record.get("callbackShapeIndex"), 1)

    def test_call_passes_the_receiver_first(self) -> None:
        record = scan_export(self.SOURCE % "cb.call(ctx, null, new Item());")
        self.assertEqual(record.get("callbackShapeIndex"), 1)


class AnUnreadableBodyBlocks(unittest.TestCase):
    """The scan reads masked text -- comments and strings blanked -- and
    trusts that every name left in it is code, and that every name the
    function can reach is resolved statically. Where either fails, the rule
    refuses, because a guess here is a narrowing on nobody's evidence
    (Codex on PR #265)."""

    def assertBlocked(self, record: dict, invocations: list[int],
                      blocking: list[int]) -> None:
        self.assertNotIn("accessors", record)
        self.assertEqual(record.get("contested"), {
            "cb": {"invocations": invocations, "blocking": blocking}})

    def test_a_regex_literal(self) -> None:
        """`/cb()/` is not masked, and reads as a call."""
        record = body("return /cb()/;")
        self.assertNotIn("accessors", record)

    def test_a_quote_inside_a_regex_hides_the_rest_of_its_line(self) -> None:
        """The string mask opens at the `'` and blanks `store(cb)`."""
        self.assertBlocked(body("cb();",
                                "if(/'/.test(s)) store(cb);"), [2], [3])

    def test_division_refuses_too(self) -> None:
        """A `/` left in masked text is a regex or a division, and telling
        them apart is a guess. Measured: the only `/` in every masked core
        module is one regex literal, `movian/http.js:55`."""
        self.assertBlocked(body("cb();", "var half = width / 2;"),
                           [2], [3])

    def test_a_string_continued_onto_the_next_line(self) -> None:
        """The continuation's closing quote opens a string to the mask, and
        `store(cb)` vanishes with it."""
        self.assertBlocked(body("var s = 'a\\",
                                "b'; store(cb);",
                                "cb();"), [4], [2])

    def test_a_unicode_escape_in_an_identifier(self) -> None:
        """`\\u0063b` is the identifier `cb` (ES5 7.6), so `\\u0063b = 1`
        reassigns the parameter where no literal `cb` appears. Checked in
        a JS engine: `function f(cb){ \\u0063b = 42; return cb; }` returns
        42. Found by the muse review on PR #265."""
        self.assertBlocked(body("cb();", "\\u0063b = 1;"), [2], [3])

    def test_direct_eval(self) -> None:
        """`eval("cb = ...")` rebinds the parameter inside a masked
        string."""
        self.assertBlocked(body("eval(source);", "cb();"), [3], [2])

    def test_direct_eval_in_a_nested_function(self) -> None:
        """A closure's `eval` reaches the parameter as well."""
        self.assertBlocked(body("sub(function() { eval(source); });",
                                "cb();"), [3], [2])

    def test_with(self) -> None:
        """Inside `with (o)`, `cb` may be `o.cb`."""
        self.assertBlocked(body("with (o) {", "  cb();", "}"), [3], [2])


def build_probe_module(source: str) -> dict:
    """The `movian/_accessor_probe` record `build_commonjs_modules` makes."""
    modules = scan(source, lambda path: gen.build_commonjs_modules())
    return next(m for m in modules if m["name"] == "movian/_accessor_probe")


class MergedReceiverMembers(unittest.TestCase):
    """One module-level declaration describes every implementation of a
    receiver member, so it may call a slot an accessor only where every one
    of them does (Codex on PR #265)."""

    TWO = ("exports.A = function() {\n"
           "  this.__proto__ = sp;\n"
           "  this.m = function(cb) { cb(); };\n"
           "}\n"
           "exports.B = function() {\n"
           "  this.__proto__ = sp;\n"
           "  this.m = function(fn) { %s };\n"
           "}\n")

    def merged(self, module: dict) -> dict:
        return {m["name"]: m for m in module["receiverMembers"]}["m"]

    def own(self, module: dict, export: str) -> dict:
        record = {e["name"]: e for e in module["exports"]}[export]
        return {m["name"]: m for m in record["receiverMembers"]}["m"]

    def test_one_implementation_that_stores_withholds_it(self) -> None:
        module = build_probe_module(self.TWO % "this.handler = fn; fn();")
        self.assertNotIn("accessors", self.merged(module))
        # Each export's own interface still describes its own function.
        self.assertEqual(self.own(module, "A").get("accessors"), {"cb": [3]})

    def test_an_implementation_without_the_parameter_withholds_it(
            self) -> None:
        """It never uses the argument, so it takes anything."""
        module = build_probe_module(self.TWO.replace("function(fn)",
                                                     "function()")
                                    % "return 1;")
        self.assertNotIn("accessors", self.merged(module))

    def test_every_implementation_invoking_keeps_it(self) -> None:
        module = build_probe_module(self.TWO % "fn();")
        self.assertEqual(self.merged(module).get("accessors"), {"cb": [3]})


UNION = "Function | ((...args: any[]) => void)"


def emitted(record: dict, name: str = "cb") -> "gen.SlotType":
    """What `TypeScope` decides for `record`'s parameter `name`, in a module
    that declares the prototype shape `Item`."""
    record = {"name": "f", "params": [name],
              "source": {"file": "res/ecmascript/modules/movian/m.js",
                         "line": 1}, **record}
    modules = [{"name": "movian/m", "kind": "commonjs", "exports": [record],
                "shapes": [{"name": "Item", "kind": "prototype",
                            "methods": []}]}]
    global_names, by_module = gen.doc_type_scopes(modules)
    scope = gen.TypeScope(global_names | by_module.get("movian/m", set()),
                          by_module, gen._native_slot_types(modules))
    return scope.parameter(record, name, {"Item"})


class TheAccessorIsEmitted(unittest.TestCase):
    """`Function | ((...args: any[]) => void)`, with a signature the
    generator already emits in the arrow's place (ADR-0005, "What is
    emitted"). Measured under the pinned `tsc --strict`: `Function` alone
    fails an unannotated callback with TS7006, the arrow alone rejects a
    value typed `Function`, the union does neither and still rejects `42`."""

    def test_an_accessor_with_nothing_else_said(self) -> None:
        """The five `movian/settings` callbacks, `any` before."""
        slot = emitted({"accessors": {"cb": [2]}})
        self.assertEqual(slot, gen.SlotType(UNION))

    def test_the_call_site_shape_takes_the_arrows_place(self) -> None:
        """`page.Route`: what `new Page(...)` proves stays, names and return
        type included."""
        slot = emitted({"accessors": {"cb": [2]}, "callbackParam": "cb",
                        "callbackShape": "Item"})
        self.assertEqual(
            slot.type, "Function | ((value: Item, ...args: any[]) => any)")

    def test_an_annotated_signature_takes_the_arrows_place(self) -> None:
        """`subtitles.addProvider`."""
        slot = emitted({"accessors": {"cb": [2]},
                        "docParams": {"cb": "(req: any) => void"}})
        self.assertEqual(slot, gen.SlotType("Function | ((req: any) => void)"))

    def test_an_annotation_that_is_only_function_gets_the_arrow(self) -> None:
        """`Item.addOptAction`: `Function` has no call signature to keep, and
        emitted alone it fails an unannotated callback with TS7006."""
        slot = emitted({"accessors": {"cb": [2]},
                        "docParams": {"cb": "Function"}})
        self.assertEqual(slot, gen.SlotType(UNION))

    def test_a_union_is_not_taken_for_a_signature(self) -> None:
        """Beginning with a parenthesis does not make a type one function
        type. `((x: any) => void) | null` claims `null`, which an accessor
        invokes; the invocations win and the claim is reported lost."""
        slot = emitted({"accessors": {"cb": [2]},
                        "docParams": {"cb": "((x: any) => void) | null"}})
        self.assertEqual(slot.type, UNION)
        self.assertIn("invocation", slot.disagreement)

    def test_a_non_callable_annotation_loses_and_says_so(self) -> None:
        slot = emitted({"accessors": {"cb": [2]},
                        "docParams": {"cb": "string"}})
        self.assertEqual(slot.type, UNION)
        self.assertIn("@param {string}", slot.disagreement)


class TheContestedSlotIsUnchanged(unittest.TestCase):
    """It gains no type from its reads, and keeps what another route gave
    it -- widening a callback slot to `any` fails `--strict` (TS7006)."""

    CONTESTED = {"contested": {"cb": {"invocations": [3], "blocking": [2]}}}

    def test_it_keeps_the_call_site_shape(self) -> None:
        """`movian/http.request`, which tests `callback` at :110."""
        record = {**self.CONTESTED, "callbackParam": "cb",
                  "callbackShape": "Item", "callbackShapeIndex": 1,
                  "callbackShapeNullable": True}
        self.assertEqual(
            emitted(record).type,
            "(arg0: any, value: Item | null, ...args: any[]) => any")

    def test_with_nothing_else_it_stays_any(self) -> None:
        self.assertEqual(emitted(self.CONTESTED).type, "any")


class TheCorpusStillSaysSo(unittest.TestCase):
    """One accepted and one refused slot on today's tree, scanned fresh --
    the way ADR-0003's corpus test keeps its reason from rotting into a
    stale comment while the code diverges. Each anchor is checked to still
    hold the text it points at, so the pin moves with the source or fails.
    """

    @classmethod
    def setUpClass(cls) -> None:
        artifact = {"js": {"modules": gen.build_modules()}}
        cls.records = {(module, member): record for module, member, record
                       in gen._commonjs_callables(artifact)}
        cls.census = {(site["module"], site["member"], site["slot"]): site
                      for site in gen._doc_type_census(artifact)}

    def assertAnchored(self, path: str, lines: list[int], text: str) -> None:
        source = (MODULES / path).read_text(encoding="utf-8").splitlines()
        for line in lines:
            self.assertIn(text, source[line - 1], "%s:%d" % (path, line))

    def test_settings_createBool_is_accepted(self) -> None:
        record = self.records[("movian/settings", "sp.createBool")]
        self.assertEqual(record.get("accessors"), {"callback": [83, 89]})
        self.assertNotIn("contested", record)
        self.assertAnchored("movian/settings.js", [83, 89], "callback(")
        self.assertEqual(
            self.census[("movian/settings", "sp.createBool", "callback")]
            ["type"], UNION)

    def test_http_request_is_refused_and_keeps_its_signature(self) -> None:
        record = self.records[("movian/http", "request")]
        self.assertNotIn("accessors", record)
        self.assertEqual(record.get("contested"), {
            "callback": {"invocations": [114, 116], "blocking": [110]}})
        self.assertAnchored("movian/http.js", [110], "if(callback)")
        self.assertAnchored("movian/http.js", [114, 116], "callback(")
        self.assertEqual(
            self.census[("movian/http", "request", "callback")]["type"],
            "(arg0: any, value: HttpResponse | null, ...args: any[]) => any")


if __name__ == "__main__":
    unittest.main()
