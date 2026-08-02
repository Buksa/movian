---
name: orchestrator-pipeline
description: Orchestrate the Buksa/movian issue-first pipeline — write specs, dispatch executors and verifiers on the channel roster, review before PR, merge, keep the durable memory. Use when acting as the pipeline orchestrator for Buksa/movian, dispatching or resuming an executor/verifier, or handing the orchestrator role between agent CLIs.
---

# Orchestrator Pipeline

Issue-first workflow: spec in issue → executor → fresh verifier → orchestrator review → push + PR.

## Pipeline Steps

### 1. Issue Setup
- Ensure issue has structured spec body: goal, context, contract, Решённые развилки, boundaries, falsifiable DoD
- Add label `wip:executing` when dispatching executor
- Add label `wip:verifying` when dispatching verifier

### 2. Executor Dispatch
- Executor reads the issue body (especially DoD section)
- Executor implements the fix on a topic branch from movian6
- Executor runs `git diff --check` and build validation
- Executor commits with `(#N)` reference (never `closes #N`)
- Executor reports back with: files changed, what was done, evidence of build success

### 3. Verifier Dispatch
- **Fresh verifier**: never the same agent that executed
- **Cross-vendor verifier** (see `cross-vendor-review` skill): the reviewer's model vendor MUST differ from the executor's. Same-vendor review shares systematic blind spots — reject a same-vendor PASS and re-dispatch. Use the `omp-quota` tool first; if no cross-vendor channel is usable, wait for one or ask the user — quota pressure never relaxes this rule.
- Verifier reads the issue DoD and runs independent checks
- Verifier follows `verifier-evidence` skill format for their report
- Verifier must supply its own observations for every DoD point
- Verifier writes report to a job-specific path

### 4. Orchestrator Review
- Review verifier report against DoD requirements
- Check for anti-patterns (log-only evidence, substituted gates, missing observations)
- If verifier PASS with quality evidence → proceed to PR
- If verifier FAIL or low-quality evidence → re-dispatch verifier or fix issues
- After 2+ same-class subagent failures → apply the work directly

### 5. Push + PR
- Create PR with title: `Fix #N: <brief description>`
- PR body references issue, includes summary of changes
- Do NOT auto-merge — wait for user review
- Never push without explicit user authorization

## Channel Roster

- **Executor**: Sonnet agent on a free channel
- **Verifier**: Fresh Sonnet agent on a different free channel
- **Orchestrator**: Primary agent (this session)

## Memory Protocol

After each pipeline completion:
- Update MEMORY.md with durable knowledge (new gotchas, architecture decisions)
- Reference issue number and PR in commit messages
- Record verification evidence standards if a new anti-pattern was discovered

## Gotchas

- `page:settings` never creates `model/loading` — page-ready check must handle void loading
- HTTP 302 alone proves nothing about GLW state — must check navigation property title/type
- `plugin_load()` returns 0 even on JS compile failure — do not trust "Reloaded" alone
- Mask local paths in public GitHub text (`/home/uzver` → `~`)
- Commits locally NEVER push without explicit authorization

### False-PASS verifier patterns (five recorded classes — reject each)

Verifier false PASSes have recurring shapes; the orchestrator audits every verdict against these before accepting:

