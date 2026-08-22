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
20 modules it covers and moved no member; under a byte-level stamp that
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
            paths.extend(sorted(REPO_ROOT.glob(pattern)))
        return paths

    def test_every_input_is_covered(self):
        self.assertEqual(len(self._inputs()), 21)

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
