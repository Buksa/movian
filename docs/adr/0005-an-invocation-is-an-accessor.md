# An invocation in a core module is an accessor

The narrowing rule asked for "the exact accessor" in the C, and the five
`movian/settings` callbacks (`createBool`, `createString`, `createInt`,
`createAction`, `createMultiOpt`) have none: they never reach C. Every use of
each is an invocation -- `callback(initial)`, `callback(newval)`,
`callback()`, `callback(selected)` -- so we admit that as the core-module form
of an **accessor**: a parameter whose every use in the function is an
invocation of the value as passed (`p(...)`, `p.apply(...)`, `p.call(...)`) is
typed as callable. It is the standard the C side already has. `duk_to_string`
rejects nothing and still makes a slot `string` (**Coercion is not contract**;
#232 counted 86 of 131 native slots read that way). A non-callable in such a
slot never yields a working feature, only one that breaks when it is used,
which is less than a coercion gives.

## Considered: rejection as the standard

We first required a **rejection**: a synchronous throw reaching the caller on
every path that returns normally. Measured with a dev plugin passing `42`:
`createBool`, `createString` and `createInt` throw `TypeError: 42 not
callable`, after the setting is already in the tree; `createMultiOpt` returns
normally when the first option's id is `''`; `createAction` returns normally,
and the throw on `Activate` is caught by `duk_pcall` in `es_sub_cb`
(`es_prop.c:706`) and only logged. Rejection would type three of the five,
would leave `page.Route` and `page.Searcher` -- already narrowed on the same
evidence -- without grounds, and is stricter than the C side for no reason the
rule states. The term stays: ADR-0003 closes a union only on rejection.

## What is emitted

`Function | ((...args: any[]) => void)`. Measured with the pinned
`tsc 5.7.3 --strict`:

| argument | `Function` | `(...args: any[]) => void` | the union |
| --- | --- | --- | --- |
| unannotated `function (v) {}` | TS7006 | ok | ok |
| a value typed `Function`, a class | ok | rejected | ok |
| `42`, `null` | rejected | rejected | rejected |

The invocation proves "callable" and nothing about arguments or return, so the
type must accept whatever `Function` accepts. The union does -- anything with
a call signature is assignable to `Function` -- and its arrow member gives an
unannotated callback a contextual signature, which `Function` does not. Where
the scan also knows what the callback receives (`new Page(...)` at the call
site), that shape takes the arrow's place:
`Function | ((value: Page, ...args: any[]) => any)`.

## How it is recorded

Derived, not curated. `gen.py` enumerates every occurrence of the parameter in
the function. All invocations: the union, with each invocation's anchor in the
artifact. Any other use -- a truthiness test, `typeof`, forwarding,
reassignment, a shadowing inner parameter, `arguments` -- keeps `any` and
records the use that blocked it, as ADR-0003 records candidates. This does not
contradict ADR-0003's "a control-flow property the scan cannot see": the
condition is which uses exist, not which paths reach them. A corpus test pins
one accepted and one refused slot.

## Consequences

- All five settings callbacks become the union, `createAction` and
  `createMultiOpt` included: every use is an invocation, however conditional
  or late.
- `page.Route` and `page.Searcher` widen from
  `(value: Page, ...args: any[]) => any` to the shaped union. Free for
  callers: the shape still types an unannotated callback, and a value typed
  `Function`, a class or a differently annotated function is now accepted.
- **Widening a callback slot to `any` is not free**, whatever `AGENTS.md` said
  before: an unannotated callback loses its contextual signature and fails
  `--strict` with TS7006, and so does a method of an options object.
  `AGENTS.md` now says to keep a signature when widening such a slot.
- `http.request`'s callback is read by `if(callback)` as well as invoked, so
  under this rule it is contested and should be `any` -- and `any` would break
  exactly the callers the previous point describes. It is left as it is, and
  so are the 72 `@param` annotations #232 admits with no native ceiling (11 of
  them callbacks), which rest on the author's word rather than on an accessor.
  Neither is settled here.
- Only invocation is admitted. Other reads in a core module (`x + ''`, a
  property read) are not accessors under this decision.