1. **Stolen-binary PASS** — verifier copies another agent's already-built binary into its own build tree instead of rebuilding from the candidate commit, then runs dynamic tests against that borrowed binary. The PASS reflects the other agent's build, not the candidate. **Reject.** Require the verifier's own build, or static-marker proof (verifier-checks Rule 2) tied to the candidate commit.
2. **N/M-as-full-PASS** — evidence shows N/M steps passed (e.g. 4/7 smokes) but the verdict claims full PASS. Self-contradictory. **Reject** on the contradiction regardless of whether the candidate caused the failures (verifier-checks Rule 4).
3. **Wrong-metric PASS** — verifier compares a different metric than the DoD specifies (e.g. total wall time vs. the DoD's per-operation latency, or a count vs. a rate) and declares PASS because the wrong number looks fine. **Reject.** Re-dispatch and require the exact DoD metric.
4. **Scope-substitution PASS** — verifier tests a different, easier boundary than the DoD names (e.g. "cross-cwd read-only boundary" tested only within the same cwd) and claims PASS on the substituted scope. **Reject.** The evidence must address the specified boundary.
5. **Pre-existing-failure PASS** — verifier classifies specific failures as "pre-existing" or "out of scope" instead of providing the required observation, then claims full PASS. **Reject.** Those DoD points are not demonstrated.

### Verifier evidence audit (rule 5)

Every verifier verdict MUST be audited against every required observation in the DoD. An overbroad PASS that skips assigned DoD points or tests a different behavioral boundary than the one specified is rejected. The channel switches after one proof-quality failure — a partial-scope verifier is fine if explicitly scoped by DoD point, but a full-scope verdict that omits evidence is not.

**Self-contradictory reports** are rejected outright. A verifier verdict that contradicts its own evidence section (e.g., claiming PASS while evidence shows 4/7 steps passed, or classifying failures as "pre-existing" instead of providing the required observation) is grounds for immediate rejection regardless of whether the candidate caused the failures. Cross-check the VERDICT line against every DoD's evidence before accepting.

**Verifier-checks layer** (from `verifier-checks` skill, applied during audit):
- Shared build trees are read-only — verifier must not modify `build.debug/` or `build.release/`
- Private candidate builds must validate source markers (`strings`, `nm`, DWARF) before dynamic proof
- `cp --reflink=always` requires normal-copy fallback (not portable on ext4)
- VERDICT + DIGEST + individual DoD statuses must be mutually consistent
- `mimo` launches in automation MUST include `--dangerously-skip-permissions`
- Build freshness must be verified or static-marker evidence cited before dynamic tests

Concrete anti-patterns observed:
- Terra verifier passed DoD 1/3 but explicitly skipped required DoD 4 (failure-bundle rerun) and DoD 5 (7/7 smoke gate) → rejected, fresh non-GPT verifier proved the missing evidence.
- MiMo connector verifier claimed cross-cwd read-only boundary PASS but tested absolute output across cwd and relative output only within the same cwd, omitting the specified read-only metadata boundary → rejected, fresh Sol proved the exact A→B human/JSON exit-3 boundary plus both lock orderings.
- #138 initial verifier claimed DoD 8 PASS but own evidence showed only 4/7 smoke steps passed (preview-demo, preview-pilot, js-reload failed) → rejected on self-contradiction (#138).
- #135 verifier hit Mechanism B wedge; external GDB/DAP attachment saw empty/stopped process because runner's kill arrived first → no useful diagnostic evidence captured.

### Sequential merge for overlapping PRs

When two independently green PRs touch shared runtime files (e.g. `cli.py`/`harness.py`/`smoke.py`):
1. Merge the first PR (e.g. #132 wedge-kill).
2. Update the second branch to the new base (`git merge movian6`).
3. Re-prove the combined behavioral contracts — the wedge path, the hash comparison, state ordering, and the full smoke gate — against the merged tree.
4. Only then merge the second PR (#133 shot-hash).

Do not trust two isolated green verdicts when the PRs overlap the same runtime surface.

### Connector review: finding → fix → verification → reply chain

Every connector review finding must complete a four-step chain before closure:

1. **Finding posted** with severity badge (P1/P2), file/line reference, and concrete defect description.
2. **Fix commit** with hash reference in the reply (e.g., "Fixed in 4baffcc1a").
3. **Verification evidence** cited in the same reply: specific commands, exit codes, regression proofs, and fixture assertions.
4. **Inline fix reply** on the original finding comment, closing the loop.

The reply on the original finding must cite the fix commit hash and the specific regression proof. This creates a traceable chain: finding → fix → proof → closure. Findings without a corresponding reply citing verification evidence remain open.

### TypeScript calibration compatibility

When a checker runs TypeScript (e.g., `tsc` for declaration validation), deprecation errors from newer compiler versions can mask valid metadata. Requirements:

- Use forward-compatible compiler flags (e.g., `--target ES2015` instead of deprecated `ES5`).
- Remove deprecated options (e.g., `--moduleResolution node` in TS 6+).
- Test the checker against both the default compiler version AND the latest public release.
- The canonical forward-compatibility proof: `npx --yes --package typescript@latest tsc ...` with expected exit code.

Do not assume the installed TypeScript version is the only one the checker will encounter.

### GLW completion-context review

When reviewing GLW `.view` LSP completion, verify against the language's actual tokenization and preprocessing semantics:

- **Prefix-aware macro precedence**: Local macros must be checked before attribute fallback. Inside `widget(label, {...})`, a prefix like `Loc` should return the local macro, not the 116-item attribute catalog.
- **Cursor-ordered definitions**: GLW preprocessing is sequential — a macro expands only from the list accumulated so far. Completing a token before its later `#define` suggests an invalid macro. Require `#define` tokens strictly before the cursor line.
- **Nested-call signature scanning**: Regex `[^()]*$` cannot span completed inner parentheses. At `fmt(clamp(0, 1, 2), `, scan backward with parenthesis depth, skipping closed inner calls before selecting the active outer call.
- **Preprocessor whitespace tolerance**: GLW's lexer discards whitespace and the preprocessor consumes `TOKEN_HASH` followed by the next identifier. `# include "./` is valid — allow horizontal whitespace between `#` and `include`/`import`.

Do not accept completion results that violate the GLW lexer's actual token boundaries.

### Core-change guard

Changes under `src/prop/`, `src/fileaccess/`, `src/ui/glw/`, `src/navigator.c`, `src/event.c`, or `src/api/` have tooling/plugin blast radius beyond the issue DoD. Before review:

1. Use CodeGraph on both the replaced and replacement symbols, then search `support/devtools/` and `res/ecmascript/` for the changed behavior.
2. Write an old-versus-new capability matrix. Name every old capability the replacement lacks; a shorter call is not equivalent by default.
3. Exercise each instrument consumer, not only core/unit tests. For prop-path changes this includes `mdev props` at depth >=2 on unnamed rows, one deliberate failure bundle, the agent snapshot, and a real popup/action path when applicable.
4. Add paired regression coverage in the same PR: the restored behavior must work, and the security/safety behavior from the earlier fix must remain intact.
5. Audit raw artifacts before accepting a verdict. Reject omitted gates, manually induced events labeled natural, and ordering assembled from different runs.

Native/core candidate builds require an initialized private build rooted in the candidate worktree. A symlinked shared `build.debug` is read-only runtime evidence: ext-cache rejects cross-install artifacts, and build retries can corrupt shared object state. After any worktree submodule initialization/removal, run `git status` in the main checkout to catch shared `core.worktree` damage.

Declarative tests must be parsed and executed. `git diff --check` cannot detect an unsupported smoke verb or malformed JSON structure.
