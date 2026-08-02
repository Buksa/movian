---
description: Dispatch an executor or verifier agent into an Orca worktree with the correct headless flags and a quota preflight. Codifies the two dispatch regressions (mimo --dangerously-skip-permissions; codex -a never -s workspace-write).
agent: build
model: standard
---

You are dispatching an agent into an Orca worktree for the Buksa/movian pipeline. Before you do anything else, satisfy the two hard guards — regressed at least twice each (incident-class: dispatch-stall):

## Step 1 — Quota preflight (pattern 5)

Call the `omp-quota` tool (no arguments) to check channel saturation. If the recommended channel is BLOCKED, stop and tell the user which channels are exhausted and when they reset. Do not dispatch into a blocked channel. Pick the least-saturated usable channel that matches the vendor you need (see step 2). If the picked channel is RISKY (>= 85%), warn the user before proceeding.

## Step 2 — Correct headless flags (pattern 1)

The vendor determines the mandatory flags. Use exactly these invocation shapes — the `guard-dispatch-and-build` hook will BLOCK any command that omits them:

**MiMoCode (mimo) dispatch** — `--dangerously-skip-permissions` is MANDATORY:
```
mimo run --dangerously-skip-permissions --print -p <worktree-path> "<prompt>"
```

**Codex dispatch** — both `-a never` AND `-s workspace-write` are MANDATORY:
```
codex exec -a never -s workspace-write "<prompt>"
```

Never omit these flags. Never gate them behind a config check. Never use them conditionally. There is no interactive approver in an Orca worktree.

## Step 3 — Cross-vendor review routing (pattern 3)

If `$ARGUMENTS` selects a reviewer, the reviewer MUST be from a different vendor family than the executor. Pairings that have worked (from the #144 round lessons):
- GLM executor + non-GLM (Claude/Sonnet) reviewer
- non-GLM executor + GLM reviewer

Do not assign a reviewer from the same vendor family as the executor — reject the dispatch and re-pick.

## Step 4 — Dispatch

Task description from the user: $ARGUMENTS

Construct the dispatch command with the mandatory flags, run it, and report the channel picked (from step 1), the flags used, and the agent's output.
