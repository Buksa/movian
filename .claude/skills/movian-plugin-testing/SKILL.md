---
name: movian-plugin-testing
description: Pass/fail judgment and verification rules for testing Movian plugins, core runtime behavior, HTTP Control/STPP prop APIs, GLW UI, media/protocol playback, and SMB2. Use when asked to smoke-test a plugin change, verify a route/prop/screenshot actually changed, judge whether a fix is proven, diagnose a false pass or false failure, or decide how much verification a change needs. Layered on the `movian-run` skill (build/launch mechanics) and the `mdev` CLI.
---

# Movian Plugin Testing

This skill is about **judgment**, not mechanics: what counts as proof, what
counts as noise, and how much verification a given change needs. Launch/stop/
open/shot mechanics live in `movian-run`; use that skill's `mdev` commands to
execute the steps described here.

## Before touching anything: state falsifiable criteria

Before launching or mutating runtime state, write down the narrow thing that
would prove or disprove the change:

- target route or user flow;
- expected page title/type/loading state;
- expected metadata fields;
- expected node count or key row types;
- expected visual/artwork state;
- disallowed log patterns.

Use the smallest flow that can prove the change. A broad "smoke everything"
pass is not a substitute for one targeted assertion.

## Rule: transport success is not proof

HTTP 200 and an `eventSink` `OK` prove only that the request was accepted —
never that the intended effect happened. Always verify the actual mutation:
the prop value changed, the route transitioned, the popup closed, playback
started, or the screenshot shows the expected frame. See
`references/httpcontrol-stpp.md` for the full HTTP Control/STPP safety model
and effect matrix.

## Page-ready semantics (and its trap)

"Ready" is not simply `loading == 0`. `mdev open`/`mdev preview` treat a page
as ready when `model/loading` is `"0"` **or void/absent** — some routes
never create a `loading` prop at all (any `page:*` URL is one: that is the
static-page backend, which only sets `type` and a title; the real settings
page is `settings:`) — AND
the title prop has a real value (not void/absent). See
`support/devtools/mdevlib/harness.py: open_and_wait()`.

Do not stop at `loading=0` alone: a redirect or protocol root can leave
`loading=0` while `currentpage.url` still points at the *previous* page.
Require the expected title/type **and**, for redirects or protocol roots,
the expected `currentpage.url` too.

## Reading props

- HTTP paths (`/api/prop/...`, `mdev props ...`) are slash-separated and
  start with `global`. Unnamed children display as `*N` — that is an
  HTTP-only display alias, not a real path component.
- STPP JSON paths are dot-separated, relative to propref `0`, and **omit**
  the `global.` prefix. Do not reuse an HTTP slash path or the `*N` alias in
  STPP — see `references/httpcontrol-stpp.md` and `references/prop-debugging.md`.
- A named path that does not yet exist is not guaranteed to be a harmless
  404: `prop_from_path()` follows symlinks and enables indexing, so a typo
  can materialize a new void prop in the tree. Read the known parent first,
  copy the child name from the response, then descend one segment at a time.
- Read a row's `url` and call `/api/open?url=...` for ordinary navigation.
  Reserve POST `action=Activate`/`action=Ok` to an `eventSink` for action
  rows, popups, and option controls — not for rows that have a real URL.
- On a fresh profile, dismiss first-launch popups (e.g. plugin TOS) with
  `action=Ok` to `global/popups/*0/eventSink` before the first `/api/open`,
  unless the popup itself is the behavior under test. An undisposed popup
  can leave the navigator at `page:home` with void model props even after
  `/api/open` reports success.

## Anti-flake timing rules

- `prop.subscribeValue()` fires once immediately on subscribe — treat that
  first callback as current state, not a user action.
- Wait for the concrete prop that proves the flow, not a fixed sleep: title
  + `loading` for a route, node `url`/title for a row, the popup node before
  sending `Ok`, playinfo props before asserting resume state.
- For a `mdev reload`/`mdev watch` view-file loop: view-parse errors surface
  in the log within a couple hundred ms of the reload/open request, but a
  prop-driven view change (e.g. `mdev preview`'s `page.metadata.glwview`
  write) is dispatched to the GLW thread asynchronously and can land slightly
  *after* the page already reports ready — poll the log for a short settle
  window rather than reading it once immediately.
