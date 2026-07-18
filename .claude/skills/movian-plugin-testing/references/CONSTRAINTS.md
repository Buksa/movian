# Movian Runtime Constraints

Use this as the quick "do not repeat known bad patterns" checklist before
launching or interpreting Movian plugin smokes.

## Launch And Process Constraints

- Do not launch Movian from the plugin directory with an absolute binary
  path. Launch from the Movian checkout root (`mdev` already does this); the
  cwd affects `dataroot://` GLW skin/shader resolution.
- `mdev run`/`mdev preview` coexist by default with a manual or foreign
  Movian instance (isolated profile + dynamic port — see the `movian-run`
  skill's coexistence guard, issue #94): they print a one-line warning
  naming the foreign pid(s) and proceed, and never signal a pid they don't
  own. Exit code 2 is reserved for a same-`--name` collision or a same-dir
  collision (a live pid using *this* instance's own `--persistent` path that
  `state.json` can't confirm as owned) — not for a foreign instance merely
  existing. If you launch the binary directly instead of via `mdev`, still
  use the basename-anchored check (`pgrep -fa movian`, then keep only lines
  where an argv token's basename is exactly `movian`) rather than a bare
  `pgrep -f movian`, which also matches unrelated processes whose *path*
  merely contains the substring "movian" — and never signal a pid you did
  not start yourself.
- Do not hardcode `42000`. Parse `http-server: Listening on port ...` from
  the current log every run (`mdev` does this for you).
- Do not use `--no-ui` for HTTP prop navigation smokes. Keep it for
  command-line URL or GDB smokes where the URL is passed as argv.

## HTTP And Prop Constraints

- Do not open routes before dismissing first-launch TOS unless the popup
  itself is the behavior under test. An undisposed TOS popup can leave the
  navigator at `page:home`, `(void)` model props, or an empty current page.
- Do not address unnamed prop children as `nodes/N`; use `nodes/*N`.
- Do not treat `loading=0` alone as success. Require the expected title and,
  for redirects/protocol roots, the expected `currentpage.url`. (Also:
  `loading` can be legitimately void/absent for routes that never create the
  prop — see `movian-plugin-testing/SKILL.md`.)
- Do not activate ordinary appendItem rows with event sinks when the URL is
  available. Read the row URL and call `/api/open`; reserve event sinks for
  action rows, popups, and option controls.
- Do not use STPP paths such as `popups.*0.username`. `*0` is an HTTP alias;
  subscribe to `popups`, recover the child propref, then set fields relative
  to that propref.
- Do not POST a body to `/api/open`. Always pass the URL as a GET query
  string (`?url=...`) — see `movian-run` and `httpcontrol-stpp.md`.

## Visual And Timing Constraints

- Do not use `/api/input/action/<Action>` as proof of visible keyboard focus
  or flat-skin highlight. Use real X11 keypresses
  (`support/devtools/mdevlib/x11_keypress.py`) for visual focus smokes.
- Do not launch Movian with the same direct route that a timing harness
  later opens through `/api/open`; that executes the route twice and skews
  requests.
- Do not classify screenshot failure as route failure unless screenshot
  capture is an explicit acceptance criterion.
- Do not trust a clean `mdev reload`/`mdev preview` exit code as full proof
  for a `.view` change — see the reload false-green note in
  `movian-plugin-testing/SKILL.md` (tracked as #92).

## Shell And Git Hygiene

- Do not build fragile Movian smokes as long inline shell one-liners with
  heavy quoting; write a small script file instead when quoting gets
  fragile.
- Do not let broad process-cleanup commands kill unrelated Movian instances.
  Track the test PID (or `mdev`'s own `--name`) and clean up only that PID.
- Watch the repo's `.gitignore` `core*` pattern (`.gitignore:11`): it
  silently un-tracks any file named `core.py`/`core.sh`/etc. anywhere in the
  tree, not just build-time core dumps. Avoid a `core` prefix for any smoke
  helper script you add.

## Long-idle GLW instance stops dispatching UI events (WSLg)

Observed 2026-07-13 (#88 rework): an mdev instance left idle ~10 minutes
under WSLg kept answering HTTP (`/api/open` returned 200/redirect,
`/api/prop` readable) but its GLW main loop was wedged — 0% CPU, not one
log line after startup, `EVENT_OPENURL` accepted and never dispatched, so
the page never changed. This mimics "route open silently ignored".
Signature: log mtime frozen at startup while HTTP still answers.
Mitigation: don't reuse long-idle instances for event-driven checks —
`mdev stop` + fresh `mdev run`; treat "open returns 200 but navigator
never logs `Opening <url>`" as an instance-health failure, not a route
bug.

Follow-up (2026-07-14, PR #95 review smokes): the same wedge can hit a
FRESH instance immediately at launch, so even X11 keypresses go
undispatched. Health probe: `GET /api/screenshot/raw` on a wedged
instance returns a small error HTML instead of a PNG after ~5 s — cheap
way to tell "GLW loop dead" from "route bug" without waiting on a 20 s
open timeout. Server-side signature in the instance log:
`HTTPSRV [ERROR]: 504 /api/screenshot/raw -- Screenshot timed out`
(src/api/screenshot.c: 5 s cond-wait for a delivered frame, then 504).

CORRECTION (2026-07-15, measured): the previously recommended
`LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe` env is a NO-OP on this
stand and is NOT a workaround. A/B run (4 mdev instances, with/without
the env) showed Mesa selects llvmpipe by default here anyway (`glxinfo`
renderer = llvmpipe even with the vars unset, despite /dev/dxg and
libd3d12 being present), and both wedged instances that night HAD the
env set. The 2026-07-14 "fix" coincided with a relaunch, not with the
variables. What is actually known about the wedge:
- It is a flaky, time-clustered launch condition: wedged instances have
  a LIVE main loop (ticking ~60 Hz in the usleep branch) and answer
  HTTP, but eventSink events (OPENURL, screenshot requests) are never
  dispatched — not even the startup `Opening page:home` nav trace
  appears.
- The same binary launched directly (e.g. under gdb) and a manually
  launched instance on the same display worked while mdev-launched
  ones were wedged; later mdev launches on the same stand were fine.
  Correlation with WSLg compositor/viewer state is suspected but not
  proven.
- Policy: after ANY launch, run
  `python3 support/devtools/mdev smoke run health --name smoke-health`
  before trusting event-driven results. It requires BOTH the startup
  `navigator ... Opening` trace and `/api/screenshot/raw` returning 200/PNG.
  Exit 2 means `mdev stop` + relaunch (env tweaks are cargo cult); if the
  health smoke still fails, report the stand as degraded instead of looping.
