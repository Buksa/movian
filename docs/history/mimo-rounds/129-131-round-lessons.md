# Digest: Rounds #129 and #131 — Reusable Pipeline Lessons

Extracted 2026-07-20 from merged PR #132 (`0e38e435a`) and PR #133 (`a1d118718`), issue threads, and verifier/connector reports.

---

## Lesson 1 — Verifier evidence must be audited against every required observation

**Trigger:** Terra re-verifier for #129 passed DoD 1–3 but explicitly skipped the dispatch-required DoD 4 (failure-bundle rerun) and DoD 5 (exact 7/7 gate), then reported only a partial verdict. MiMo connector verifier marked cross-cwd PASS but tested absolute output across cwd, omitting the specified read-only metadata boundary.

**Rule:** An overbroad PASS is rejected and the verifier channel changes. The orchestrator must audit every claim in a verifier report against every required DoD observation before accepting. If a verifier skips a required check, the verdict is rejected and a fresh verifier on a different channel is dispatched.

**Evidence pattern:** Look for reports that claim PASS while explicitly noting they "did not rerun" or "skipped" a required gate. The incidents journal at `incidents.md` entries from 2026-07-20 record two such rejections in this round.

---

## Lesson 2 — Overlapping green PRs need sequential merge with combined re-verification

**Trigger:** PR #132 (wedge cleanup) and PR #133 (screenshot hashing) both touched `cli.py`/`harness.py`/`smoke.py` and were each green on their own branches. Parallel merge would have caused textual conflicts and untested interactions.

**Rule:** When two green PRs overlap on the same files:
1. Merge the lower-risk or prerequisite PR first (#132, wedge cleanup).
2. Rebase the second PR branch onto the new `movian6` HEAD.
3. Run combined behavioral verification on the updated tree (real SIGSTOP wedge + protected cross-cwd screenshot + both lock orderings + exact 7/7 smoke).
4. Only then merge the second PR.

**Key fact:** The combined tree passed on first attempt after rebase, but the re-verification was essential — it caught that the two features interact through shared state in `harness.py` (`save_state` vs `record_shot_hash` lock ordering).

---

## Lesson 3 — mdev wedge cleanup: diagnostics before termination, outcome recorded

**Trigger:** Issue #131 root cause: `smoke.py` only printed "stop+relaunch" advice but never actually stopped wedged instances. Burst amplification traced to this gap. Initial fix captured bundle after cleanup, discarding live diagnostic data.

**Rule:** In the wedge cleanup path, the failure bundle (`steps.json`, `props.json`, `log-tail.txt`) must be written while the owned instance is still alive, then the stop outcome (`stopped-clean`, `killed-after-timeout`, `still-alive`) is appended to `steps.json` after termination. Two ordering constraints:
- Bundle capture BEFORE `kill_owned_pid()` / `stop_wedged_instance()` — a stopped server returns `not found` for its prop tree.
- `cmd_run --force` must refuse launch when the owned pid survives (exit 2) — the initial fix had a hole where `kill_owned_pid() == "still-alive"` was discarded and `launch()` proceeded.

**Evidence pattern:** Real SIGSTOP wedge proved the ordering: `props.json` captured live props before SIGKILL, then `steps.json` recorded `killed-after-timeout` and the old PID was confirmed absent via `/proc/<pid>`.

---

## Lesson 4 — Screenshot equality suppresses file creation, not vision judgment

**Trigger:** Issue #129 initial implementation compared hashes after `take_shot()` returned, so a timestamp-separated call left a duplicate file. Read-only canonical `--out` path failed at `write_bytes` instead of exiting 3. Cross-cwd relative paths became unusable when consumed from another working directory.

**Rule:** The equality check must happen inside `take_shot()` before path creation and write. The compound key for vision-cache skip is `(sha256_hash, exact_question)` — equal bytes with different question still require a vision call. Exit 3 means "no duplicate file created", not "verdict cached". State updates (`last_shot_path`, `last_shot_hash`) must use absolute resolved paths (`shot_path.resolve()`) and be protected by `fcntl.flock(LOCK_EX)` with atomic `.tmp` → `.replace()` writes to survive cwd and restart races.

**Key invariant:** `record_shot_hash` merges into existing launch state without clobbering `pid`/`port`/`started`/`argv`. Both lock orderings (record-then-save vs save-then-record) must produce valid state with the correct final values.

---

## Source trail

- Issue #129: screenshot hash comparison and unchanged-skip behavior in mdev
- Issue #131: wedge cleanup with SIGKILL escalation, diagnostics preservation, stop outcome recording
- PR #132: `0e38e435a` — wedge cleanup (#131)
- PR #133: `a1d118718` — screenshot hashing (#129)
- Verifier reports: `verifier-129-terra.md`, `verifier-129-rework-terra.md`, `verifier-129-connector-mimo.md`, `verifier-129-final-mimo.md`, `verifier-129-sol-exact.md`, `verifier-131-terra.md`, `verifier-131-rework-terra.md`, `verifier-131-connector.md`
- Incidents journal: entries from 2026-07-20 (verifier-proof, verifier-proof)
