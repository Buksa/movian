# A local object built by Object.defineProperties is a named shape

`movian/settings`' `createSetting` (`settings.js:5-42`) builds `var item = {}`,
attaches its whole public surface with `Object.defineProperties(item, {...})`
-- `model` as a value descriptor, `value` and `enabled` as get/set pairs --
and returns it. `sp.createBool`, `createString`, `createInt` and
`createAction` call it (`:73`, `:99`, `:129`, `:193`) and return what it
returns (`:90`, `:118`, `:155`, `:202`). The generator read
`Object.defineProperties` only on `this` and on `X.prototype`, warned about
`item` on every run, and declared the four results `any`.

We treat such a local as a **named shape**, the kind
`Object.defineProperties(this, ...)` already produces: `Service`
(`service.js:7-19`) defines `id` as a value descriptor and `enabled` as a
get/set pair, and `interface Service` declares both as `any` beside its
`destroy()` method. A named shape claims its member SET and types every
member `any`. That is all the descriptors prove here: `model` holds
`group.nodes[id]`, and the `value` getter returns `model.value`, neither of
which has a type the source shows.

This does not reopen the all-or-nothing contract of #160. That contract
governs an anonymous `return {...}`, whose claim is the type of each field,
so a field the recogniser cannot type makes a partial claim and the whole
object is declined. A named shape claims which members exist, as every
prototype shape the generator emits does, and here the scan declines the
shape unless the calls it reads state that set in full.

## The name

