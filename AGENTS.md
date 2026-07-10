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

After changing source files, run `codegraph sync` so later CodeGraph queries
(yours or another agent's) reflect the edits. The file watcher lags writes by
about a second; `codegraph sync` makes the refresh explicit. This applies to
executor and verifier agents as well, not just interactive sessions.

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
