---
name: verifier-checks
description: Reusable verifier checks for the Buksa/movian issue-first pipeline. Covers build-tree isolation, source-marker validation before dynamic proof, portable file-copy fallbacks, and verdict-evidence consistency. Trigger when acting as a verifier, reviewing verifier reports, or when the orchestrator audits evidence quality. Layered on verifier-evidence (report format) and mdev-plugin-testing (harness mechanics).
---

# Verifier Checks — Reusable Rules

Generalized from #139 and earlier rounds. Every verifier must satisfy these checks before issuing a verdict. The orchestrator audits against them during review.

## 1. Shared Build Trees Are Read-Only

A shared `build.debug/` or `build.release/` tree belongs to the build system and must never be mutated by a verifier or executor.

**What this means:**
- Do NOT modify, overwrite, or delete files inside `build.debug/` or `build.release/` during verification.
- Do NOT write temporary test artifacts, captured binaries, or diagnostic dumps into the build tree.
- If you need to inspect a built binary, copy it to a temporary location first (see Rule 3 for portable copy).
- If the build tree is missing or stale, that is a build-system problem — report it as a blocker, do not "fix" it by editing files in-place.

**Why:** Shared build trees are rebuilt by `make` without awareness of verifier mutations. In-place edits create phantom test results that do not correspond to any committed code state.

## 2. Private Candidate Builds Must Validate Source Markers Before Dynamic Proof

When verifying that a commit's code is present in a candidate build (e.g., a `.deb`, a pre-built binary, or a copied build tree), static source-level evidence must come before any dynamic test (running the binary, launching mdev, taking screenshots).

**Required static checks (in order):**
1. **Source marker via `strings`**: Extract a human-readable string that exists only in the new code. Verify it appears in the binary:
   ```sh
   strings build.debug/movian | grep -c '<unique-marker>'
   ```
   Expected: `≥ 1`. Evidence: paste the `strings` output line containing the marker.

2. **Symbol presence via `nm`**: Verify the new/modified function symbol is linked into the binary:
   ```sh
   nm build.debug/movian | grep '<function_name>'
   ```
   Expected: at least one match (T/t for text, D/d for data). Evidence: paste the `nm` line.

3. **DWARF source-file association** (when available): Confirm the binary was compiled from the expected source file:
   ```sh
   readelf -wF build.debug/movian 2>/dev/null | grep '<source_file>'
   ```
   Or:
   ```sh
   objdump -g build.debug/movian 2>/dev/null | grep '<source_file>'
   ```
   Expected: match present. If DWARF is stripped, note "DWARF stripped — static proof limited to strings+nm".

**Why:** A candidate build may contain stale binaries from a prior build. Running dynamic tests against a stale build produces false positives — the feature appears to work because of a previous build, not the commit under test. Static markers prove the commit's code was actually compiled into the binary being tested.

**Anti-pattern:** Jumping straight to `mdev run` or `smbclient` tests without first proving the binary contains the expected code. If the build is shared and not freshly rebuilt for this commit, the dynamic test is untrustworthy.

## 3. Portable File Copy: `cp --reflink=always` Is Not Safe on ext4

When a verifier or executor needs to copy a binary, build artifact, or test fixture:

**Always use a safe fallback:**
```sh
cp --reflink=always <src> <dst> 2>/dev/null || cp <src> <dst>
```

Or equivalently:
```sh
if ! cp --reflink=always "$src" "$dst" 2>/dev/null; then
  cp "$src" "$dst"
fi
```

**Why:** `cp --reflink=always` relies on Copy-on-Write (CoW) filesystem support (btrfs, XFS with reflink). On ext4 (the default filesystem for most Ubuntu/Debian systems, including CI runners and Steam Deck), reflink is not supported. `cp --reflink=always` fails with:
```
cp: failed to clone '...' from reflink: Operation not supported
```

A bare `cp` (normal copy) is always safe and portable, just slower for large files. The reflink attempt is a performance optimization — the normal copy is the correctness requirement.

**Scope:** This applies to any copy operation in verification scripts, mdev tooling, smoke tests, and executor build-validation steps. The fallback pattern is mandatory — never assume reflink is available.

## 4. Verdict-Evidence Consistency: Never Accept a Contradictory Summary

The VERDICT line and the DIGEST summary must be consistent with every individual DoD's evidence. A verdict that contradicts its own evidence is rejected regardless of whether the candidate caused the failures.

