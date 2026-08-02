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

Run `configure-linux-debug.sh` **only from the checkout that owns its real
`build.debug`**. From a worktree it rewrites the shared `config.mak` with that
worktree's `TOPDIR` and breaks every checkout sharing it — and the breakage
surfaces in a *different* tree, long after the command that caused it.

The same applies to read-only-looking probes. `make -q` builds nothing, but GNU
make still remakes the included dependency files, which live in the shared build
directory — so a freshness query from a second checkout leaves the owner
permanently "stale". Verification worktrees get no `build.debug` at all: not a
copy, not a symlink. Build-dependent gates run only in the owning checkout.

## Repo-root requirement

Launch from the repo root. The debug build resolves `dataroot://` — skins,
shaders — against the process's cwd. `mdev` handles this for you; a direct
`./build.debug/movian` invocation does not.
