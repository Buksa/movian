# Lifecycle Reconstruction Report

## Scope

This branch reconstructs the M7 lifecycle/debug tooling on top of the exact
`devtools-mdev` tip. Scope is limited to lifecycle inventory, GDB collection,
ordering/resource analysis, controlled scenarios, and wedge recovery.

Protected refs were not changed:

| Ref | Object |
|---|---|
| `devtools-mdev` | `81a4ade8b2f9037d126685792521dfc792b464b0` |
| `feature/smb` | `6b4b42b8fbeb901d26045ea714664fe064da3b9f` |
| `feature/smb-server` | `07db76485e8b1e0b7e4d2fd9d791556001f5b24b` |
| `plugin-runtime-api` | `6de27fb0affdc090a0719a1ef0cae9df5c98437b` |
| `plugin-api` | `734b18d4e1ad9f902dc37c211f438737695a8436` |
| `devtools-analyze` | `57b540d8e4adfddb62ad31dbc8f0e69ca94314e2` |
| `devtools-lsp` | `06fab1728f1b52851169252627a3a2bfffc60b71` |

M7 authority was commit
`977a5c5c110f1d9928c8f85b79b1a58e7dcb2ce5`.

## Reconstruction

The implementation is split into explicit layers:

1. **Binary inventory** — `support/devtools/gdb/inventory.py` defines the
   source-confirmed lifecycle candidate contracts, scans the selected ELF with
   `nm`, records exact symbol evidence, and preserves absent candidates in
   `missingCandidates`.
2. **GDB collector** — `support/devtools/gdb/movian_lifecycle.py` loads only
   inventory entries present in the selected binary, installs launch-attached
   breakpoints, writes ordered JSONL events, captures thread/inferior context,
   and separates event output from GDB console output.
3. **Yama opt-in** — `src/arch/linux/linux_misc.c` enables
   `PR_SET_PTRACER_ANY` only when `MOVIAN_MDEV_ALLOW_GDB=1`. Ordinary launches
   do not execute this path.
4. **Analyzer** — `support/devtools/gdb/lifecycle_analyze.py` validates JSONL,
   derives startup/finalizer order, reports conservative thread balance, and
   analyzes plugin reload resource windows. Missing or insufficient evidence is
   `UNKNOWN`, never an inferred `PASS`.
5. **Scenarios** — the existing S3/S4 runner was restored and extended with
   UI reload, plugin reload/unload, three-cycle reload, and a safe invalid-HTTP
   endpoint scenario. `run_lifecycle_extended.py` reports action success
   independently from final inferior success.
6. **Wedge recovery** — the launch-attached control protocol records a
   classified stop, captures stack/state through GDB, supports emergency-eject
   state, and preserves cleanup/ownership evidence.

## M7 provenance and cutover

| Path | Classification | Evidence/decision |
|---|---|---|
| `support/devtools/gdb/lifecycle_test_plugin/plugin.json` | `BYTE_IDENTICAL` | SHA-256 matches M7 authority. |
| `support/devtools/gdb/lifecycle_test_plugin/lifecycle_test.js` | `BYTE_IDENTICAL` | SHA-256 matches M7 authority. |
| `tests/tooling/gdb/test_movian_lifecycle.py` | `BYTE_IDENTICAL` | SHA-256 matches M7 authority. |
| `support/devtools/gdb/movian_lifecycle.py` | `CLEAN_RECONSTRUCTION` | M7 collector behavior retained; inventory loading, exact-binary selection, context fields, normal-exit cleanup, and current branch paths adapted. |
| `support/devtools/gdb/run_lifecycle_scenarios.py` | `CLEAN_RECONSTRUCTION` | M7 S3/S4 behavior retained; environment overrides, all-symbol evidence, and current binary/inventory plumbing added. |
| `support/devtools/gdb/run_lifecycle_s34.py` | `CLEAN_RECONSTRUCTION` | M7 extended runner retained; event evidence now includes create/destroy symbols. |
| `support/devtools/gdb/lifecycle_analyze.py` | `CLEAN_RECONSTRUCTION` | Ordering/UNKNOWN semantics rebuilt; reload resource windows use M7 lifecycle pairs and pointer evidence. |
| `support/devtools/gdb/inventory.json` | `REGENERATED_FOR_CLEAN_BINARY` | Generated from the durable debug ELF, not copied from M7. |
| `src/arch/linux/linux_misc.c` | `INTENTIONAL_MINIMAL_CHANGE` | Opt-in debugger attach is required under the host Yama policy; no default behavior change. |
| `support/devtools/gdb/run_lifecycle_extended.py` | `NEW_LIFECYCLE_SCENARIO` | Adds requested UI/reload/repeated/error coverage without changing core feature paths. |

No aliases or compatibility copies were retained. The inventory deliberately
omits SMB/WSD and unrelated LSP/analyze/metadata/API paths.

## Verification result

The durable selected binary was built at
`/home/uzver/lifecycle-build/build.debug/movian` from the lifecycle branch's
M7-derived core. It is a debug ELF with build ID
`f1b8084e54961d0b24d594eb7e9aed4310c38317` and SHA-256
`ed58a8175a0143a19304eda43af2fe7003e4d8534e0764d8b1b359f278660246`.

The inventory check passed with 78 entries. Real launch-attached GDB runs
reached HTTP-ready Movian and produced valid JSONL. The durable startup stream
contains 248 validated lines; S3 contains 298; S4 contains 211; the extended
scenarios contain 209–371 lines. A real wedge capture produced 122 validated
lines and a 146-frame/16-thread response with a Movian frame present.

Action-level scenarios passed their explicit action contracts. The selected
M7-derived binary's ordinary GLW shutdown path fails independently of GDB:
a direct no-opt-in launch with `DISPLAY=:0` received HTTP Quit, returned code 8,
and logged `Signal: 11` in the `glw` thread. GDB S3/S4 and extended runs show
the same shutdown failure. These outcomes remain `FAIL` or `UNKNOWN`; the
runner does not promote them to `PASS`.

## Open evidence boundaries

- Duration-only startup collection does not reach finalizer evidence.
- Thread-create breakpoints execute in the creator thread; creator TIDs cannot
  be treated as new-thread identities, so mismatched counts remain `UNKNOWN`.
- The third repeated reload window lacks complete resource-pointer evidence and
  remains `UNKNOWN`.
- The baseline GLW shutdown crash is outside this lifecycle tooling cutover and
  is recorded rather than suppressed.
