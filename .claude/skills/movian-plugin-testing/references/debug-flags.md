# Debug flags: core-developer sections

The plugin-facing flag surface — smoke launch, playback/probe runs, GLW debug,
pointer/touch, dev-flag seeding, line-buffered output — moved to the
`movian:verify` skill (`references/debug-flags.md`) in movian-plugin-sdk.
What stays here needs a core checkout and a core developer.

## Core Crash / GDB Builds

For plugin route/UI testing use the normal `build.debug` binary. For C
crashes, hard-to-read stacks, or memory lifetime bugs, build a separate
development profile instead of replacing the normal debug package:

```bash
LIBAV_COMMON_FLAGS="--disable-inline-asm" ./configure.linux \
  --build=debug-gdb \
  --disable-vdpau \
  --disable-avahi \
  --disable-webkit \
  --enable-polarssl \
  --cc=gcc-9 \
  --optlevel=0 \
  --extra-cflags=-fno-omit-frame-pointer \
  --enable-bughunt

make BUILD=debug-gdb -j$(nproc)
gdb --args ./build.debug-gdb/movian -d --disable-upgrades
```

Use `--sanitize=address` only in a separate `build.asan` style tree when the
goal is memory corruption or use-after-free diagnosis. ASan is valuable, but
it changes runtime behavior and is too noisy for routine plugin smoke tests.

## CLI Sanity After Core Changes

After changing CLI parsing, run `movian --help` and a short flag smoke
proving that advertised options are consumed as options. Specifically check
that logs do not contain:

```text
navigator |N| Opening -j
navigator |N| Opening -s
navigator |N| Opening --some-option
```

Minimum flags to test: `-d`, `-s`, `-j`, `--persistent`, `--cache`, `-p`, and
`--disable-upgrades`.

## libsmb2 `smb2_echo` socket-validity check (historical, now fixed)

`smb2_echo()` in `ext/libsmb2/lib/sync.c` once had an inverted
socket-validity check (`SMB2_VALID_SOCKET` instead of `!SMB2_VALID_SOCKET`)
in an earlier `libsmb2-6.2`-based revision, so it returned `-ENOMEM` ("Not
Connected") on every live session. The current gitlink
(`ext/libsmb2` at `libsmb2-6.2-208-gc443352` as of this writing) has the
fix — `grep '!SMB2_VALID_SOCKET(smb2->fd)' ext/libsmb2/lib/sync.c` matches at
line 83, 861, and 961. If `smbdebug` ever shows `Keepalive ... echo rc=-12`
after a submodule bump, re-check that grep first; it means the fix regressed
out of the pinned commit, not that Movian's client logic is broken.
