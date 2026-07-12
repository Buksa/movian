# Movian Runtime Constraints

Use this as the quick "do not repeat known bad patterns" checklist before
launching or interpreting Movian plugin smokes.

## Launch And Process Constraints

- Do not launch Movian from the plugin directory with an absolute binary
  path. Launch from the Movian checkout root (`mdev` already does this); the
  cwd affects `dataroot://` GLW skin/shader resolution.
- Do not start another Movian if a manual or non-test instance is already
  running. `mdev run`/`mdev preview` already refuse to start when a
  `movian`-basename process not owned by their own state dir is alive (exit
  code 2) — if you launch the binary directly instead, use the same
  basename-anchored check (`pgrep -fa movian`, then keep only lines where an
  argv token's basename is exactly `movian`) rather than a bare
  `pgrep -f movian`, which also matches unrelated processes whose *path*
  merely contains the substring "movian".
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
