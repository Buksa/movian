# Issue #136 — reference-declaration calibration fixtures

## What Was Delivered

Calibration checker extensions and `.d.ts` fixtures for `movian/settings`, `movian/service`, `movian/store`, and the `Plugin` global, merged as PR #155 (`f4806d493`). Expanded the checker with `native-calls-exact` mode, `object_prototypes`, `exact_member_types`, `export_instances`, and transitive store-to-native/fs resolution. All paths confined to `support/devtools/metadata/`.

## Technical Lessons

### Callback-or-return falsification requires the right mutation

**Lesson:** Changing a Plugin data field type (`apiversion: number` → `string`) does not falsify the callback/return fixture. It exercises a different observable (data-field type enforcement) rather than the callback type contract. The correct DoD 4 mutation must change a callback parameter type (e.g. `boolean` → `string` on `createBool`'s callback value) so the positive fixture fails at the callback assignment lines.

**Evidence:** DoD 4 proof gap was identified when the first verifier's `apiversion` mutation did not produce the expected callback-line failures. The fresh MiMo verifier independently used `createBool` callback `boolean → string`, producing TS2322 at positive fixture lines 108 and 163.

### TypeScript rejection proofs require the full (line, TS code) tuple set

**Lesson:** Reporting "21 expected diagnostics" as a count is insufficient. The verifier must enumerate every `(fixture line, TS error code)` pair and confirm the observed set equals the expected set exactly. A count mismatch is indistinguishable from extra/missing diagnostics without the full map.

**Evidence:** The DoD 4 proof gap specifically called out that "21 expected diagnostics" without enumeration did not satisfy the proof requirement. The MiMo verifier enumerated all 21 `(line, TS code)` tuples in a table and confirmed exact match.

### Generic constraint `Record<string, unknown>` rejects named interfaces

**Lesson:** `T extends Record<string, unknown>` imposes a string index signature, so an ordinary named interface without that signature does not satisfy the constraint. The correct constraint is `T extends object = Record<string, unknown>`: `extends object` permits named interfaces while the default preserves the untyped value contract for callers that omit the generic parameter.

**Evidence:** Codex connector P2 finding: `T extends Record<string, unknown>` rejected `ReferenceStoreState` as a store schema. Fix `bf0b50dc7` broadened to `T extends object = Record<string, unknown>`. Positive fixture now instantiates `createFromPath<ReferenceStoreState>` with a named interface.

### Exact native-table acceptance is per name and nargs with artifact-specific diagnostics

**Lesson:** `native-calls-exact` mode compares the full `duk_function_list_entry` table (name + `nargs`) against source call arities. Diagnostics must reference the source artifact ("source calls missing native fnlist_X.Y") and the native artifact ("native fnlist_X member Y not exercised by source"), not the generic term "declaration" which elsewhere means the `.d.ts` file. The arity comparison uses the maximum source call arity because `nargs` is the registered maximum and trailing optional args may be omitted.

**Evidence:** The rework commit `0e018141e` replaced the generic `_compare_name_sets` call with two explicit loops with artifact-specific labels, adding precondition and max-arity rationale comments. The in-memory mutation test verified both directions plus alias scoping.

### Worktree-symlink corpus proof combines the main guard with the direct corpus

**Lesson:** When a worktree's `build.debug` is a symlink to a shared non-writable build directory, the exact `make` command fails on symlinked stamps. The correct proof strategy is: (1) run the named make guard in the main checkout, and (2) run the corpus script directly against the existing debug binary from the candidate. Both must pass; the symlink topology failure is a wrapper issue, not product failure, and must not be counted as proof.

**Evidence:** DoD 5 make command failed with `rm: cannot remove '.../build.debug/stamps/libav.stamp': Read-only file system`. The corpus guard was then executed directly against the existing debug binary: 99 flat+golden, 92 old, 11 fixtures, 0 crashes, 0 regressions. Main guard also passed independently.

### Provider quota/proof-quality failures require a fresh channel

**Lesson:** When a verifier hits a provider rate limit (429) or produces an incomplete report, the partial result is not accepted as a verdict. Verification restarts from scratch in a fresh channel (different vendor or fresh context). This prevents partial evidence from being elevated to a verdict and ensures the verifier exercised the full contract independently.

**Evidence:** Zai/GLM completed DoD 1–2 and part of DoD 3, then hit a provider 429 with reset 2026-08-03. Per the proof-quality/channel rule, verification restarted from scratch in a fresh Google Gemini context. The complete DoD 1–5 contract was re-executed unchanged.

### Core-change guard is explicit and path-scoped

**Lesson:** The core-change guard is evaluated against a fixed set of guarded runtime paths (`src/prop/`, `src/fileaccess/`, `src/ui/glw/`, `src/navigator.c`, `src/event.c`, `src/api/`). When all changed paths are confined to `support/devtools/metadata/` (tooling/test scaffolding only), the guard is explicitly classified as NOT TRIGGERED. The guard is mandatory in every verification report and PR body.

**Evidence:** All seven tracked paths under `support/devtools/metadata/` confirmed in executor report, both pre-PR reviews, both verifier reports, and connector review. No path under guarded runtime directories was modified; temporary `src/ecmascript/es_service.c` mutations used only for falsification were restored.

## DIGEST

- #136 merged as `f4806d493`; PR #155; connector P2 fixed in `bf0b50dc7`.
- Callback-or-return falsification must mutate the callback type, not a data field.
- TS rejection proofs need full `(line, TS code)` tuples, not just a count.
- `T extends object = Record<string, unknown>` permits named interfaces while preserving the default.
- `native-calls-exact` diagnostics must name source vs native artifacts, not "declaration".
- Worktree-symlink corpus proof: main guard + direct corpus; symlink failure is wrapper, not product.
- Provider 429/proof-quality failures restart in a fresh channel; partial reports never become verdicts.
- Core-change guard is explicit and path-scoped; `support/devtools/metadata` is NOT TRIGGERED.
