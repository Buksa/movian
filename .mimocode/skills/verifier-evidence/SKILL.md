---
name: verifier-evidence
description: Use when writing a verifier report for the Buksa/movian issue-first pipeline. Codifies the evidence table format, DoD section structure, and pass/fail judgment rules that verifiers must follow. Trigger on "write verifier report", "evidence table", "DoD verification", or when acting as a fresh verifier agent.
---

# Verifier Evidence Standards

Every verifier report MUST follow this structure. Reports that deviate will be rejected by the orchestrator.

## Report Structure

```markdown
# Verifier Report — Issue #N

## DoD 1 — <name>
**Status: PASS | FAIL**

### Command(s)
<exact commands run>

### Evidence
<pasted output — not paraphrased, not summarized>

## DoD 2 — <name>
**Status: PASS | FAIL**

### Command(s)
...

### Evidence
...

(repeat for each DoD point)

## замечания
<any observations, flakes, or non-blocking notes>

## VERDICT: PASS | FAIL
PASS only if EVERY DoD point is PASS.

## DIGEST
≤15 lines summarizing what was verified and the outcome.
```

## Rules

1. **Own observations only**: Each DoD point must be independently demonstrated by the verifier. Substituting executor logs, claiming evidence from another agent's run, or replacing one required gate with a different check is grounds for rejection.

2. **Command + output pairs**: Every claim must be backed by the exact command that produced it and the verbatim output. No paraphrasing output as "it worked".

3. **Transport success ≠ proof**: HTTP 200 or eventSink OK proves only acceptance, not effect. Verify the actual mutation: prop changed, route transitioned, screenshot shows expected frame.

4. **Screenshot evidence**: When a DoD requires visual verification, include the screenshot path and a description of what it shows. Do not claim "screenshot taken" without describing the content.

5. **Log evidence**: When checking for absence of errors, show the grep command AND its empty output. Do not claim "no errors" without showing the check.

6. **VERDICT = all PASS**: A single FAIL DoD means the overall verdict is FAIL. No partial passes.

7. **Self-contradictory reports are rejected**: A verifier verdict that contradicts its own evidence section is rejected regardless of whether the candidate caused the failures. The supervisor must cross-check the VERDICT line against every DoD's evidence before accepting. Partial evidence cannot support a full PASS verdict. Specifically:
   - If the evidence shows N/M steps passed, the verdict cannot claim full PASS.
   - If the report classifies specific failures as "pre-existing" or "out of scope" rather than providing the required observation, that DoD point is not demonstrated.
   - Look for the gap between what the VERDICT claims and what the evidence actually demonstrates.

8. **Mechanism B capture timing**: When diagnosing wedged processes (504 on `/api/screenshot/raw` or similar health endpoints), diagnostic capture must occur *inside* the health-failure path, *before* any `kill_owned_pid()` or process termination is called. External debugger attachment (GDB/DAP) after the runner marks the instance for stop will always see an empty/stopped process because the runner's kill arrives first. Evidence from external post-stop attachment shows "stopped-clean" with no stack frames — this proves nothing about the wedge cause. Best-effort capture must be timeout-bounded so it never blocks the stop path.

9. **DIGEST format**: One line per DoD point, e.g.:
   - DoD 1: PASS — pre-fix alive/HTTP-up confirmed, event accepted, screenshot shows broken state
   - DoD 2: PASS — fix applied, build succeeds, no regressions
   - DoD 3: FAIL — soak showed 1/10 wedges

## Anti-patterns (from #125, #135, #138, #139 rejections)

- Reporting "logs show retry/teardown" without your own event-effect observation
- Substituting "four opened pages" for a corpus guard requirement
- Omitting `viewdoc --check` when the DoD explicitly requires it
- Claiming "HTTP 302 proves navigation" without checking the navigation property title/type
- Recording screenshot presence without describing what it shows
- Claiming full PASS when evidence shows only N/M steps passed (e.g., 4/7 smoke steps with 3 marked "pre-existing") — this is self-contradictory and rejected (#138)
- Classifying specific test failures as "pre-existing" or "out of scope" instead of providing the required observation — that DoD point is not demonstrated
- **Stolen-binary PASS** — copying another agent's already-built binary into your own build tree and testing that, instead of rebuilding from the candidate commit. The PASS reflects the other agent's build. You must build from the candidate yourself, or cite static-marker proof (verifier-checks Rule 2) tied to the candidate commit.
- **Wrong-metric PASS** — comparing a different metric than the DoD specifies (total wall time vs. per-operation latency, a count vs. a rate, etc.) and calling PASS because the wrong number looks fine. Report exactly the metric the DoD names.
- Capturing diagnostic state (thread dumps, process trees) *after* the runner's kill has already terminated the process — the evidence will always show "stopped-clean" with empty frames (#135, #138 Mechanism B)
- Running dynamic tests (mdev, smbclient) without first proving the binary contains the commit's code via `strings`/`nm`/DWARF static markers (#139)
- Using `cp --reflink=always` without a normal-copy fallback — fails on ext4 (#139)

## See also

**`verifier-checks`** skill for reusable checks that layer on top of this report format:
- Rule 1: Shared build trees are read-only
- Rule 2: Static source markers before dynamic proof
- Rule 3: Portable `cp` with reflink fallback
- Rule 4: Verdict-evidence consistency (cross-check)
- Rule 5: Mandatory `--dangerously-skip-permissions` for Orca launches
- Rule 6: Build freshness before dynamic tests
