# Options objects carry an index signature

Several natives read named properties off an argument rather than reading the
argument itself — `es_http_req` reads thirteen keys off `httpReq`'s second
argument. Those keys are derivable and are emitted as optional members. But
nothing in the C *rejects* a key it does not read: the object is handed over
untouched and unknown keys are ignored. Declaring only the known keys would
hand TypeScript's excess-property check a rule the runtime does not have, and
`httpReq(url, {debug: true, somethingNewer: 'ok'})` would stop compiling for
no reason anyone could point at in the source. So every options interface also
carries `[key: string]: any`.

## Consequences

A typo in a key name is not caught. That is the price of not inventing a
restriction, and it is the right way round: a false error costs a plugin author
a debugging session against a compiler that is wrong, while a missed typo costs
them a key that does nothing — which the C would have ignored anyway.
