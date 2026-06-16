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

## Build And Validation

For Linux debug changes:

```sh
./support/configure-linux-debug.sh
make BUILD=debug -j$(nproc)
./build.debug/movian --help
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
