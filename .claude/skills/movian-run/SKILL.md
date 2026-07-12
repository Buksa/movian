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
  process it doesn't own (see stale-process guard below).
- `mdev open` POSTs `/api/open?url=...` (GET-style query string, not a POST
  body — core `hc_open` at `src/api/httpcontrol.c:49` reads a plain query arg
  either way, but a POST body triggers a connection reset in this HTTP
  server; always pass the URL as a query parameter) and waits for page-ready.
  What "ready" means, and its caveats, are documented in
  `movian-plugin-testing` — that judgment does not belong to the launcher.

## Stale-process / foreign-pid guard

`mdev run` (and `mdev preview`'s auto-start) refuse to start when a movian
process **not owned by this instance's state dir** is already alive — exit
code 2, and it never kills a pid it doesn't own
(`support/devtools/mdevlib/harness.py: movian_pids()` / `kill_owned_pid()`).
The process match is basename-anchored (checks that some argv token's
`os.path.basename(...)` is exactly `"movian"`), not a bare substring match —
a bare `pgrep -f movian` also matches unrelated processes whose *path*
happens to contain the string "movian" (e.g. a checkout directory name).

If you see the refusal: stop the owning instance by its own `--name` first
(`mdev stop --name <name>`), or use `--force` only on the instance you
already own. Never reach for a broad `pkill`/`killall`.

## Logs, props, screenshots

- `mdev log [--tail N] [--errors]` — dump/tail `movian.log`. `--errors`
  filters to error-signal lines (`TypeError`, `ReferenceError`, GLW view
  errors, etc.) and exits 1 if any matched.
- `mdev props <slash-path> [--depth N]` — pretty-prints an `/api/prop`
  subtree, e.g. `mdev props global/navigators/current/currentpage --depth 2`.
- `mdev shot [--out PATH]` — PNG via `/api/screenshot/raw`; default path is
  `/tmp/mdev/<name>/shots/<timestamp>.<ext>`.

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