**What to check:**
- If the DIGEST says "0/7 smoke steps passed" but the evidence table shows 3/7 PASS, that is a self-contradiction → reject.
- If the DIGEST says "all checks passed" but individual DoD sections note failures classified as "pre-existing" or "out of scope", those DoD points are not demonstrated → reject.
- If the VERDICT says PASS but any DoD section has status FAIL or notes incomplete evidence, that is a contradiction → reject.

**How to cross-check:**
1. Read the VERDICT line.
2. Read the DIGEST summary.
3. For each DoD section, check the Status line and Evidence content.
4. Verify: VERDICT == PASS implies every DoD Status == PASS and DIGEST count matches.
5. If any inconsistency is found, reject the report and request a corrected version.

**Why:** Self-contradictory reports indicate the verifier did not audit its own output before submitting. Accepting them masks real failures and erodes trust in the verification process. Observed in #138 (4/7 steps claimed as full PASS) and #135 (DoD points skipped then reported as passed).

## 5. Orca Launch: Mandatory `--dangerously-skip-permissions`

When launching `mimo` (MiMoCode) from an Orca worktree or automation context, the `--dangerously-skip-permissions` flag is **mandatory**.

**Rule:** Every `mimo run` or `mimo` invocation in an Orca-managed automation, worktree handoff, or pipeline context MUST include `--dangerously-skip-permissions`. Do not omit it. Do not gate it behind a configuration flag. Do not use it conditionally.

**Why:** Orca worktrees operate headlessly — there is no human at the terminal to approve permission prompts. Without `--dangerously-skip-permissions`, the mimo process blocks on permission asks and the automation stalls or times out. The flag is the documented and intended mechanism for headless/automation contexts.

**Scope:** This applies to:
- `mimo run` invocations in orchestrator dispatches
- mimo processes spawned by the `mdev-lifecycle` tool
- Any automated pipeline step that launches mimo
- Worktree handoffs between agent CLIs

## 6. Build Freshness Before Dynamic Tests

Before running any dynamic test (mdev, smbclient, curl, etc.), verify the build is fresh for the commit under test.

**Required check:**
```sh
# The binary's mtime against the commit's. `git diff` CANNOT answer this:
# .gitignore:1 is `/build.*`, so `git diff -- build.debug/` prints nothing on a
# fresh tree and nothing on a six-month-old one. It was the required check here
# and it could not fail. (incident-class: vacuous-gate)
test build.debug/movian -nt .git/HEAD && echo "binary newer than HEAD" \
  || echo "STALE: binary predates the checked-out commit -- rebuild"
strings build.debug/movian | grep -c '<marker from the change under test>'
```
If the binary predates HEAD, or the marker count is 0, either:
- Rebuild: `make BUILD=debug -j$(nproc)`
- Or explicitly document that the shared build is being reused and cite the static-marker evidence (Rule 2) that proves the commit's code is present.

**Why:** A stale build tree produces tests against old code. The static-marker check (Rule 2) is the fallback when a fresh build is not feasible, but a fresh build is always preferred.

## Anti-patterns Summary

| Anti-pattern | Rule violated | Source round |
|---|---|---|
| Modifying files in `build.debug/` during verification | Rule 1 | General |
| Running `mdev run` without first proving binary contains commit code | Rule 2 | #139 |
| `cp --reflink=always` without fallback on ext4 systems | Rule 3 | #139 |
| Claiming "0/7 passed" when evidence shows 3/7 PASS | Rule 4 | #138 |
| Classifying failures as "pre-existing" to claim full PASS | Rule 4 | #138 |
| Launching mimo without `--dangerously-skip-permissions` in automation | Rule 5 | General |
| Testing against stale build without static-marker proof | Rule 6 | #139 |
| Capturing diagnostics after runner kills the process | Rule 2 of verifier-evidence | #135, #138 |

## Source trail

- Issue #139: Mechanism B GLW/screenshot wedge root-cause and capture improvements
- Issue #138: GLW completion verification (self-contradictory report rejection)
- Issue #135: TypeScript calibration (Mechanism B capture timing)
- PR #142: `4160c5ef4` — pre-kill thread-backtrace capture, GDB gating, prctl
- Digest: `.mimocode/digest/135-138-round-lessons.md`
- Related skills: `verifier-evidence` (report format), `mdev-plugin-testing` (harness mechanics)
