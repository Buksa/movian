---
name: cross-vendor-review
description: Enforces cross-vendor review for the Buksa/movian pipeline — the reviewer's model vendor MUST differ from the executor's. Trigger when assigning a reviewer, auditing who reviewed a change, or dispatching a verifier. Codifies the rule that emerged from #144–#145 round lessons (GLM executor + non-GLM reviewer pairings caught complementary defects).
---

# Cross-Vendor Review Rule

## The rule (non-negotiable)

For every executor→verifier (or executor→reviewer) assignment in the Buksa/movian pipeline, **the reviewer MUST come from a different model vendor family than the executor**. A same-vendor reviewer is rejected before dispatch — do not even propose it.

## Why

Same-vendor reviewers share systematic blind spots: they tend to miss the same classes of defects the executor made. This was measured across #144 (tooling) and #145 (lifecycle) — non-GLM reviewers found 8+ issues on GLM-executed code (unbindable macro-only entries, platform-exclusive symbols, mislocated anchors, missing core-init pairs, prop_subscribe gating defects). The pairing GLM-executor + Claude/Sonnet-reviewer, and the reverse, are both productive.

## Vendor families

Treat these as distinct vendor families (members of the same family count as same-vendor and are rejected):

| Family | Members |
|--------|---------|
| Anthropic | Claude, Sonnet, Opus, Haiku |
| OpenAI | GPT, o-series, Codex |
| Google | Gemini, Antigravity |
| ZAI / Xiaomi | GLM (zai/glm-*), MiMo |
| Mistral | Mistral, Mixtral, Codestral |
| DeepSeek | DeepSeek-* |
| xAI | Grok |

When in doubt about whether two models share a vendor family, treat them as same-vendor (conservative).

## How to enforce it

1. **At dispatch** (use the `/dispatch-dispatcher` command): record the executor's vendor. When selecting a reviewer, pick a channel whose vendor differs. If the user named a reviewer, check the vendor before spawning — reject and re-pick if it matches the executor.
2. **At audit**: when reading a verifier report, confirm the reviewer's vendor differs from the executor's. A same-vendor PASS is not trustworthy — re-dispatch a cross-vendor verifier.
3. **With quota pressure**: call the `omp-quota` tool first. If the only usable channel is the executor's vendor family, that is NOT an excuse to relax the rule — wait for a cross-vendor channel to reset, or ask the user.

## Anti-patterns (reject these)

- "The Sonnet channel is free, so let Sonnet review the Sonnet execution" — no. Wait for or re-pick a cross-vendor channel.
- "The verifier already passed, vendor doesn't matter now" — a same-vendor PASS does not satisfy the rule. Re-dispatch cross-vendor.
- "It's a small/tooling change, cross-vendor is overkill" — #144 was tooling code and the cross-vendor review found the most defects. Size does not relax the rule.

## Source trail

- Digest `.mimocode/digest/144-145-round-lessons.md` — "Parallel Review Strategy": GLM executor + non-GLM reviewer is the productive pairing; non-GLM reviewers found 8+ issues on #144.
- Issue #145 final review was a fresh Google Gemini review (cross-vendor vs the executor) → PASS with no P0/P1/P2 findings.
- `orchestrator-pipeline` skill — pipeline steps 3 and 4 reference fresh-verifier dispatch; this skill adds the cross-vendor constraint on top.

## See also

- **`orchestrator-pipeline`** — pipeline steps 3 (verifier dispatch) and 4 (orchestrator review)
- **`verifier-evidence`** / **`verifier-checks`** — what the cross-vendor reviewer must produce
- **`omp-quota`** tool + **`/dispatch-dispatcher`** command — quota-aware dispatch that honors this rule
