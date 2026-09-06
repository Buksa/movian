#!/usr/bin/env python3
"""Behavior tests for the runtime oracle's freshness stamp and coverage
floor (movian#166).

Why these need a unit test. Both mechanisms are guards, and a guard's whole
value is in the runs it refuses -- none of which happens on a healthy tree,
which is the only tree the gate ever sees. `gen.py --check` passing proves
the stamp accepts the sources it was captured from; it cannot prove the stamp
would have rejected anything else, and a stamp that accepts everything passes
that check exactly as well as a correct one. That is not hypothetical: before
this work the cross-check's best possible score was achieved by an EMPTY
oracle -- `match 0, oracle-unreachable 273, exit 0` -- because nothing
required it to have seen anything.

So the cases below are almost all negative. They pin what the stamp must
refuse and, just as importantly, what it must NOT refuse: a stamp that goes
red on a reindent or a comment makes a recapture -- a built Movian, a run, a
live route -- the price of editing a docblock, and the first person to pay it
twice will delete the check. #212 added 231 lines of JSDoc across 16 of the
20 CommonJS modules it covers and moved no member; under a byte stamp that
commit alone would have demanded one.

The stripper's error directions are not symmetric and the tests are weighted
to match. Keeping a comment in the hash costs a needless recapture. DROPPING
real code -- which is what mistaking a `//` inside a regex or a string for a
comment does -- makes the stamp agree with a tree it was not captured from,
and that failure is silent and green.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
GEN_PY = REPO_ROOT / "support" / "devtools" / "metadata" / "gen.py"

_spec = importlib.util.spec_from_file_location("movian_metadata_gen", GEN_PY)
assert _spec and _spec.loader
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


class CommentStripping(unittest.TestCase):
    """What the stamp is allowed to ignore."""

    def test_line_comment_ignored(self):
        self.assertEqual(gen._js_hash_text("var a = 1; // why"),
                         gen._js_hash_text("var a = 1;"))

    def test_block_comment_ignored(self):
        self.assertEqual(
            gen._js_hash_text("/** doc */\nvar a = 1;"),
            gen._js_hash_text("var a = 1;"))

    def test_indentation_ignored(self):
        self.assertEqual(gen._js_hash_text("if (a) {\n    b();\n}"),
                         gen._js_hash_text("if (a) {\n  b();\n}"))

    def test_blank_lines_ignored(self):
        self.assertEqual(gen._js_hash_text("var a = 1;\n\n\nvar b = 2;"),
                         gen._js_hash_text("var a = 1;\nvar b = 2;"))


class CommentStrippingRefusals(unittest.TestCase):
    """What it must not ignore. Each of these hides a member if it slips."""

    def test_added_member_changes_the_hash(self):
        self.assertNotEqual(
            gen._js_hash_text("exports.a = 1;"),
            gen._js_hash_text("exports.a = 1;\nexports.b = 2;"))

    def test_newline_kept_because_asi_reads_it(self):
        # `return\n x` returns undefined; `return x` does not. Collapsing the
        # newline would hash two different programs the same.
        self.assertNotEqual(gen._js_hash_text("return\n  x"),
                            gen._js_hash_text("return x"))

    def test_block_comment_does_not_join_tokens(self):
        self.assertNotEqual(gen._js_hash_text("a/*x*/b"),
                            gen._js_hash_text("ab"))

    def test_double_slash_inside_a_string_is_not_a_comment(self):
        kept = gen._js_hash_text("var s = '// text'; var y = 2;")
        self.assertIn("var y = 2;", kept)

    def test_double_slash_inside_a_regex_is_not_a_comment(self):
        kept = gen._js_hash_text(r"var re = /https?:\/\//; var y = 2;")
        self.assertIn("var y = 2;", kept)

    def test_comment_opener_inside_a_regex_class_is_not_a_comment(self):
        kept = gen._js_hash_text("if (a) return /x[/*]y/.test(b);\nvar y = 2;")
        self.assertIn("var y = 2;", kept)

    def test_regex_directly_after_a_condition_is_not_division(self):
        # `if (c) /re/.test(s)` with no `return` between them. Reading the
        # `/` as division lets the `/*` in the character class open a comment
        # that runs to the next `*/` -- here, past the end of the file. The
        # first version of this scanner did exactly that and swallowed
        # everything after the condition, which is a hash that agrees with a
        # tree it never saw.
        kept = gen._js_hash_text(
            "if (condition) /x[/*]y/.test(value);\nexports.stillHere = 1;")
        self.assertIn("exports.stillHere", kept)

    def test_division_after_a_call_is_still_division(self):
        # The other half of the same rule: `)` that closes a CALL is followed
        # by division, and reading it as a regex would swallow the statement.
        kept = gen._js_hash_text("var q = f(a) / b / c;\nvar y = 2;")
        self.assertIn("var y = 2;", kept)

    def test_regex_after_a_labelled_jump(self):
        # `break outer` puts a LABEL where the keyword was, so the word
        # immediately before the regex is not the one on the allow-list.
        for keyword in ("break", "continue"):
            kept = gen._js_hash_text(
                "%s outer\n/x[/*]y/.test(v);\nexports.stillHere = 1;"
                % keyword)
            self.assertIn("exports.stillHere", kept, keyword)

    def test_unicode_line_terminators_end_a_line_comment(self):
        # Duktape treats U+2028 and U+2029 as line terminators
        # (duktape.c:10493-10494), so the code after one is live and must
        # not be read as part of the comment before it.
        # CR belongs in the same set on its own: a CR-only source is valid
        # JavaScript, not a stray Windows artefact.
        for separator in ("\r", "\u2028", "\u2029"):
            kept = gen._js_hash_text("// note%sexports.a = 1;" % separator)
            self.assertIn("exports.a", kept, repr(separator))

    def test_unicode_line_terminators_separate_statements(self):
        for separator in ("\r", "\u2028", "\u2029"):
            self.assertNotEqual(
                gen._js_hash_text("// n%sexports.a = 1;" % separator),
                gen._js_hash_text("// n%sexports.b = 1;" % separator))

    def test_crlf_hashes_the_same_as_lf(self):
        # The other direction: CR being a terminator must not make a file
        # with Windows endings look like a different program.
        self.assertEqual(gen._js_hash_text("var a = 1;\r\nvar b = 2;"),
                         gen._js_hash_text("var a = 1;\nvar b = 2;"))

    def test_whitespace_inside_a_string_is_content(self):
        # `exports["a  b"]` and `exports["a b"]` declare different members.
        self.assertNotEqual(gen._js_hash_text('exports["a  b"] = 1;'),
                            gen._js_hash_text('exports["a b"] = 1;'))

    def test_regex_after_an_asi_terminated_keyword(self):
        # ASI ends `break`, `continue` and `debugger` at the newline, so what
        # follows is a fresh statement and may open with a regex. Reading
        # that `/` as division lets `/*` in the character class comment out
        # the rest of the file.
        for keyword in ("break", "continue", "debugger"):
            kept = gen._js_hash_text(
                "%s\n/x[/*]y/.test(v);\nexports.stillHere = 1;" % keyword)
            self.assertIn("exports.stillHere", kept, keyword)

    def test_whitespace_inside_a_regex_is_content(self):
        # An introspector regex can change what it matches by a space alone.
        self.assertNotEqual(gen._js_hash_text("var r = /a  b/;"),
                            gen._js_hash_text("var r = /a b/;"))

    def test_division_is_not_read_as_a_regex(self):
        # `a / b / c` after an identifier is division; reading the span as a
        # regex would swallow the `;` and everything to the next `/`.
        kept = gen._js_hash_text("var q = a / b / c;\nvar y = 2;")
        self.assertIn("var y = 2;", kept)


class StripperOverTheRealInputs(unittest.TestCase):
    """The corpus the stamp is actually taken over, checked for the one
    failure that is silent: text the stripper invented or dropped."""

    def _inputs(self):
        paths = []
        for pattern in gen.RUNTIME_ORACLE_INPUT_GLOBS:
            paths.extend(path for path in sorted(REPO_ROOT.glob(pattern))
                         if gen._is_oracle_input(path))
        return paths

    def test_every_input_is_covered(self):
        # 20 CommonJS modules, the 26 C files the natives are registered in,
        # and the introspector. The C half matters: 18 of the 52 modules the
        # oracle observes are `native/*`, so a stamp over the JS alone
        # guards half of what the cross-check covers.
        self.assertEqual(len(self._inputs()), 49)

    def test_the_apiversion_1_bootstrap_is_stamped(self):
        # The introspector's manifest selects apiversion 1, so the legacy
        # bootstrap runs in its context before it and anything the bootstrap
        # adds to a cached module is surface the capture sees. The manifest
        # is stamped too, because it is what selects the bootstrap.
        stamped = gen.runtime_oracle_input_digests()
        for name in ("res/ecmascript/legacy/api-v1.js",
                     "support/devtools/api-introspector/plugin.json"):
            self.assertIn(name, stamped)

    def test_every_loadable_plugin_file_is_stamped(self):
        # es_modsearch() tries the plugin directory before the core module
        # tree, so anything loadable there can shadow a core module.
        stamped = set(gen.runtime_oracle_input_digests())
        for path in sorted(gen.INTROSPECTOR_DIR.rglob("*")):
            if path.suffix not in (".js", ".json") or not path.is_file():
                continue
            name = path.relative_to(REPO_ROOT).as_posix()
            if path.name == gen.RUNTIME_ORACLE_PATH.name:
                self.assertNotIn(name, stamped)
            else:
                self.assertIn(name, stamped)

    def test_a_new_plugin_module_enters_the_stamp(self):
        # The previous test only walks files that exist, and today the
        # plugin directory holds exactly the two the glob used to name --
        # so it passes whether the glob is a pattern or a list. This one
        # creates the third file, which is the case the pattern is for:
        # a url.js here shadows the core module for the introspector's own
        # require(), and the stamp has to notice it arriving.
        shadow = gen.INTROSPECTOR_DIR / "url.js"
        name = shadow.relative_to(REPO_ROOT).as_posix()
        self.assertNotIn(name, gen.runtime_oracle_input_digests())
        try:
            shadow.write_text("exports.format = function(){};\n",
                              encoding="utf-8")
            self.assertIn(name, gen.runtime_oracle_input_digests())
        finally:
            shadow.unlink(missing_ok=True)

    def test_the_oracle_is_not_stamped_into_its_own_stamp(self):
        # It sits in the plugin directory and matches the json glob. Writing
        # the file changes the digest that was just written, so including it
        # could never converge.
        self.assertNotIn(
            gen.RUNTIME_ORACLE_PATH.relative_to(REPO_ROOT).as_posix(),
            gen.runtime_oracle_input_digests())

    def test_compiled_and_runtime_inputs_are_disjoint_and_complete(self):
        compiled = {path.relative_to(REPO_ROOT).as_posix()
                    for pattern in gen.RUNTIME_ORACLE_COMPILED_GLOBS
                    for path in REPO_ROOT.glob(pattern)}
        runtime = {path.relative_to(REPO_ROOT).as_posix()
                   for pattern in gen.RUNTIME_ORACLE_RUNTIME_GLOBS
                   for path in REPO_ROOT.glob(pattern)
                   if gen._is_oracle_input(path)}
        self.assertEqual(compiled & runtime, set())
        self.assertEqual(compiled | runtime,
                         set(gen.runtime_oracle_input_digests()))

    def test_the_native_sources_are_stamped(self):
        stamped = gen.runtime_oracle_input_digests()
        registering = {
            path.relative_to(REPO_ROOT).as_posix()
            for path in (REPO_ROOT / "src" / "ecmascript").rglob("*.c")
            if "duk_function_list_entry" in path.read_text(
                encoding="utf-8", errors="replace")}
        self.assertTrue(registering)
        self.assertTrue(registering <= set(stamped),
                        sorted(registering - set(stamped)))

    def test_c_sources_lose_no_code(self):
        # C has no regex literals, so the regex heuristic is the one part of
        # the scanner that could misfire on it. Checked against a stripper
        # that knows only strings and comments: any disagreement is the
        # heuristic eating C.
        def plain(source):
            out, index, length = [], 0, len(source)
            while index < length:
                char = source[index]
                if char in "\"'":
                    cursor = index + 1
                    while cursor < length:
                        if source[cursor] == "\\":
                            cursor += 2
                            continue
                        if source[cursor] == char:
                            cursor += 1
                            break
                        cursor += 1
                    out.append(source[index:cursor])
                    index = cursor
                    continue
                if source.startswith("//", index):
                    stop = source.find("\n", index)
                    index = length if stop < 0 else stop
                    out.append(" ")
                    continue
                if source.startswith("/*", index):
                    stop = source.find("*/", index + 2)
                    index = length if stop < 0 else stop + 2
                    out.append(" ")
                    continue
                out.append(char)
                index += 1
            return " ".join("".join(out).split())

        checked = 0
        for path in sorted((REPO_ROOT / "src" / "ecmascript").rglob("*.c")):
            source = path.read_text(encoding="utf-8", errors="replace")
            self.assertEqual(
                " ".join(gen._js_code_only(source).split()),
                plain(source), str(path))
            checked += 1
        self.assertGreater(checked, 20)

    def test_output_is_a_subsequence_of_the_input(self):
        for path in self._inputs():
            source = path.read_text(encoding="utf-8")
            cursor = iter(source)
            stripped = gen._js_code_only(source)
            for char in stripped:
                if char.isspace():
                    continue
                self.assertTrue(
                    any(char == seen for seen in cursor),
                    "%s: stripper emitted %r out of order" % (path, char))

    def test_stripping_is_idempotent(self):
        for path in self._inputs():
            once = gen._js_code_only(path.read_text(encoding="utf-8"))
            self.assertEqual(gen._js_code_only(once), once, str(path))


class Freshness(unittest.TestCase):
    def setUp(self):
        self.current = gen.runtime_oracle_input_digests()

    def test_the_committed_oracle_is_stamped_against_this_tree(self):
        import json
        stamp = json.loads(
            gen.RUNTIME_ORACLE_PATH.read_text(encoding="utf-8")).get("inputs")
        self.assertIsInstance(stamp, dict)
        self.assertEqual(stamp.get("version"),
                         gen.RUNTIME_ORACLE_INPUTS_VERSION)
        self.assertEqual(gen.runtime_oracle_stale_inputs(stamp["files"]), [])

    def test_changed_file_is_named(self):
        stamped = dict(self.current)
        victim = sorted(stamped)[0]
        stamped[victim] = "0" * 64
        self.assertEqual(gen.runtime_oracle_stale_inputs(stamped),
                         ["code changed since the capture: %s" % victim])

    def test_removed_file_is_named(self):
        stamped = dict(self.current)
        victim = sorted(stamped)[0]
        del stamped[victim]
        self.assertEqual(gen.runtime_oracle_stale_inputs(stamped),
                         ["added since the capture: %s" % victim])

    def test_extra_file_is_named(self):
        stamped = dict(self.current)
        stamped["res/ecmascript/modules/ghost.js"] = "0" * 64
        self.assertEqual(
            gen.runtime_oracle_stale_inputs(stamped),
            ["gone since the capture: res/ecmascript/modules/ghost.js"])

    def test_missing_stamp_is_not_silently_accepted(self):
        self.assertTrue(gen.runtime_oracle_stale_inputs(None))

    def test_digest_folds_every_file(self):
        first = gen.runtime_oracle_inputs_digest(self.current)
        moved = dict(self.current)
        moved[sorted(moved)[-1]] = "0" * 64
        self.assertNotEqual(first, gen.runtime_oracle_inputs_digest(moved))


class Adoption(unittest.TestCase):
    """Moving the stamp takes a run, not an edit."""

    def test_the_committed_oracle_records_its_capture(self):
        import json
        oracle = json.loads(
            gen.RUNTIME_ORACLE_PATH.read_text(encoding="utf-8"))
        self.assertIsInstance(oracle.get("capturedAt"), (int, float))

    def test_re_adopting_the_committed_oracle_is_refused(self):
        import json
        import subprocess
        import tempfile
        before = gen.RUNTIME_ORACLE_PATH.read_bytes()
        with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
            handle.write(json.dumps(json.loads(before)))
            handle.flush()
            result = subprocess.run(
                ["python3", str(GEN_PY), "--adopt-oracle", handle.name],
                capture_output=True, text=True, cwd=str(REPO_ROOT))
        try:
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("same capturedAt", result.stderr)
            # The refusal must come before the write.
            self.assertEqual(gen.RUNTIME_ORACLE_PATH.read_bytes(), before)
        finally:
            gen.RUNTIME_ORACLE_PATH.write_bytes(before)

    def _build_revision(self):
        """The commit the committed oracle was captured from, skipping if
        this clone does not have it.

        A shallow clone is the normal state of a fresh `git clone --depth`,
        and refusing to run there would fail a correct tree. CI fetches this
        one commit explicitly so the skip is unreachable there -- if it ever
        becomes reachable in CI, the fetch step failed and the run is red for
        that instead.
        """
        import json
        import subprocess
        oracle = json.loads(
            gen.RUNTIME_ORACLE_PATH.read_text(encoding="utf-8"))
        version = oracle.get("movianVersion")
        self.assertIsInstance(version, str)
        revision = version.rsplit(".g", 1)[-1]
        present = subprocess.run(
            ["git", "cat-file", "-e", revision + "^{commit}"],
            cwd=str(REPO_ROOT), capture_output=True)
        if present.returncode != 0:
            self.skipTest(
                "shallow clone: build %s not present" % revision)
        return version

    def test_the_committed_oracle_records_its_build(self):
        self.assertEqual(
            gen.runtime_oracle_build_mismatch(self._build_revision()), [])

    def test_a_capture_with_no_build_is_refused(self):
        self.assertTrue(gen.runtime_oracle_build_mismatch(None))
        self.assertTrue(gen.runtime_oracle_build_mismatch(""))

    def test_a_build_this_repository_does_not_have_is_refused(self):
        # `mdev run` launches whatever binary is in build.debug; it does not
        # rebuild. A capture that cannot be traced back to a commit here
        # cannot be shown to describe these sources.
        reasons = gen.runtime_oracle_build_mismatch("5.0.1017.gdeadbee")
        self.assertTrue(reasons)
        self.assertIn("deadbee", reasons[0])

    def test_a_build_predating_a_c_change_is_refused(self):
        # The case the C half of the stamp exists for: the sources moved,
        # the binary did not, and the capture reports the old surface.
        version = self._build_revision()
        victim = REPO_ROOT / "src" / "ecmascript" / "es_fs.c"
        original = victim.read_text(encoding="utf-8")
        try:
            victim.write_text(
                original + "\nstatic int probe_added(void) { return 1; }\n",
                encoding="utf-8")
            reasons = gen.runtime_oracle_build_mismatch(version)
            self.assertTrue(reasons)
            self.assertIn("es_fs.c", reasons[0])
        finally:
            victim.write_text(original, encoding="utf-8")

    def test_a_module_change_needs_no_rebuild(self):
        # The other half, and the reason only the compiled inputs are
        # checked: modules reach the runtime through dataroot:// and are read
        # from disk, so an unchanged binary answers correctly for an edited
        # module. Making that cost a rebuild would be the "too much" failure
        # in a different place.
        version = self._build_revision()
        victim = (REPO_ROOT / "res" / "ecmascript" / "modules"
                  / "movian" / "sqlite.js")
        original = victim.read_text(encoding="utf-8")
        try:
            victim.write_text(
                original + "\nexports.DB.prototype.probeNotFirst ="
                           " function(a){};\n",
                encoding="utf-8")
            self.assertEqual(
                gen.runtime_oracle_build_mismatch(version), [])
        finally:
            victim.write_text(original, encoding="utf-8")

    def test_the_committed_oracle_read_this_tree(self):
        import json
        oracle = json.loads(
            gen.RUNTIME_ORACLE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            gen.runtime_oracle_read_mismatch(
                oracle.get("runtimeInputs"),
                oracle.get("runtimeInputsError")),
            [])

    def test_a_capture_that_could_not_read_is_refused(self):
        # A plugin's fs access is ACL-limited to its own directory, so
        # without --bypass-ecmascript-acl the run cannot enumerate the core
        # modules. Recording that is the point: a partial map would have
        # looked like a successful capture.
        self.assertTrue(gen.runtime_oracle_read_mismatch(None, "denied"))
        self.assertTrue(gen.runtime_oracle_read_mismatch(None, None))
        self.assertTrue(gen.runtime_oracle_read_mismatch({}, None))

    def test_a_file_edited_after_the_capture_is_named(self):
        import json
        recorded = dict(json.loads(
            gen.RUNTIME_ORACLE_PATH.read_text())["runtimeInputs"])
        victim = "res/ecmascript/modules/movian/sqlite.js"
        recorded[victim] = "0" * 64
        reasons = gen.runtime_oracle_read_mismatch(recorded, None)
        self.assertEqual(
            reasons, ["%s differs from the copy the capture read" % victim])

    def test_a_file_the_capture_never_read_is_named(self):
        # The shadowing case: a module dropped into the plugin directory
        # after the run would be loaded by a fresh one and was not by this.
        import json
        recorded = dict(json.loads(
            gen.RUNTIME_ORACLE_PATH.read_text())["runtimeInputs"])
        shadow = gen.INTROSPECTOR_DIR / "url.js"
        try:
            shadow.write_text("exports.format = function(){};\n",
                              encoding="utf-8")
            reasons = gen.runtime_oracle_read_mismatch(recorded, None)
            self.assertTrue(
                any("api-introspector/url.js is here and the capture never"
                    in reason for reason in reasons), reasons)
        finally:
            shadow.unlink(missing_ok=True)

    def test_a_capture_the_acl_blocked_names_the_flag(self):
        """The capture that read NOTHING is not the capture that read a
        DIFFERENT tree, and only one of them is fixed by recapturing the
        same way.

        `movian` without `--bypass-ecmascript-acl` cannot list
        `dataroot://res/ecmascript/modules`, so `runtimeInputs` comes back
        null and the payload says why in `runtimeInputsError`. Adoption
        already refused it -- but under the headline "the capture read a
        different tree than the one being stamped", with the remedy
        "recapture against this tree", which produces the same failure again.
        The flag was sitting in the reason line, named by neither.
        """
        import json
        import subprocess
        import tempfile
        payload = json.loads(gen.RUNTIME_ORACLE_PATH.read_text())
        payload["capturedAt"] = payload["capturedAt"] + 1000
        payload["movianVersion"] = "5.0.1017.gdeadbee"
        payload["runtimeInputs"] = None
        payload["runtimeInputsError"] = (
            "Error: cannot read dataroot://res/ecmascript/modules -- "
            "Error: Bad filename dataroot://res/ecmascript/modules -- "
            "Access not allowed (run movian with --bypass-ecmascript-acl)")
        before = gen.RUNTIME_ORACLE_PATH.read_bytes()
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
                handle.write(json.dumps(payload))
                handle.flush()
                result = subprocess.run(
                    ["python3", str(GEN_PY), "--adopt-oracle", handle.name],
                    capture_output=True, text=True, cwd=str(REPO_ROOT))
            self.assertEqual(result.returncode, 1, result.stdout)
            # The REMEDY, not the flag. The runtime's own error text already
            # contains `--bypass-ecmascript-acl`, so asserting the string
            # cannot tell a message that names the remedy from one that only
            # echoes the error -- which is what this test did first, and a
            # mutation removing the remedy passed it.
            self.assertIn("recapture with `mdev run", result.stderr)
            self.assertIn("the stamp's input set and the module census",
                          result.stderr)
            self.assertNotIn("read a different tree", result.stderr)
            self.assertEqual(gen.RUNTIME_ORACLE_PATH.read_bytes(), before)
        finally:
            gen.RUNTIME_ORACLE_PATH.write_bytes(before)

    ACL_BLOCKED = ("Error: cannot %s dataroot://res/ecmascript/modules -- "
                   "Error: Bad filename dataroot://res/ecmascript/modules -- "
                   "Access not allowed (run movian with "
                   "--bypass-ecmascript-acl)")

    def test_a_real_acl_blocked_run_reaches_the_remedy(self):
        """The payload a real blocked run actually produces.

        `discoverFileModules()` and `runtimeInputs()` read the SAME blocked
        path, so an ACL-blocked capture carries `moduleDiscoveryError` AND
        `runtimeInputsError`. The discovery guard returned first, so the
        remedy naming the flag was unreachable in production -- the only case
        it exists for. The earlier tests missed it by mutating the committed
        payload, whose `moduleDiscoveryError` is null: a payload that cannot
        occur (Codex, on movian#239).
        """
        import json
        import subprocess
        import tempfile
        payload = json.loads(gen.RUNTIME_ORACLE_PATH.read_text())
        payload["capturedAt"] = payload["capturedAt"] + 1000
        payload["runtimeInputs"] = None
        payload["runtimeInputsError"] = self.ACL_BLOCKED % "read"
        payload["moduleDiscoveryError"] = self.ACL_BLOCKED % "list"
        before = gen.RUNTIME_ORACLE_PATH.read_bytes()
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
                handle.write(json.dumps(payload))
                handle.flush()
                result = subprocess.run(
                    ["python3", str(GEN_PY), "--adopt-oracle", handle.name],
                    capture_output=True, text=True, cwd=str(REPO_ROOT))
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("recapture with `mdev run", result.stderr)
            self.assertIn("the stamp's input set and the module census",
                          result.stderr)
        finally:
            gen.RUNTIME_ORACLE_PATH.write_bytes(before)

    def test_either_field_alone_reaches_the_remedy(self):
        """Both fields are inspected, and each on its own is enough.

        A test that sets BOTH cannot tell an owner reading one field from an
        owner reading two -- removing either from the loop survived it.
        """
        import json
        import subprocess
        import tempfile
        for field, verb in (("moduleDiscoveryError", "list"),
                            ("runtimeInputsError", "read")):
            with self.subTest(field):
                payload = json.loads(gen.RUNTIME_ORACLE_PATH.read_text())
                payload["capturedAt"] = payload["capturedAt"] + 1000
                payload[field] = self.ACL_BLOCKED % verb
                if field == "runtimeInputsError":
                    payload["runtimeInputs"] = None
                before = gen.RUNTIME_ORACLE_PATH.read_bytes()
                try:
                    with tempfile.NamedTemporaryFile(
                            "w", suffix=".json") as handle:
                        handle.write(json.dumps(payload))
                        handle.flush()
                        result = subprocess.run(
                            ["python3", str(GEN_PY), "--adopt-oracle",
                             handle.name],
                            capture_output=True, text=True,
                            cwd=str(REPO_ROOT))
                    self.assertEqual(result.returncode, 1, result.stdout)
                    self.assertIn("the ecmascript ACL blocked this run",
                                  result.stderr)
                finally:
                    gen.RUNTIME_ORACLE_PATH.write_bytes(before)

    def test_check_reports_the_same_cause_as_adoption(self):
        """A committed oracle captured without the flag is refused by
        `--check` too, and with the whole symptom rather than half of it."""
        import json
        # The committed oracle, so the stamp and version checks that run
        # BEFORE this one are satisfied -- a fixture missing them is refused
        # for a reason that has nothing to do with what this test is about,
        # which is how the first draft of it passed for the wrong reason.
        oracle = json.loads(gen.RUNTIME_ORACLE_PATH.read_text())
        oracle["moduleDiscoveryError"] = self.ACL_BLOCKED % "list"
        artifact = json.loads(
            (REPO_ROOT / "generated" / "movian-metadata.json").read_text())
        ok, output, _report = gen._check_runtime_oracle(artifact, oracle)
        self.assertFalse(ok)
        self.assertIn("the ecmascript ACL blocked this run", output)
        self.assertIn("--bypass-ecmascript-acl", output)

    def test_the_printed_recapture_recipe_is_one_that_works(self):
        """The gate prints a command somebody follows while it is red.

        The two-step form it used to print -- `mdev run` then `mdev open` --
        loses the open to a startup race with a variable window (movian#233),
        so the route goes in as the start URL.
        """
        self.assertIn("--bypass-ecmascript-acl introspect:page",
                      gen.RUNTIME_ORACLE_RECAPTURE)
        self.assertNotIn("mdev open", gen.RUNTIME_ORACLE_RECAPTURE)

    def test_a_discovery_failure_that_is_not_the_acl_keeps_its_message(self):
        """The other half. Only the ACL gets the flag; an unrelated
        enumeration failure keeps the message that fits it."""
        import json
        import subprocess
        import tempfile
        payload = json.loads(gen.RUNTIME_ORACLE_PATH.read_text())
        payload["capturedAt"] = payload["capturedAt"] + 1000
        payload["moduleDiscoveryError"] = "Error: out of memory"
        before = gen.RUNTIME_ORACLE_PATH.read_bytes()
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
                handle.write(json.dumps(payload))
                handle.flush()
                result = subprocess.run(
                    ["python3", str(GEN_PY), "--adopt-oracle", handle.name],
                    capture_output=True, text=True, cwd=str(REPO_ROOT))
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("could not enumerate", result.stderr)
            self.assertNotIn("--bypass-ecmascript-acl", result.stderr)
        finally:
            gen.RUNTIME_ORACLE_PATH.write_bytes(before)

    def test_an_unrelated_capture_error_does_not_get_the_acl_remedy(self):
        """The other half of the branch. An error that is not the ACL gets
        "recapture" and not a flag that would not have helped -- a gate that
        prints a remedy owns that remedy, in both directions."""
        import json
        import subprocess
        import tempfile
        payload = json.loads(gen.RUNTIME_ORACLE_PATH.read_text())
        payload["capturedAt"] = payload["capturedAt"] + 1000
        payload["runtimeInputs"] = None
        payload["runtimeInputsError"] = "Error: out of memory"
        before = gen.RUNTIME_ORACLE_PATH.read_bytes()
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
                handle.write(json.dumps(payload))
                handle.flush()
                result = subprocess.run(
                    ["python3", str(GEN_PY), "--adopt-oracle", handle.name],
                    capture_output=True, text=True, cwd=str(REPO_ROOT))
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("out of memory", result.stderr)
            self.assertNotIn("--bypass-ecmascript-acl", result.stderr)
        finally:
            gen.RUNTIME_ORACLE_PATH.write_bytes(before)

    def test_a_capture_that_recorded_nothing_says_so_without_the_flag(self):
        """Same class, different cause: a payload with no `runtimeInputs`
        and no error explaining it. Recapturing is the right remedy there,
        and the ACL flag would be a guess."""
        import json
        import subprocess
        import tempfile
        payload = json.loads(gen.RUNTIME_ORACLE_PATH.read_text())
        payload["capturedAt"] = payload["capturedAt"] + 1000
        payload["movianVersion"] = "5.0.1017.gdeadbee"
        payload["runtimeInputs"] = None
        payload.pop("runtimeInputsError", None)
        before = gen.RUNTIME_ORACLE_PATH.read_bytes()
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
                handle.write(json.dumps(payload))
                handle.flush()
                result = subprocess.run(
                    ["python3", str(GEN_PY), "--adopt-oracle", handle.name],
                    capture_output=True, text=True, cwd=str(REPO_ROOT))
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("recorded nothing", result.stderr)
            self.assertNotIn("--bypass-ecmascript-acl", result.stderr)
            self.assertNotIn("read a different tree", result.stderr)
        finally:
            gen.RUNTIME_ORACLE_PATH.write_bytes(before)

    def test_a_capture_whose_discovery_failed_is_not_adopted(self):
        # --check rejects it, but adoption used not to: the committed oracle
        # would be overwritten by a payload the very next check refuses.
        import json
        import subprocess
        import tempfile
        payload = json.loads(gen.RUNTIME_ORACLE_PATH.read_text())
        payload["capturedAt"] = payload["capturedAt"] + 1000
        payload["moduleDiscoveryError"] = "Error: cannot list dataroot://..."
        # Deliberately unresolvable, to pin the ORDER: what the capture says
        # about itself is checked before anything that needs git history, so
        # this test means the same thing in a shallow clone.
        payload["movianVersion"] = "5.0.1017.gdeadbee"
        before = gen.RUNTIME_ORACLE_PATH.read_bytes()
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
                handle.write(json.dumps(payload))
                handle.flush()
                result = subprocess.run(
                    ["python3", str(GEN_PY), "--adopt-oracle", handle.name],
                    capture_output=True, text=True, cwd=str(REPO_ROOT))
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("could not enumerate", result.stderr)
            self.assertEqual(gen.RUNTIME_ORACLE_PATH.read_bytes(), before)
        finally:
            # Asserting the file is untouched is not the same as leaving it
            # untouched: when the guard being tested is broken, adoption
            # SUCCEEDS and the committed oracle is overwritten before the
            # assertion runs. Restoring is what keeps this test from
            # corrupting the tree exactly when it is doing its job.
            gen.RUNTIME_ORACLE_PATH.write_bytes(before)

    def test_a_capture_the_census_rejects_is_not_adopted(self):
        # Adoption used to write a payload that the very next --check
        # refuses. Discovery succeeding is not the same as every module
        # loading.
        import json
        import subprocess
        import tempfile
        payload = json.loads(gen.RUNTIME_ORACLE_PATH.read_text())
        payload["capturedAt"] = payload["capturedAt"] + 2000
        payload.pop("inputs", None)
        payload["loadErrors"] = {"movian/page": "Error: boom"}
        payload["movianVersion"] = "5.0.1017.gdeadbee"
        before = gen.RUNTIME_ORACLE_PATH.read_bytes()
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
                handle.write(json.dumps(payload))
                handle.flush()
                result = subprocess.run(
                    ["python3", str(GEN_PY), "--adopt-oracle", handle.name],
                    capture_output=True, text=True, cwd=str(REPO_ROOT))
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("does not account for the modules", result.stderr)
            self.assertEqual(gen.RUNTIME_ORACLE_PATH.read_bytes(), before)
        finally:
            gen.RUNTIME_ORACLE_PATH.write_bytes(before)

    def test_a_capture_without_a_capturedat_is_refused(self):
        import json
        import subprocess
        import tempfile
        payload = json.loads(gen.RUNTIME_ORACLE_PATH.read_text())
        payload.pop("capturedAt", None)
        before = gen.RUNTIME_ORACLE_PATH.read_bytes()
        with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
            handle.write(json.dumps(payload))
            handle.flush()
            result = subprocess.run(
                ["python3", str(GEN_PY), "--adopt-oracle", handle.name],
                capture_output=True, text=True, cwd=str(REPO_ROOT))
        try:
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("capturedAt", result.stderr)
            self.assertEqual(gen.RUNTIME_ORACLE_PATH.read_bytes(), before)
        finally:
            gen.RUNTIME_ORACLE_PATH.write_bytes(before)


class Recipe(unittest.TestCase):
    """Which sources the build compiles is a fact about the Makefile, and the
    Makefile is not in the sources."""

    def _makefile(self):
        return (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    def test_the_recipe_names_every_ecmascript_source_on_disk(self):
        selection = gen.makefile_ecmascript_selection(self._makefile())
        on_disk = {path.relative_to(REPO_ROOT).as_posix()
                   for path in (REPO_ROOT / "src" / "ecmascript").rglob("*.c")}
        self.assertEqual(on_disk - set(selection), set())
        self.assertEqual(set(selection) - on_disk, set())

    def test_the_gate_each_source_sits_behind_is_recorded(self):
        selection = gen.makefile_ecmascript_selection(self._makefile())
        self.assertEqual(selection["src/ecmascript/es_sqlite.c"],
                         "SRCS-$(CONFIG_SQLITE)")
        self.assertEqual(selection["src/ecmascript/es_gumbo.c"],
                         "SRCS-$(CONFIG_GUMBO)")
        self.assertEqual(selection["src/ecmascript/es_fs.c"], "SRCS")

    def test_a_source_moved_into_a_make_conditional_changes_its_gate(self):
        # `SRCS-$(CONFIG_X) +=` is one way to make a source conditional; a
        # bare `SRCS +=` inside `ifeq (...) ... endif` is another, and the
        # recipe already uses it (Makefile:283-285). Reading only the
        # variable name makes the two indistinguishable.
        text = self._makefile()
        moved = text.replace(
            "SRCS-$(CONFIG_SQLITE) += src/ecmascript/es_sqlite.c",
            "ifeq ($(CONFIG_FOO),yes)\n"
            "SRCS += src/ecmascript/es_sqlite.c\n"
            "endif")
        selection = gen.makefile_ecmascript_selection(moved)
        self.assertEqual(selection["src/ecmascript/es_sqlite.c"],
                         "SRCS under ifeq ($(CONFIG_FOO),yes)")
        self.assertTrue(gen.selection_mismatch(
            selection, gen.makefile_ecmascript_selection(text), "abc1234"))

    def test_a_conditional_closes_at_its_endif(self):
        selection = gen.makefile_ecmascript_selection(
            "ifeq ($(A),yes)\n"
            "SRCS += src/ecmascript/inside.c\n"
            "endif\n"
            "SRCS += src/ecmascript/outside.c\n")
        self.assertEqual(selection["src/ecmascript/inside.c"],
                         "SRCS under ifeq ($(A),yes)")
        self.assertEqual(selection["src/ecmascript/outside.c"], "SRCS")

    def test_the_else_branch_is_not_the_if_branch(self):
        selection = gen.makefile_ecmascript_selection(
            "ifeq ($(A),yes)\n"
            "SRCS += src/ecmascript/yes.c\n"
            "else\n"
            "SRCS += src/ecmascript/no.c\n"
            "endif\n")
        self.assertNotEqual(selection["src/ecmascript/yes.c"],
                            selection["src/ecmascript/no.c"])

    def test_a_commented_out_source_is_not_selected(self):
        # GNU Make omits it; reading the pathname anyway keeps a source in
        # the selection that the next build will not compile, and the
        # comparison then finds nothing to report.
        text = self._makefile()
        commented = text.replace("\tsrc/ecmascript/es_fs.c \\",
                                 "#\tsrc/ecmascript/es_fs.c \\")
        self.assertNotEqual(commented, text)
        selection = gen.makefile_ecmascript_selection(commented)
        self.assertNotIn("src/ecmascript/es_fs.c", selection)
        self.assertTrue(gen.selection_mismatch(
            selection, gen.makefile_ecmascript_selection(text), "abc1234"))

    def test_a_trailing_comment_does_not_drop_the_source(self):
        # The other direction of the same rule: stripping must remove the
        # comment, not the line. Over-stripping would leave a compiled source
        # unnamed, which reads as a recipe that dropped it.
        selection = gen.makefile_ecmascript_selection(
            "SRCS += src/ecmascript/es_fs.c # why this one\n")
        self.assertEqual(selection, {"src/ecmascript/es_fs.c": "SRCS"})

    def test_an_else_if_keeps_the_predicate_it_is_the_else_of(self):
        # `else ifeq ($(B),yes)` takes effect only when the OUTER predicate
        # was false, so two recipes whose outer conditions differ select the
        # source under different circumstances even though the inner text is
        # identical.
        outer_a = ("ifeq ($(CONFIG_A),yes)\n"
                   "SRCS += src/ecmascript/x.c\n"
                   "else ifeq ($(CONFIG_B),yes)\n"
                   "SRCS += src/ecmascript/y.c\n"
                   "endif\n")
        outer_c = outer_a.replace("CONFIG_A", "CONFIG_C")
        self.assertNotEqual(
            gen.makefile_ecmascript_selection(outer_a)["src/ecmascript/y.c"],
            gen.makefile_ecmascript_selection(outer_c)["src/ecmascript/y.c"])

    def test_a_final_else_keeps_the_whole_chain(self):
        # `ifeq A / else ifeq B / else`: the last branch takes effect only
        # when BOTH A and B were false, so changing B changes what the final
        # else compiles.
        chain = ("ifeq ($(A),y)\n"
                 "SRCS += src/ecmascript/x.c\n"
                 "else ifeq ($(B),y)\n"
                 "SRCS += src/ecmascript/y.c\n"
                 "else\n"
                 "SRCS += src/ecmascript/z.c\n"
                 "endif\n")
        first = gen.makefile_ecmascript_selection(chain)
        second = gen.makefile_ecmascript_selection(
            chain.replace("$(B),y", "$(B),no"))
        self.assertNotEqual(first["src/ecmascript/z.c"],
                            second["src/ecmascript/z.c"])

    def test_every_occurrence_of_a_source_is_kept(self):
        # A source named in two mutually exclusive branches is compiled
        # under either; keeping only the last hides a change to the other.
        both = ("ifeq ($(A),y)\n"
                "SRCS += src/ecmascript/d.c\n"
                "else\n"
                "SRCS += src/ecmascript/d.c\n"
                "endif\n")
        first = gen.makefile_ecmascript_selection(both)
        second = gen.makefile_ecmascript_selection(
            both.replace("ifeq ($(A),y)", "ifeq ($(C),y)"))
        self.assertIn(" | ", first["src/ecmascript/d.c"])
        self.assertNotEqual(first["src/ecmascript/d.c"],
                            second["src/ecmascript/d.c"])

    def test_a_plain_if_carries_no_negation(self):
        selection = gen.makefile_ecmascript_selection(
            "ifeq ($(A),y)\nSRCS += src/ecmascript/z.c\nendif\n")
        self.assertEqual(selection["src/ecmascript/z.c"],
                         "SRCS under ifeq ($(A),y)")

    def test_every_make_assignment_operator_is_recognised(self):
        # `=`, `+=`, `:=`, `::=`, `?=` and `!=` are all assignments. Reading
        # only two left a source under the gate `?`, which compares equal to
        # the same `?` after the variable was renamed.
        for operator in (":=", "?=", "!=", "::=", "+=", "="):
            selection = gen.makefile_ecmascript_selection(
                "SRCS %s src/ecmascript/p.c\n" % operator)
            self.assertEqual(selection.get("src/ecmascript/p.c"), "SRCS",
                             operator)

    def test_an_unclassified_source_is_a_problem(self):
        selection = gen.makefile_ecmascript_selection(
            "OTHER := src/ecmascript/p.c\n")
        self.assertEqual(selection["src/ecmascript/p.c"], "?")
        self.assertTrue(any("no assignment the parser recognises" in problem
                            for problem in gen._selection_problems(
                                selection, "OTHER := src/ecmascript/p.c\n")))

    def test_a_blind_parser_is_a_problem_not_a_pass(self):
        # A parser that stops matching returns an empty selection, and an
        # empty selection compares equal to another empty one -- green
        # because it read nothing, which is the failure this file is about.
        self.assertTrue(gen._selection_problems({}, self._makefile()))

    def test_a_source_dropped_since_the_build_is_reported(self):
        here = gen.makefile_ecmascript_selection(self._makefile())
        there = dict(here)
        here.pop("src/ecmascript/es_fs.c")
        reasons = gen.selection_mismatch(here, there, "build abc1234")
        self.assertEqual(
            reasons,
            ["src/ecmascript/es_fs.c was compiled into build abc1234 and the"
             " recipe no longer names it"])

    def test_a_source_added_since_the_build_is_reported(self):
        here = gen.makefile_ecmascript_selection(self._makefile())
        there = dict(here)
        there.pop("src/ecmascript/es_fs.c")
        reasons = gen.selection_mismatch(here, there, "build abc1234")
        self.assertTrue(any("is compiled now" in reason
                            for reason in reasons), reasons)

    def test_a_source_that_changed_gate_is_reported(self):
        # The sharpest case: membership is identical and the file has not
        # changed a byte, but it is now compiled in a different configuration.
        here = gen.makefile_ecmascript_selection(self._makefile())
        there = dict(here)
        there["src/ecmascript/es_sqlite.c"] = "SRCS"
        reasons = gen.selection_mismatch(here, there, "build abc1234")
        self.assertEqual(
            reasons,
            ["src/ecmascript/es_sqlite.c moved from SRCS to"
             " SRCS-$(CONFIG_SQLITE) since build abc1234"])

    def test_an_unchanged_recipe_reports_nothing(self):
        here = gen.makefile_ecmascript_selection(self._makefile())
        self.assertEqual(
            gen.selection_mismatch(here, dict(here), "build abc"), [])

    def test_a_source_the_recipe_stops_naming_is_a_problem(self):
        selection = gen.makefile_ecmascript_selection(self._makefile())
        selection.pop("src/ecmascript/es_fs.c")
        problems = gen._selection_problems(selection, self._makefile())
        self.assertTrue(any("es_fs.c" in problem for problem in problems),
                        problems)


class Configuration(unittest.TestCase):
    """Configuration moves the native surface with every source byte-identical.

    `#if ENABLE_PLUGINS` guards `native/misc.selectView` (es_misc.c:316-318).
    Flip that flag and the next build exposes a different surface while the
    sources, the recipe selection and every digest stay the same -- so a
    capture from the previous binary could be stamped against a tree whose
    build no longer matches it.
    """

    def test_only_flags_the_ecmascript_sources_read_are_in_scope(self):
        # The whole point is NOT to key over the configuration. This tree's
        # config.h defines dozens of ENABLE_* symbols (86 when measured in
        # movian-public-clean); three of them can move the
        # JS-visible surface, and a change to the other 83 must stay silent.
        symbols = gen.ecmascript_config_symbols()
        self.assertEqual(symbols, ["ENABLE_HTTPSERVER", "ENABLE_PLUGINS",
                                   "ENABLE_WEBPOPUP"], symbols)

    def test_a_flag_only_the_rest_of_the_tree_reads_is_out_of_scope(self):
        for absent in ("ENABLE_VDPAU", "ENABLE_LIBAV", "ENABLE_GLW"):
            self.assertNotIn(absent, gen.ecmascript_config_symbols())

    def test_the_values_come_from_the_generated_header(self):
        config = ("#define ENABLE_PLUGINS 1\n"
                  "#define ENABLE_WEBPOPUP 0\n"
                  "#define ENABLE_VDPAU 1\n")
        self.assertEqual(
            gen.configuration_values(config, ["ENABLE_PLUGINS",
                                              "ENABLE_WEBPOPUP",
                                              "ENABLE_HTTPSERVER"]),
            {"ENABLE_PLUGINS": "1", "ENABLE_WEBPOPUP": "0",
             "ENABLE_HTTPSERVER": None})

    def test_a_flag_flip_is_a_mismatch(self):
        before = {"ENABLE_PLUGINS": "1", "ENABLE_WEBPOPUP": "0"}
        after = {"ENABLE_PLUGINS": "0", "ENABLE_WEBPOPUP": "0"}
        reasons = gen.configuration_mismatch(after, before, "the build")
        self.assertTrue(any("ENABLE_PLUGINS" in reason for reason in reasons),
                        reasons)
        self.assertEqual(gen.configuration_mismatch(before, before, "x"), [])

    def test_a_flag_appearing_or_vanishing_is_a_mismatch_too(self):
        # A configure that stops defining a symbol the sources still read is
        # the same hazard wearing a different hat.
        self.assertTrue(gen.configuration_mismatch(
            {"ENABLE_PLUGINS": None}, {"ENABLE_PLUGINS": "1"}, "the build"))
        self.assertTrue(gen.configuration_mismatch(
            {"ENABLE_PLUGINS": "1"}, {"ENABLE_PLUGINS": None}, "the build"))


class StampedConfiguration(unittest.TestCase):
    """The configuration is a third axis, independent of the sources and of
    the recipe.

    `#if ENABLE_PLUGINS` decides whether `native/misc.selectView` exists
    (es_misc.c:316-318). Flip it and the next build exposes a different
    surface with every `.c`, every digest and the recipe selection
    byte-identical -- so nothing else in this file can see it.
    """

    def _oracle(self):
        import json
        return json.loads(
            gen.RUNTIME_ORACLE_PATH.read_text(encoding="utf-8"))

    def _artifact(self):
        import json
        return json.loads(gen.ARTIFACT_PATH.read_text(encoding="utf-8"))

    def _tree_with_build(self, directory, config_newer: bool):
        import os
        root = Path(directory)
        (root / "build.debug").mkdir()
        config = root / "build.debug" / "config.h"
        binary = root / "build.debug" / "movian"
        config.write_text("#define ENABLE_PLUGINS 0\n", encoding="utf-8")
        binary.write_bytes(b"\x7fELF")
        linked = 1_000_000_000
        os.utime(binary, (linked, linked))
        os.utime(config, (linked + (60 if config_newer else -60),) * 2)
        return root

    def test_a_build_older_than_its_configuration_is_refused(self):
        # Reconfigure, then do NOT rebuild. Every source, every digest and
        # the recipe selection are identical, so this is the only witness --
        # and comparing config.h against the CAPTURE instead of the binary
        # would pass here, because the capture is newer than both.
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = self._tree_with_build(directory, config_newer=True)
            self.assertIsNotNone(gen.configuration_predates_binary(root))

    def test_a_build_newer_than_its_configuration_is_fine(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = self._tree_with_build(directory, config_newer=False)
            self.assertIsNone(gen.configuration_predates_binary(root))

    def test_adoption_asks_before_it_stamps(self):
        # The refusal is worth nothing if the adoption path does not consult
        # it, and no test here drives cmd_adopt_oracle far enough to reach
        # this branch without a real build.
        source = (REPO_ROOT / "support" / "devtools" / "metadata"
                  / "gen.py").read_text(encoding="utf-8")
        body = source.split("def cmd_adopt_oracle")[1]
        self.assertIn("configuration_predates_binary", body)

    def test_a_tree_with_no_build_cannot_refuse_on_that_ground(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(
                gen.configuration_predates_binary(Path(directory)))

    def test_a_release_tree_owns_a_build_too(self):
        # BUILDDIR is build.${BUILD}; hardcoding build.debug told a
        # release-configured checkout it did not own the build it owns.
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "build.release").mkdir()
            (root / "build.release" / "config.h").write_text(
                "#define ENABLE_PLUGINS 1\n", encoding="utf-8")
            found = gen.build_config_path(root)
            self.assertIsNotNone(found)
            self.assertEqual(found.parent.name, "build.release")

    def test_every_symbol_on_a_directive_counts_not_the_first(self):
        # `#if ENABLE_A && ENABLE_B` guards on both. No such line is in the
        # tree today, so this is asserted on text -- otherwise the rule is
        # unfalsifiable and a regression to first-only would pass.
        self.assertEqual(
            gen.config_symbols_in("#if ENABLE_A && ENABLE_B\nx\n#endif\n"),
            {"ENABLE_A", "ENABLE_B"})
        self.assertEqual(
            gen.config_symbols_in("#elif defined(ENABLE_C) || ENABLE_D\n"),
            {"ENABLE_C", "ENABLE_D"})
        # and a mention outside a directive is not a guard
        self.assertEqual(gen.config_symbols_in("/* ENABLE_E */\nENABLE_F;\n"),
                         set())

    def test_a_blind_symbol_scan_raises_rather_than_compares_nothing(self):
        # Zero symbols would make every configuration compare equal to every
        # other -- the check passing because it looked at nothing.
        from unittest import mock
        with mock.patch.object(gen, "_CONFIG_SYMBOL_RE",
                               gen.re.compile(r"\b(NOTHING_MATCHES_THIS)")):
            with self.assertRaises(gen.BlindConfigurationScan):
                gen.ecmascript_config_symbols()

    def test_a_flag_flip_since_the_stamp_fails_the_check(self):
        # The measured case from movian#222.
        import copy
        from unittest import mock
        oracle = copy.deepcopy(self._oracle())
        oracle.setdefault("inputs", {})["configuration"] = {
            "ENABLE_PLUGINS": "1"}
        with mock.patch.object(gen, "runtime_oracle_configuration",
                               return_value={"ENABLE_PLUGINS": "0"}):
            ok, out, _report = gen._check_runtime_oracle(self._artifact(),
                                                        oracle)
        self.assertFalse(ok)
        self.assertIn("ENABLE_PLUGINS", out)

    def test_an_axis_that_cannot_be_judged_is_printed_not_swallowed(self):
        # The reasons were built and discarded, so a run with no build passed
        # the configuration axis with nothing said -- which reads exactly
        # like a run that compared it and agreed.
        import json
        from unittest import mock
        artifact = json.loads(gen.ARTIFACT_PATH.read_text(encoding="utf-8"))
        oracle = json.loads(
            gen.RUNTIME_ORACLE_PATH.read_text(encoding="utf-8"))
        with mock.patch.object(gen, "runtime_oracle_configuration",
                               return_value=None):
            ok, out, report = gen._check_runtime_oracle(artifact, oracle)
        self.assertTrue(ok, out)
        self.assertTrue(report["notCompared"], report)
        self.assertIn("not compared", out)
        self.assertIn("no build", out)

    def test_a_stamp_predating_the_axis_says_so(self):
        import copy, json
        from unittest import mock
        oracle = copy.deepcopy(json.loads(
            gen.RUNTIME_ORACLE_PATH.read_text(encoding="utf-8")))
        oracle.get("inputs", {}).pop("configuration", None)
        with mock.patch.object(gen, "runtime_oracle_configuration",
                               return_value={"ENABLE_PLUGINS": "1"}):
            ok, out, _report = gen._check_runtime_oracle(
                json.loads(gen.ARTIFACT_PATH.read_text(encoding="utf-8")),
                oracle)
        self.assertTrue(ok, out)
        self.assertIn("stamped before the configuration was recorded", out)

    def test_a_valueless_define_is_defined_not_unset(self):
        # `#define ENABLE_X` with no value defines the macro. Reading it as
        # unset reports a mismatch against a stamp of "1" for a build that
        # has it.
        values = gen.configuration_values("#define ENABLE_PLUGINS\n",
                                          ["ENABLE_PLUGINS"])
        self.assertIsNotNone(values["ENABLE_PLUGINS"])

    def test_a_symbol_named_only_in_a_comment_is_not_a_guard(self):
        self.assertEqual(
            gen.config_symbols_in("#if 0 // TODO: enable ENABLE_FOOBAR\n"),
            set())
        self.assertEqual(
            gen.config_symbols_in("#if ENABLE_REAL /* not ENABLE_FAKE */\n"),
            {"ENABLE_REAL"})

    def test_a_blind_scan_during_adoption_is_reported_not_raised(self):
        source = (REPO_ROOT / "support" / "devtools" / "metadata"
                  / "gen.py").read_text(encoding="utf-8")
        body = source.split("def cmd_adopt_oracle")[1]
        self.assertIn("except BlindConfigurationScan", body)

    def test_an_out_of_scope_flag_never_enters_the_comparison(self):
        # The other half of the DoD. A flag the ecmascript sources never read
        # cannot make this red because it is not recorded at all -- 83 of
        # this tree's ENABLE_* symbols are in that position. Asserted where
        # it is decided, in the recorded set, because a comparison of two
        # dicts that never contained it could not have come out differently.
        symbols = gen.ecmascript_config_symbols()
        config = ("#define ENABLE_PLUGINS 1\n"
                  "#define ENABLE_VDPAU 1\n")
        recorded = gen.configuration_values(config, symbols)
        self.assertIn("ENABLE_PLUGINS", recorded)
        self.assertNotIn("ENABLE_VDPAU", recorded)
        # ...so flipping it changes nothing that is compared.
        flipped = gen.configuration_values(
            config.replace("ENABLE_VDPAU 1", "ENABLE_VDPAU 0"), symbols)
        self.assertEqual(gen.configuration_mismatch(flipped, recorded, "x"),
                         [])

    def test_a_checkout_with_no_build_does_not_go_red(self):
        # CI checks out sources and never builds, so there is no config.h and
        # nothing local that could contradict the stamp. Failing there would
        # paint every run red for a hazard that cannot exist without a build.
        import copy
        from unittest import mock
        oracle = copy.deepcopy(self._oracle())
        oracle.setdefault("inputs", {})["configuration"] = {
            "ENABLE_PLUGINS": "1"}
        with mock.patch.object(gen, "runtime_oracle_configuration",
                               return_value=None):
            ok, out, _report = gen._check_runtime_oracle(self._artifact(),
                                                        oracle)
        self.assertTrue(ok, out)


class RecipeTransformations(unittest.TestCase):
    """A removal need not name a pathname.

    `makefile_ecmascript_selection()` records the paths it SEES, so a recipe
    that drops a source through a Make expression leaves the selection
    byte-identical -- it compares equal to the recipe that built the binary
    and a stale oracle keeps passing on a tree whose next binary has lost the
    module. Evaluating Make needs Make; knowing when the parser is out of its
    depth does not.
    """

    def _makefile(self):
        return (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    def test_this_recipe_reshapes_nothing_the_parser_cannot_follow(self):
        self.assertEqual(
            gen.makefile_selection_transformations(self._makefile()), [])

    def test_the_source_list_reaches_six_further_names(self):
        # The reason the check is not just about `SRCS`: every source travels
        # into names the scan was not watching, and a transformation applied
        # to any of them is equally invisible.
        self.assertEqual(
            sorted(gen.makefile_source_variables(self._makefile())),
            ["DEPS", "OBJS", "OBJS2", "OBJS3", "OBJS4", "SRCS", "SRCS-*",
             "SSRCS"])

    def test_a_filter_out_that_names_no_path_is_reported(self):
        # The measured case: `es_fs.c` stays in the selection while Make
        # drops it, and the next binary has no `native/fs`. Modelling
        # replacement (below) now also empties the selection for the `:=`
        # spelling, which is loud in its own way -- but it says every source
        # was dropped when only one was, and an appending spelling moves the
        # selection not at all. The report is what names the real thing.
        recipe = self._makefile() + \
            "\nSRCS += $(filter-out %/es_fs.c,$(SRCS))\n"
        selection = gen.makefile_ecmascript_selection(recipe)
        self.assertEqual(selection["src/ecmascript/es_fs.c"], "SRCS")
        self.assertEqual(
            gen.selection_mismatch(
                selection,
                gen.makefile_ecmascript_selection(self._makefile()),
                "the build"),
            [])
        self.assertTrue(
            any("filter-out" in problem for problem in
                gen._selection_problems(selection, recipe)),
            gen._selection_problems(selection, recipe))

    def test_a_transformation_one_hop_away_is_reported(self):
        # `SSRCS` is not `SRCS`, and it carries every source.
        recipe = self._makefile() + \
            "\nSSRCS := $(filter-out %/es_fs.c,$(SSRCS))\n"
        self.assertTrue(
            any("SSRCS" in problem for problem in
                gen.makefile_selection_transformations(recipe)))

    def test_an_assignment_from_a_function_is_reported(self):
        # Nothing here mentions a tracked variable on the right, so the
        # dataflow rule alone would miss it.
        recipe = self._makefile() + "\nSRCS := $(shell cat more.mk)\n"
        self.assertTrue(
            any("shell" in problem for problem in
                gen.makefile_selection_transformations(recipe)))

    def test_a_membership_preserving_function_is_not_reported(self):
        # `sort` is what this recipe actually uses. Reporting it would make
        # the check permanently red on a correct tree, which is the other way
        # to be useless.
        recipe = self._makefile() + "\nSRCS += $(sort $(SRCS-yes))\n"
        self.assertEqual(gen.makefile_selection_transformations(recipe), [])

    def test_the_enclosing_call_is_found_by_parens_not_by_looking_left(self):
        # `$(sort $(A)) $(SRCS)` has a function name to the left of `$(SRCS)`
        # and does not pass it through anything.
        recipe = self._makefile() + "\nFOO := $(sort $(A)) $(SRCS)\n"
        self.assertEqual(gen.makefile_selection_transformations(recipe), [])

    def test_one_finding_per_place_not_one_per_reading(self):
        recipe = self._makefile() + \
            "\nSRCS := $(filter-out %/es_fs.c,$(SRCS))\n"
        self.assertEqual(
            len(gen.makefile_selection_transformations(recipe)), 1)

    def test_a_replacement_discards_what_the_variable_held(self):
        # `+=` accumulates, `=` throws the list away. Reading only the
        # variable name made the two identical, so turning a late `SRCS +=`
        # into `SRCS =` -- which drops every source named above it -- gave a
        # byte-identical selection and no mismatch at all.
        appended = ("SRCS += src/ecmascript/es_a.c\n"
                    "SRCS += src/ecmascript/es_b.c\n")
        replaced = ("SRCS += src/ecmascript/es_a.c\n"
                    "SRCS = src/ecmascript/es_b.c\n")
        before = gen.makefile_ecmascript_selection(appended)
        after = gen.makefile_ecmascript_selection(replaced)
        self.assertEqual(sorted(before), ["src/ecmascript/es_a.c",
                                          "src/ecmascript/es_b.c"])
        self.assertEqual(sorted(after), ["src/ecmascript/es_b.c"])
        self.assertEqual(
            gen.selection_mismatch(after, before, "the build"),
            ["src/ecmascript/es_a.c was compiled into the build and the "
             "recipe no longer names it"])

    def test_every_replacing_operator_discards(self):
        for operator in ("=", ":=", "::=", "!="):
            selection = gen.makefile_ecmascript_selection(
                "SRCS += src/ecmascript/es_a.c\n"
                "SRCS %s src/ecmascript/es_b.c\n" % operator)
            self.assertEqual(sorted(selection), ["src/ecmascript/es_b.c"],
                             operator)

    def test_a_replacement_clears_a_conditional_append_above_it(self):
        # Make discards the whole value, including what a branch added.
        selection = gen.makefile_ecmascript_selection(
            "ifeq ($(A),y)\nSRCS += src/ecmascript/es_a.c\nendif\n"
            "SRCS = src/ecmascript/es_b.c\n")
        self.assertEqual(sorted(selection), ["src/ecmascript/es_b.c"])

    def test_a_conditional_replacement_is_reported_not_modelled(self):
        # It replaces in one configuration and not another, and the scan
        # records one selection -- so it says so instead of picking.
        recipe = self._makefile() + \
            "\nifeq ($(A),y)\nSRCS := src/ecmascript/es_z.c\nendif\n"
        self.assertTrue(
            any("inside a conditional" in problem for problem in
                gen.makefile_selection_transformations(recipe)),
            gen.makefile_selection_transformations(recipe))

    def test_a_conditional_append_is_still_ordinary(self):
        recipe = self._makefile() + \
            "\nifeq ($(A),y)\nSRCS += src/ecmascript/es_z.c\nendif\n"
        self.assertEqual(gen.makefile_selection_transformations(recipe), [])

    def test_assign_if_unset_is_reported(self):
        # `?=` does nothing when the variable is already set, and the scan
        # cannot know which it is.
        recipe = self._makefile() + "\nSRCS ?= src/ecmascript/es_z.c\n"
        self.assertTrue(
            any("?=" in problem for problem in
                gen.makefile_selection_transformations(recipe)))

    def test_every_run_refuses_a_recipe_the_parser_cannot_follow(self):
        # Not only adoption. The stamped selection compares equal to a recipe
        # that reshapes the list, because reshaping does not change the text
        # the scan reads -- the tautology one level up, and `--check` is
        # where a tree drifts into it.
        import json
        path = REPO_ROOT / "Makefile"
        before = path.read_bytes()
        try:
            path.write_bytes(
                before + b"\nSRCS := $(filter-out %/es_fs.c,$(SRCS))\n")
            ok, out, _report = gen._check_runtime_oracle(
                json.loads(gen.ARTIFACT_PATH.read_text(encoding="utf-8")),
                json.loads(
                    gen.RUNTIME_ORACLE_PATH.read_text(encoding="utf-8")))
            self.assertFalse(ok)
            self.assertIn("filter-out", out)
        finally:
            path.write_bytes(before)
        self.assertEqual(path.read_bytes(), before)


class RecaptureInstruction(unittest.TestCase):
    """The way out of a stale oracle is a printed command, so the command has
    to be one mdev accepts.

    It was not. `--extra-flags --bypass-ecmascript-acl` is not an argument
    `mdev run` defines, and following the instruction verbatim produced a
    usage error; the `mdev open` line named no instance, so it addressed the
    default one rather than the instance the line above had just started. A
    check nobody can act on is a check that fails closed forever.
    """

    def _commands(self):
        import shlex
        text = gen.RUNTIME_ORACLE_RECAPTURE.replace("\\\n", " ")
        return [shlex.split(line)[1:]
                for line in text.split("\n")
                if line.strip().startswith("mdev ")]

    def _parser(self):
        import sys
        devtools = (REPO_ROOT / "support" / "devtools").as_posix()
        if devtools not in sys.path:
            sys.path.insert(0, devtools)
        from mdevlib import cli
        return cli.build_parser()

    def test_every_printed_command_is_one_mdev_accepts(self):
        parser = self._parser()
        for command in self._commands():
            # parse_args exits the process on a bad argument, which is
            # exactly the outcome being tested for.
            try:
                parser.parse_args(command)
            except SystemExit as error:
                self.fail("mdev rejects %r (exit %s)"
                          % (" ".join(command), error.code))

    def test_the_two_commands_address_the_same_instance(self):
        parser = self._parser()
        names = {parser.parse_args(command).name
                 for command in self._commands()}
        self.assertEqual(len(names), 1, names)

    def test_the_capture_needs_the_flag_it_asks_for(self):
        # Not decoration: es_fs.c:filename_is_allowed limits a plugin's `fs`
        # reads to its own directory, and the walk covers the core module
        # tree. Without it the capture records a discovery error and adoption
        # refuses -- which is how the missing flag stayed invisible.
        run = [command for command in self._commands()
               if command[0] == "run"][0]
        self.assertTrue(self._parser().parse_args(run).bypass_ecmascript_acl)


class Preprocessor(unittest.TestCase):
    """`#if 0` is how C comments out code that already contains comments.

    The compiler registers nothing inside it, so a registration there is not
    a module -- in exactly the way `/* ES_MODULE(...) */` is not. Reading it
    as one makes every fresh capture fail to load a name that does not exist,
    and no recapture can ever clear that.
    """

    def test_a_registration_in_a_dead_branch_is_not_a_module(self):
        self.assertEqual(
            gen._c_registrations('#if 0\nES_MODULE("dead", f);\n#endif\n'
                                 'ES_MODULE("alive", f);\n'),
            (["alive"], 0))

    def test_the_live_arm_of_a_dead_branch_survives(self):
        for source, expected in (
                ('#if 0\nES_MODULE("d", f);\n#else\n'
                 'ES_MODULE("live", f);\n#endif\n', ["live"]),
                ('#if 1\nES_MODULE("live", f);\n#else\n'
                 'ES_MODULE("d", f);\n#endif\n', ["live"]),
                ('#if 0\nES_MODULE("d", f);\n#elif 1\n'
                 'ES_MODULE("live", f);\n#else\n'
                 'ES_MODULE("d2", f);\n#endif\n', ["live"])):
            self.assertEqual(gen._c_registrations(source)[0], expected,
                             source)

    def test_a_dead_branch_takes_its_nested_conditionals_with_it(self):
        self.assertEqual(
            gen._c_registrations(
                '#if 0\n#ifdef FOO\nES_MODULE("d", f);\n#endif\n#endif\n'
                'ES_MODULE("live", f);\n')[0],
            ["live"])

    def test_a_condition_nobody_can_evaluate_keeps_both_arms(self):
        # Deciding it would need the build's own preprocessing. Keeping the
        # registration means it is reported rather than quietly dropped,
        # which is the safe direction for a scan that cannot know.
        self.assertEqual(
            sorted(gen._c_registrations(
                '#if 0\nES_MODULE("d", f);\n#elif ENABLE_X\n'
                'ES_MODULE("maybe", f);\n#else\n'
                'ES_MODULE("other", f);\n#endif\n')[0]),
            ["maybe", "other"])

    def test_a_directive_inside_a_string_is_not_a_directive(self):
        # Anchored at the start of a line, which is where C requires one.
        self.assertEqual(
            gen._c_registrations('const char *s = "#if 0";\n'
                                 'ES_MODULE("live", f);\n')[0],
            ["live"])

    def test_no_registration_in_this_tree_is_conditional(self):
        # If one ever is, the census goes red until somebody decides what it
        # means -- so this records that today none of them are.
        for path in sorted(gen.ECMASCRIPT_DIR.glob("es_*.c")):
            source = path.read_text(encoding="utf-8", errors="replace")
            self.assertEqual(
                gen._c_registrations(source),
                gen._c_registrations(gen._c_active_code(source)),
                path.name)


class ReservedNamespaces(unittest.TestCase):
    """Two prefixes es_modsearch answers before it resolves any path, and one
    length past which it cannot resolve one at all."""

    def _oracle(self):
        import json
        return json.loads(
            gen.RUNTIME_ORACLE_PATH.read_text(encoding="utf-8"))

    def _artifact(self):
        import json
        return json.loads(gen.ARTIFACT_PATH.read_text(encoding="utf-8"))

    def _probe(self, relative):
        path = (REPO_ROOT / "res" / "ecmascript" / "modules" / relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("exports.a = function(){};\n", encoding="utf-8")
        return path

    def _remove(self, path):
        path.unlink(missing_ok=True)
        directory = path.parent
        modules = REPO_ROOT / "res" / "ecmascript" / "modules"
        while directory != modules and directory.is_dir() \
                and not any(directory.iterdir()):
            directory.rmdir()
            directory = directory.parent

    def test_nothing_in_this_tree_is_unreachable(self):
        self.assertEqual(gen.unreachable_module_files(), [])

    def test_a_file_that_collides_with_a_native_module_is_reported(self):
        # The sharpest one. `native/fs` is a real registration, so the file's
        # entry was overwritten by it -- one expected entry for two things,
        # the capture observing the C module, and nothing reported at all.
        probe = self._probe("native/fs.js")
        try:
            self.assertEqual(gen.expected_runtime_modules()["native/fs"],
                             "ES_MODULE in src/ecmascript/es_fs.c")
            problems = gen.runtime_oracle_census(
                self._oracle(), self._artifact())
            self.assertTrue(
                any("native/fs.js cannot be loaded" in problem
                    for problem in problems), problems)
        finally:
            self._remove(probe)

    def test_a_file_in_the_native_namespace_is_reported_for_what_it_is(self):
        # With nothing to collide with, the census used to say the capture
        # had not walked it -- sending the reader to fix a capture that was
        # behaving correctly.
        probe = self._probe("native/nope.js")
        try:
            self.assertIsNone(
                gen.expected_runtime_modules().get("native/nope"))
            problems = gen.runtime_oracle_census(
                self._oracle(), self._artifact())
            self.assertTrue(
                any("without ever resolving a path" in problem
                    for problem in problems), problems)
            self.assertFalse(
                any("did not walk it" in problem for problem in problems),
                problems)
        finally:
            self._remove(probe)

    def test_an_id_too_long_for_the_resolver_is_reported(self):
        # snprintf into `char path[512]` truncates in silence, so the module
        # simply never loads and no capture can be made to see it.
        probe = self._probe("movian/" + "d/" * 240 + "deep.js")
        try:
            self.assertTrue(
                any("truncated path" in problem
                    for problem in gen.unreachable_module_files()),
                gen.unreachable_module_files())
        finally:
            self._remove(probe)

    def test_the_largest_addressable_id_is_accepted_by_both_sides(self):
        # The introspector checked `url + '.js'` at a leaf whose url already
        # ended in `.js`, making its bound three bytes tighter than the
        # resolver's: the generator said a 474-character id fit, the capture
        # refused to walk it, and no capture could satisfy both.
        longest = "movian/" + "a" * 467
        self.assertEqual(len(gen._MODSEARCH_PATH_FORMAT % longest),
                         gen.MODSEARCH_PATH_SIZE - 1)
        self.assertTrue(gen.module_id_fits_resolver(longest))
        source = (gen.INTROSPECTOR_DIR / "introspector.js").read_text(
            encoding="utf-8")
        self.assertIn("url.slice(-3) === '.js' ? url : url + '.js'", source)

    def test_a_module_name_cannot_poison_a_payload_map(self):
        # Module names come off the filesystem, and Duktape implements the
        # `Object.prototype.__proto__` setter (duktape.c:33224): on an
        # ordinary object `tier1['__proto__'] = record` reassigns the
        # prototype and creates no own property, so `require('__proto__')`
        # would succeed while its record vanished from the payload.
        source = (gen.INTROSPECTOR_DIR / "introspector.js").read_text(
            encoding="utf-8")
        for maker in ("var before", "var tier1", "var tier2", "var tier3",
                      "var moduleRefs", "var loadErrors"):
            self.assertIn("%s = Object.create(null);" % maker, source)

    def test_no_directory_symlink_in_the_walked_trees(self):
        self.assertEqual(gen.symlinked_directories(), [])

    def test_a_directory_symlink_is_reported_before_it_diverges(self):
        # The capture descends it -- fs_scandir classifies with stat(), not
        # lstat() -- and Python's glob does not, so the module beneath it is
        # loaded by the runtime, absent from the census, absent from the
        # stamped inputs and absent from the artifact. Every honest capture
        # becomes inadmissible with nothing saying why.
        import os
        modules = REPO_ROOT / "res" / "ecmascript" / "modules"
        real = modules / "movian" / "realdir"
        link = modules / "movian" / "linked"
        try:
            real.mkdir(parents=True, exist_ok=True)
            (real / "probe.js").write_text("exports.a = function(){};\n",
                                           encoding="utf-8")
            os.symlink(real, link, target_is_directory=True)
            self.assertIsNone(
                gen.expected_runtime_modules().get("movian/linked/probe"))
            problems = gen.runtime_oracle_census(
                self._oracle(), self._artifact())
            self.assertTrue(
                any("directory symlink" in problem for problem in problems),
                problems)
        finally:
            if link.is_symlink():
                link.unlink()
            (real / "probe.js").unlink(missing_ok=True)
            if real.is_dir():
                real.rmdir()
        self.assertEqual(gen.symlinked_directories(), [])

    def test_the_resolver_bound_is_the_one_the_introspector_uses(self):
        # Two different bounds would make the census expect a module the
        # capture cannot reach, and no recapture could clear it.
        source = (gen.INTROSPECTOR_DIR / "introspector.js").read_text(
            encoding="utf-8")
        self.assertIn("var MODSEARCH_PATH_SIZE = %d;"
                      % gen.MODSEARCH_PATH_SIZE, source)
        # And that both walks actually consult it. The bound is what
        # terminates them: fs_scandir classifies with stat(), not lstat()
        # (fa_fs.c:137-142), so a directory symlink pointing back at an
        # ancestor reads as an ordinary directory and the recursion descends
        # through it until the interpreter stack gives out -- measured, and
        # the error it raised then said only "cannot list", which sends the
        # reader after an ACL problem that is not there.
        self.assertEqual(source.count("refuseUnaddressablePath(url);"), 2)
        self.assertTrue(gen.module_id_fits_resolver("movian/" + "a" * 467))
        self.assertFalse(gen.module_id_fits_resolver("movian/" + "a" * 468))


class StampedRecipe(unittest.TestCase):
    """The recipe travels with the capture, so every run rechecks it.

    Comparing it only at adoption would bind it to that moment and nothing
    after: a later commit could drop a source from SRCS, or move it behind
    another gate, without touching a .c or the oracle, and every check would
    stay green while the next binary omits the API the artifact advertises.
    """

    def _stamped(self):
        import json
        oracle = json.loads(
            gen.RUNTIME_ORACLE_PATH.read_text(encoding="utf-8"))
        return oracle["inputs"].get("selection")

    def test_the_committed_stamp_carries_the_recipe(self):
        stamped = self._stamped()
        self.assertIsInstance(stamped, dict)
        self.assertEqual(
            stamped,
            gen.makefile_ecmascript_selection(
                (REPO_ROOT / "Makefile").read_text(encoding="utf-8")))

    def test_adoption_writes_the_recipe_into_the_stamp(self):
        # Checking the committed stamp is not the same as checking that
        # adoption produces one: renaming the key in cmd_adopt_oracle left
        # every other test green.
        import json
        import subprocess
        import tempfile
        payload = json.loads(gen.RUNTIME_ORACLE_PATH.read_text())
        payload["capturedAt"] = payload["capturedAt"] + 3000
        payload.pop("inputs", None)
        before = gen.RUNTIME_ORACLE_PATH.read_bytes()
        # Adoption stamps the configuration the capture ran under, so it
        # needs a generated header. Plant one only when this checkout has no
        # build of its own -- never touch a real one, and never depend on
        # what a real one's timestamps happen to be.
        config = gen.build_config_path()
        planted = config is None
        if planted:
            config = REPO_ROOT / "build.debug" / "config.h"
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text("#define ENABLE_PLUGINS 1\n"
                              "#define ENABLE_WEBPOPUP 1\n"
                              "#define ENABLE_HTTPSERVER 1\n",
                              encoding="utf-8")
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
                handle.write(json.dumps(payload))
                handle.flush()
                result = subprocess.run(
                    ["python3", str(GEN_PY), "--adopt-oracle", handle.name],
                    capture_output=True, text=True, cwd=str(REPO_ROOT))
            self.assertEqual(result.returncode, 0, result.stderr)
            written = json.loads(gen.RUNTIME_ORACLE_PATH.read_text())
            self.assertEqual(
                written["inputs"]["selection"],
                gen.makefile_ecmascript_selection(
                    (REPO_ROOT / "Makefile").read_text(encoding="utf-8")))
            # The configuration axis is stamped by the same adoption, and
            # asserting it on the written file beats grepping gen.py for the
            # key: this is the artifact a later check will read.
            self.assertEqual(written["inputs"]["configuration"],
                             gen.runtime_oracle_configuration())
        finally:
            gen.RUNTIME_ORACLE_PATH.write_bytes(before)
            if planted:
                config.unlink(missing_ok=True)
                if config.parent.exists() and not any(config.parent.iterdir()):
                    config.parent.rmdir()

    def test_a_recipe_change_after_adoption_is_caught(self):
        import json
        artifact = json.loads(gen.ARTIFACT_PATH.read_text(encoding="utf-8"))
        oracle = json.loads(gen.RUNTIME_ORACLE_PATH.read_text())
        makefile = REPO_ROOT / "Makefile"
        original = makefile.read_text(encoding="utf-8")
        try:
            makefile.write_text(
                original.replace("\tsrc/ecmascript/es_fs.c \\\n", ""),
                encoding="utf-8")
            ok, output, _report = gen._check_runtime_oracle(artifact, oracle)
            self.assertFalse(ok)
            self.assertIn("es_fs.c", output)
        finally:
            makefile.write_text(original, encoding="utf-8")

    def test_a_stamp_without_a_recipe_is_refused(self):
        import copy
        import json
        artifact = json.loads(gen.ARTIFACT_PATH.read_text(encoding="utf-8"))
        oracle = copy.deepcopy(
            json.loads(gen.RUNTIME_ORACLE_PATH.read_text()))
        oracle["inputs"].pop("selection")
        ok, output, _report = gen._check_runtime_oracle(artifact, oracle)
        self.assertFalse(ok)
        self.assertIn("records no recipe selection", output)


class ModuleCensus(unittest.TestCase):
    """A module nobody listed was unobserved by the capture and, in syntax
    the scanner cannot read, missing from the artifact too. Two blind sides
    agree about nothing."""

    def _oracle(self):
        import json
        return json.loads(
            gen.RUNTIME_ORACLE_PATH.read_text(encoding="utf-8"))

    def _artifact(self):
        import json
        return json.loads(gen.ARTIFACT_PATH.read_text(encoding="utf-8"))

    def test_the_committed_capture_walked_everything_this_tree_has(self):
        self.assertEqual(
            gen.runtime_oracle_census(self._oracle(), self._artifact()), [])

    def test_the_census_asks_what_was_walked_not_what_was_attempted(self):
        # `modules` is the list of names the run TRIED, and the introspector
        # builds it by walking the same directory this census walks -- so
        # comparing the two could not disagree. A module that failed to load
        # is in `modules`, in `loadErrors`, and has no members at all; for a
        # module the static scanner also cannot read there is then nothing
        # left to compare, and the gate would pass on a capture that saw
        # nothing.
        import copy
        oracle = copy.deepcopy(self._oracle())
        oracle["loadErrors"] = {"movian/page": "Error: boom"}
        problems = gen.runtime_oracle_census(oracle, self._artifact())
        self.assertTrue(any("could not load it" in problem
                            for problem in problems), problems)

    def test_a_primitive_export_counts_as_observed(self):
        # `exports = null` or a string loads fine and has no object to walk;
        # the introspector records `not-applicable`. Calling that unobserved
        # would make the gate permanently red for a valid module, and the
        # artifact records no members for it either.
        import copy
        oracle = copy.deepcopy(self._oracle())
        oracle["tier1"]["url"] = {"status": "not-applicable"}
        self.assertEqual(
            gen.runtime_oracle_census(oracle, self._artifact()), [])

    def test_a_record_claiming_a_walk_must_carry_one(self):
        # `{"status": "walked"}` is a claim of observation with none in it.
        # For modules whose shapes are reached through tier2/tier3 rather
        # than tier1 -- http, movian/settings, url -- nothing downstream
        # notices the loss, so the census is the only barrier. Measured:
        # gutting tier1 for movian/sqlite or movian/page does fail the
        # member comparison; for these three it did not.
        import copy
        for name in ("http", "url", "movian/settings"):
            oracle = copy.deepcopy(self._oracle())
            oracle["tier1"][name] = {"status": "walked"}
            problems = gen.runtime_oracle_census(oracle, self._artifact())
            self.assertTrue(
                any("carries no functionExports" in problem
                    for problem in problems), (name, problems))

    def test_a_malformed_record_is_reported_not_raised(self):
        # A record that is not an object at all. Reporting it keeps the gate
        # a gate; raising turns a corrupt oracle into a traceback whose
        # meaning depends on the harness.
        import copy
        oracle = copy.deepcopy(self._oracle())
        oracle["tier1"]["fs"] = "unavailable"
        problems = gen.runtime_oracle_census(oracle, self._artifact())
        self.assertTrue(any("not an object" in problem
                            for problem in problems), problems)

    def test_a_module_the_capture_did_not_walk_fails(self):
        import copy
        oracle = copy.deepcopy(self._oracle())
        oracle["tier1"]["movian/page"] = {"status": "unavailable"}
        problems = gen.runtime_oracle_census(oracle, self._artifact())
        self.assertTrue(any("did not walk it (unavailable)" in problem
                            for problem in problems), problems)

    def test_an_oracle_without_tier1_fails(self):
        import copy
        oracle = copy.deepcopy(self._oracle())
        oracle.pop("tier1")
        self.assertTrue(gen.runtime_oracle_census(oracle, self._artifact()))

    def test_a_registration_this_census_cannot_read_fails(self):
        # `#define NAME "probe"` then `ES_MODULE(NAME, ...)`. The artifact
        # scanner cannot name it either, and natives are not files to
        # discover, so all three sides would be blind at once.
        victim = REPO_ROOT / "src" / "ecmascript" / "es_fs.c"
        original = victim.read_text(encoding="utf-8")
        try:
            victim.write_text(
                original + '\n#define PROBE_NAME "probe"\n'
                           'ES_MODULE(PROBE_NAME, fnlist_fs);\n',
                encoding="utf-8")
            self.assertTrue(gen.unresolved_native_registrations())
            problems = gen.runtime_oracle_census(
                self._oracle(), self._artifact())
            self.assertTrue(any("cannot read" in problem
                                for problem in problems), problems)
        finally:
            victim.write_text(original, encoding="utf-8")

    def test_a_commented_out_registration_is_not_a_module(self):
        # The mirror of the Makefile-comment rule, in the other language:
        # `/* ES_MODULE("dead", ...) */` registers nothing, and expecting it
        # makes the census go red on a correct runtime.
        victim = REPO_ROOT / "src" / "ecmascript" / "es_fs.c"
        original = victim.read_text(encoding="utf-8")
        try:
            victim.write_text(
                original + '\n/* ES_MODULE("dead", fnlist_fs); */\n'
                           '// ES_MODULE("alsodead", fnlist_fs);\n',
                encoding="utf-8")
            expected = gen.expected_runtime_modules()
            self.assertNotIn("native/dead", expected)
            self.assertNotIn("native/alsodead", expected)
            self.assertEqual(gen.unresolved_native_registrations(), [])
        finally:
            victim.write_text(original, encoding="utf-8")

    def test_nothing_is_unreachable_today(self):
        self.assertEqual(gen.unreachable_module_files(), [])

    def test_a_file_in_the_alias_namespace_is_rejected(self):
        # es_modsearch rewrites `showtime/` to `movian/` before resolving a
        # path, so `showtime/probe.js` on disk can never be loaded. Recording
        # it as a module file collapsed it into the alias of the same name --
        # one expected entry for two things, with the capture observing only
        # one of them, and the census reporting nothing.
        directory = (REPO_ROOT / "res" / "ecmascript" / "modules"
                     / "showtime")
        probe = directory / "probe.js"
        try:
            directory.mkdir(exist_ok=True)
            probe.write_text("exports.a = function(){};\n", encoding="utf-8")
            self.assertIsNone(
                gen.expected_runtime_modules().get("showtime/probe"))
            problems = gen.runtime_oracle_census(
                self._oracle(), self._artifact())
            self.assertTrue(any("cannot be loaded" in problem
                                for problem in problems), problems)
        finally:
            probe.unlink(missing_ok=True)
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()

    def test_nothing_shadows_a_core_module_today(self):
        self.assertEqual(gen.shadowing_plugin_modules(), [])

    def test_a_plugin_file_taking_a_core_module_id_fails(self):
        # es_modsearch tries the plugin directory first, so `url.js` here is
        # what require('url') returns -- the capture would describe this file
        # while the artifact describes the core module, under one name.
        shadow = gen.INTROSPECTOR_DIR / "url.js"
        try:
            shadow.write_text("exports.format = function(){};\n",
                              encoding="utf-8")
            problems = gen.shadowing_plugin_modules()
            self.assertTrue(any("shadows the core module url" in problem
                                for problem in problems), problems)
            self.assertTrue(any("shadows the core module url" in problem
                                for problem in gen.runtime_oracle_census(
                                    self._oracle())))
        finally:
            shadow.unlink(missing_ok=True)

    def test_nothing_registers_outside_the_artifact_scanner_today(self):
        self.assertEqual(gen.native_registrations_out_of_scope(), [])

    def test_an_expected_module_absent_from_the_artifact_fails(self):
        # `ES_MODULE ("probe", ...)` -- valid C, a space before the paren --
        # is read by the census and not by build_native_modules(), whose
        # regex anchors at the line start with no space. Aligning the two
        # regexes would be the wrong repair: the census would then read the
        # C exactly as the artifact scanner does and a syntax neither
        # understands would be missing from both. Two independent readings
        # that must agree is the point.
        import copy
        victim = REPO_ROOT / "src" / "ecmascript" / "es_fs.c"
        original = victim.read_text(encoding="utf-8")
        try:
            victim.write_text(
                original + '\nES_MODULE ("probe", fnlist_probe);\n',
                encoding="utf-8")
            oracle = copy.deepcopy(self._oracle())
            oracle["modules"] = oracle["modules"] + ["native/probe"]
            oracle["tier1"]["native/probe"] = {"status": "walked",
                                               "functionExports": {}}
            problems = gen.runtime_oracle_census(oracle, self._artifact())
            self.assertTrue(
                any("the artifact has no record of it" in problem
                    for problem in problems), problems)
        finally:
            victim.write_text(original, encoding="utf-8")

    def test_the_showtime_aliases_are_not_expected_in_the_artifact(self):
        # The artifact carries no record for an alias; es_modsearch resolves
        # it to the movian/* file. Requiring one would be 14 false failures.
        problems = gen.runtime_oracle_census(self._oracle(), self._artifact())
        self.assertEqual(problems, [])

    def test_the_census_without_an_artifact_refuses(self):
        # Rather than skipping the comparison it cannot make.
        self.assertTrue(gen.runtime_oracle_census(self._oracle()))

    def test_a_registration_the_artifact_scanner_cannot_see_fails(self):
        # `build_native_modules()` opens `es_*.c` and nothing else, so a
        # module registered in `ecmascript.c` is real at runtime and absent
        # from the artifact forever.
        victim = REPO_ROOT / "src" / "ecmascript" / "ecmascript.c"
        original = victim.read_text(encoding="utf-8")
        try:
            victim.write_text(
                original + '\nES_MODULE("probe", fnlist_probe);\n',
                encoding="utf-8")
            problems = gen.native_registrations_out_of_scope()
            self.assertTrue(any("never reads" in problem
                                for problem in problems), problems)
            self.assertNotIn("native/probe", gen.expected_runtime_modules())
            self.assertTrue(any("never reads" in problem for problem in
                                gen.runtime_oracle_census(
                                    self._oracle(), self._artifact())))
        finally:
            victim.write_text(original, encoding="utf-8")

    def test_a_macro_ending_in_es_module_is_not_a_registration(self):
        # `MY_ES_MODULE(...)` is a different macro. Without an identifier
        # boundary it reads as a registration and invents a module, or an
        # unresolved one -- a red on a tree that registers nothing.
        victim = REPO_ROOT / "src" / "ecmascript" / "es_fs.c"
        original = victim.read_text(encoding="utf-8")
        try:
            victim.write_text(
                original + '\nMY_ES_MODULE("probe", fnlist_fs);\n'
                           'MY_ES_MODULE(OTHER_NAME, fnlist_fs);\n',
                encoding="utf-8")
            self.assertNotIn("native/probe", gen.expected_runtime_modules())
            self.assertEqual(gen.unresolved_native_registrations(), [])
            self.assertEqual(gen.native_registrations_out_of_scope(), [])
        finally:
            victim.write_text(original, encoding="utf-8")

    def test_a_registration_inside_a_c_string_is_text(self):
        # The name reader cannot be fooled -- it needs an unescaped quote
        # after the paren and a quote inside a C string is escaped. The
        # invocation COUNTER can be, since `ES_MODULE(` needs no quote, and
        # it would then report an unreadable registration in a file that
        # registers nothing. Both halves are asserted because only the
        # second one actually needs the guard.
        names, unreadable = gen._c_registrations(
            'const char *s = "ES_MODULE(\\"phantom\\", f);";\n'
            'ES_MODULE("real", fnlist);\n')
        self.assertEqual(names, ["real"])
        self.assertEqual(unreadable, 0)

    def test_no_native_name_is_registered_twice_today(self):
        self.assertEqual(gen.duplicate_native_registrations(), [])

    def test_one_name_registered_from_two_files_is_reported(self):
        # The expectation dict keeps whichever came last, so every later
        # message would name the wrong file.
        victim = REPO_ROOT / "src" / "ecmascript" / "es_fs.c"
        original = victim.read_text(encoding="utf-8")
        try:
            victim.write_text(original + '\nES_MODULE("crypto", fnlist_fs);\n',
                              encoding="utf-8")
            problems = gen.duplicate_native_registrations()
            self.assertTrue(any("native/crypto is registered in both" in p
                                for p in problems), problems)
        finally:
            victim.write_text(original, encoding="utf-8")

    def test_no_registration_is_unresolved_today(self):
        self.assertEqual(gen.unresolved_native_registrations(), [])

    def test_every_module_file_is_expected_with_its_alias(self):
        expected = gen.expected_runtime_modules()
        self.assertIn("movian/page", expected)
        self.assertIn("showtime/page", expected)
        self.assertIn("fs", expected)
        self.assertNotIn("showtime/fs", expected)

    def test_natives_come_from_the_c_registrations(self):
        expected = gen.expected_runtime_modules()
        natives = {name for name in expected if name.startswith("native/")}
        self.assertIn("native/fs", natives)
        self.assertTrue(expected["native/fs"].startswith("ES_MODULE in "))

    def test_a_new_module_file_the_capture_never_loaded_fails(self):
        probe = (REPO_ROOT / "res" / "ecmascript" / "modules" / "movian"
                 / "probe.js")
        try:
            probe.write_text("exports['probe' + 'Fn'] = function(){};\n",
                             encoding="utf-8")
            problems = gen.runtime_oracle_census(
                self._oracle(), self._artifact())
            self.assertTrue(
                any("movian/probe exists" in problem for problem in problems),
                problems)
            self.assertTrue(
                any("showtime/probe exists" in problem
                    for problem in problems), problems)
        finally:
            probe.unlink(missing_ok=True)

    def test_a_new_native_registration_the_capture_never_loaded_fails(self):
        victim = REPO_ROOT / "src" / "ecmascript" / "es_fs.c"
        original = victim.read_text(encoding="utf-8")
        try:
            victim.write_text(original + '\nES_MODULE("probe", fnlist_fs);\n',
                              encoding="utf-8")
            problems = gen.runtime_oracle_census(
                self._oracle(), self._artifact())
            self.assertTrue(
                any("native/probe exists" in problem for problem in problems),
                problems)
        finally:
            victim.write_text(original, encoding="utf-8")

    def test_a_module_nothing_provides_fails(self):
        import copy
        oracle = copy.deepcopy(self._oracle())
        oracle["modules"] = oracle["modules"] + ["movian/ghost"]
        problems = gen.runtime_oracle_census(oracle, self._artifact())
        self.assertTrue(any("movian/ghost was loaded" in problem
                            for problem in problems), problems)

    def test_a_capture_that_could_not_enumerate_fails_the_check(self):
        # The capture records the failure rather than a short list. It has to
        # reach the verdict, or a run that could not look would pass as a run
        # that found nothing.
        import copy
        import json
        oracle = json.loads(
            gen.RUNTIME_ORACLE_PATH.read_text(encoding="utf-8"))
        artifact = json.loads(
            gen.ARTIFACT_PATH.read_text(encoding="utf-8"))
        ok, _out, _report = gen._check_runtime_oracle(artifact, oracle)
        self.assertTrue(ok)
        blinded = copy.deepcopy(oracle)
        blinded["moduleDiscoveryError"] = "Error: cannot list dataroot://..."
        ok, output, _report = gen._check_runtime_oracle(artifact, blinded)
        self.assertFalse(ok)
        self.assertIn("could not enumerate the module files", output)

    def test_a_capture_that_is_not_an_oracle_fails(self):
        self.assertTrue(gen.runtime_oracle_census(None, self._artifact()))
        self.assertTrue(
            gen.runtime_oracle_census("not an object", self._artifact()))


class Floor(unittest.TestCase):
    """The floor exists because an oracle that observed nothing scored
    perfectly. Each case is a way of scoring well by observing less."""

    def _unreachable(self, keys):
        return [{"module": m, "shape": s, "member": n} for m, s, n in keys]

    def test_clean_run_has_no_problems(self):
        reviewed = list(gen.RUNTIME_ORACLE_UNREACHABLE)
        self.assertEqual(
            gen._runtime_oracle_floor_problems(
                gen.RUNTIME_ORACLE_MIN_MATCH, self._unreachable(reviewed),
                gen.RUNTIME_ORACLE_MIN_MATCH + len(reviewed) + 2, 2),
            [])

    def test_an_unreviewed_exclusion_fails(self):
        reviewed = list(gen.RUNTIME_ORACLE_UNREACHABLE)
        grown = reviewed + [("movian/prop", "module", "print")]
        problems = gen._runtime_oracle_floor_problems(
            gen.RUNTIME_ORACLE_MIN_MATCH - 1, self._unreachable(grown),
            gen.RUNTIME_ORACLE_MIN_MATCH + len(reviewed) + 2, 2)
        self.assertTrue(any("unreviewed exclusion movian/prop.module.print"
                            in problem for problem in problems), problems)

    def test_an_exclusion_that_became_reachable_fails(self):
        reviewed = list(gen.RUNTIME_ORACLE_UNREACHABLE)
        problems = gen._runtime_oracle_floor_problems(
            gen.RUNTIME_ORACLE_MIN_MATCH + 1,
            self._unreachable(reviewed[1:]),
            gen.RUNTIME_ORACLE_MIN_MATCH + len(reviewed) + 2, 2)
        self.assertTrue(any(problem.startswith("stale exclusion")
                            for problem in problems), problems)

    def test_coverage_below_the_floor_fails(self):
        reviewed = list(gen.RUNTIME_ORACLE_UNREACHABLE)
        problems = gen._runtime_oracle_floor_problems(
            gen.RUNTIME_ORACLE_MIN_MATCH - 1, self._unreachable(reviewed),
            gen.RUNTIME_ORACLE_MIN_MATCH + len(reviewed) + 1, 2)
        self.assertTrue(any("below the floor" in problem
                            for problem in problems), problems)

    def test_a_slack_floor_fails(self):
        # The number cannot be set low enough to be safe. Lowering it to buy
        # room is the move this catches: the check recomputes what a clean
        # run must score and refuses anything below it.
        reviewed = list(gen.RUNTIME_ORACLE_UNREACHABLE)
        problems = gen._runtime_oracle_floor_problems(
            gen.RUNTIME_ORACLE_MIN_MATCH, self._unreachable(reviewed),
            gen.RUNTIME_ORACLE_MIN_MATCH + len(reviewed) + 10, 2)
        self.assertTrue(any("slack" in problem for problem in problems),
                        problems)


if __name__ == "__main__":
    unittest.main()
