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
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("same capturedAt", result.stderr)
        # The refusal must come before the write.
        self.assertEqual(gen.RUNTIME_ORACLE_PATH.read_bytes(), before)

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
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("capturedAt", result.stderr)
        self.assertEqual(gen.RUNTIME_ORACLE_PATH.read_bytes(), before)


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

    def test_a_blind_parser_is_a_problem_not_a_pass(self):
        # A parser that stops matching returns an empty selection, and an
        # empty selection compares equal to another empty one -- green
        # because it read nothing, which is the failure this file is about.
        self.assertTrue(gen._selection_problems({}))

    def test_a_source_dropped_since_the_build_is_reported(self):
        here = gen.makefile_ecmascript_selection(self._makefile())
        there = dict(here)
        here.pop("src/ecmascript/es_fs.c")
        reasons = gen.selection_mismatch(here, there, "abc1234")
        self.assertEqual(
            reasons,
            ["src/ecmascript/es_fs.c was compiled into build abc1234 and the"
             " recipe no longer names it"])

    def test_a_source_added_since_the_build_is_reported(self):
        here = gen.makefile_ecmascript_selection(self._makefile())
        there = dict(here)
        there.pop("src/ecmascript/es_fs.c")
        reasons = gen.selection_mismatch(here, there, "abc1234")
        self.assertTrue(any("is compiled now" in reason
                            for reason in reasons), reasons)

    def test_a_source_that_changed_gate_is_reported(self):
        # The sharpest case: membership is identical and the file has not
        # changed a byte, but it is now compiled in a different configuration.
        here = gen.makefile_ecmascript_selection(self._makefile())
        there = dict(here)
        there["src/ecmascript/es_sqlite.c"] = "SRCS"
        reasons = gen.selection_mismatch(here, there, "abc1234")
        self.assertEqual(
            reasons,
            ["src/ecmascript/es_sqlite.c moved from SRCS to"
             " SRCS-$(CONFIG_SQLITE) since build abc1234"])

    def test_an_unchanged_recipe_reports_nothing(self):
        here = gen.makefile_ecmascript_selection(self._makefile())
        self.assertEqual(gen.selection_mismatch(here, dict(here), "abc"), [])

    def test_a_source_the_recipe_stops_naming_is_a_problem(self):
        selection = gen.makefile_ecmascript_selection(self._makefile())
        selection.pop("src/ecmascript/es_fs.c")
        problems = gen._selection_problems(selection)
        self.assertTrue(any("es_fs.c" in problem for problem in problems),
                        problems)


class ModuleCensus(unittest.TestCase):
    """A module nobody listed was unobserved by the capture and, in syntax
    the scanner cannot read, missing from the artifact too. Two blind sides
    agree about nothing."""

    def _oracle(self):
        import json
        return json.loads(
            gen.RUNTIME_ORACLE_PATH.read_text(encoding="utf-8"))

    def test_the_committed_capture_walked_everything_this_tree_has(self):
        self.assertEqual(gen.runtime_oracle_census(self._oracle()), [])

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
        problems = gen.runtime_oracle_census(oracle)
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
        self.assertEqual(gen.runtime_oracle_census(oracle), [])

    def test_a_module_the_capture_did_not_walk_fails(self):
        import copy
        oracle = copy.deepcopy(self._oracle())
        oracle["tier1"]["movian/page"] = {"status": "unavailable"}
        problems = gen.runtime_oracle_census(oracle)
        self.assertTrue(any("did not walk it (unavailable)" in problem
                            for problem in problems), problems)

    def test_an_oracle_without_tier1_fails(self):
        import copy
        oracle = copy.deepcopy(self._oracle())
        oracle.pop("tier1")
        self.assertTrue(gen.runtime_oracle_census(oracle))

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
            problems = gen.runtime_oracle_census(self._oracle())
            self.assertTrue(any("cannot read" in problem
                                for problem in problems), problems)
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
            problems = gen.runtime_oracle_census(self._oracle())
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
            problems = gen.runtime_oracle_census(self._oracle())
            self.assertTrue(
                any("native/probe exists" in problem for problem in problems),
                problems)
        finally:
            victim.write_text(original, encoding="utf-8")

    def test_a_module_nothing_provides_fails(self):
        import copy
        oracle = copy.deepcopy(self._oracle())
        oracle["modules"] = oracle["modules"] + ["movian/ghost"]
        problems = gen.runtime_oracle_census(oracle)
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
        self.assertTrue(gen.runtime_oracle_census(None))
        self.assertTrue(gen.runtime_oracle_census("not an object"))


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