The local's own identifier, `item`, as the shared object `sp` is named after
its variable. `SettingItem`, the accepted corpus's name, is not in the source
and would need a curated sidecar. A module block holds one interface per name
and TypeScript merges a second declaration into the first, so a local is
declared only when its name is not already declared at the module's top level
or exported, no prototype shape of the module has it, no other function builds
a local of that name, and TypeScript accepts it as an interface name -- `var
object = {}` is legal JavaScript and `interface object` is TS2427. A prototype
shape takes its receiver's name whether or not a constructor is declared:
`item = function () {}` is an implicit global, and `item.prototype.actual =
...` still emits `interface item` (#272).

## When it holds

The scan reads a function DECLARATION `function F(...) { ... }` at the
module's top level -- not `x = function F(...)`, whose name binds only inside
itself -- and requires:

- F's masked text to pass the readable-body whitelist ADR-0005's scan uses
  (`UNREADABLE_BODY_RE`): no `/`, no backslash -- `\u0069tem` is `item` --
  no non-ASCII character, and no `eval` or `with`;
- no `this` in F: a descriptor's accessor runs with the object as `this`, so
  a setter could add a member the text never names;
- `var V = {};` as a statement of F's own body, `{}` the whole initializer --
  `var V = {} && x` holds `x`;
- every `Object.defineProperties(V, {...})` a statement of the own body --
  not inside a nested function, a block, an unbraced conditional or an
  expression -- whose map is the whole second argument and whose call is the
  whole statement: `{...} && d` passes `d`, and `(...).x = 1` writes to the
  object. The statement before the call must end in `;`, `{` or `}`; the
  call's own statement ends as every target's does, below;
- every descriptor an object literal of `value`, `get`, `set`, `writable`,
  `enumerable` and `configurable`, with `get` and `set` function literals
  written in place: an accessor runs with the object as `this`, and only
  one written in place is text the `this` rule above reads;
- no other occurrence of V anywhere in F, nested functions included, beyond
  the declaration, those calls and `return V`. This is a whitelist: a
  reassignment, a member write, an alias, a call that receives V,
  `Object.setPrototypeOf`, or a map that is not a literal each decline the
  shape, and so does a plain read, which the rule has no use for. Text that
  only spells the name -- an object key, a label -- counts too, and declines;
- F declared once at the module's top level, and its name occurring
  anywhere in the module only in that declaration and in calls `F(...)`: a
  call of F runs whatever F holds by then, and `F = g`, `(F) = g`, `var F`
  or a parameter named F would each change that. A whitelist again, after
  a list of assignment forms missed the parenthesized one;
- no `new V(...)` anywhere in the module: the generator reads it as the
  shape named V, and a module block holds one type of that name;
- `return V` as F's only own return, always reached, with no line terminator
  between `return` and V (ES5.1 7.9.1 makes that `return;`);
- every key of every map one the scan can read and a plain identifier --
  `'foo-bar'` cannot be declared unquoted, and `'x\u0061'` is the key `xa` --
  and at least one of them.

A local that fails any of these keeps the unsupported-target warning, now
with the reason.

What the list says about a map is not the local's alone. The generator also
reads `Object.defineProperties` on `this` in a constructor and on
`X.prototype`, and all three targets share one reading of the call (#272): it
begins and ends its statement, the map is the whole second argument, every
descriptor is an object literal of descriptor keys with `get` and `set`
written in place, every key is one the scan can read and a plain
identifier, and no `/` in the map follows a `}` (below). A call that fails
records no member and is reported with the reason. Where the call may sit
differs by target -- the constructor's own body, the module's top level, F's
own body. A member is an `accessor` when its descriptor has `get` or `set`
among its own keys, read with literals masked: `value: 'get: x'` is a value.

The map is read on the module's scanner, `_js_spans`, where a regex literal is
a literal: the comma in `value: /x,y/` separates no fields, and the brace in
`/}/` closes nothing. The mask the calls are found in works a line at a time
and knows neither, so the first reading split `/x,y/` into two fields and
refused the call. A statement is the scanner's too, ended as ES5.1 7.9.1 ends
it:

- after the call's `)` it ends at `;`, at `}`, at the end of the module, or
  at a line terminator followed by a token that cannot continue the
  expression -- anything but `(`, `[`, `.`, `,`, `?`, `:`, `=`, `+`, `-`, `*`,
  `/`, `%`, `&`, `|`, `^`, `<`, `>`, `!`, `in` and `instanceof`, a comment
  counting as whitespace. So the call then `(f)()` on the next line is one
  statement, `f` an argument to what the call returns;
- before the call, the previous one ends at `;`, `{` or `}`, or at a line
  terminator after a token no expression may begin after -- a name, a
  literal, `]`, or a `)` that is not a control header's. `d &&` or `if (d)`
  at the end of the line makes the call part of what they begin.

The first reading required the `;` written and nothing but `;`, `{` or `}`
before the call, and refused every call that relied on ASI. For a local the
end is shared and the start is not: `Object.defineProperties(V, {...})`, a
line terminator and `return V` now read, while the local's own rule above
still wants `;`, `{` or `}` before the call, `var V = {};` with its `;`, and
`return V` as written.

## The caller

A method returns the shape through `var x = F(...); ... return x;` only under
a whitelist of its own, because each caller-side guard added one at a time
was followed by another way through: a member read off the result, a
conditional declaration, a line terminator after `return`, a nested function
shadowing F, a reassignment. In the method:

- the text passes the same readable-body whitelist;
- `var x = F(...)` is a statement of its own body, and the call is the whole
  initializer -- `F(...).model` and `F(...) || y` hold something else;
- F occurs only in that call and is not a parameter, so it is the module's
  function and nothing in the method rebinds it;
- x occurs otherwise only as `x.<a member of the shape>`, which reads or
  writes a member the object already has -- not after `delete`, which
  removes it, and not called, which runs what the member holds with the
  object as `this`, parenthesized or not: `(x.m)()` keeps the reference
  (ES5.1 11.1.6) -- and in `return x`, the method's only own return,
  always reached, on one line. Only a grouping keeps it. The `)` of a
  control header, `if (x.m) (f)();`, or of an argument list, `g(x.m)()`,
  ends the reference, and the `(` after it calls something else; a `(`
  opens a grouping where an expression may begin.

`movian/settings`' four methods use `item` only as `item.model...`, and pass.

## What it does not see

The check is textual, over source with comments and strings masked. It reads
F, the methods that call it, and the module's text for another binding of
either name, and within a readable function it sees every mention of a name.
Nothing else is traced: not an object reached through another variable, and
not the code that calls the four methods. It assumes `Object` is the
intrinsic, as every `Object.defineProperties` reader in the generator does --
a module that rebinds `Object` is not detected.

The readers take the call spelled `Object.defineProperties(` only. Every
other spelling of the name in the module's code -- a space before `(`, an
alias, and `Object['defineProperties']`, the one string that spells it, in
brackets that read a member -- is read by nothing and reported with its line
(#272). A string that only mentions the name, an array literal
`['defineProperties']` included, is not a call and is not reported. A name
built at run time, `Object[k]`, is not seen, even when `k` holds
`'defineProperties'`. The census walks the module again with its own scanner
of code, literals and comments, not the readers' mask: a census needs an
enumerator independent of the builder (ADR-0004), and a regex literal holding
a quote or a string continued onto the next line makes the mask hide the code
after it, so a census reading the masked text was blind exactly where the
readers were. The readers now read a map on that scanner, but they still find
their calls in the mask, and it is what they found that the census is
compared with. The scanner has a blind spot of its own: it reads a `/` right
after `}` as the start of a regex, the half that is safe for stripping
comments. Here a literal is blanked, so a division there --
`{} / Object['defineProperties'](...) / 2`, valid ES5 and absurd -- hides
the call from the census as well. No lexer settles `}` then `/` without
parsing, so for the census it is accepted.

For the readers it is not. Inside a map the same reading hid fields: in
`{ a: { value: {} / 2 }, b: { value: 4 / 2 } }` the scanner takes
`/ 2 }, b: { value: 4 /` for a regex, and `b` went unrecorded without a
warning. A reader now refuses a call whose map holds a regex right after
`}`, comments between them counting as whitespace, in a nested descriptor
or an accessor's body as well, and reports that the `/` cannot be told from
a division. That refuses a real regex too -- `if (x) {} /re/.test(y)` in a
getter -- because telling it from a division is what the scanner cannot do.
A member may still be lost there, but not silently.

## The `new` path

`_returned_shape` also answers a class: for `return new X(...)`, and for
`var x = new X(...); ... return x;`, which `movian/page`'s `appendItem`,
`appendAction` and `appendPassiveItem` use. Until #268 it found `return new
X(` and `var x = new X(` anywhere in the method and skipped every other
return, so `x = y`, `x.extra = 1`, `new X(...).page`, a line terminator
after `return`, and an `if (t) return 5;` beside it all still answered `X`.
The owner settled the rule on 2026-10-02. In the method:

- the text passes the readable-body whitelist. The mask takes the quote in
  `/'/` for a string's and blanks the rest of the line, a `return 5` on it
  included;
- every return the method itself makes is accounted for: `return x` for a
  local that passes the rules below, `return new X(...)` with the
  construction the whole value, or `return x` for a factory local (above),
  all of one class -- or every one a mapped array of one element, which is
  how `movian/html`'s selectors answer `Node[]`. A plain value, a bare
  `return;`, or a line terminator right after `return` declines.
  `_always_returns` still gates first;
- the local is declared as `var x = new X(...)`, the construction the whole
  initializer, as an unconditional statement of the method's own body, and
  that is the first mention of the name: `var` is hoisted and its value is
  not, so a `return x` above it returns `undefined`. `_whole_construction`
  is the one reading of "a whole `new X(...)`" (#266);
- x occurs otherwise only in an own `return x`, as a whole argument of a
  call or a construction, and as `x.<m>`. Anything else declines, so every
  write to the name does: an assignment, a compound assignment, `++` or
  `--`, a second `var`, a parameter, a catch clause or a function of that
  name, in a nested function as well. Matching is by name, not by scope,
  so a binding that only shadows it declines too (#274);
- `x.<m>` may be read or called, and written only when X declares `m` --
  by `=`, a compound assignment, `++` or `--`, through a grouping, or as a
  for-in target. `delete x.<m>` declines, and so does `x[k]`, a member the
  scan cannot name. X declares what its built shape holds, the members its
  constructor and its prototype give it, so a method's return type is read
  after the module's shapes are built.

`movian/page`'s three methods use `item` only in the declaration, in
`this.items.push(item)` (two of them), as `item.root`, and in `return
item`, and pass.

### Why it differs from the caller rule

The caller rule declines a call that receives its local, and a member
call; this one keeps both. The difference is deliberate. Do not make one
rule follow the other.

The claims differ. A factory local's shape is derived from that very
local: its member set is what the `Object.defineProperties` calls on it
define, and nothing else states it. A function that receives the local
may add to that set or take from it, and a value member, called, runs a
function nothing has read with the object as `this`. Either way the
declaration describes what the text no longer guarantees.

A class's shape is not derived from the local. X's constructor and
prototype claim their members for every instance, and the method claims
only that what it returns is one. A call cannot rebind the caller's local,
so after `push(item)` the local still holds the instance it was given. A
callee may add a member the declaration does not offer, as it may to any
X anywhere, and a plugin that reaches for it gets TS2339 as it would on
any X. A member call is the same: `x.m()` runs what an X holds as `m`, and
whatever that does to an instance belongs to X, not to what this method
says about its result.

What the method writes itself is different. `x.extra = 1` in its own text
adds a member to what it hands back, and `delete x.m` takes one away, so
both decline: the method is telling its callers about an object X does not
describe.

## Considered

- **Record only.** Read the descriptors into the artifact, silence the
  warning, and declare nothing. It is faithful to #160 but gives a plugin
  author nothing, and nothing would read the record.
- **An anonymous object with an exception.** `createBool(...): { enabled:
  any; model: any; value: any }` by exempting descriptor members from
  all-or-nothing. That reverses the defect #160 recorded and fixed.

## Consequences

- The four methods return `item` in both emitted forms: the function a plain
  `globalSettings(...)` call installs on the module, and `interface sp`. An
  invented member on the result, such as `.title`, is TS2339.
  `createMultiOpt`, `createDivider` and `createInfo` return nothing and are
  unchanged.
- Members stay `any`, `model` is not emitted `readonly` (`Service.id` is not
  either), and there is no type parameter.
- `Object.defineProperty(V, 'x', ...)` alone is not read and keeps its
  warning. No core module does it.
- The `new` path has a whitelist of its own since #268, and it differs
  from the caller rule on purpose (above).
- The runtime oracle calls `globalSettings` and none of the four methods, so
  `item`'s three members are reviewed exclusions. Tier3 results are matched
  to shapes by lowercased name, and `item` made the `items` key ambiguous with
  `movian/page`'s `Item`. The matcher now skips local shapes, since a
  construction never yields one.
