# LSP final audit

## Scope and result

`devtools-lsp` is a clean branch from `plugin-api`, not a Git child of
`devtools-analyze` or `devtools-mdev`. The LSP implementation is complete for
the requested GLW/JavaScript stdio workflow, with the analyzer consumed as a
sibling executable/build product.

Final implementation HEAD before this report: `a8b3ebf6b409dcf91b3f01af92c8cf93d464c4a0`.
M7 source authority: `/tmp/movian-movian6` at
`977a5c5c110f1d9928c8f85b79b1a58e7dcb2ce5`.

## M7 path comparison

| LSP-owned path/slice | Classification | Evidence/decision |
|---|---|---|
| `support/devtools/lsp/__init__.py` | `BYTE_IDENTICAL` | SHA-256 equal |
| `support/devtools/lsp/server.py` | `CLEAN_RECONSTRUCTION` | M7 server byte-equivalent before required sibling-path, timeout, and JS-completion additions |
| `support/devtools/movian-lsp` | `CLEAN_RECONSTRUCTION` | M7 launcher retained; explicit dependency options added |
| `.lsp.json` | `BYTE_IDENTICAL` | SHA-256 equal |
| `support/devtools/lsp/editors/*` | `BYTE_IDENTICAL` | language config, grammar, and `jsconfig.json` equal |
| GLW completion fixtures/golden | `BYTE_IDENTICAL` | all common fixtures equal |
| `lsp_client.py`, soak harness | `BYTE_IDENTICAL` | common protocol client/soak code equal |
| `run_smoke.py`, `run_review_regressions.py` | `CLEAN_RECONSTRUCTION` | fixture paths adapted to LSP-owned diagnostics |
| `run_diagnostics.py` | `CLEAN_RECONSTRUCTION` | LSP-owned fixtures plus disposable root-escape check |
| `run_corpus.py` | `CLEAN_RECONSTRUCTION` | removed stale analyzer-specific semantic expectation; analyzer output remains authoritative |
| `support/devtools/mdevlib/lspdoctor.py` | `CLEAN_RECONSTRUCTION` | external sibling executable discovery; mdev CLI not copied |
| protocol/JS completion tests and fixtures | `CLEAN_RECONSTRUCTION` | new requested coverage absent from M7 final paths |
| `support/devtools/mdevlib/cli.py` | `INTENTIONAL_OMISSION` | mdev CLI base is protected sibling `devtools-mdev`; only LSP doctor glue is owned here |
| `.claude/skills/movian-view-design/SKILL.md` from M7 integration commit | `INTENTIONAL_OMISSION` | unrelated view-design skill, not LSP-owned |
| generated metadata/DTS | `GENERATED_EQUIVALENT` | normalized DTS equal; metadata differs only in revision and known source-line relocation |

There are no `UNEXPLAINED` differences in LSP-owned paths.

## Boundary audit

```text
support/devtools/analyze/**       tracked files: 0
support/devtools/mdevlib/**       tracked files: lspdoctor.py only
```

The server delegates all syntax/token authority to `movian-analyze`; it does
not copy analyzer source or create another parser. The JavaScript completion
provider uses `generated/movian-metadata.json`; it does not duplicate API facts.
The TypeScript declarations remain inherited from `plugin-api`.

## Behavioral gates

Passed:

```text
python3 -m py_compile support/devtools/lsp/server.py \
  support/devtools/movian-lsp support/devtools/mdevlib/lspdoctor.py
python3 support/devtools/metadata/gen.py --check
python3 tests/tooling/lsp/run_protocol.py support/devtools/movian-lsp
python3 tests/tooling/lsp/run_smoke.py support/devtools/movian-lsp
python3 tests/tooling/lsp/run_review_regressions.py support/devtools/movian-lsp
python3 tests/tooling/lsp/run_diagnostics.py support/devtools/movian-lsp
python3 tests/tooling/lsp/run_javascript.py support/devtools/movian-lsp
python3 tests/tooling/lsp/run_corpus.py support/devtools/movian-lsp
python3 tests/tooling/lsp/run_soak.py support/devtools/movian-lsp --changes 25
python3 tests/tooling/lsp/run_soak.py support/devtools/movian-lsp --changes 1000
```

Observed statuses:

- `METADATA OK`, `DTS OK`, `DTS V1 OK`;
- `LSP PROTOCOL OK`;
- `LSP SMOKE OK`;
- `LSP REVIEW REGRESSIONS OK`;
- `LSP DIAGNOSTICS OK`;
- `LSP JAVASCRIPT COMPLETION OK`;
- `LSP CORPUS OK`;
- bounded soak p95 `162.922 ms`;
- full 1000-change soak p95 `160.632 ms`;
- direct LSP doctor: all checks `OK`, status `0`.

The disposable workspace stdio session verified valid open, invalid change,
diagnostic clear on close, shutdown `null`, exit `0`, and empty stderr with
`MOVIAN_ANALYZER`, `MOVIAN_METADATA`, and `MOVIAN_SKIN` pointing at the sibling
product/artifacts.

## Protected-ref check

At final audit time the protected refs still resolved to their required values:

```text
feature/smb          6b4b42b8fbeb901d26045ea714664fe064da3b9f
feature/smb-server   07db76485e8b1e0b7e4d2fd9d791556001f5b24b
plugin-runtime-api   6de27fb0affdc090a0719a1ef0cae9df5c98437b
plugin-api           734b18d4e1ad9f902dc37c211f438737695a8436
devtools-mdev        81a4ade8b2f9037d126685792521dfc792b464b0
devtools-analyze     57b540d8e4adfddb62ad31dbc8f0e69ca94314e2
```

No push was performed.
