# Lifecycle Inventory Audit

## Selected binary

The inventory is bound to one exact debug ELF:

- Path: `/home/uzver/lifecycle-build/build.debug/movian`
- SHA-256: `ed58a8175a0143a19304eda43af2fe7003e4d8534e0764d8b1b359f278660246`
- Build ID: `f1b8084e54961d0b24d594eb7e9aed4310c38317`
- Defined symbols scanned: 24,192
- Lifecycle entries: 78

`support/devtools/gdb/inventory.py scan` generated
`support/devtools/gdb/inventory.json`; the following check passed:

```text
python3 support/devtools/gdb/inventory.py check \
  --binary /home/uzver/lifecycle-build/build.debug/movian \
  --inventory support/devtools/gdb/inventory.json
status: OK, count: 78
```

The generator uses `nm --defined-only --format=posix` and records address,
type, and size evidence for every armed symbol. Regeneration is deterministic;
JSON serialization is canonical and contract order is explicit.

## Category coverage

| Category | Entries |
|---|---:|
| `backend` | 3 |
| `cache` | 8 |
| `callout` | 4 |
| `core-init` | 6 |
| `es-context` | 4 |
| `es-plugin` | 2 |
| `es-resource` | 4 |
| `glw` | 7 |
| `init-helper` | 22 |
| `init-system` | 3 |
| `navigator` | 3 |
| `plugin` | 3 |
| `prop-subscribe` | 2 |
| `service` | 3 |
| `shutdown-hook` | 2 |
| `thread-create` | 2 |

Every entry contains the symbol, category, lifecycle event, phase, contract
order, pair where applicable, confidence, and binary evidence. Duplicate IDs,
duplicate symbols, invalid event kinds, missing pair evidence, and order
collisions are rejected by the validator.

## Explicit omissions

Four source-confirmed candidates are absent from this binary and remain
explicit in `missingCandidates` rather than being silently dropped:

- `nav_reload_current`
- `plugin_load`
- `prop_courier_create`
- `prop_subscribe`

The collector does not arm an absent candidate. Missing observations therefore
remain visible to the analyzer as `UNKNOWN`/`NOT_INSTRUMENTED`; they cannot
produce a false success.

No inventory entry or candidate contains SMB or WSD symbols. The inventory has
no LSP, analyze-tooling, metadata, plugin-API, or unrelated feature paths.

## Pair and ordering audit

Create/destroy pairs are represented in both directions where the binary
contains both symbols. Init/fini and reload analysis use the recorded
`contractOrder`, not incidental lexical or breakpoint-install order. Reload
resource analysis additionally uses the M7 lifecycle pair model:

- plugin reload/unload
- ECMAScript plugin load/unload
- ES context create/end
- ES resource link/unlink
- service create/destroy

Pointer values are used for ES-resource duplicate suppression when available.
Missing pointer evidence is reported as `UNKNOWN`.

## Tests

`tests/tooling/gdb/test_inventory.py` covers duplicate IDs/symbols, missing
binary evidence, order collisions, forbidden symbols, deterministic output,
and exact-binary checks. The final targeted suite ran 46 tests successfully.
