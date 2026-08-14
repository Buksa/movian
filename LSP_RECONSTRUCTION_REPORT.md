# LSP reconstruction report

## Result

`devtools-lsp` was reconstructed on a clean branch from `plugin-api`:

- base: `plugin-api` at `734b18d4e1ad9f902dc37c211f438737695a8436`;
- branch: `devtools-lsp`;
- final implementation commit at report time: `38f14f0a59e59e32cdfc9129f41bbfd130ed8acd`;
- M7 authority: `/tmp/movian-movian6`, commit `977a5c5c110f1d9928c8f85b79b1a58e7dcb2ce5`.

The branch contains the LSP implementation, editor assets, tests, fixtures, and
LSP-only doctor code. It does not contain analyzer source or the mdev base.
`movian-analyze` remains a sibling executable dependency.

## Logical reconstruction slices

1. **Protocol MVP** — `f81dd03d6` and `cbd7ab83a`
   - stdio `Content-Length` JSON-RPC framing;
   - initialize/shutdown/exit;
   - full-text document open/change/save/close state;
   - URI and workspace-root handling;
   - diagnostics publication and deterministic stderr-only failures;
   - GLW token/document-symbol/hover/definition plumbing;
   - race, UTF-16, fallback, and stdout framing regressions.
2. **Editor integration** — `ab9528135` and `566907779`
   - project `.lsp.json`;
   - GLW language configuration and TextMate grammar;
   - JavaScript `jsconfig.json` was restored byte-identically from M7 in
     `38f14f0a5`;
   - LSP doctor checks, without importing mdev implementation.
3. **Duktape JavaScript diagnostics** — `0b49d0ba5` and `d0b49148e`
   - `movian-analyze --js` delegation;
   - Duktape diagnostic source and save lifecycle;
   - metadata-backed module definition resolution;
   - arrow-syntax regression coverage.
4. **GLW completion/signature history** — `be7a264e0` through `124343832`
   - attributes, functions, widgets, scopes, enum values, local macros;
   - cursor filtering, include-directory completion, text edits;
   - signature help and completion hardening.
5. **Clean-branch GLW diagnostics** — `d07775f8e`
   - LSP-owned diagnostic fixtures instead of analyzer-owned fixture paths;
   - syntax, EOF, invalid attribute/expression, missing include, and root escape
     coverage.
6. **Protocol and JavaScript completion additions** — `9252b0da7` and
   `59a3c519c`
   - raw malformed JSON-RPC and unknown-method tests;
   - metadata-driven JavaScript completion for globals, legacy APIs, modules,
     settings receivers, and prototype shapes;
   - explicit analyzer/metadata/skin/repository-root overrides;
   - analyzer process-group cleanup on timeout.
7. **Sibling dependency integration** — `3817fe32d`
   - doctor discovery through `MOVIAN_ANALYZER`;
   - documentation of sibling analyzer operation and metadata authority;
   - no mdev CLI/base files copied into this branch.

## Ownership boundary

The server owns editor state and protocol translation. It does not reimplement
GLW parsing, preprocessing, JavaScript parsing, or plugin API facts:

```text
editor buffer
  -> movian-lsp JSON-RPC/state/UTF-16 layer
  -> sibling movian-analyze (--check, --tokens, --js)
  -> committed generated/movian-metadata.json for completion/hover
  -> LSP diagnostics/completion/definition responses
```

`generated/movian-api.d.ts` remains the TypeScript declaration authority. The
LSP reads the same generated metadata artifact; it does not maintain a second
API database.

## Verification

The following gates passed with the sibling `build.debug/movian-analyze` from
ref `devtools-analyze` (`57b540d8e4adfddb62ad31dbc8f0e69ca94314e2`):

```text
python3 support/devtools/metadata/gen.py --check       METADATA OK / DTS OK / DTS V1 OK
python3 tests/tooling/lsp/run_protocol.py              LSP PROTOCOL OK
python3 tests/tooling/lsp/run_smoke.py                 LSP SMOKE OK
python3 tests/tooling/lsp/run_review_regressions.py    LSP REVIEW REGRESSIONS OK
python3 tests/tooling/lsp/run_diagnostics.py           LSP DIAGNOSTICS OK
python3 tests/tooling/lsp/run_javascript.py            LSP JAVASCRIPT COMPLETION OK
python3 tests/tooling/lsp/run_corpus.py                LSP CORPUS OK
python3 tests/tooling/lsp/run_soak.py --changes 25     p95 162.922 ms
```

A disposable workspace stdio session also passed: valid open published zero
diagnostics, a changed invalid GLW buffer published `movian-glw` / `Invalid char
'`'`, close cleared diagnostics, shutdown returned `null`, exit status was `0`,
and stderr was empty.
