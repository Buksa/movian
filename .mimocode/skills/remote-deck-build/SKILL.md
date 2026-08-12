---
name: remote-deck-build
description: Use when building and testing Movian on a remote Steam Deck. Covers SSH setup, file sync, remote build, and verification. Trigger on "build on deck", "test on stand", "remote build", "SSH to deck", or when cross-validating changes on the ARM target.
---

# Remote Deck Build

Standardized workflow for building and testing Movian on a remote Steam Deck.

## Connection details are yours, not this file's

`AGENTS.md` forbids committing machine-specific paths, so the host, user, key and
remote repo path live in **your environment**, not here. Put them in a shell
profile or a local untracked file:

```sh
export DECK_HOST="deck.lan"          # or an IP on your own network
export DECK_USER="user"
export DECK_KEY="$HOME/.ssh/movian_deck"
export DECK_REPO="~/GitHub/movian"   # path on the deck, expanded remotely
```

An `~/.ssh/config` entry is the tidier form and lets you drop `DECK_KEY`:

```
Host movian-deck
  HostName deck.lan
  User user
  IdentityFile ~/.ssh/movian_deck
```

## Setup Variables

Always define these at the top of your script:

```sh
: "${DECK_HOST:?set DECK_HOST}" "${DECK_USER:?set DECK_USER}" "${DECK_REPO:?set DECK_REPO}"
SSH="ssh -i ${DECK_KEY:-$HOME/.ssh/movian_deck} -o BatchMode=yes -o ConnectTimeout=8 $DECK_USER@$DECK_HOST"
SCP="scp -i ${DECK_KEY:-$HOME/.ssh/movian_deck} -o BatchMode=yes"
```

The `:?` guards fail loudly on an unset variable instead of silently building an
`ssh @` command that hangs.

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
$SCP src/sd/wsd.c "$DECK_USER@$DECK_HOST":"$DECK_REPO"/src/sd/wsd.c
```

For multiple files:
```sh
for f in src/sd/wsd.c src/fileaccess/smb2/fa_libsmb2.c; do
  $SCP "$f" "$DECK_USER@$DECK_HOST":"$DECK_REPO"/"$f"
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
$SCP src/sd/wsd.c "$DECK_USER@$DECK_HOST":"$DECK_REPO"/src/sd/wsd.c
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
$SCP /tmp/instrument.py "$DECK_USER@$DECK_HOST":/tmp/instrument.py
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
