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

## Before Publishing, Scan The Diff

A commit message, a PR body and a rules file are all published. Machine-specific
detail reaches them easily, because it is exactly what you were just looking at:
a host address, a home directory, a key filename, a machine name, a local-file
URL that resolves for nobody else.

It has happened twice here. Once a batch of local-file links reached a public
PR, because the screen meant to catch them sat behind a `&&` that never ran.
Once a LAN address reached this very file, because nothing was checking at
all.

So check the change AND the messages, not just the file you were editing:

`support/check-publishable.sh <base>` does it, over both the changed files and
the commit messages. It is deliberately short enough to read in one sitting and
deliberately not clever: an address, a home path, a key path, a local-file URL.

The point is not the pattern, which will always miss something. It is that the
scan runs unconditionally, on its own line, and that its silence is read as a
result rather than assumed. Run it once against a revision you know carries an
address, so you have seen it speak before you trust it to be quiet.

Environment specifics belong in private notes. A rule should name the
requirement, not the host that happens to satisfy it today.

## Where Work Is Pushed

Work goes to this project's own repositories: `Buksa/movian` for the core and
`Buksa/movian-plugin-sdk` for the SDK. This is a fork, so a checkout may also
carry a remote for the upstream it came from; if you add one, point its push
URL at something that is not a URL, so a push aimed there fails on the remote
name rather than reaching a server. That is per-checkout configuration, not a
property of the repository -- a fresh clone has one remote and no such guard.

**Push by explicit URL, not by remote name.** Six checkouts are in play and
they do not agree on which remote is called what, so `git push origin
<branch>` means different things depending on where you are standing.
`git push git@github.com:Buksa/movian.git <branch>` says where it is going and
cannot be surprised by local configuration.

**Confirm, do not assume, in either direction.** `gh api repos/<owner>/<repo>
--jq .permissions.push` answers whether a push could land at all, and
`gh api --paginate repos/<owner>/<repo>/branches --jq '.[].name'` answers
whether anything already did -- both in one call, neither by recollection.

## What Counts As Verified

Two standards, because the two kinds of change fail in different ways.

**A change a user can observe is verified by observing it**, on real
hardware rather than from the WSL workspace -- see the guardrail above for
why that distinction is not cosmetic. A log line saying a thing happened is
weaker than the thing; where the change is on the wire, the evidence is the
wire.
`wss://` was found putting a plaintext handshake on port 443 by capturing it,
after the log had said nothing at all for the same failure.

And a silent instrument is not evidence until the instrument is known to be
working. After proving the cleartext was gone, the capture was checked with an
unrelated request that appeared in it — otherwise "no packets" and "tcpdump
died" are the same observation.

**A change to the generator or a gate is verified by falsifying it.** Break the
rule deliberately, and name in advance which pins must drop; if the same set
does not drop, the check was not measuring what it claimed. A gate whose
verdict cannot change is not a gate, whatever colour it prints. Ask of any
check: *what, exactly, could make this come out differently?*

Both standards share one rule. Before reporting a number, name what it counted
and what would move it. A count that moves in a direction it cannot move is a
measurement bug, not a finding.

## Narrowing The Generated API

`generated/movian-api.d.ts` is a contract. It has one plugin author today and
is written for more later, which means the moment to be careful is now.

**The test is what a parameter ACCEPTS, not how its type is spelled.** A
change that accepts strictly more is free; one that accepts less is narrowing
and needs the proof below.

Free, because every call that compiled still does -- measured, not assumed:
`string` to `any`, `string` to `string | number`, a required parameter becoming
optional, an object parameter gaining an optional member.

Narrowing, and this is the case that matters here: `any` to a concrete type.
`any` accepts every argument, so `f(42)` is fine against `any` and an error
against `string`. An earlier draft called that widening, which would have
licensed exactly the unproven restrictions the rule exists to prevent -- and
#209 replaced `any` on 203 parameter slots, four of them wrongly.

**Narrowing requires proof from the C**, quoted in the commit: the exact
accessor, at the line where the callee reads that argument. Replacing `any`
is narrowing and needs that proof like any other. `plugin_examples`
and the fixtures cannot license a narrowing, because both are our own corpus
and contain no third-party call site by construction. Four signatures were
narrowed wrongly in one round and every one of them passed the whole battery
green; three of the four were caught by review, not by a gate.

When the C is ambiguous -- more than one branch reading a slot differently,
with no proof that anything else is rejected -- the type stays `any` and the
evidence goes in the artifact instead.

## Recovery

When `.codex/context.sh` exists, run its `check` command at the start of a
resumed session and `refresh` after a merge so local integrations are updated.
Otherwise use `support/codex/context.sh` directly. Local state belongs under
ignored `.codex/`; never commit generated handoff files, indexes, credentials,
machine-specific paths, or test artifacts.
Use the `Knowledge Registry` block from `support/codex/context.sh check` before
inspecting vault files.