- **Known blind spot (tracked as #92):** `mdev reload`/`mdev preview` only
  grep the log for GLW *parser/preprocessor* errors
  (`GLW [ERROR]: Error <file>:<line>: ...`). A `.view` **lexer** error (e.g.
  an unterminated string literal) or a file-open failure on the target
  `.view` does not match that pattern and is currently invisible to these
  commands' exit code — they will report a clean reload/preview even though
  the view failed to load. Until #92 lands, do not trust a bare `mdev
  reload`/`preview` exit-0 as full proof for a `.view` syntax change; also
  read `mdev log --tail` (or a screenshot) and look for any `GLW` line near
  the reload, not just the two patterns the command itself checks.

## Error-signal triage

Grep the log delta (not the whole log) for: `TypeError`, `ReferenceError`,
`Cannot read property`, `Unable to load image`, `Unknown format`, a
plugin-specific error trace, or a GLW view-parse error. `mdev log --errors`
already applies this set. Ignore known noise: repository/update checks
against dead `movian.tv` show network errors unrelated to the task unless
repo/update behavior is itself under test.

## Hash-before-vision rule (issue #129)

`mdev shot` computes the screenshot's SHA-256 and prints `sha256=<hex>`.
The hash is also stored in the instance's `state.json` as `last_shot_hash`.

**Rule: hash first, vision only on change.** Before sending a screenshot to
a vision model, compare its `sha256` hash against the previously-judged hash:

- If the hash matches the last judgment, reuse the cached verdict — do not
  re-send the image to the vision role.
- If the hash differs (or no prior judgment exists), send the image and
  cache the verdict keyed by `(hash, question)`.
- `mdev shot --if-changed` exits with code 3 when the hash is unchanged;
  agents skip the vision call on exit 3.

This saves external quota: GLW static-page rendering is deterministic, so
a content hash is a reliable "same picture" signal. The hash proves
identity; it never proves non-identity (dynamic pages may produce the same
hash by coincidence, but different hashes always mean different images).

## Verification minimums per change class

- **Plugin JS change**: `node --check`, `git diff --check`, a focused Movian
  smoke of the affected route, a screenshot when the change is visual, and a
  log grep for new JS/GLW/image errors.
- **Core/launcher change**: relevant build, plus `./build.debug/movian
  --help` and a short flag smoke proving `-d`, `-s`/`--persistent`,
  `--cache`, `-p`, `-j` are consumed as options — logs must not show
  `navigator |N| Opening -<flag>` (a flag that leaked through as a URL).
- **Media/protocol change**: prove the specific layer under test —
  `routed`, `probed`, `decoded`, or `rendered` (see
  `references/media-playback-smoke.md`) — not just that the URL dispatched.
- **Native crash/hang**: preserve artifacts (command, log tail, screenshot,
  exit status) and use a separate debug/GDB build rather than repeating the
  same failing smoke. Reach for ASan only when evidence points at memory
  corruption.
- **`.view` change**: before/after screenshot, plus the reload false-green
  caveat above — a clean `mdev reload`/`preview` exit code is not sufficient
  proof by itself for anything touching quoting/lexing.

## Human handoff rule

If a UI action, popup, route transition, render state, or playback wait
exceeds 120 seconds, stop blind waiting. Save URL, title, loading, node
count, popups, a screenshot, and the last 120-200 log lines. Ask the user to
perform one exact manual action while you keep observing. Do not spin
indefinitely.

## Housekeeping gotchas

- The repo's `.gitignore` has a bare `core*` pattern (`.gitignore:11`). This
  silently un-tracks **any** file named `core.py`, `core.sh`, etc.,
  *anywhere* in the tree — not just build-time core dumps. If you add a
  helper script for a smoke test, avoid the `core` prefix, or you'll lose it
  silently to `git status`/`git add -A`.
- Use the process-guard pattern from `movian-run` (basename-anchored, not a
  bare substring match) before assuming "no Movian is running".

## Source navigation

`.codegraph/` is indexed in this tree — use `codegraph_explore` /
`codegraph explore` before grep/Read for any core-code claim, and re-check
line numbers in the current checkout before citing them (a chain of
`observation -> handler file:line -> downstream file:line -> effect` is the
expected reporting shape). Fall back to scoped `rg -n` for `.view` files,
preprocessor macros, and generated registrations that CodeGraph does not
resolve as precisely. This repo's own `AGENTS.md` and CodeGraph replace the
old Codex-specific code-navigation walkthrough; there is no ported reference
for it here.

## Reference routing

- General pass/fail, prop-path, and popup rules beyond the summary above:
  `references/test-rules.md`.
- Known bad patterns to never repeat (process/launch/HTTP/timing gotchas):
  `references/CONSTRAINTS.md`.
- Launch/CLI/debug flags, dev-flag seeding, GDB/ASan builds:
  `references/debug-flags.md`.
- `/api/prop` internals, subscriber source anchors, `prop.print`,
  ECMAScript stats/GC: `references/prop-debugging.md`.
- HTTP Control endpoint matrix, STPP JSON/binary protocol, known protocol
  defects: `references/httpcontrol-stpp.md`.
- Playback/HLS/FFmpeg/RTMP evidence levels and artifact shape:
  `references/media-playback-smoke.md`.
- Deterministic focus on async-loading GLW pages: `references/glw-async-focus.md`.
- Pointer/touch/kinetic-scroll smoke matrix: `references/glw-pointer-touch-smoke.md`.
- SMB/SMB2 client/server/browser testing (navigation, auth, embedded server,
  keepalive/pool assertions): `references/smb2-smoke.md`.
- Steam Deck/Flatpak remote validation: `references/steamdeck-flatpak.md`
  (only relevant when testing against real Deck/Flatpak hardware).
- Structured HTTP/STPP inspection scripts (JSON snapshot, live prop watch,
  STPP JSON/binary probe, X11 keypress/pointer injection): run as modules
  from `support/devtools/mdevlib/` — see `references/prop-debugging.md` and
  `references/httpcontrol-stpp.md` for exact invocations.

## Not ported from the Codex skill (with reasons)

- `smoke-runner.md` — the bash smoke-runner shape it documented is
  superseded by `mdev run`/`open`/`shot`/`reload`/`log`. Its SMB2-specific
  and Steam-Deck-specific lessons (pool/keepalive assertions, remote Deck
  smoke lessons) were folded into `smb2-smoke.md` and
  `steamdeck-flatpak.md` respectively rather than dropped outright.
- `code-navigation.md` — this repo already has `.codegraph/` plus
  `AGENTS.md`'s own CodeGraph-first guidance; the Codex doc's WSL/Node-PATH
  workaround content doesn't apply to this harness.
- `api-audit-system-prompt.md` — a full Codex system-prompt for a standalone
  audit agent persona; out of scope for a project skill here.
- `agent-workflow-prompt.md` — a Codex-specific autonomous QA/worklog
  prompt template; not applicable to this harness's workflow.
