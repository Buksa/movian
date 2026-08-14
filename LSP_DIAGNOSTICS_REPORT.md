# LSP diagnostics report

## Analyzer bridge

For each live GLW snapshot the server:

1. writes a temporary UTF-8 file beside the on-disk document when possible;
2. invokes the sibling executable with:
   ```text
   movian-analyze --check --root <workspace-root> --skin <skin-root> <snapshot>
   movian-analyze --tokens --root <workspace-root> --skin <skin-root> <snapshot>
   ```
3. parses stdout as one JSON object;
4. maps the temporary file back to the document path/URI;
5. publishes the analyzer error at its reported one-based line as a full-line
   UTF-16 LSP range;
6. removes the snapshot in a `finally` block.

JavaScript snapshots use `movian-analyze --js` on open/save and intentionally do
not analyze every unsaved change. The diagnostic source is `duktape`; GLW
source is `movian-glw`.

The analyzer's JSON failure is not hidden. Missing binaries, timeout, invalid
JSON, non-object output, and malformed diagnostic objects become deterministic
severity-1 diagnostics rather than silent empty results. Analyzer stderr is
captured and does not corrupt the protocol stream.

## Confinement and context

The workspace root comes from the LSP initialize request. Skin resolution uses
the configured flat-skin root. Include/import resolution therefore preserves
the analyzer's workspace/skin confinement instead of resolving from the LSP
checkout accidentally. Definition and completion path helpers apply the same
root/skin confinement.

The subprocess now starts a new session. On timeout, the server kills the
process group, reaps the process, and reports the timeout. A direct fake
analyzer test spawned a sleeping child; after timeout the child process was
absent. No analyzer child was left running.

## Fixtures

`tests/tooling/lsp/fixtures/diagnostics/` contains:

| Fixture | Contract |
|---|---|
| `valid.view` | clean GLW snapshot |
| `invalid-syntax.view` | lexer invalid-character diagnostic |
| `eof.view` | parser unexpected EOF |
| `invalid-attribute.view` | malformed attribute target |
| `invalid-expression.view` | invalid object dereference expression |
| `missing-include.view` | missing relative include |
| `escape-root.view` | include that escapes a disposable workspace root |

`run_diagnostics.py` compares the first six fixture results with direct
`movian-analyze --check` JSON and runs the root-escape fixture in a temporary
workspace with an outside target. It passed with:

```text
{"fixtures": 7, "status": "LSP DIAGNOSTICS OK"}
```

The flat-skin corpus also passed:

```text
{"clean": 97, "files": 98, "include_fragments": 1,
 "status": "LSP CORPUS OK"}
```

The M7 corpus's historical `Unknown function: ListItemBevel` assertion was
removed because the sibling analyzer product reports that fragment clean in
this clean branch. The adapted test still rejects any unexpected LSP
mismatch; it does not synthesize or suppress analyzer diagnostics.

## Verification commands

```text
python3 tests/tooling/lsp/run_diagnostics.py support/devtools/movian-lsp
python3 tests/tooling/lsp/run_corpus.py support/devtools/movian-lsp
python3 tests/tooling/lsp/run_soak.py support/devtools/movian-lsp --changes 25
python3 tests/tooling/lsp/run_soak.py support/devtools/movian-lsp --changes 1000
```

All passed. The bounded soak reported p95 latency `162.922 ms`; the full
1000-change soak reported p95 latency `160.632 ms`, both below the `300 ms`
gate.
