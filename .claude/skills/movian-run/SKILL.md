---
name: movian-run
description: Build, launch, stop, and drive a Movian dev instance via the mdev CLI. Use when asked to build Movian, run/start/stop/restart Movian, launch it with a dev plugin, open a route, or take a screenshot of the running UI. Does not cover pass/fail testing judgment (see movian-plugin-testing) or the .view edit/reload loop (see movian-view-design).
---

# Movian: Build, Run, Screenshot

Thin procedural reference. For "is this actually correct" judgment (pass/fail
criteria, false-signal traps, verification minimums), see the
`movian-plugin-testing` skill — this skill only covers the launch/stop/open/
shot mechanics.

## Build

```
./configure.linux --build=debug
make BUILD=debug -j$(nproc)
```

Binary lands at `./build.debug/movian`. Use the `debug` build suffix, not
`build.debug` (that is the output *directory* name, not a `--build=` value).

## Repo-root requirement

Always launch from the repo root. The debug build resolves `dataroot://`
(skins, shaders) against the process's current working directory —
`support/devtools/mdevlib/harness.py` launches with
`cwd=str(REPO_ROOT)` for exactly this reason. `mdev` handles this for you; if
you ever invoke `./build.debug/movian` directly, `cd` to the repo root first.

## mdev: launch / stop / open / shot

All state for one named instance lives under `/tmp/mdev/<name>/`
(`state.json`, `movian.log`, `persistent/`, `cache/`, `shots/`). Default name
is `dev` (`preview` for `mdev preview`). Multiple named instances can coexist;
each subcommand defaults to `--name dev` unless given `--name`.

```
mdev run [start_url] [--name NAME] [-p PLUGIN_DIR ...] [--skin DIR] \
         [--dev-flags K=1,K2=1] [--libav-log] [--force]
mdev open <url> [--name NAME] [--timeout SECONDS]     # default timeout: 20s
mdev shot [--name NAME] [--out PATH]
mdev stop [--name NAME]
```

- `-p DIR` (repeatable): load a dev plugin directory, same as core `-p`.
- `--skin DIR`: GLW `--skin` (see `movian-view-design` for the workflow this
  enables).
- `--dev-flags k=1,k2=1`: seeds `<persistent>/settings/dev` as JSON before
  launch (e.g. `smbdebug=1`) — the profile-scoped way to turn on subsystem
  debug logging; see `movian-plugin-testing/references/debug-flags.md`.
- `--force`: restart the instance *this state dir* owns. It never signals a
  process it doesn't own (see coexistence guard below).
- `mdev open` POSTs `/api/open?url=...` (GET-style query string, not a POST
  body — core `hc_open` at `src/api/httpcontrol.c:49` reads a plain query arg
  either way, but a POST body triggers a connection reset in this HTTP
  server; always pass the URL as a query parameter) and waits for page-ready.
  What "ready" means, and its caveats, are documented in
  `movian-plugin-testing` — that judgment does not belong to the launcher.

## Coexistence / foreign-pid guard (updated, issue #94)

