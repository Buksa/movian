# Movian Agent Instructions

## Scope

- Treat every tracked file and commit as public.
- Use only public sources and independently implemented behavior.
- Keep changes small, focused, and reviewable.
- Work from `movian6` on a topic branch.
- Keep separate logical changes in separate commits; do not squash by default.
- Do not push, merge, or delete remote branches without an explicit request.

## Code Navigation

When `.codegraph/` exists, use CodeGraph before broad searches or exploratory
file reads:

- `codegraph explore "<question>"` for flows and related symbols;
- `codegraph node <symbol-or-file>` for one symbol or file;
- `codegraph query <name>` to locate a symbol.

Use the equivalent MCP tools when available. Fall back to `rg` when the index
does not cover the question or reports pending changes.

## WSL Workspace Guardrail

When working from Windows tooling against this WSL checkout, open the workspace
through the WSL UNC path (`\\wsl.localhost\<Distro>\...`) instead of a Windows
mirror such as `C:\home\...`. Run compound shell commands inside one WSL bash
invocation, for example `wsl -d Ubuntu --cd /path/to/repo -- bash -lc '...'`,
so pipes, redirects, `$(...)`, `&&`, and tools such as `sed` execute in Linux
rather than PowerShell.

## Build And Validation

For Linux debug changes:

```sh
./support/configure-linux-debug.sh
make BUILD=debug -j$(nproc)
./build.debug/movian --help
```

When running Movian, always launch it from the repository root directory to ensure relative paths for resources (skins, fonts, translations, etc.) are resolved correctly:

```sh
cd ~/repos/movian_ag && ./build.debug/movian -d
```

Always run `git diff --check`. Scale additional tests to the changed behavior.

## Recovery

When `.codex/context.sh` exists, run its `check` command at the start of a
resumed session and `refresh` after a merge so local integrations are updated.
Otherwise use `support/codex/context.sh` directly. Local state belongs under
ignored `.codex/`; never commit generated handoff files, indexes, credentials,
machine-specific paths, or test artifacts.
Use the `Knowledge Registry` block from `support/codex/context.sh check` before
inspecting vault files.

## General Coding Guidelines

### 1. Think Before Coding
Don't assume. Don't hide confusion. Surface tradeoffs.
Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First
Minimum code that solves the problem. Nothing speculative.
- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.
Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes
Touch only what you must. Clean up only your own mess.
When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.
When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.
The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution
Define success criteria. Loop until verified.
Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"
For multi-step tasks, state a brief plan:
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
Strong success criteria let you loop independently.

