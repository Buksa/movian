# Digest: Rounds #135 and #138 — Reusable Pipeline Lessons

Extracted 2026-07-21 from PR #140 (`4baffcc1a`) and PR #141 (`1aae9127e`), issue threads, #139 evidence comments, and verifier reports.

---

## Lesson 1 — Self-contradictory verifier reports must be rejected

**Trigger:** #138 initial verifier claimed DoD 8 PASS but its own evidence showed only 4/7 smoke steps passed (preview-demo, preview-pilot, js-reload failed with page-not-ready / openerror). The report classified those failures as "pre-existing" rather than providing the required observation.

**Rule:** A verifier verdict that contradicts its own evidence section is rejected regardless of whether the candidate caused the failures. The supervisor must cross-check the VERDICT line against every DoD's evidence before accepting. Partial evidence cannot support a full PASS verdict. The incidents journal records two such rejections in this round (#135 halted mid-verification, #138 rejected on self-contradiction).

**Evidence pattern:** Look for reports that claim "PASS" while noting specific steps failed or were skipped. The key is the gap between what the VERDICT claims and what the evidence actually demonstrates.

---

## Lesson 2 — External DAP/GDB attachment loses the pid to automatic stop

**Trigger:** Multiple verifier retries across #135 and #138 hit Mechanism B (504 on `/api/screenshot/raw`). An external agent attempted GDB/DAP attachment to the wedged pid, but `smoke run all` auto-stopped the owned pid before thread enumeration could complete.

**Root cause sequence:**
1. Smoke runner detects a wedge (504 on health screenshot).
2. Runner marks the instance for stop and begins `kill_owned_pid()`.
3. External agent attaches GDB/DAP to the wedged pid.
4. Runner's kill arrives, the pid exits, and the attach sees an empty/stopped process.

**Consequence:** Thread dumps from external attaches always show "stopped-clean" with no stack frames — the pid is already dead by enumeration time. Evidence preserved in bundles contains `thread-dump.txt` recording the attach result with an empty post-stop thread set.

**Fix direction for #139:** Capture must occur *inside* the health-failure path, before `kill_owned_pid()` is called. Auto-capture needs to be best-effort and timeout-bounded so it never blocks the stop path.

**Lesson:** When the smoke runner and an external debugger compete for the same pid, the runner always wins because it owns the lifecycle. Diagnostic capture must be integrated into the runner's failure path, not bolted on from outside.

---

## Lesson 3 — TypeScript 6 requires forward-compatible compiler flags

**Trigger:** TypeScript 6 (6.0.3) deprecated `target=ES5` and `moduleResolution=node`, causing TS5107 errors that broke `gen.py --check` even when declarations were valid.

**Fix (PR #140, commit `4baffcc1a`):** Changed fixture invocation to `--target ES2015` and removed `--moduleResolution node`. Verified: public TypeScript 6.0.3 positive fixture exits 0 with no TS5107; negative fixture produces exactly 9 expected TS2322/TS2345 diagnostics.

**Rule:** When a checker runs TypeScript whenever it is present, deprecation errors from newer compiler versions can mask valid metadata. The invocation must target a forward-compatible flag set. Always test the checker against both the default compiler version and the latest public release.

**Evidence pattern:** The `npx --yes --package typescript@6.0.3 tsc ...` invocation is the canonical way to prove forward-compatibility without installing a specific version.

---

## Lesson 4 — Raw-pointer-only APIs need branded non-constructible types

**Trigger:** `prop.release(prop.createRoot())` type-checks with `Property<T>` declarations, but `createRoot()` returns a Duktape Proxy while `es_prop_release_duk()` calls `duk_require_pointer()` on its argument. A proxied native object is not a Duktape pointer — the accepted API call throws at runtime.

**Fix (PR #140, commit `4baffcc1a`):**
- Introduced `RawProperty` interface with `readonly [rawPropertyBrand]: never` — non-constructible.
- `release(prop: RawProperty)` requires the branded type.
- No public producer (`makeProp`, `createRoot`, `create`) returns `RawProperty`; all return `Property<T>`.
- Negative fixture proves `release(createRoot())` fails with TS2345.

**Rule:** Calibration declarations for legacy raw-pointer-only APIs must use branded non-constructible types. The declaration should represent the actual runtime contract, not the convenient developer-facing shape. Verify that no public API path can produce the restricted type.

---

## Lesson 5 — GLW completion context is prefix-aware and cursor-ordered

### 5a. Prefix-Aware Macro Precedence

Inside a `widget(label, {...})` block, the attribute catalog was returned before the macro branch was reached, making local macro completion unavailable. Fix: check prefix-matching earlier local macros *before* the attribute fallback. `Loc` returns only `LocalCard`; `al` returns the 116-item attribute catalog.

### 5b. Cursor-Ordered Definitions

GLW preprocessing is sequential: a macro expands only from the list accumulated so far. Completing `FutureCard` before its later `#define` suggested an invalid macro. Fix: require `#define` tokens strictly before the cursor line.

### 5c. Nested-Call Signature Scanning

`[^()]*$` regex cannot span completed inner parentheses. At `fmt(clamp(0, 1, 2), `, no call was matched. Fix: scan backward with parenthesis depth, skipping closed inner calls before selecting the active outer call.

### 5d. Preprocessor Whitespace Tolerance

GLW's lexer discards whitespace and the preprocessor consumes `TOKEN_HASH` followed by the next identifier (`glw_view_lexer.c:196-200`, `glw_view_preproc.c:288-300`). `# include "./` is valid but received no completion. Fix: allow horizontal whitespace between `#` and `include`/`import`.

**Rule:** LSP completion for GLW must match the language's actual tokenization and preprocessing semantics, not the source text's formatting. Cursor position, prefix content, parenthesis depth, and whitespace tolerance all affect which completions are valid.

---

## Lesson 6 — Connector review: finding → fix → verification → reply chain

**Pattern observed in both PR #140 and #141:**

1. **Finding posted** with severity badge (P1/P2), file/line reference, and concrete defect description.
2. **Fix commit** with hash reference in the reply (e.g., "Fixed in 4baffcc1a").
3. **Verification evidence** cited in the same reply: specific commands, exit codes, regression proofs, and fixture assertions.
4. **Inline fix reply** on the original finding comment, closing the loop.

**PR #140 findings (3):** TS6 flag deprecation (P1), Route RegExp acceptance (P2), release accepting proxied properties (P2).

**PR #141 findings (4):** Local macro suppressed in widget blocks (P2), nested calls broke signature help (P2), preprocessor whitespace not tolerated (P2), future macros suggested before `#define` (P2).

**Rule:** Each connector review finding must have a corresponding fix commit, verification evidence, and a textual reply on the original comment. The reply should cite the fix commit hash and the specific regression proof. This creates a traceable chain: finding → fix → proof → closure.

---

## Source trail

- Issue #135: TypeScript calibration fixtures for movian/page, movian/prop, movian/http
- Issue #138: GLW completion and signature help for .view files
- Issue #139: Mechanism B GLW/screenshot wedge root-cause (evidence-only this round)
- PR #140: `4baffcc1a` — TypeScript calibration fixtures (#135)
- PR #141: `1aae9127e` — GLW completion (#138)
- Verifier reports: `verifier-135.md`, `verifier-138.md`
- Connector review comments: PR #140 (3 findings), PR #141 (4 findings)
