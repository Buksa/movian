# An invocation in a core module is an accessor

The narrowing rule asked for "the exact accessor" in the C, and the five
`movian/settings` callbacks (`createBool`, `createString`, `createInt`,
`createAction`, `createMultiOpt`) have none: they never reach C. Every use of
each is an invocation -- `callback(initial)`, `callback(newval)`,
`callback()`, `callback(selected)` at `settings.js:83,89`, `:110,117`,
`:147,154`, `:197` and `:242,251` -- so we admit that as the core-module form
of an **accessor**: a parameter the function uses at least once, every use an
invocation of the value as passed (`p(...)`, `p.apply(...)`, `p.call(...)`),
is typed as callable. A parameter the function never uses is not one, however
vacuously "every use" holds for it. This is the standard the C side already
has. `duk_to_string` rejects nothing and still makes a slot `string`
(**Coercion is not contract**; #232 counted 86 of 131 native slots read by a
coercing accessor). `p.apply(...)` likewise lets through more than a callable
-- an object carrying its own `apply` works there -- and counts for the same
reason: an accessor names what the author meant, not every value the runtime
lets past.

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
rule states. The term stays: the one union ADR-0003 calls exact is
`getChild`'s, where anything outside it throws, which is a rejection.

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
the generator already emits a signature for the slot -- from what the call
site passes (`new Page(...)`) or from a `@param` annotation -- that signature
takes the arrow's place, parameter names and return type included:
`Function | ((value: Page, ...args: any[]) => any)`,
`Function | ((req: any) => void)`.

## How it is recorded

Derived, not curated. `gen.py` is to enumerate every occurrence of the
parameter in the function. At least one, all invocations: the union, with each
invocation's anchor in the artifact. None, or any other use -- a truthiness
test, `typeof`, forwarding, storing, reassignment, a shadowing inner
parameter, `arguments` -- and this rule types nothing; a blocking use is
recorded with its anchor, as ADR-0003 records candidates. That does not
contradict ADR-0003's "a control-flow property the scanner cannot see": the
condition is which uses exist, not which paths reach them. A corpus test is to
pin one accepted and one refused slot.

**None of this is implemented yet.** Today `gen.py` only finds that a
parameter is invoked somewhere (`gen.py:3687-3692`), in order to read the
callback's shape. It does not look at the other uses, and it records
`callbackParam` for `movian/http.request` despite that function's
`if(callback)`. Until the generator does what this section says, no
declaration changes.

## Consequences

- All five settings callbacks become the union, `createAction` and
  `createMultiOpt` included: every use is an invocation, however conditional
  or late.
- Six slots already typed by #232's annotations or by the call-site shape are
  accessors too, and move to the union with the signature they have today as
  its arrow: `page.Route` (`page.js:465`), `page.Searcher` (`page.js:508`),
  `prop.subscribeValue` (`prop.js:120`), `subtitles.addProvider`
  (`subtitles.js:13`), `Item.addOptAction` (`page.js:76`) and
  `Page.appendAction` (`page.js:364`). Each is a widening, so free for
  callers: a value typed `Function`, a class or a differently annotated
  function is now accepted, and `addOptAction` and `appendAction`, `Function`
  today, stop failing an unannotated callback with TS7006.
- **Widening a callback slot to `any` is not free.** An unannotated callback
  loses its contextual signature and fails `--strict` with TS7006, and so does
  a method of an options object. `AGENTS.md`'s list of free changes now says
  so, and says to keep a signature when widening such a slot.
- The other five callbacks among #232's 72 no-ceiling annotations are not
  accessors and stay as they are. `movian/http.request` also reads its
  callback with `if(callback)` (`movian/http.js:110`); by ADR-0003 that makes
  it `any`, and `any` would break exactly the callers the previous point
  describes. `Request.on`, `Response.on` and both `onEvent`s store the
  function instead of calling it. With the 61 annotations that are not
  callbacks, they rest on the author's word rather than on an accessor, and
  are not settled here.
- A parameter no function body uses gets nothing from this rule. The root
  `http.request`'s `callback`, `unknown` on purpose, stays `unknown`.
- The accepted corpus is narrower than what this emits:
  `support/devtools/metadata/tests/reference/movian-settings.d.ts:48-54`
  gives `createBool` a `(value: boolean) => void` callback. The implementing
  change has to reconcile the two.
- Only invocation is admitted. Other reads in a core module (`x + ''`, a
  property read) are not accessors under this decision.
