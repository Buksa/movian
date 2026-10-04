# A skin is chosen from a built-in list, at startup

**Decided, not yet implemented.** The owner took this decision on
2026-10-04. #283 implements it, and #283 waits on #256. Until #283 lands,
`glw_init4()` reads only `--skin` and the compiled default. The selector, the
saved ID and the startup check described below do not exist yet.

Look and feel gets a **Skin** selector. What it saves is a **skin ID**, such as
`flat` or `old`, and never a path. `app_dataroot()` differs between a source
checkout, an installed package, and a ZIP or compiled-in bundle, so one saved
value has to mean the same skin in all of them. The choice takes effect at the
next start. `glw_init4()` already prefers `--skin` and otherwise falls back to
`SHOWTIME_GLW_DEFAULT_SKIN` (`glw.c:214,234-238`). Between the two it is to
consult the saved ID, resolved under `<dataroot>/glwskins/`. The setting's
callback only records the ID, so a root that is already built does not change.
`--skin` is never written to the setting and keeps taking any path.

The selectable skins are a list in the code, `flat` and `old`, not a scan of
`glwskins/`. An entry is offered only when its `universe.view` is readable, and
the selector row is hidden when fewer than two entries pass. At startup the
same check runs on the saved ID. If the ID is not on the list, or its
`universe.view` cannot be read, the failure is logged, the default is used, and
the saved value is reset. Nothing is stored until the user chooses, so a user
who never chose follows the build's default if that default changes.

## Why a list and not a scan

A directory with a `universe.view` is not necessarily a working skin. `old` has
one, and `movian-analyze --check` passes it, yet at `2f0d19837` `old` cannot
render its own Look and feel page (#256). A scan would have offered it, and a
user who picked it could not have got back to the selector. Nothing in GLW can
tell either. `glw_load_universe()` returns `void`, and a view that fails to
load or evaluate is still returned, rendered as an error label. The list is
therefore what "supported" means. Adding a skin takes one line in it, one in
`BUNDLES` (`support/configure.inc`), and an acceptance pass on a display.

## Considered

**Live switching.** `ACTION_RELOAD_UI` reloads the universe of the same root.
Switching skins on top of it would need several things the code does not do
today:
- draining the old root's loaders;
- resetting what the old skin wrote into `$ui`, plus `gr_default_font` and
  `ui.skin.path`, none of which a reload resets;
- replacing `gr_skin` at a point where nothing still reads it (`glw.c:340-382`).

A restart costs the user one step and needs none of that.

**Typed load errors with recovery.** `glw_view_create()` and
`glw_load_universe()` could return a status, and a failed root could be rebuilt
with the default. That would catch a skin that loads and then breaks on one
page. But it touches the root lifecycle of every frontend (X11, macOS, Android,
PS3, RPi, Sunxi, iOS, NaCl), to guard against a case the list already excludes.
It becomes necessary once skins arrive that nobody here accepted, such as
user-installed or discovered ones. At that point the scan comes back too.

**A virtual "Default" entry** that always follows the build's default. Today it
would sit beside "Flat" and be the same skin. Storing nothing until the user
chooses gives that behaviour to everyone who never chose.

**"Theme" as the word users see.** The project renamed theme to skin in 2012
(`877b9380d`), and `--skin`, `skin://`, `gr_skin` and `glwskins/` all say skin.
`flat/theme.view` is a file of style macros inside one skin, so "theme" already
names something smaller. One word is used at every level.

## Consequences

A saved skin that loads but fails on some page is neither detected nor
reverted. On a platform without a command line, the only way back is the
selector itself, which is why a skin's acceptance covers its settings pages.

`old` is bundled in every build, at about 0.44 MB compressed. It costs several
times `flat`'s idle CPU (#256). That cost is stated here, not gated per
platform.
