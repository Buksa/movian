# DuktapeBuffer is a branded type, not a structural one

The generated declarations describe a Duktape buffer with `length`, indexed
bytes, `toString()` and `valueOf()`. Structurally, an ordinary `number[]`
satisfies all of that — and would then type-check straight into
`native/websocket.clientSend`'s binary branch, where `duk_get_buffer_data`
does not recognise an array, so the value silently goes out as a stringified
text frame instead of a binary one. We therefore carry an uninhabitable brand,
`readonly __duktapeBuffer__: never`, so only values that really came from a
buffer-returning native satisfy the type.

## Consequences

No plugin can construct a `DuktapeBuffer` from a literal, which is correct:
they only ever come from natives. The cost is that the type is unusable as a
structural description of "something buffer-shaped", which is deliberate.

The decision was inherited rather than invented — the accepted corpus settled
it first, at `support/devtools/metadata/tests/reference/movian-http.d.ts`, and
the generator adopted it verbatim so the two cannot diverge.
