#!/usr/bin/env python3
"""One decision about a slot's type, asked by both the renderer and the census.

The four functions that decided what a declaration says were defined INSIDE
`render_dts` and were not module attributes, so nothing could call them --
not a test, and not the census. The census therefore re-derived the same
answer from the same inputs, and disagreed with the emitted file three times
in one change: a `*` annotation rendered `any` and was counted typed; a proved
shape the module cannot spell rendered `any` and was counted typed; a
documented method parameter rendered `any` while the census counted it typed.

Each was fixed pointwise. This file pins the shape that makes them
unrepeatable: the type of a slot is decided once, behind `TypeScope`, and both
consumers cross that seam.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from support.devtools.metadata import gen  # noqa: E402


def scope_for(artifact, module_name="movian/m"):
    modules = artifact["js"]["modules"]
    global_names, by_module = gen.doc_type_scopes(modules)
    return gen.TypeScope(
        visible=global_names | by_module.get(module_name, set()),
        declared_by_module=by_module,
        native_slots=gen._native_slot_types(modules))


class TheRendererHasAnInterface(unittest.TestCase):
    """Reachability. Every one of these was private to `render_dts`."""

    def test_the_record_and_scope_cannot_be_omitted(self) -> None:
        """`member_signature` docstring says `export` and `scope` are
        required rather than defaulted -- that is the regression that cost 42
        emitted-`any` method parameters, because a defaulted argument let
        every method site silently omit the record.

        Prose is not a guard. Re-adding `= None` keeps a `hasattr` test green,
        so the signature itself is pinned.
        """
        import inspect
        parameters = inspect.signature(gen.member_signature).parameters
        for name in ("member", "export", "shape_names", "scope"):
            with self.subTest(name):
                self.assertIs(parameters[name].default,
                              inspect.Parameter.empty,
                              "%s gained a default; a call site can omit it "
                              "again" % name)

    def test_the_rendering_vocabulary_is_callable(self) -> None:
        for name in ("native_signature", "params_signature",
                     "member_signature", "member_return_type", "TypeScope"):
            with self.subTest(name):
                self.assertTrue(
                    hasattr(gen, name),
                    "%s is not reachable outside render_dts" % name)


class OneDecision(unittest.TestCase):
    """`TypeScope` answers, and answers the same thing to everybody."""

    def artifact(self, export):
        return {"js": {"modules": [
            {"name": "movian/m", "kind": "commonjs",
             "shapes": [{"name": "Item", "kind": "prototype", "methods": []}],
             "exports": [dict(export, source={
                 "file": "res/ecmascript/modules/movian/m.js", "line": 1})]},
            {"name": "native/string", "kind": "native", "functions": [
                {"name": "utf8FromBytes", "nargs": 2, "params": [
                    {"type": "DuktapeBuffer"}, {}]}]}]}}

    def test_a_documented_parameter_and_its_reason(self) -> None:
        artifact = self.artifact(
            {"name": "f", "params": ["a"], "docParams": {"a": "string"}})
        scope = scope_for(artifact)
        export = artifact["js"]["modules"][0]["exports"][0]
        slot = scope.parameter(export, "a")
        self.assertEqual(slot.type, "string")
        self.assertIsNone(slot.reason)

    def test_a_wildcard_renders_any_and_says_why(self) -> None:
        """`*` is Closure's any. The renderer emits `any`; the census must be
        told that, not left to conclude `typed` from the annotation."""
        artifact = self.artifact(
            {"name": "f", "params": ["a"], "docParams": {"a": "*"}})
        slot = scope_for(artifact).parameter(
            artifact["js"]["modules"][0]["exports"][0], "a")
        rendered, reason = slot.type, slot.reason
        self.assertEqual(rendered, "any")
        self.assertIsNotNone(reason)
        self.assertIn("Closure", reason)

    def test_a_type_the_module_cannot_write_renders_any(self) -> None:
        artifact = self.artifact(
            {"name": "f", "params": ["a"], "docParams": {"a": "HttpResponse"}})
        slot = scope_for(artifact).parameter(
            artifact["js"]["modules"][0]["exports"][0], "a")
        rendered, reason = slot.type, slot.reason
        self.assertEqual(rendered, "any")
        self.assertIn("this module declares", reason)

    def test_the_native_ceiling_is_part_of_the_same_decision(self) -> None:
        artifact = self.artifact(
            {"name": "f", "params": ["enc"], "docParams": {"enc": "string"},
             "forwardsTo": {"enc": ["native/string", "utf8FromBytes", 1]}})
        slot = scope_for(artifact).parameter(
            artifact["js"]["modules"][0]["exports"][0], "enc")
        rendered, reason = slot.type, slot.reason
        self.assertEqual(rendered, "any")
        self.assertIn("more than one way", reason)

    def test_a_proved_return_beats_the_annotation(self) -> None:
        artifact = self.artifact(
            {"name": "f", "params": [], "returns": "Item",
             "docReturns": "HttpResponse"})
        slot = scope_for(artifact).returns(
            artifact["js"]["modules"][0]["exports"][0], {"Item"})
        rendered, reason = slot.type, slot.reason
        self.assertEqual(rendered, "Item")
        self.assertIsNone(reason)

    def test_a_shared_shape_is_not_a_name_a_return_may_use(self) -> None:
        """The census must resolve against the RENDERER's shape set.

        `doc_type_scopes` collects every shape plus the native options
        interfaces; the renderer's return types resolve against PROTOTYPE
        shapes only. A census using the wider set reports `typed: Sp` for a
        shared shape while the emitted file says `any` -- the same defect as
        the three already fixed, reached only by a shared shape, which the
        corpus does not currently return.
        """
        artifact = {"js": {"modules": [{
            "name": "movian/m", "kind": "commonjs",
            "shapes": [{"name": "Sp", "kind": "shared", "methods": []}],
            "exports": [{"name": "f", "params": [], "returns": "Sp",
                         "receiverMutation": True,
                         "source": {"file": "res/ecmascript/modules/"
                                            "movian/m.js", "line": 1}}]}]}}
        _global, by_module = gen.doc_type_scopes(artifact["js"]["modules"])
        self.assertIn("Sp", by_module["movian/m"])   # the wider set has it
        site = [s for s in gen._doc_type_census(artifact)
                if s["kind"] == "return"][0]
        self.assertEqual(site["status"], "any")
        self.assertIn("does not declare", site["reason"])

    def test_a_proved_return_the_module_cannot_spell_renders_any(self) -> None:
        artifact = self.artifact(
            {"name": "f", "params": [], "returns": "Foo"})
        slot = scope_for(artifact).returns(
            artifact["js"]["modules"][0]["exports"][0], set())
        rendered, reason = slot.type, slot.reason
        self.assertEqual(rendered, "any")
        self.assertIn("does not declare", reason)


class EvidenceOutranksTheAnnotation(unittest.TestCase):
    """A callback signature read off the CALL SITE is evidence.

    `movian/http` calls `callback(null, new HttpResponse(res))`, and that is
    what the parameter must accept, so it outranks a `@param` the way a
    proved return outranks a `@returns`. The renderer already behaved this
    way; the census reported the annotation, so three slots were counted as
    one type while the file emitted another.
    """

    def artifact(self, doc):
        export = {"name": "f", "params": ["cb"], "callbackParam": "cb",
                  "callbackShape": "Item", "callbackShapeIndex": 1,
                  "callbackShapeNullable": True,
                  "source": {"file": "res/ecmascript/modules/movian/m.js",
                             "line": 1}}
        if doc is not None:
            export["docParams"] = {"cb": doc}
        return {"js": {"modules": [{
            "name": "movian/m", "kind": "commonjs",
            "shapes": [{"name": "Item", "kind": "prototype", "methods": []}],
            "exports": [export]}]}}

    def test_the_call_site_wins_and_the_loss_is_reported(self) -> None:
        artifact = self.artifact("(err: any) => void")
        site = [s for s in gen._doc_type_census(artifact)
                if s["slot"] == "cb"][0]
        self.assertEqual(site["status"], "typed")
        self.assertEqual(
            site["type"],
            "(arg0: any, value: Item | null, ...args: any[]) => any")
        self.assertIn("the call site wins", site["disagreement"])

    def test_an_agreeing_annotation_reports_no_loss(self) -> None:
        """An annotation that already says what the call site proves has not
        been overruled. Reporting it prints two identical types."""
        spelled = "(arg0: any, value: Item | null, ...args: any[]) => any"
        site = [s for s in gen._doc_type_census(self.artifact(spelled))
                if s["slot"] == "cb"][0]
        self.assertEqual(site["type"], spelled)
        self.assertNotIn("disagreement", site)
        # Spacing is not a contradiction either -- the two sides are written
        # by different code.
        site = [s for s in gen._doc_type_census(self.artifact(
            "(arg0:any, value:Item|null, ...args:any[])=>any"))
                if s["slot"] == "cb"][0]
        self.assertNotIn("disagreement", site)

    def test_an_undocumented_callback_reports_no_loss(self) -> None:
        """Nothing was overruled, so nothing is reported -- the noise cut
        this report has needed three times already."""
        site = [s for s in gen._doc_type_census(self.artifact(None))
                if s["slot"] == "cb"][0]
        self.assertEqual(site["status"], "typed")
        self.assertNotIn("disagreement", site)


class TheTwoConsumersAgree(unittest.TestCase):
    """The differential that makes the seam worth having.

    Not "both are correct" -- "both are the same answer". A census that
    computes its own verdict can drift from the file; one that asks cannot.
    """

    def setUp(self) -> None:
        artifact = REPO_ROOT / "generated" / "movian-metadata.json"
        dts = REPO_ROOT / "generated" / "movian-api.d.ts"
        if not artifact.is_file() or not dts.is_file():
            self.skipTest("generated/ is absent")
        self.artifact = json.loads(artifact.read_text())
        self.dts = dts.read_text()

    def declarations(self):
        """`{(module, callable name): [declaration text, ...]}` from the
        emitted file.

        Scoped per declaration on purpose. Searching the whole file for
        `url?: string` finds it 21 times, so flipping one declaration to
        `any` left the pin green -- a check passing because something ELSE
        satisfied it.

        A first cut then indexed only lines carrying their own name, and a
        constructor is not written that way:

            const Route: {
              new (re?: string, callback?: ...): Route;
            };

        The outer line has no parentheses and the inner one has no name, so
        36 of 251 census sites -- 14 of them TYPED parameters -- were indexed
        nowhere and skipped by `if not lines`. Nothing satisfied those pins;
        they simply were not made. The enclosing `const` name is carried down
        so the call signatures inside land under it.
        """
        found: dict[tuple[str, str], list[str]] = {}
        module = None
        const = None
        for raw in self.dts.splitlines():
            line = raw.strip()
            head = re.match(r"declare module '([^']+)'", line)
            if head is not None:
                module, const = head.group(1), None
                continue
            if module is None:
                continue
            opening = re.match(r"(?:export\s+)?const\s+([A-Za-z_0-9]+)\s*:"
                               r"\s*\{\s*$", line)
            if opening is not None:
                const = opening.group(1)
                continue
            if const is not None and line.startswith("}"):
                const = None
                continue
            call = re.search(
                r"(?:function\s+|new\s*|^)([A-Za-z_0-9]*)\s*\([^;]*\)\s*:",
                line)
            if call is None:
                continue
            name = call.group(1) or const
            if name is None:
                continue
            found.setdefault((module, name), []).append(line)
        return found

    def emitted_for(self, site, declarations):
        """Every declaration the emitted file has for this census site.

        The census names a member by its path -- `create`, `Page.appendItem`,
        `exports.w3cwebsocket.send` -- and the file declares it under the
        last component.
        """
        return declarations.get(
            (site["module"], site["member"].split(".")[-1]), [])

    def matching(self, lines, slot, spelled):
        """Whether EVERY declaration that mentions this slot spells it this
        way.

        `any(...)` over the bucket was the remaining hole: a name can carry
        more than one declaration -- an overload, or a receiver member
        emitted both hoisted and inside its export's interface, which really
        do render from different records -- and one of them satisfying the
        pin let the other be wrong. Only declarations that actually mention
        the slot are judged; the rest are a different member's signature.
        """
        mentioning = [line for line in lines if ("%s?:" % slot) in line]
        if not mentioning:
            return False
        return all(("%s?: %s" % (slot, spelled)) in line
                   for line in mentioning)

    def test_every_typed_parameter_appears_in_its_own_declaration(self) -> None:
        declarations = self.declarations()
        checked = 0
        for site in gen._doc_type_census(self.artifact):
            if site["kind"] != "parameter" or site["status"] != "typed":
                continue
            if site["slot"] == "...args":
                continue
            lines = self.emitted_for(site, declarations)
            checked += 1
            with self.subTest("%s.%s" % (site["member"], site["slot"])):
                self.assertTrue(lines, "no declaration indexed for %s.%s"
                                % (site["module"], site["member"]))
                self.assertTrue(
                    self.matching(lines, site["slot"], site["type"]),
                    "%s not spelled %s in %s"
                    % (site["slot"], site["type"], lines))
        # Every typed parameter is checked -- there is no `continue` left to
        # skip one. Measured, not a target: re-measure before changing it.
        self.assertEqual(checked, 87)

    def test_every_typed_return_appears_in_its_own_declaration(self) -> None:
        declarations = self.declarations()
        checked = 0
        for site in gen._doc_type_census(self.artifact):
            if site["kind"] != "return" or site["status"] != "typed":
                continue
            lines = self.emitted_for(site, declarations)
            checked += 1
            with self.subTest("%s.(return)" % site["member"]):
                self.assertTrue(lines, "no declaration indexed for %s.%s"
                                % (site["module"], site["member"]))
                # One declaration must return it, and any OTHER may only
                # be the `voidWhen` overload -- `movian/http.request` emits
                # the synchronous form and a callback form returning `void`,
                # and the census models one return per callable. Anything
                # else in the bucket is a real divergence, so `all` would be
                # wrong and `any` would be vacuous.
                self.assertTrue(
                    any(line.rstrip().endswith("): %s;" % site["type"])
                        for line in lines),
                    "%s returned by none of %s" % (site["type"], lines))
                self.assertTrue(
                    all(line.rstrip().endswith("): %s;" % site["type"])
                        or line.rstrip().endswith("): void;")
                        for line in lines),
                    "%s has a third return form: %s"
                    % (site["member"], lines))
        self.assertEqual(checked, 19)

    def test_no_parameter_counted_typed_renders_any(self) -> None:
        """The exact defect, as a property over the whole corpus."""
        for site in gen._doc_type_census(self.artifact):
            if site["kind"] == "parameter" and site["status"] == "typed":
                with self.subTest("%s.%s" % (site["member"], site["slot"])):
                    self.assertNotEqual(site.get("type"), "any")


if __name__ == "__main__":
    unittest.main(verbosity=2)
