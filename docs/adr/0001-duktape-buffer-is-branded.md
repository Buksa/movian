# DuktapeBuffer is a branded type, not a structural one

The generated declarations describe a Duktape buffer with `length`, indexed
bytes, `toString()` and `valueOf()`. Structurally that is satisfiable by an
ordinary object literal, which then type-checks straight into
`native/websocket.clientSend`'s binary branch, where `duk_get_buffer_data`
does not recognise it and the value silently goes out as a stringified text
frame instead of a binary one. We therefore carry an uninhabitable brand,
`readonly __duktapeBuffer__: never`, so only values that really came from a
buffer-returning native satisfy the type.

## The impostor, exactly

Measured against the unbranded interface, not assumed:

```ts
const impostor: Unbranded = {
  length: 3, 0: 65, 1: 66, 2: 67,
  toString: () => "ABC",
  valueOf: function () { return this as Unbranded; },
};
send(impostor);   // accepted
```

A plain `number[]` is **not** the impostor and never was: `Array.valueOf()`
returns `Object`, which is incompatible with the declared
`valueOf(): DuktapeBuffer`, so an array is rejected with or without the brand.
The first version of this ADR said otherwise and was wrong; the decision
stands, its stated reason did not.

## Consequences

No plugin can construct a `DuktapeBuffer` from a literal, which is correct:
they only ever come from natives. The cost is that the type is unusable as a
structural description of "something buffer-shaped", which is deliberate.

The decision was inherited rather than invented -- the accepted corpus settled
it first, at `support/devtools/metadata/tests/reference/movian-http.d.ts`, and
the generator adopted it verbatim so the two cannot diverge.
