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

### Testing the embedded SMB2 server

Movian's process lifetime is tied to its UI event loop (`main()` blocks in
`glw_x11_main()`); it runs with a UI in every real deployment, so the embedded
SMB2 server stays alive for as long as the app is open. **There is no headless
daemon mode and none is planned** — do not add one for a media player.

Consequence for testing: a **headless launch** (no X display, or `--no-ui`,
with a fresh `--persistent` profile) **self-terminates ~2.5–3 s after startup**
("Opening page:home" → "ASYNCIO Shutdown"). A server driven that way only ever
answers **immediate one-shot** requests before the process exits — it never
exercises an idle session, so signing/keepalive behavior is invisible. This is
exactly how the #76 signing guard's rejection of Samba's unsigned `SMB2_ECHO`
keepalive slipped past the one-shot smokes (fixed in #79).

So any test of **idle / keepalive / interactive** server behavior must run
against a **persistent, UI-backed** Movian — launch it on a real or virtual
display (e.g. `DISPLAY=:0 XAUTHORITY=... ./build.debug/movian -d`, or `Xvfb`)
and hold the client connection open (`smbclient` interactive, or an idle then a
second operation). A one-shot `smbclient -c 'ls'` proves nothing about the idle
path. The existing one-shot smokes stay valid for the non-idle surface.

## Recovery

When `.codex/context.sh` exists, run its `check` command at the start of a
resumed session and `refresh` after a merge so local integrations are updated.
Otherwise use `support/codex/context.sh` directly. Local state belongs under
ignored `.codex/`; never commit generated handoff files, indexes, credentials,
machine-specific paths, or test artifacts.
Use the `Knowledge Registry` block from `support/codex/context.sh check` before
inspecting vault files.
