# Standards Resolution Review — `18f7106ea`

## P0

None.

## P1

1. `support/devtools/metadata/check_reference_dts.py:1200` derives deferred natives from the already CommonJS-only set created at `:1185`; live metadata instead has 18 records with `kind == "native"`, so `--commonjs` falsely reports `deferred-native 0`. Coverage also walks declaration files at `:1207`/`:1218` but never compares the derived set with `MODULES`: removing `movian/sqlite` from the in-memory registry still returned no errors. Impact: a fixture can be unverified while coverage remains green. Smallest fix: partition CommonJS/native records from `all_modules`, report the native partition, and compare the post-six CommonJS set directly with both fixture and registered-spec names.

2. `support/devtools/metadata/check_reference_dts.py:143` begins 14 new `ModuleSpec`s whose prototype/member/native-shape fields are all empty; `:897` therefore never checks their constructors, methods, aliases, or accessors. Empirical in-memory mutations changing `DB` from class to value and renaming public `lastRowId` each produced `total-errors=0`. Impact: the checker certifies declarations after public runtime shape drift. Smallest fix: register every source shape (including exported constructors, prototype `defineProperties`, and exact aliases) and compare export kind; retain private-member exclusions explicitly.

3. Source-derived contracts remain wrong: `support/devtools/metadata/tests/reference/movian-xmlrpc.d.ts:12` declares zero arguments although `res/ecmascript/modules/movian/xmlrpc.js:3` consumes `arguments[0..]`; `movian-popup.d.ts:12` is unconstrained variadic although `src/ecmascript/es_misc.c:331` registers exact arity 3; and `https.d.ts:12` duplicates inline HTTP shapes despite the two-argument wrappers at `res/ecmascript/modules/https.js:3`. Impact: valid calls are rejected while invalid ones pass, and duplicated types can drift. Smallest fix: declare xmlrpc’s two required plus rest arguments, popup’s exact native tuple, and reuse HTTP request/options types in the HTTPS wrapper signatures.

## P2

1. `support/devtools/metadata/tests/reference/fs.d.ts:9` and `:18` claim native `close` calls, but `res/ecmascript/modules/fs.js:10`/`:22` call `Core.resourceDestroy`. Correct the comments.

`git diff --check f4806d493...HEAD` is clean; the duplicate checker is deleted and no unrelated scope was found.
