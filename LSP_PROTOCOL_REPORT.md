# LSP protocol report

## Framing

`support/devtools/lsp/server.py` implements stdio JSON-RPC without third-party
packages:

- reads ASCII headers until the blank line;
- requires non-negative `Content-Length`;
- reads exactly the declared UTF-8 byte count;
- emits compact UTF-8 JSON with one `Content-Length` frame;
- serializes writes with an output lock;
- sends parse errors as `-32700`, invalid requests as `-32600`, unknown methods
  as `-32601`, invalid parameters as `-32602`, and caught handler failures as
  `-32603`.

The server never writes diagnostics, tracebacks, or informational text to
stdout. Unexpected handler errors go to stderr and receive a JSON-RPC internal
error response when the message is a request.

## Lifecycle contract

`initialize` selects the first valid `rootUri`, workspace-folder URI, or
`rootPath` that is a local file URI. The response advertises:

- full text open/change/save synchronization;
- document symbols, hover, definition, completion, signature help;
- workspace symbols;
- completion triggers `.`, `$`, `/`;
- signature triggers `(`, `,`;
- `serverInfo.name = movian-lsp`, version `0.1.0`.

`didOpen`, `didChange`, `didSave`, and `didClose` maintain normalized LF
snapshots keyed by URI. Changes are full-text changes. `didClose` clears all
owned diagnostics, including diagnostics whose URI refers to an included file.
`shutdown` cancels timers and returns `null`; `exit` terminates the read loop.
A normal shutdown/exit returns process status `0`.

## Position and URI behavior

- LSP columns are UTF-16 code units, including correct supplementary-plane
  character widths.
- Tabs remain one protocol unit.
- CRLF and CR input is normalized before analysis.
- Only `file:` URIs are translated to paths; non-file schemes are rejected.
- `localhost` file URI hosts are accepted; other hosts are not treated as local.
- Published ranges are full-line ranges using UTF-16 end columns.

## Diagnostics publication

Analysis is debounced, generation-checked, and serialized per document. A
stale analysis cannot publish after a change or close. Notifications include the
snapshot version for the document URI. Previous diagnostic URIs are cleared
when ownership changes.

## Evidence

`python3 tests/tooling/lsp/run_protocol.py support/devtools/movian-lsp`
passed with `LSP PROTOCOL OK` and seven parsed stdout frames. That test sends:

1. initialize/initialized;
2. an unknown request and checks `-32601`;
3. a raw malformed JSON frame and checks `-32700` with null id;
4. open/change/close and diagnostic publication/clearance;
5. shutdown/exit and process status/stderr.

`run_smoke.py` additionally passed the historical initialize/document-symbol/
hover/definition/workspace-symbol/fallback/UTF-16 session with
`LSP SMOKE OK`. The framed client parsed every stdout byte; no protocol safety
failure occurred.
