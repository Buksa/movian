#!/usr/bin/env python3
"""Behavior tests for the tier2 construction the runtime oracle performs
(movian#237).

`RUNTIME_ORACLE_UNREACHABLE` is the residual gap: members no differential
covers, held shut by a hand-written note instead. ADR-0004 settled that the
only honest way to shrink it is to make the capture REACH the members, so
this file is about the one mechanism that can -- tier2 constructing a shape
offline -- and about the two ways that mechanism can lie.

The direction that matters is not the happy one. A construction that throws
and a construction that returns an empty object are indistinguishable to
anything downstream unless the payload says which happened, and they mean
opposite things:

* `status=failed` -- the runtime was never asked. The members stay
  unreachable, the excuse stays visible, and nothing claims to have measured
  them.
* `status=constructed` with an empty `result` -- a claim that the runtime HAS
  no such members. That is drift, and it must print as drift, because the
  remedy ("the shape lost these members") is the opposite of the remedy for a
  failed construction ("the capture could not run").

Getting those two the same way round is the whole point; `--check` on a
healthy tree cannot tell them apart, because on a healthy tree neither
happens.

Every probe below starts from the COMMITTED capture and corrupts one stage of
it, per the AGENTS.md standard: the envelope is what `introspector.js`
actually emits, and only the condition under test is changed. The stage
replacements are copied from `describeHttpConstruction()`'s own two
exits
(`support/devtools/api-introspector/introspector.js`), not invented.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
GEN_PY = REPO_ROOT / "support" / "devtools" / "metadata" / "gen.py"

_spec = importlib.util.spec_from_file_location("movian_metadata_gen", GEN_PY)
assert _spec is not None and _spec.loader is not None
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


# The six the issue is about: four the constructor sets on the instance, two
# the prototype carries. `gen.py` resolves them into different scopes -- own
# and prototype -- so a fix that reached only one half would still leave the
# other on the exclusion list.
REQUEST_MEMBERS = (
    ("http", "Request", "url"),
    ("http", "Request", "headers"),
    ("http", "Request", "onResponse"),
    ("http", "Request", "onError"),
    ("http", "Request", "end"),
    ("http", "Request", "on"),
)


def _stage_failed(error: str) -> dict:
    """What `describeHttpConstruction` emits from its catch."""
    return {
        "status": "failed",
        "factory": "request",
        "error": error,
        "unreachable": [{
            "class": "Request",
            "members": ["url", "headers", "onResponse", "onError"],
            "reason": "Request construction failed",
        }, {
            "class": "Response",
            "members": ["statusCode", "encoding", "bytes", "onData", "onEnd"],
            "reason": "Response is constructed only by the end() "
                      "transfer callback",
        }],
    }


class TierTwoConstruction(unittest.TestCase):
    def setUp(self) -> None:
        self.oracle = json.loads(
            gen.RUNTIME_ORACLE_PATH.read_text(encoding="utf-8"))
        self.artifact = json.loads(
            gen.ARTIFACT_PATH.read_text(encoding="utf-8"))
        # The committed stamp binds the capture to the sources it was taken
        # from, and every probe here edits the payload rather than the tree.
        # Re-stamping against THIS tree is what `--adopt-oracle` writes
        # (gen.py:8238), computed by the same functions -- without it the
        # check stops at freshness and never reaches the comparison these
        # tests are about, and every case below would pass for the wrong
        # reason.
        digests = gen.runtime_oracle_input_digests()
        self.oracle["inputs"] = {
            "version": gen.RUNTIME_ORACLE_INPUTS_VERSION,
            "digest": gen.runtime_oracle_inputs_digest(digests),
            "files": digests,
            "configuration": gen.runtime_oracle_configuration(),
            "selection": gen.makefile_ecmascript_selection(
                (gen.REPO_ROOT / "Makefile").read_text(encoding="utf-8")),
        }

    def _report(self, oracle: dict) -> dict:
        _ok, _output, report = gen._check_runtime_oracle(self.artifact, oracle)
        self.assertNotIn(
            "error", report,
            "the check stopped before comparing members: %s"
            % report.get("error"))
        return report

    def _keys(self, report: dict, field: str) -> set:
        return {(entry["module"], entry["shape"], entry["member"])
                for entry in report.get(field, [])}

    def test_the_capture_reaches_every_request_member(self) -> None:
        """The issue's own claim, measured rather than asserted.

        `http.js:61-64` formats a URL and calls `new Request(url)`; the socket
        opens in `end()`. So all six are constructible offline, and a capture
        that reaches them puts none of them on either list.
        """
        report = self._report(self.oracle)
        unreachable = self._keys(report, "unreachableMembers")
        drift = self._keys(report, "driftMembers")
        for key in REQUEST_MEMBERS:
            self.assertNotIn(key, unreachable)
            self.assertNotIn(key, drift)

    def test_a_failed_construction_keeps_the_members_unreachable(self) -> None:
        """DoD 3 and 4: failing honestly is failing to the SAFE side.

        The error text is one the producer writes: the catch records
        `String(e)`, and the guard just above it throws
        `new Error('request export is not callable')`
        (introspector.js, describeHttpConstruction). Observed in the same
        shape from a deliberately broken factory on the test stand, which
        recorded `Error: probe: factory refused` and left all six unreachable
        with drift 0.
        """
        oracle = copy.deepcopy(self.oracle)
        oracle["tier2"]["http"] = _stage_failed(
            "Error: request export is not callable")
        report = self._report(oracle)
        unreachable = self._keys(report, "unreachableMembers")
        drift = self._keys(report, "driftMembers")
        for key in REQUEST_MEMBERS:
            self.assertIn(
                key, unreachable,
                "a failed construction must leave %s unmeasured" % (key,))
            self.assertNotIn(
                key, drift,
                "a failed construction must not read as a missing member")

    def test_a_failed_construction_says_why(self) -> None:
        """The reason has to carry the failure, not a stale excuse.

        A reason nobody can act on is the failure mode #239 was closed for:
        the exclusion prints, the run is red, and the text names a cause that
        is not the one that happened.
        """
        oracle = copy.deepcopy(self.oracle)
        oracle["tier2"]["http"] = _stage_failed(
            "Error: request export is not callable")
        report = self._report(oracle)
        reasons = {
            (entry["module"], entry["shape"], entry["member"]): entry["reason"]
            for entry in report.get("unreachableMembers", [])
        }
        for key in REQUEST_MEMBERS:
            self.assertIn("status=failed", reasons.get(key, ""))
            self.assertIn("request export is not callable",
                          reasons.get(key, ""))

    def test_an_empty_construction_is_drift_not_agreement(self) -> None:
        """The dangerous direction, and the reason `failed` exists at all.

        This payload CLAIMS the construction succeeded and shows nothing on
        the instance. If that read as "unreachable" the two failures would be
        indistinguishable and a shape that genuinely lost its members would
        hide behind the same line as a capture that could not run.
        """
        oracle = copy.deepcopy(self.oracle)
        # `describeConstructed` always records a prototype level for an
        # object -- it only leaves `prototype` null when getPrototypeOf
        # returns null (introspector.js:524-532). A probe with no prototype
        # at all is one the producer cannot write, and it would pass this
        # test for free by keeping the two prototype members off the drift
        # list for a reason no capture could cause. So the empty shape here
        # is an object with an empty prototype level, which is what
        # constructing `{}` would actually yield.
        oracle["tier2"]["http"] = {
            "status": "constructed",
            "factory": "request",
            "result": {"type": "object", "keys": {},
                       "prototype": {"type": "object", "keys": {},
                                     "prototype": None},
                       "nested": {}},
            "unreachable": [],
        }
        report = self._report(oracle)
        drift = self._keys(report, "driftMembers")
        for key in REQUEST_MEMBERS:
            self.assertIn(
                key, drift,
                "an empty constructed shape must print as drift")

    def test_only_the_stage_factory_names_the_result(self) -> None:
        """A returned-shape fact belongs to the export that was CALLED.

        `returns` is a per-export fact and tier2 calls exactly one factory. If
        the result were attributed to every export declaring a shape return,
        one call would certify shapes nothing constructed -- and the capture
        would report members it never saw, which is the drift-in-the-wrong-
        direction this whole mechanism exists to avoid.
        """
        oracle = copy.deepcopy(self.oracle)
        stage = copy.deepcopy(oracle["tier2"]["http"])
        if stage.get("status") != "constructed":
            self.skipTest("the committed capture does not construct http yet")
        stage["factory"] = "get"
        oracle["tier2"]["http"] = stage
        report = self._report(oracle)
        unreachable = self._keys(report, "unreachableMembers")
        for key in REQUEST_MEMBERS:
            self.assertIn(
                key, unreachable,
                "%s was claimed by an export the stage did not call" % (key,))


if __name__ == "__main__":
    unittest.main(verbosity=2)
