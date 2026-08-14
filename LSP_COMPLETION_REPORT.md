# LSP completion report

## GLW

GLW completion remains metadata-driven from `generated/movian-metadata.json`:

- registered widgets and aliases after `widget(`;
- attributes inside blocks, including inline and same-line contexts;
- GLW functions after expression separators;
- enum values from attribute `enumValues`;
- local `#define` macros visible only before the cursor;
- `$scope` roots from metadata;
- relative, `skin://`, `dataroot://`, and `file://` include/import paths;
- path completion text edits with safe root confinement;
- signatures from fixed/variadic function arity.

The completion implementation preserves the M7 context hardening and does not
invent child schemas for GLW scope roots. Prefix filtering and cursor columns
are deterministic.

## JavaScript

The clean-branch completion layer indexes only the committed `js` metadata
artifact. It covers:

- global functions and objects (`Plugin`, `Core`, `console`);
- legacy `showtime` and `plugin` globals;
- CommonJS module names and exports;
- native module names and explicit native function tables;
- settings receiver members (`globalSettings`, `kvstoreSettings`);
- declared CommonJS prototype/shared shapes and return-type shapes;
- direct `require('module').member` contexts;
- simple `var`/`let`/`const` require and export assignments;
- prefix-filtered module path completion inside `require('...` strings.

No parser or duplicate API database was added. Completion records are built
from the same generated artifact used by `movian-api.d.ts` generation. Unknown,
private, dynamic, or synthesized runtime facts are not emitted.

`generated/movian-api.d.ts` remains the declaration-file authority for editors
that perform TypeScript analysis; the LSP completion provider only offers
metadata-backed items and does not claim to be a JavaScript type checker.

## Verification

Historical GLW completion fixtures and regressions passed:

```text
{"checks": 10, "status": "LSP REVIEW REGRESSIONS OK"}
```

The dedicated JavaScript fixture/test passed:

```text
{"contexts": 11, "status": "LSP JAVASCRIPT COMPLETION OK"}
```

The test derives expected labels from `generated/movian-metadata.json`, covering
`Plugin`, `Core`, `showtime`, `plugin`, `fs`, `native/fs`, settings receivers,
Route/Request prototypes, module-path completion, and private/dynamic leakage
checks. Metadata generation and both DTS checks also passed.
