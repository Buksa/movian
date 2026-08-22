# A contested argument records its candidates instead of becoming a union

Several natives read one argument index more than one way, and the obvious move
is to emit a union — `native/prop.getChild` reads slot 1 as a number or a
string. We do not, because whether such a union is *closed* is a control-flow
property the scanner cannot see, and three natives in this tree are
indistinguishable to it: `getChild` falls through to `duk_require_string`, so
anything else throws and `number | string` would be exact; `kvstore.set`'s
final `else` stores `KVSTORE_SET_VOID`, so `undefined` and `null` are accepted
on purpose; `htsmsg.get` falls through to `duk_safe_to_string`, which coerces
anything at all. Emitting the union would be right for the first and would
reject legal calls for the other two, so the candidates are recorded in the
artifact for a human and the emitted type stays `any`.

## Consequences

Seven parameters stay `any` that look like they could be narrower, and the
artifact carries a `candidates` field explaining each. A corpus test asserts
those three natives still look alike to the scanner, so the reasoning here
cannot rot into a stale comment while the code diverges.
