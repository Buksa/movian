---
name: movian-run
description: Build the Movian core for Linux debug work — configure wrapper, build targets, and the build-directory ownership rule. Use when asked to build or rebuild Movian, configure a checkout, or diagnose a broken build. Launching and driving a built Movian lives in the `movian:run` skill.
---

# Building Movian

**The launch half of this skill moved.** `mdev` mechanics — run, stop, open,
shot, logs, props, reload, watch, the coexistence guard — now live in the
**`movian:run`** skill, delivered by the
[movian-plugin-sdk](https://github.com/Buksa/movian-plugin-sdk) plugin. Those
commands resolve the core checkout themselves, so one copy serves core and plugin
work alike:

```
claude plugin marketplace add Buksa/movian-plugin-sdk
claude plugin install movian@movian-plugin-sdk
```

Building is core work and stays here.

## Build

```
./support/configure-linux-debug.sh
make BUILD=debug -j$(nproc)
```

The binary lands at `./build.debug/movian`. Note `debug` is the `--build=` value;
`build.debug` is the output *directory* name.

Use the wrapper rather than a bare `./configure.linux --build=debug` — it supplies
the libav and gmp flags newer Ubuntu and WSL need, and resolves `TOPDIR` from its
own location instead of the caller's cwd.

`movian-analyze` is **not** part of the default target, and several checks depend
on it:

```
make BUILD=debug -j$(nproc) movian-analyze
```

## A build directory belongs to exactly one checkout

Every checkout — worktrees included — configures and owns its own
`build.debug`. `configure-linux-debug.sh` resolves `TOPDIR` from its own
location and builds there, so running it inside a worktree is correct and
touches no other tree. The external-dependency cache under `~/.cache/movian-ext`
is what keeps a private build cheap (`docs/Guides/LINUX_FLATPAK_SMOKE_CHECKLIST.md`).

```
./support/configure-linux-debug.sh
make BUILD=debug -j$(nproc)
```

**The restriction applies to a shared `build.debug` symlink, which is legacy.**
Where one still exists, running configure through it rewrites the owner's
`config.mak` with this worktree's `TOPDIR` and breaks every checkout sharing it,
and the breakage surfaces in a *different* tree long after the command that
caused it. Read-only-looking probes are no safer: `make -q` builds nothing, but
GNU make still remakes the included dependency files inside that shared
directory, so a freshness query from a second checkout leaves the owner
permanently "stale".

So: check first, then act.

```
[ -L build.debug ] && echo "shared symlink — do not configure here"
```

If it is a symlink, remove it and configure a private build before running any
build-dependent gate in this worktree. If it is absent or a real directory, the
gate belongs here and skipping it means the change went unverified — or worse,
was verified against another checkout's binary.

## Repo-root requirement

Launch from the repo root. The debug build resolves `dataroot://` — skins,
shaders — against the process's cwd. `mdev` handles this for you; a direct
`./build.debug/movian` invocation does not.
