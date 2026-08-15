# Issue #146 Round Lessons

## What Was Delivered

A wedge lifecycle capture instrument for the #143 umbrella, merged as PR #151 (`376eb19db..1b9352d40`):

1. **Same-session GDB wedge capture** (`support/devtools/gdb/movian_lifecycle.py`) — the existing launch-attached GDB session (from #144) now serves as the wedge capture back-end. On a health/screenshot wedge classification, the smoke harness writes a request to the instance state directory and signals the inferior via pidfd SIGSTOP. The owned GDB session captures a full `thread apply all bt` before bounded cleanup, producing the final 149-frame/15-thread proof instead of the empty dumps that #139's reactive `gdb -p <pid>` produced due to the PID race.

2. **Emergency-eject tracker** (`EmergencyEjectTracker`) — a pure state machine (`unobserved → not-requested → requested → armed → fired`) driven by three mandatory core-init probes (`app_shutdown`, `shutdown_eject`, `arch_exit`). The tracker advances before the rate-limit cap check, so mandatory eject transitions are never suppressed by category-level disabling. The snapshot is embedded in both JSONL enter events and wedge-capture responses.

3. **Wedge-control GDB command** (`MovianLifecycleWedgeControl`) — a post-`run` loop that captures only SIGSTOP signals paired with a fully-identified current request (protocol/session/GDB PID/inferior PID/request ID match). Any non-SIGSTOP stop (SIGSEGV, SIGABRT, etc.) is delivered with its original signal so the inferior terminates truthfully — no crash suppression, no signal-0 tight loop.

4. **Smoke harness integration** (`support/devtools/mdevlib/smoke.py`) — `_capture_wedge_backtrace()` uses the launch-attached GDB session via pidfd SIGSTOP (never numeric `os.kill`, never reactive `gdb --attach`). The harness writes the request; the collector captures the backtrace. Ordering: `classified → capture → owned-cleanup → gdb-cleanup → final-owned-cleanup`.

5. **Wedge event schema** (`validate_wedge_event()`) — deterministic validation of same-session wedge events including `emergencyEject` state consistency, `session` identity fields, `remainingThreads` structure, and `capture` status/thread-count matching.

6. **Regression tests** (`tests/tooling/gdb/test_wedge_lifecycle.py`, 1079 lines) — deterministic coverage for stale stop recomputation, GDB basename matching with 15-byte comm truncation, `--nx` presence, schema error appending, crash signal propagation, consumed-request rejection, and emergency-eject state transitions.

## Technical Lessons

### Launch-Attached Same-Session Capture Before main_init

**Lesson:** The collector attaches at process launch (`--batch -x cmdfile` with `run` + `movian-lifecycle-wedge-control`), so the GDB session is already present and all mandatory eject probes are armed before `main_init` executes. This is fundamentally different from #139's reactive `gdb -p <pid>` which raced `kill_owned_pid()` and produced empty thread dumps. The same-session approach guarantees that when a wedge classification arrives, the GDB session is already tracing the inferior — no attach race, no PID reuse window.

**Evidence:** The harness proof at `pr151e16-wedge-harness` captured 149 frames across 15 threads with `capture-source: launch-attached-gdb`. The earlier `finalhead146-harness` captured 151 frames across 16 threads. Both contrast explicitly against #139's PR #142 which captured 382/373 pre-kill frames only by racing cleanup, while the external-attach approach produced empty dumps.

### Final Stop Recomputation After Debugger Cleanup

**Lesson:** The first owned stop outcome can be stale. If `stop_wedged_instance()` reports `still-alive` but GDB cleanup releases ptrace and the inferior is then gone, the final result must be corrected to `stopped-clean` with `final-owned-cleanup:already-gone`. Without this recomputation, the stale `still-alive` propagates as a false failure.

**Evidence:** `StaleStopOutcomeTest.test_stale_still_alive_corrected_when_inferior_dies_during_cleanup` proves the fix: `stop_outcome == "stopped-clean"` and `"final-owned-cleanup:already-gone" in transcript["ordering"]`. The real harness runs confirm: `classified → capture:success → owned-cleanup:stopped-clean → gdb-cleanup:exited → final-owned-cleanup:already-gone`.

### Configured GDB Basename and 15-Byte Comm

**Lesson:** `_gdb_process_matches()` must accept the configured GDB executable basename, not just bare `gdb`. Linux truncates `/proc/PID/comm` to 15 bytes, so `aarch64-linux-gnu-gdb` becomes `aarch64-linux-g`. The matcher must handle: standard `gdb`, `gdb-multiarch`, truncated cross-build names, and old-state fallback when `gdbBasename` is absent.

**Evidence:** `GdbIdentityMatchTest` covers five scenarios: `gdb-multiarch` matches, truncated comm matches, old-state defaults to `gdb`, wrong binary rejected. All pass at HEAD; `gdb-multiarch` and truncated comm fail against pre-fix `adc10006f`.

### --nx Suppresses Foreign Init Files

**Lesson:** Every GDB launch must include `--nx` to suppress system (`/etc/gdb/gdbinit`) and user (`~/.gdbinit`) init files. Foreign Python hooks or redefined commands in init files can interfere with the collector's breakpoint installation, causing silent failures or unexpected behavior. This applies to all GDB variants including `gdb-multiarch`.

**Evidence:** `GdbArgvNxTest` verifies `--nx` is present in `_build_gdb_argv()` for both `gdb` and `gdb-multiarch`. Pre-fix `adc10006f` lacks `--nx`.

### GDB Detail Plus Appended Schema Errors

**Lesson:** When `gdb.execute('thread apply all bt')` fails, the `threadCount` must still be set from `_all_thread_info()`, and any later schema error must be *appended* to the original GDB error detail, not overwrite it. Before the fix, `threadCount` stayed 0 in the except block (causing a spurious schema mismatch), and the schema error completely replaced the GDB failure message.

**Evidence:** `CaptureDetailPreservationTest.test_backtrace_failure_sets_thread_count_from_all_thread_info` asserts: `threadCount == 3`, detail contains both `"PC register is not available"` and `"wedge event schema: forced schema error"`, and the GDB error is not lost.

### Real Fail-Before Method

**Lesson:** Pre-fix proof must run the new tests against production code from a known old commit; checking out the whole old commit and getting “test class missing” is not behavioral evidence. Against `adc10006f`, six targeted assertions failed: stale stop outcome; `gdb-multiarch` and truncated cross-GDB `comm` rejection; missing `--nx` for standard and multiarch GDB; and backtrace failure leaving `threadCount=0` while losing the original detail. Three compatibility guards correctly passed.

**Evidence:** Pre-fix proof at `/tmp/movian-ai-evals/issue-146-eject-proof/pre-fix-fail/`. The rejected intermediate report (`issue-146-eject-proof/final-report.md`) incorrectly claimed "all 9 tests fail" — the three compatibility tests (`standard_gdb_still_matches`, `old_state_without_gdbBasename_defaults_to_gdb`, `wrong_binary_name_rejected`) correctly pass pre-fix. The final report corrects this.

### Same-Run Natural Classifier Proof vs Manual/Conflated Evidence

**Lesson:** The definitive proof must be a *same-run* capture where the harness creates the request naturally (the smoke health step times out and writes its own wedge request), not a manually injected request. Manual request injection conflates the capture path with the classification path and doesn't prove the harness integration works end-to-end.

**Evidence:** At `pr151e16-wedge-harness`, the harness ran `mdev smoke run health` as a real subprocess. After 40.5s the health smoke wrote its own request when Movian was frozen. The classification (`instance-health-wedge`), subsystem (`startup-readiness`), and resource (`global/userinterfaces/ui/framerate`) were all harness-generated, not manually set. All 22 machine-readable verdicts pass.

### Signed Overhead Formula and Exact Fields

**Lesson:** The locked overhead gate uses the signed diff (collector median minus plain median), with threshold `max(10% of plain_median, 100ms)`. The exact fields are `screenshotLatencyMs` and `startupMsInternal`; no absolute-difference gate exists.

**Evidence:** From `metrics.json`: screenshot signed diff = -5ms (threshold 100ms), startup signed diff = -622ms (threshold 100ms). Both PASS under the locked overhead contract. The noisy negative values do not establish that instrumentation makes startup or screenshots faster.

### Evidence Audit Overriding Verdicts

**Lesson:** Verdicts are untrusted until raw evidence is audited. The first rejected report (`issue-146-eject-proof/final-report.md`) armed all collector categories instead of core-init, omitted the resulting startup failure (68→471ms, +403ms), labeled a manually written request as classifier-driven, conflated its ordering with different `capture:skipped` runs, and claimed all nine regressions failed although three compatibility guards passed. A later correction briefly introduced an unrequested absolute-difference gate; the authoritative final report uses the locked signed formula.

**Evidence:** The rejected bundle remains useful only for its commit-pinned pre-fix raw proof. The authoritative runtime evidence is the fresh core-init-only control bundle and same-run natural-classifier harness under `pr151-review-fix-*`.

### Ownership Cleanup

**Lesson:** Every wedge capture must leave no owned processes behind. Both the inferior PID and GDB PID must be confirmed gone post-run. The `_cleanup_collector_debugger()` function bounds the wait (default 10s), then escalates SIGTERM → SIGKILL. A stuck launch GDB is bounded and force-killed.

**Evidence:** All authoritative harness runs confirm `inferior_pid_gone == true` and `gdb_pid_gone == true`. `OrderingAndCleanupTest.test_stuck_launch_gdb_is_bounded_and_force_killed` proves bounded debugger cleanup and escalation. Wedge SIGSTOP delivery uses pidfd plus ownership revalidation; cleanup retains the existing owned-process safeguards.

### Cross-Vendor Review

**Lesson:** External cloud review found four defects in the initial implementation, all fixed with behavioral regressions:
1. Stale first-stop outcome (fixed: final recomputation)
2. `threadCount=0` on backtrace failure (fixed: set from `_all_thread_info()`)
3. GDB basename matching too strict (fixed: accept multiarch/truncated comm)
4. Missing `--nx` (fixed: always include)

**Evidence:** Pre-fix proof: 6 behavioral assertions fail. At HEAD: all 9 focused tests pass. The cross-vendor pattern (executor + different-family reviewer) continues to catch complementary defects.

### Mandatory Eject Probe Rate-Limit Immunity

**Lesson:** The `EmergencyEjectTracker` mandatory probes (`app_shutdown`, `shutdown_eject`, `arch_exit`) are rate-limit-immune: `rate_limit_should_disable()` returns `False` for mandatory symbols even when their category is capped. This ensures the eject chain stays observable after an ordinary category is saturated. The immunity is unit-testable from CPython (pure predicate, no GDB dependency).

**Evidence:** `RateLimitImmunityTest` and `CollectorRateLimitOrderingTest` prove: mandatory probes stay enabled after `_disable_category()`, and the tracker advances to `armed`/`fired` even when every hit is rate-limited (cap=1).

## Workflow Lessons

### Evidence Architecture

The final verification structured evidence in three tiers:
1. **Pre-fix proof** (`pre-fix-fail/`): 9 tests against `adc10006f`, 6 behavioral failures, 3 passes — establishes the baseline
2. **HEAD static gates**: pycompile, diff-check, help, test discovery (94/94)
3. **Runtime evidence**: paired controls (5 plain + 5 collector, 14 probes, signed overhead gates) + same-run classifier wedge (22/22 verdicts)

### Rejected Report Pattern

The intermediate report was rejected for both factual and methodological errors. Its commit-pinned pre-fix raw proof remained valid, but its summary and runtime interpretation did not. Lesson: preserve independently valid sub-evidence while rejecting the enclosing verdict, then reshoot every affected runtime gate under the exact contract.

### DoD Precision

Issue #146's DoD specified: reproduce a real or induced wedge with the collector attached from launch, non-empty thread dump with real frames, non-wedged control with no false classification and no measurable slowdown. All three were met with real classifier-driven evidence, not synthetic inductions.

---

## DIGEST

Issue #146 delivered a same-session GDB wedge capture instrument (PR #151, merged `1b9352d40`): the #144 launch-attached collector captures full thread backtraces on natural health/screenshot wedge classifications via pidfd SIGSTOP, replacing #139's race-prone reactive attach. Four cloud-review defects were fixed with behavioral regressions: stale final stop outcome, lost GDB error detail, configured/multiarch GDB identity, and missing `--nx`. Fail-before proof requires new tests against old production, not missing test classes. The authoritative same-run proof captured 149 frames across 15 threads with 22/22 verdicts and ordering `classified → capture:success → owned-cleanup:stopped-clean → gdb-cleanup:exited → final-owned-cleanup:already-gone`; both PIDs were gone. Core-init-only controls passed the locked signed overhead formula (`screenshotLatencyMs` -5ms; `startupMsInternal` -622ms); negative differences do not prove a speedup. A raw-evidence audit rejected an all-category/manual-request report before the final reshoot. Merged-head guards: 94/94 GDB tests, 7/7 smoke, 5/5 readiness. Scope remained tooling/tests/inventory only.
