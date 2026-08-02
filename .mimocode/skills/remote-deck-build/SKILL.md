---
name: remote-deck-build
description: Use when building and testing Movian on the remote Steam Deck (movian_deck). Covers SSH setup, file sync, remote build, and verification. Trigger on "build on deck", "test on stand", "remote build", "SSH to deck", or when cross-validating changes on the ARM target.
---

# Remote Deck Build

Standardized workflow for building and testing Movian on the remote Steam Deck (`movian_deck` at `192.168.1.82`).

## Connection Details

- **Host**: `192.168.1.82`
- **User**: `user`
- **SSH key**: `~/.ssh/movian_deck`
- **Repo on deck**: `~/GitHub/movian`

## Setup Variables

Always define these at the top of your script:

```sh
SSH="ssh -i ~/.ssh/movian_deck -o BatchMode=yes -o ConnectTimeout=8 user@192.168.1.82"
SCP="scp -i ~/.ssh/movian_deck -o BatchMode=yes"
```

## Standard Workflow

### 1. Verify connectivity

```sh
$SSH 'echo OK && hostname && uname -sr'
```

### 2. Sync branch

```sh
$SSH 'cd ~/GitHub/movian && \
  git fetch -q origin zCode-smb2-client-parity 2>&1 | tail -2 && \
  git stash -q 2>/dev/null && \
  git checkout -q zCode-smb2-client-parity 2>&1 | tail -2 && \
  git reset --hard origin/zCode-smb2-client-parity 2>&1 | tail -2 && \
  echo "HEAD=$(git rev-parse --short HEAD)"'
```

### 3. Sync specific files (faster than full branch reset)

```sh
$SCP src/sd/wsd.c user@192.168.1.82:/home/user/GitHub/movian/src/sd/wsd.c
```

For multiple files:
```sh
for f in src/sd/wsd.c src/fileaccess/smb2/fa_libsmb2.c; do
  $SCP "$f" user@192.168.1.82:/home/user/GitHub/movian/"$f"
done
```

### 4. Build on deck

```sh
$SSH 'cd ~/GitHub/movian && \
  make BUILD=debug -j"$(nproc)" 2>&1 | grep -iE "warning|error| CC | LD " | tail -15; \
  echo "EXIT=${PIPESTATUS[0]}"; \
  git diff --check && echo diff-check-clean'
```

Key flags:
- `grep -iE "warning|error| CC | LD "` — filter noise, show only compilation/linking events
- `PIPESTATUS[0]` — capture make exit code through the pipe
- `git diff --check` — verify no whitespace errors

### 5. Verify build artifact

```sh
$SSH 'ls -la ~/GitHub/movian/build.debug/movian'
```

### 6. Run on deck (optional)

```sh
$SSH 'cd ~/GitHub/movian && \
  pkill -f build.debug/movian 2>/dev/null; sleep 1 && \
  ./build.debug/movian -d &'
```

Note: Movian requires a display. On the deck, it uses the physical display. For headless testing, use `mdev` on the local machine instead.

## Common Patterns

### Incremental build (single file changed)

```sh
$SCP src/sd/wsd.c user@192.168.1.82:/home/user/GitHub/movian/src/sd/wsd.c
$SSH 'cd ~/GitHub/movian && \
  touch src/sd/wsd.c && \
  make BUILD=debug -j"$(nproc)" 2>&1 | grep -iE "wsd|warning|error" | tail -10; \
  echo "EXIT=${PIPESTATUS[0]}"'
```

### Full rebuild with error capture

```sh
$SSH 'cd ~/GitHub/movian && \
  make BUILD=debug -k -j"$(nproc)" > /tmp/build.log 2>&1; \
  echo "MAKE_EXIT=$?"; \
  grep -E "error:" /tmp/build.log | head -20; \
  tail -3 /tmp/build.log'
```

### Cleanup remote state

```sh
$SSH 'cd ~/GitHub/movian && \
  git checkout -- . 2>/dev/null; \
  git clean -fd 2>/dev/null | tail -5; \
  echo "=== status ==="; git status --short | head'
```

### Instrument and rebuild (for debugging)

```sh
$SCP /tmp/instrument.py user@192.168.1.82:/tmp/instrument.py
$SSH 'cd ~/GitHub/movian && \
  cp ext/libsmb2/lib/libsmb2.c /tmp/libsmb2.c.bak && \
  python3 /tmp/instrument.py ext/libsmb2/lib/libsmb2.c && \
  touch ext/libsmb2/lib/libsmb2.c && \
  make BUILD=debug -j"$(nproc)" 2>&1 | tail -3'
```

## Gotchas

- **`BatchMode=yes`** — Prevents SSH from hanging on password prompts. If the key is missing or passphrase-protected, the connection fails immediately.
- **`ConnectTimeout=8`** — Deck may be slow to respond. 8 seconds is enough for LAN.
- **`PIPESTATUS[0]`** — Bash-specific. Must use `bash -c '...'` if the shell on deck is not bash.
- **Deck build is GCC-14** — Different from WSL GCC-13. Some warnings/errors may differ.
- **`git diff --check` after every build** — Catches whitespace errors that CI would flag.
- **Never push from deck** — The deck is a test target, not a source of truth.
- **Kill existing Movian before testing** — `pkill -f build.debug/movian` prevents port conflicts.
