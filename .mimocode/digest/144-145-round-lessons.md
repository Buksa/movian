# Issues #144–#145 Round Lessons

## What Was Delivered

A two-issue tooling pipeline for runtime lifecycle observability of Movian, merged as `376eb19db`:

1. **Static lifecycle inventory** (`support/devtools/gdb/inventory.json`) — 117 symbols across 16 categories (core-init, shutdown-hook, init-system, init-helper, thread-create, plugin, es-plugin, es-context, es-resource, navigator, glw, backend, service, prop-subscribe, callout, cache), with 45 enumerated INITME uses. Each entry carries `category`, `phase`, `pairedWith`, `confidence` (`source-confirmed` only where the pairing was read in source), and optional `platforms`/`configFlag`/`pairedWithAll`.

2. **GDB Python event collector** (`support/devtools/gdb/movian_lifecycle.py`) — dual-role: pre-main capture from exec (arms before `main_init`), and interactive source/use path. Emits JSONL with `seq`, `monotonicNs`, `category`, `event`, `symbol`, `thread{gdbId,name,osTid}`, `arguments`, `objects`, and 3–6 stack frames. Category filters, rate-limiting for high-volume probe categories (prop-subscribe, callout, es-resource, thread-create, glw, service), tolerance for missing/optimized-out arguments, and no full backtraces.

3. **JSONL post-processor** (`support/devtools/gdb/lifecycle_analyze.py`, 1112 lines) — derives `init-order.json`, `fini-order.json`, `thread-lifecycle.json`, `lifecycle-graph.mmd` (Mermaid with edges tagged `confirmed-static` / `confirmed-dynamic` / `inferred` / `unknown`), and `resource-balance.json` from real captured events. Pure post-processing; touches no production C/runtime code.

4. **Scenario runners** (`run_lifecycle_scenarios.py`, `run_lifecycle_s34.py`) — execute four lifecycle scenarios (S1 minimal startup, S2 startup with dev plugin, S3 plugin reload via HTTP ReloadData, S4 normal shutdown via HTTP Quit), each a fresh `mdev`-launched instance with ownership-proven lifecycle.

5. **GDB in-process interaction helper** (`support/devtools/gdb/scenario_interact.py`) — daemon thread that polls for HTTP-ready, sends an HTTP action, and waits for natural exit. Works around the GIL starvation limitation of GDB Python threading during breakpoint storms.

6. **Test plugin** (`support/devtools/gdb/lifecycle_test_plugin/`) — minimal ECMAScript plugin that creates five destroyable resources (1 route, 1 service, 2 prop subscriptions, 1 timer) with try/catch wrapping per resource so a single API mismatch doesn't fail the whole load.

## Technical Lessons

### GDB Python + HTTP Action Interaction

**Lesson:** GDB Python threads cannot reliably interact with the running inferior when breakpoint storms are active — the GIL causes starvation. The solution is an *external* action sender (HTTP request from a separate process or a daemon thread that avoids GDB API calls during the storm window), not trying to make GDB Python do everything.

**Evidence:** The `scenario_interact.py` helper uses a plain `threading.Thread` + `urllib.request.urlopen` pattern, avoiding any GDB Python API calls in the worker thread. The `run_lifecycle_s34.py` runner uses a subprocess-based approach with `proc.wait(timeout=duration)` for natural inferior exit detection.

### Reload Resource Balance Proof

**Lesson:** Proving plugin reload resource balance requires correlation by `(plugin_id, es_context_pointer)`, not raw pointer diffing. The `es_resource_link`/`es_resource_unlink` pair is the correct abstraction level — counting lower-level prop/callout side effects would duplicate resources and mix in unrelated global runtime activity.

**Evidence:** `RESOURCE_PAIRS` in `lifecycle_analyze.py` maps five kinds: `es-context` (create/end), `es-resource` (link/unlink), `service` (create/destroy), `es-plugin` (plugin_load/plugin_unload), and `plugin` (plugin_load/plugin_unload). The `RELOAD_REQUIRED_KINDS` set defines the five kinds the test plugin exercises; zero/zero is flagged as missing evidence, not a clean pass.

### Orchestrator Review Finding Pattern

**Lesson:** First implementations of GDB/tooling code can pass happy-path evidence but have unsafe failure paths. The orchestrator review of #144 found four defects in the initial GLM implementation:
1. Host `launch` returns exit 0 unconditionally (missing port/PID, failed ownership, invalid JSONL, failed cleanup).
2. Stale process signal result ignored (second launch possible after still-live owner).
3. Failure cleanup only kills GDB parent after early PID miss (no final exact-profile inferior scan).
4. Collector closes on GDB `before_prompt` (breaks interactive source/use path); `objects` excludes `0x…` pointers instead of preserving them as correlation keys.

**Pattern:** Tooling code that manages process lifecycles needs explicit falsification tests for every failure path, not just the happy path.

### Parallel Review Strategy

**Lesson:** Running two parallel reviewers of different model families (Claude/non-GLM and GLM) on tooling code catches complementary defects. The non-GLM reviewers found 8+ issues on #144 (unbindable macro-only entries, platform-exclusive symbols, mislocated anchors, missing core-init pairs, prop_subscribe gating defect). GLM executor + non-GLM reviewer is a productive pairing.

### Collector Overhead Budget

**Lesson:** The collector must not measurably slow the target process. The overhead budget was `max(20%, 1.0 s)` of baseline exec-to-HTTP-ready wall time. This was verified with 3 uninstrumented + 3 instrumented minimal-startup runs; the actual overhead was ~589 ms, within budget.

**Implication:** Every breakpoint the collector installs must auto-continue. Rate-limiting for high-volume categories (prop-subscribe, callout, etc.) is essential to keep the overhead bounded.

