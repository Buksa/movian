# Issue #137 Spec resolution review — HEAD `18f7106ea`

## P0

None.

## P1

- **Inventory misreports the explicit deferment.** Requirement: “deferred-native count explicit.” `check_reference_dts.py:1185,1200` derives natives from the `kind == "commonjs"` set, although metadata has 18 `kind == "native"` entries; baseline prints `deferred-native 0`. Live added metadata module failed named `review/missing`; removed `url.d.ts` failed named `url`; phantom fixture failed named, so missing/phantom work. Impact: C2 scope is silently erased. Smallest fix: count `kind == "native"` from `all_modules` and report 18.

- **Both-direction member/kind guards are still partial.** Requirement: “Every included module has a both-direction export/member check.” All 14 specs pass empty prototype tuples (`check_reference_dts.py:144-226`). Removing real `Node.getElementByClassName`, adding phantom `Node.reviewPhantom`, and adding phantom `DB.reviewPhantom` each left the full checker exit 0. Constructor→callable and callable→value mutations only failed through positive TypeScript while “source shapes OK” remained. Impact: member/accessor/kind drift is accepted. Smallest fix: model `NodeProto`, `DB`, `VideoScrobbler`, and `w3cwebsocket` prototypes/instances/accessors and compare runtime kinds exactly.

- **Alias identity is unverified.** Requirement: “Repointing one alias to an existing but wrong target must fail.” `EXPORT_ALIAS_RE` only handles identifier RHS (`check_reference_dts.py:239-241,597`); changing `url.parse` from existing `string.parseURL` to existing `string.resolveURL` passed exit 0. Native aliases in popup/subtitles/querystring/url have no exact receiver/member check. Smallest fix: resolve `require()` bindings and compare each alias’s module and member target.

- **Source-derived declarations remain wrong.** Requirement: “source-symbol evidence for every nontrivial semantic type.” `movian-xmlrpc.d.ts:12` declares zero arguments, but `xmlrpc.js:3-6` consumes URL, method, and rest parameters. `movian-popup.d.ts:12` is unconstrained variadic although `es_misc.c:226-231,331` is `(text, delay, icon)`, native arity 3. `url.d.ts:34` says `port?: string`, while `es_string.c:318-320` returns a number and also returns undeclared `path` (`:330-331`). Smallest fix: correct these signatures/fields and targeted cases.

- **TypeScript coverage is incomplete.** Requirement: “cases cover each distinct signature family.” Top-level `http` is never imported—`http` at fixture line 3 is `movian/http`; negative cases only import, but do not use, popup/videoscrobbler/fs/websocket (`reference-negative.ts:8-20`). Add distinct aliases and positive/negative constructor, callback, member, options, variadic, and native-alias cases, including URL’s boolean.

## P2

- `url.d.ts:10` claims Node `URL.format` semantics not established by this limited implementation; remove the claim.

## Verification

Baseline source/tsc checker and `git diff --check` passed; absent-`tsc` path exited 0. Callback-type mutation failed the positive fixture. Expected and observed diagnostics matched exactly: `(22,2345),(23,2345),(25,2322),(30,2322),(36,2322),(41,2322),(46,2322),(51,2322),(54,2322),(55,2345),(56,2322),(58,2345),(67,2322),(77,2345),(85,2322),(91,2322),(92,2322),(95,2322),(97,2322),(99,2339),(100,2322),(103,2345),(106,2345),(112,2345),(116,2345),(120,2345),(123,2554),(126,2345),(129,2345),(132,2345),(133,2345),(134,2345)`. Per the read-only resolution spec, `gen.py --check`, viewdoc, debug corpus, metadata/LSP regressions, and smoke-pr are deferred to verifier.
