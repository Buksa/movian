# Digest: Round #139 — Reusable Pipeline Lessons

Extracted 2026-07-22 from PR #142 (`4160c5ef4`), issue #139 thread, and verifier/connector reports across #135–#139.

---

## Lesson 1 — Shared build trees are read-only

**Trigger:** Verifier attempted to inspect a built binary in `build.debug/` and the question of whether to modify or replace files in the shared build tree arose. Shared trees are rebuilt by `make` without awareness of verifier mutations.

**Rule:** The shared `build.debug/` or `build.release/` tree must never be modified by a verifier or executor. If inspection is needed, copy the binary to a temporary location first. If the build is stale, report it as a blocker — do not "fix" it by editing files in-place.

**Generalized to:** Verifier-checks Rule 1.

---

## Lesson 2 — Static source markers must precede dynamic proof

**Trigger:** #139 involved verifying that a commit's code was present in a candidate build. Running dynamic tests (mdev, smbclient) without first proving the binary contained the commit's code produced ambiguous results — the feature appeared to work, but it was unclear whether the binary was freshly built or stale from a prior build.

**Rule:** Before any dynamic test, validate the binary contains the commit's code via:
1. `strings <binary> | grep '<unique-marker>'` — human-readable marker from the new code
2. `nm <binary> | grep '<function_name>'` — symbol presence
3. `readelf -wF <binary> | grep '<source_file>'` — DWARF source association (when available)

**Why:** A stale build produces false positives. Static markers are the only way to prove the commit's code was actually compiled into the binary under test.

**Generalized to:** Verifier-checks Rule 2.

---

## Lesson 3 — `cp --reflink=always` is not portable on ext4

**Trigger:** A verification script used `cp --reflink=always` to duplicate a binary for inspection. On the ext4 filesystem (Ubuntu default), this failed with "Operation not supported" because ext4 does not support Copy-on-Write reflinks.

**Rule:** Always use a safe fallback: `cp --reflink=always <src> <dst> 2>/dev/null || cp <src> <dst>`. The reflink is a performance optimization; the normal copy is the correctness requirement.

**Scope:** Applies to all copy operations in verification scripts, mdev tooling, smoke tests, and executor build-validation steps.

**Generalized to:** Verifier-checks Rule 3.

---

## Lesson 4 — Verdict-evidence consistency is non-negotiable

**Trigger:** #138 initial verifier claimed DoD 8 PASS but its own evidence showed only 4/7 smoke steps passed (preview-demo, preview-pilot, js-reload failed). The report classified those failures as "pre-existing" rather than providing the required observation.

**Rule:** The VERDICT line and DIGEST summary must be consistent with every individual DoD's evidence. A verdict that contradicts its own evidence is rejected regardless of whether the candidate caused the failures. Cross-check: VERDICT == PASS implies every DoD Status == PASS and DIGEST count matches.

**Previously documented in:** verifier-evidence Rule 7, orchestrator-pipeline audit section.

**Generalized to:** Verifier-checks Rule 4.

---

## Lesson 5 — Orca launch requires `--dangerously-skip-permissions`

**Trigger:** Orca worktrees operate headlessly. Without `--dangerously-skip-permissions`, mimo processes block on permission asks and automations stall.

**Rule:** Every `mimo run` invocation in an Orca-managed automation, worktree handoff, or pipeline context MUST include `--dangerously-skip-permissions`. This is the documented and intended mechanism for headless/automation contexts.

**Generalized to:** Verifier-checks Rule 5.

---

## Lesson 6 — Build freshness before dynamic tests

**Trigger:** Shared build trees may be stale from prior rounds. Testing against a stale build produces results that do not correspond to the commit under test.

**Rule:** Before dynamic tests, verify build freshness via `git diff --name-only HEAD -- build.debug/`. If stale, rebuild or cite static-marker evidence (Rule 2) proving the commit's code is present.

**Generalized to:** Verifier-checks Rule 6.

---

## Source trail

- Issue #139: Mechanism B GLW/screenshot wedge root-cause and capture improvements
- Issue #138: GLW completion verification (self-contradictory report rejection)
- Issue #135: TypeScript calibration (Mechanism B capture timing)
- PR #142: `4160c5ef4` — pre-kill thread-backtrace capture, GDB gating, prctl
- PR #140: `04eccec98` — TypeScript calibration fixtures
- PR #141: `2c03c9405` — GLW completion
- Digest: `.mimocode/digest/135-138-round-lessons.md`
- Skills: `verifier-checks` (new), `verifier-evidence`, `mdev-plugin-testing`, `orchestrator-pipeline`
