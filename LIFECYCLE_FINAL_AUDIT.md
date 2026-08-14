# Lifecycle Final Audit

## Branch and protected refs

- Branch: `devtools-lifecycle`
- HEAD: verified locally with `git rev-parse HEAD` at final audit; no push performed.
- Exact merge-base with `devtools-mdev`:
  `81a4ade8b2f9037d126685792521dfc792b464b0`
- No merge or rebase was performed.
- No push was performed.
- Protected refs remain at the objects listed in
  `LIFECYCLE_RECONSTRUCTION_REPORT.md`.

The tracked worktree is clean after the audit commit. Pre-existing
untracked reports, build output, `.codegraph`, and other user files were not
deleted or rewritten; they are outside this lifecycle cutover.

## Delivered paths

- `support/devtools/gdb/inventory.py`
- `support/devtools/gdb/inventory.json`
- `support/devtools/gdb/movian_lifecycle.py`
- `support/devtools/gdb/lifecycle_analyze.py`
- `support/devtools/gdb/run_lifecycle_scenarios.py`
- `support/devtools/gdb/run_lifecycle_s34.py`
- `support/devtools/gdb/run_lifecycle_extended.py`
- `support/devtools/gdb/lifecycle_test_plugin/`
- `src/arch/linux/linux_misc.c` opt-in GDB attach hook
- lifecycle tooling tests, including 46 final targeted unit tests
- the six required lifecycle reports

SMB/WSD, LSP, analyze-tooling, metadata, plugin-runtime/API, and unrelated
feature paths are absent from the lifecycle inventory and collector scope.

## Verification evidence

### Static and unit checks

```text
python3 -m unittest \
  tests.tooling.gdb.test_inventory \
  tests.tooling.gdb.test_lifecycle_collector \
  tests.tooling.gdb.test_lifecycle_analyze \
  tests.tooling.gdb.test_lifecycle_extended \
  tests.tooling.gdb.test_movian_lifecycle \
  tests.tooling.gdb.test_wedge_lifecycle
46 tests: OK

inventory.py check: status OK, count 78
py_compile: OK
```

### Real binary and GDB

The selected debug ELF is `/home/uzver/lifecycle-build/build.debug/movian`
with SHA-256
`ed58a8175a0143a19304eda43af2fe7003e4d8534e0764d8b1b359f278660246` and
build ID `f1b8084e54961d0b24d594eb7e9aed4310c38317`.

Real launch-attached GDB runs reached HTTP-ready Movian, loaded the exact
inventory, and emitted validated JSONL. Durable artifacts are in
`/home/uzver/lifecycle-artifacts`:

- `startup/` — 248-line startup collection and analysis;
- `scenarios/s3/` and `scenarios/s4/` — M7 reload/shutdown runs;
- `scenarios/extended/` — UI, plugin, repeated, and safe-error runs;
- `wedge/` — controlled launch-attached wedge proof;
- `no-optin/` — ordinary environment-unset startup check;
- `baseline-no-optin-quit-display/` — direct no-GDB shutdown baseline.

The wedge proof verified exact inferior ownership and `TracerPid`, captured
146 frames across 16 threads with a Movian frame, and left no matching Movian
or GDB process. Scenario cleanup checks likewise found no owned inferior.

## Known failures and disposition

1. **GLW shutdown SIGSEGV** — Direct no-opt-in Quit on the selected binary
   returns 8 and logs `Signal: 11` in `glw`. GDB S3/S4 and extended runs show
   the same result. This is recorded as `FAIL`, not hidden by collector cleanup.
2. **Partial streams** — Duration startup and non-reload scenarios cannot prove
   fini/resource balance; analyzer status is `UNKNOWN`.
3. **Thread identity** — create events identify the creator thread, not the
   spawned thread; count mismatches are `UNKNOWN`.
4. **Third reload resource evidence** — missing pointer observations keep the
   third window `UNKNOWN`.
5. **Older mdev CLI** — this `devtools-mdev` branch has no `doctor` command;
   verification used its actual `run`, collector, HTTP, log, and ownership
   interfaces rather than claiming an unavailable command.

These are explicit evidence classifications, not unresolved silent behavior.
The only core-source change is the opt-in ptrace hook, and ordinary
`MOVIAN_MDEV_ALLOW_GDB`-unset behavior was verified separately.

## Final disposition

The branch is a clean, binary-bound lifecycle reconstruction with real GDB,
JSONL, scenario, resource, and wedge evidence. It is not a claim that the
M7-derived Movian binary's GLW shutdown is healthy. No protected ref was
modified, no forbidden feature leaked into the inventory, and no false PASS
was recorded for missing or failed lifecycle evidence.