### INITME Dynamic Verification

**Lesson:** The INITME order must be verified *dynamically* (through `init_group`/`fini_group` runtime hits), not assumed from source alone. The analyzer checks: does fini run in reverse of init, do any init callbacks lack a fini (or vice versa), is any callback registered twice. The `hasDeclaredFini` field in `init-order.json` flags mismatches.

### Plugin Reload Cycle Splitting

**Lesson:** A single `plugins_reload_dev_plugin` event may contain multiple reload cycles if multiple dev plugins are loaded. The analyzer splits by `(plugin_id, context_pointer)` and verifies balance per cycle, not across the whole event window. The `_reload_cycles` function in `lifecycle_analyze.py` handles this.

### Test Plugin Design

**Lesson:** A lifecycle test plugin must create a bounded, known set of destroyable resources with try/catch wrapping per resource. If the plugin fails to compile or execute, `mdev reload --js` becomes a false green — the reload succeeds but no resources were created to verify balance. The `lifecycle_test.js` plugin wraps each resource creation independently and logs a count line.

## Workflow Lessons

### Issue Dependency Chain

Issues #144 → #145 formed a clean dependency chain under umbrella #143. #144 (collector + inventory) was the prerequisite; #145 (scenarios) was unblocked only after #144 merged (`ba3a8d87d`). This ordering was enforced: #145 executor dispatch only after #144 merge confirmation.

### Review-First Before External Dispatch

The orchestrator review of #144 caught four contract defects *before* PR/reviewer dispatch. This prevented sending a fundamentally flawed implementation to external reviewers. The correction task was narrow and falsifiable — it had to add failure-path tests while keeping existing gates green.

### Evidence Architecture

The four-scenario capture structure (S1–S4) covers the full lifecycle with each scenario as a separate fresh instance:
- S1: baseline startup (272 events, 38 init callbacks, 19 threads)
- S2: startup with plugin (284 events, 5 plugin resources)
- S3: plugin reload (303–311 events, resource balance proof)
- S4: shutdown (189–319 events, complete fini sequence)

Each scenario's raw JSONL + derived JSON is archived under `/tmp/movian-lifecycle/<RUN_ID>/` (not committed), keeping the git tree clean while preserving reproducibility.

### DoD Precision

Both issues had precise, falsifiable DoD criteria. #144 specified: collector arms before `main_init`, overhead budget with specific pass/fail thresholds, malformed-argument tolerance test. #145 specified: per-kind resource balance with correlation keys, `confirmed-static`/`confirmed-dynamic`/`inferred`/`unknown` edge tagging (no `inferred` rendered as `confirmed`), genuine invariant violation or explicit clean-pass report.

### Cross-Vendor Verification

Both issues used cross-vendor review (non-executor model family) as a quality gate. The #145 final review was a fresh Google Gemini review with PASS and no P0/P1/P2 findings. This multi-model verification pattern is becoming standard for the pipeline.

## Merge Evidence

**Merge commit:** `376eb19db595523c82f1a752ee76ca8827ba1fb3`
**Files changed:** 10 files, +2458 / -9 lines
**Key files:**
- `support/devtools/gdb/inventory.json` — 117-entry static inventory
- `support/devtools/gdb/lifecycle_analyze.py` — 1112-line post-processor
- `support/devtools/gdb/movian_lifecycle.py` — collector (84 lines modified)
- `support/devtools/gdb/run_lifecycle_s34.py` — S3/S4 runner (386 lines)
- `support/devtools/gdb/run_lifecycle_scenarios.py` — scenario runner (449 lines)
- `support/devtools/gdb/scenario_interact.py` — GDB interaction helper (64 lines)
- `support/devtools/gdb/lifecycle_test_plugin/` — test plugin (JS + manifest)
- `tests/tooling/gdb/test_lifecycle_analyze.py` — 29 unit tests (230 lines)
- `tests/tooling/gdb/test_movian_lifecycle.py` — collector tests (36 lines)

**Merged-head verification:**
- 29 lifecycle tooling tests PASS
- Python compilation, plugin JS syntax, inventory consistency, whitespace, executable-help guards PASS
- Real S3 reload capture: 304 events, authoritative reload success, required reload/unload/load events, clean inferior exit 0, no owned process remaining
- Real S4 shutdown capture: 193 events, `app_shutdown` and `main_fini`, clean inferior exit 0, no owned process remaining
- S3 analyzer verdict: `balanced` — context 1/1, ECMAScript resources 5/5, service 1/1, plugin layers 1/1
- Independent cross-vendor review: PASS, no P0/P1/P2 findings

---

## DIGEST

Issues #144–#145 delivered a runtime lifecycle observability tooling pipeline under the #143 umbrella: a 117-entry static inventory, a non-stopping GDB Python JSONL collector (pre-main capture, auto-continuing, rate-limited), an 1112-line post-processor producing init/fini order, thread lifecycle, Mermaid graph, and plugin-reload resource-balance proof, plus four scenario runners (startup, startup+plugin, reload, shutdown) and a test plugin. The key architectural finding was that GDB Python threads cannot reliably interact with the inferior during breakpoint storms due to GIL starvation — solved by external HTTP action injection. Reload resource balance is proven by `(plugin_id, es_context_pointer)` correlation, not raw pointer arithmetic, using the `es_resource_link`/`es_resource_unlink` abstraction level. Orchestrator review caught four failure-path defects before external dispatch. The pipeline established the parallel-review pattern (GLM executor + non-GLM reviewer) and cross-vendor verification as standard. All 29 unit tests pass; real S3/S4 captures demonstrate balanced reload (5/5 ECMAScript resources, 1/1 context/service/plugin-layer) and clean shutdown (app_shutdown + main_fini, exit 0). Merged as `376eb19db`.
