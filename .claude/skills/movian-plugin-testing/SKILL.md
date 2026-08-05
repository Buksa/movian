---
name: movian-plugin-testing
description: Verification judgment for changes to the Movian core — the core/launcher change class, SMB2 subsystem testing, Steam Deck/Flatpak validation, and core-side debugging anchors. Use when judging whether a core change is proven, testing SMB/SMB2, validating on real Deck hardware, or tracing a prop through the C sources. General plugin-testing judgment lives in the `movian:verify` skill.
---

# Movian core testing

**Most of this skill moved.** The general verification judgment — falsifiable
criteria, transport-success-is-not-proof, the page-ready trap, prop-reading
rules, anti-flake timing, error triage, hash-before-vision, the 120-second
handoff rule — now lives in the **`movian:verify`** skill, delivered by the
[movian-plugin-sdk](https://github.com/Buksa/movian-plugin-sdk) plugin. It
applies to core work identically; install the plugin rather than keeping a
second copy here.

```
claude plugin marketplace add Buksa/movian-plugin-sdk
claude plugin install movian@movian-plugin-sdk
```

What remains here needs a core checkout and a core developer.

## Verification minimum: core/launcher change

Build, then `./build.debug/movian --help`, then a short flag smoke proving `-d`,
`-s`/`--persistent`, `--cache`, `-p` and `-j` are consumed as options — the logs
must not show `navigator |N| Opening -<flag>`, which means a flag leaked through
and was opened as a URL.

## Verification minimum: native crash or hang

Preserve artifacts — command, log tail, screenshot, exit status — and move to a
separate debug/GDB build rather than repeating the same failing smoke. Reach for
ASan only when the evidence points at memory corruption. Build recipes are in
`references/debug-flags.md`.

## Housekeeping gotchas

- `.gitignore` carries a bare `core*` pattern, which silently untracks **any**
  file named `core.py`, `core.sh` and so on, anywhere in the tree — not just
  build-time core dumps. A helper script named with a `core` prefix will vanish
  from `git status` without a word.
- Use the basename-anchored process guard, never a bare substring match, before
  concluding that no Movian is running.

## Source navigation

`.codegraph/` is indexed in this tree — use `codegraph_explore` before grep or
Read for any claim about core code, and re-check line numbers in the current
checkout before citing them. The expected reporting shape is a chain:
`observation -> handler file:line -> downstream file:line -> effect`. Fall back
to scoped `rg -n` for `.view` files, preprocessor macros and generated
registrations, which CodeGraph does not resolve as precisely.

## References

- `references/smb2-smoke.md` — SMB/SMB2 client, server, browser; embedded-server
  keepalive and pool assertions
- `references/steamdeck-flatpak.md` — Steam Deck and Flatpak remote validation
- `references/debug-flags.md` — GDB/crash builds, post-core-change CLI sanity
- `references/prop-debugging.md` — subscriber source anchors into the C sources
