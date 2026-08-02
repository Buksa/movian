# Issue #152 — indexed non-creating `/api/prop` lookup

## Outcome

PR #153 merged into `movian6` as `f2c88eb08cfb760c4d166f8be9dcb49d70c9f9eb` from reviewed head `35a35390aae9af778f1e03c21e4e8a37e2dca284`. Codex Connector reviewed that exact head and found no major issues. Issue #152 is closed.

## Capability regression and repair

Commit `c7fb5aa9` correctly stopped unauthenticated HTTP GET from mutating the prop tree, but its replacement was not behaviorally equivalent:

- `prop_get_by_name` called `prop_subfind(... allow_indexing=1 ...)`: it followed originators and resolved positional `*N`, but could call `prop_create0`, convert a scalar to `PROP_DIR`, and notify subscribers.
- `prop_findv` followed originators and never created or converted, but matched named children only and skipped unnamed children.
- The merged repair adds explicit `allow_indexing` to non-creating `prop_findv`: HTTP passes `1`; STPP passes `0`. The mutex, originator, refcount, and miss behavior remain non-mutating.

Rule: a replacement is equivalent only after listing every capability of the old path and proving the difference set is empty or intentional.

## Paired regression

The existing `preview-demo` smoke now protects both directions through real HTTP:

1. parent has 12 unnamed rows;
2. GET a definitely missing named child and assert absence;
3. parent still has 12 rows;
4. `nodes/*0/url` equals `viewpreview:demo:1`.

With the new smoke and old production, step 6 fails because `*0/url` is 404. A rollback before `c7fb5aa9` would instead fail the post-miss child count. At merged HEAD the focused smoke passes 7/7 steps and full smoke passes 7/7 scenarios.

## Runtime proof

Fresh no-sandbox verification observed:

- a real blocking `popup.message()` exposed `global/popups/*0`; POST `action=Ok` returned 200, removed the popup, changed loading 1→0, and let the route complete;
- `nodes/*0`, `*1`, and `*11` returned 200 and expected URLs;
- a missing named GET returned 404 while parent count, 698-byte body, and SHA-256 stayed unchanged;
- a scalar-child miss returned 404 without changing the scalar or emitting an STPP update after the initial callback.

Merged-head consumer proof:

- `_collect_props`: 12/12 unnamed children were directories, 0 `not found`;
- `mdev props --depth 2`: 12/12 directories, 0 `not found`;
- agent snapshot: 12/12 roots, URLs, and metadata titles returned HTTP 200;
- a deliberate failure bundle improved from 12/12 `not found` before the fix to 12/12 captured directories after it.

Historical smoke audit found no false individual verdict: the seven pre-fix `assert_prop` checks used named paths only. The damage was silent diagnostic-tree truncation, so old all-green runs did not prove indexed lookup.

## Proof-quality and workflow lessons

- A sandboxed Codex verifier correctly returned FAIL when display sockets were denied with `EPERM`; static source inspection was not substituted for missing runtime proof. The retry switched to OMP/GPT without sandbox and observed every DoD point.
- Core changes require CodeGraph consumer enumeration plus behavior-oriented tooling searches. Here the affected indexed consumers were exactly `smoke.py`, `cli.py`, and `movian_agent.py`; none existed in `res/ecmascript/`.

## Evidence

- `/tmp/movian-ai-evals/issue-152-baseline/`
- `/tmp/movian-ai-evals/issue-152-pre-fix-failure-bundle/`
- `/tmp/movian-ai-evals/issue-152-post-fix/`
- `/tmp/movian-ai-evals/issue-152-verifier/`
- `/tmp/movian-ai-evals/issue-152-verifier-nosandbox/`
- `/tmp/movian-ai-evals/issue-152-merged/`

## DIGEST

- #152 merged as `f2c88eb08`; connector found no major issues.
- HTTP `prop_findv(..., 1)` restores `*N`; STPP keeps strict `0`.
- Missing names still return 404 without create, conversion, or notification.
- Permanent smoke pairs missing-name count preservation with `*0/url` success.
- Real blocking popup completed after indexed `eventSink` action.
- `_collect_props`, `mdev props`, and agent snapshot all restored 12/12 rows.
- No historical smoke verdict was false; diagnostic bundles were silently truncated.
- Missing runtime observations are FAIL, not source-inferred PASS.
- Core-change audits need a capability matrix and indexed-consumer inventory.
