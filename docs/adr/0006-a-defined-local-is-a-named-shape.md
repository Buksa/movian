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
or exported, no other function builds a local of that name, and TypeScript
accepts it as an interface name -- `var object = {}` is legal JavaScript and
`interface object` is TS2427.

## When it holds

The scan reads a top-level `function F(...) { ... }` and requires:

- F's masked text to pass the readable-body whitelist ADR-0005's scan uses
  (`UNREADABLE_BODY_RE`): no `/`, no backslash -- `\u0069tem` is `item` --
  no non-ASCII character, and no `eval` or `with`;
- `var V = {}` as a statement of F's own body;
- every `Object.defineProperties(V, {...})` a statement of the own body --
  not inside a nested function, a block, an unbraced conditional or an
  expression;
- no other occurrence of V anywhere in F, nested functions included, beyond
  the declaration, those calls and `return V`. This is a whitelist: a
  reassignment, a member write, an alias, a call that receives V,
  `Object.setPrototypeOf`, or a map that is not a literal each decline the
  shape, and so does a plain read, which the rule has no use for;
- `return V` as F's only own return, always reached;
- every key of every map one the scan can read, and at least one of them.

Then F returns the shape, and a caller's `var x = F(...); ... return x;`
returns it, as `var x = new C(...); ... return x;` returns `C`. The call has
to be the whole initializer: `var x = F(...).model` holds something else. A
local that fails any of these keeps the unsupported-target warning, now with
the reason.

The check is textual, over source with comments and strings masked. Within a
readable F it sees every mention of V, and it sees nothing outside F: F's
other variables and the callers are not traced.

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
- A caller that adds members to the object after the factory returns it is
  not seen: `var x = F(...); x.extra = 1; return x;` still returns the shape.
  That is the limit `var x = new C()` already has, and it is not closed here.
- The runtime oracle calls `globalSettings` and none of the four methods, so
  `item`'s three members are reviewed exclusions. Tier3 results are matched
  to shapes by lowercased name, and `item` made the `items` key ambiguous with
  `movian/page`'s `Item`. The matcher now skips local shapes, since a
  construction never yields one.