`mdev run` (and `mdev preview`'s auto-start) **coexist by default** with any
movian process **not owned by this instance's state dir**: each instance uses
an isolated `--persistent`/`--cache` under `/tmp/mdev/<name>/` and a
dynamically-assigned HTTP port parsed into its own `state.json`, so a foreign
instance (a hand-started Movian, another `mdev` name, the SMB2 test stand's
persistent UI-backed instance — see this repo's `AGENTS.md`) cannot cross-talk
with it. `mdev run` prints a one-line warning naming the foreign pid(s) and
cmdline (`coexisting with foreign movian: pid ... (cmdline)`) and proceeds;
it never signals a pid it doesn't own
(`support/devtools/mdevlib/harness.py: classify_foreign()` /
`kill_owned_pid()`). The process match is basename-anchored (checks that some
argv token's `os.path.basename(...)` is exactly `"movian"`), not a bare
substring match — a bare `pgrep -f movian` also matches unrelated processes
whose *path* happens to contain the string "movian" (e.g. a checkout
directory name).

Exit code 2 is reserved for two narrower cases:

- **Same-name collision**: this instance's own `--name` is already alive —
  use `--force` to restart it (kills only the pid recorded in *this*
  instance's own `state.json`, never a foreign one).
- **Same-dir collision**: a live movian pid's cmdline references this
  instance's own `--persistent` path, but `state.json` doesn't confirm it as
  the owned pid (stale/corrupted state, or a race). This is not a foreign
  instance — investigate before retrying rather than blindly `--force`ing.

`mdev stop` is unchanged: it only ever signals the pid recorded in this
instance's own `state.json`. Never reach for a broad `pkill`/`killall`.

(Supersedes #85's original "refuse if any foreign movian is alive" contract
— see the amendment comment on #85.)

## Reload: views vs. dev-plugin JS (issue #93)

Plain `mdev reload`/`mdev watch` are **views-only** (`ReloadUI`); they never
touch a dev plugin's JS. `--js` opts into the other core reload path:

```
mdev reload --js [--name NAME] [--shot]
mdev watch --js [--dir <plugin-dir>] [--name NAME] [--shot]
```

- `--js` sends `ReloadData` instead of `ReloadUI`. Core routes it
  `hc_action` (`/api/input/action`) → `event_dispatch` → the navigator's
  eventSink → `nav_reload_current` (`src/navigator.c:862`) →
  `plugins_reload_dev_plugin()` (`src/plugins.c:1453`), which force-reloads
  **every** `-p` dev plugin's ECMAScript (unloads+re-executes the JS,
  destroying its permanent resources: routes, services, hooks,
  subscriptions), then reloads the current page as a side effect
  (`nav_reload_page`) — page state resets, same as a fresh open at that URL.
  This is why `--js` is opt-in, not the `reload`/`watch` default.
- Exit 0 only when every `-p` plugin of the instance reports reloaded;
  `mdev reload --js` prints one line per plugin. An instance with no `-p`
  plugin exits non-zero with a clear message instead of a silent green.
- **Known quirk** (`support/devtools/mdevlib/harness.py:
  RELOAD_JS_COMPILE_ERROR_RE`): `plugin_load()` (`src/plugins.c:611`)
  unconditionally falls through to `return 0` for an `"ecmascript"` plugin
  even when the JS fails to compile — so the log can show
  `plugins [INFO]: Reloaded dev plugin <path>` **right alongside** a
  `[ERROR]: Unable to compile <path> -- ...` line for the very same failed
  reload. `do_reload_js()` treats the compile-error line (or
  `Unable to reload development plugin: ...`) as authoritative over a
  same-tick "Reloaded" line for that plugin — don't trust "Reloaded dev
  plugin" alone as proof of a working JS reload.
- `mdev watch --js` extends the existing `.view` watcher: it additionally
  polls the same root for `*.js`/`plugin.json` and runs the `--js` flow on
  change; `.view` changes under `--js` still run the plain `ReloadUI` flow.
  Default root without `--dir` is `glwskins/flat` (unchanged) unless `--js`
  is given, in which case it defaults to this instance's own `-p` plugin
  dir — but only when there is exactly one; pass `--dir` explicitly for a
  multi-plugin instance. A poll tick with both `.view` and JS changes runs
  the JS reload only (it already implies a page reload, so a separate
  `ReloadUI` would be redundant).

## Logs, props, screenshots

- `mdev log [--tail N] [--errors]` — dump/tail `movian.log`. `--errors`
  filters to error-signal lines (`TypeError`, `ReferenceError`, GLW view
  errors, etc.) and exits 1 if any matched.
- `mdev props <slash-path> [--depth N]` — pretty-prints an `/api/prop`
  subtree, e.g. `mdev props global/navigators/current/currentpage --depth 2`.
- `mdev shot [--out PATH]` — PNG via `/api/screenshot/raw`; default path is
  `/tmp/mdev/<name>/shots/<timestamp>.<ext>`.

## Regression smokes

Before opening a PR that changes `mdev`, `support/devtools/viewpreview`, or
GLW-adjacent C, run the declarative regression gate from the repository root:

```
python3 support/devtools/mdev smoke run all --name smoke-pr
```

Exit 0 means every smoke passed; exit 1 is a scenario assertion failure; exit
2 is an instance-health wedge and calls for stop+relaunch rather than a code
diagnosis. `run all` assumes a fresh instance — `mdev stop --name smoke-pr`
first if one is alive: `keyboard-mode` asserts the pristine
`$ui.keyboard` precondition and will correctly fail on a reused instance
that already entered keyboard mode; an `assert_log` in the first step of a
smoke (only `health` does this) matches the whole instance log, not a
delta. A failure prints its bundle directory as the last stderr line.
Read `steps.json` first for the failing step index, verb, and evidence, then
use `log-tail.txt` and `props.json` to correlate log and current-page state;
`shot.png` is best-effort and is deliberately skipped for a wedge.


## Quickstart

Run every step from the repo root; stop the instance when done so no test
Movian lingers:

```
ls build.debug/movian          # build check — rebuild first if missing
mdev run
mdev open page:settings
mdev shot
mdev stop
```

An X11 window is expected to appear during this sequence (WSLg or an
equivalent X server) — that is normal, not a failure.

## Fallback (no mdev)

Raw launch, only when `mdev` itself is unavailable or under test:

```
./build.debug/movian -d --disable-upgrades \
  --persistent /tmp/<test>/persistent \
  --cache /tmp/<test>/cache \
  -p /path/to/plugin
```

`-d` is maximum trace in this tree (stdout gets debug plus navigation/tuning
trace). Parse the real port from `http-server: Listening on port <N>` in the
log — never hardcode `42000`. Add `--libav-log` only for playback/HLS/FFmpeg
diagnosis; it makes ordinary route smoke logs noisier without adding signal.
