# GLW view-language reference

Agent-oriented reference for the `.view` DSL implemented by
`src/ui/glw/glw_view_*.c` in **this tree** (movian6). Terse tables, not a
tutorial. Every semantic claim carries a `file:line` anchor; corpus files
(`glwskins/flat/**/*.view`, sibling plugin checkouts) only illustrate idiom
and are cited separately as `(corpus: path:line)`.

Companion docs: `glw-widget-catalog.md` (every registered widget class),
`glw-patterns.md` (worked recipes). Drift between this doc and the source
tables is caught by `mdev viewdoc --check` (`support/devtools/mdevlib/viewdoc.py`).

## 1. Lexical elements

Source: `src/ui/glw/glw_view_lexer.c`, token types in `src/ui/glw/glw_view.h:34-101`.

- **Comments**: `// ...` and `/* ... */` (`glw_view_lexer.c:223-245`).
- **Identifiers**: `[A-Za-z_][A-Za-z0-9_]*` (`glw_view_lexer.c:161-167,347-355`).
- **Numbers**: integer or float, optional leading `-`, optional trailing `f`;
  `123`, `1.5`, `-2` (`glw_view_lexer.c:357-375`, float parsing
  `glw_view_lexer.c:74-119`). An int/float immediately followed by the bare
  identifier `em` becomes a `TOKEN_EM` ("1em" == `$ui.size`, i.e. the
  current UI size unit) — parser-level rewrite at
  `glw_view_parser.c:512-525`.
- **Booleans**: bareword `true`/`false` lex directly to `TOKEN_INT` 1/0
  (`glw_view_lexer.c:208-221`). There is no boolean type distinct from int.
- **`void`**: bareword literal, lexes to `TOKEN_VOID` (`glw_view_lexer.c:202-206`)
  — GLW's explicit "no value" / unset marker, also what a prop subscription
  resolves to when the property doesn't exist or was set to void.
- **Strings**:
  - `"..."` → plain string, `TOKEN_RSTRING` (`glw_view_lexer.c:320-343`).
  - `'...'` → **rich string**, same token type but tagged
    `t_rstrtype = PROP_STR_RICH` (`glw_view_lexer.c:340-341`). When used as
    a `label`/`text` caption, a rich string is parsed for a small HTML-like
    markup subset (tags + entities) instead of shown literally —
    `TEXT_PARSE_HTML_TAGS | TEXT_PARSE_HTML_ENTITIES` gating at
    `glw_text_bitmap.c:794-795`. Plain `"..."` captions are never
    tag-parsed.
  - Both string forms support C-style backslash escapes
    (`deescape_cstyle()`, `glw_view_lexer.c:64`).
- **Operators** (single/double-char, `glw_view_lexer.c:126-158,247-305`):
  `+ - * / % ! & && || ^^ == != < > ? : . , ; { } ( ) [ ]`, plus the
  assignment family below and `??` (null-coalesce, see §3).

## 2. Property-path sigils and scope roots

Source: `src/ui/glw/glw_view_parser.c:460-505`, `src/ui/glw/glw_scope.c:49-64`,
`src/ui/glw/glw.h:290-297`.

- `$name` — a property-path reference rooted at one of the scope roots
  below (`TOKEN_DOLLAR` + identifier → `TOKEN_PROPERTY_NAME`,
  `glw_view_parser.c:460-469`). `.` chains further path segments
  (`glw_view_parser.c:479-503`): `$self.model.metadata.title`.
- `&name` — **deprecated** equivalent of `$name` that additionally sets
  `TOKEN_F_CANONICAL_PATH` (don't follow symlinks when resolving) and logs
  a `TRACE_INFO` deprecation notice (`glw_view_parser.c:464-467`). Prefer
  `$name`.
- `.name` (leading dot, no `$`) — rewritten to a bare attribute-name token
  identical to writing `name` directly (`glw_view_parser.c:531-544`,
  `glw_view_attrib_resolve()`); `.sizeScale = 3;` and `sizeScale: 3;` are
  the same attribute assignment. Seen in third-party corpus views
  (`~/movian-plugin-tmdb/views/text.view:10`); rare in this repo's own
  skin but not an idiom-only claim — it is a real parser rewrite.

**Scope roots** — the only names that resolve a leading `$`/`&` segment:

| root | anchor | meaning |
|---|---|---|
| `self` | `glw_scope.c:54` | the widget/clone's own bound prop (e.g. a page, or the current clone item) |
| `parent` | `glw_scope.c:55` | the enclosing scope's `self` (one level up — set when a `loader`/`widget` block rebinds `self`) |
| `view` | `glw_scope.c:56` | per-view-file scratch root, fresh each time the `.view` file is loaded — see `glw-patterns.md` |
| `args` | `glw_scope.c:57` | arguments passed in via the `args:` attribute |
| `clone` | `glw_scope.c:58` | scratch root local to one `cloner()`-generated clone instance |
| `core` | `glw_scope.c:59` | global core-service props (e.g. `$core.stpp`, `$core.popups`, `$core.clipboard`) |
| `parentview` | `glw_scope.c:60` | the `view` root one level up (companion to `parent`) |
| `ui` | `glw_x11.c:1431` (`prop_create_root("ui")`) | global per-`glw_root_t` UI prop tree (`$ui.width`, `$ui.aspect`, `$ui.keyboard`, `$ui.pointerVisible`, ...) — resolves because the prop node itself is named `"ui"`, matched by `prop_resolve_tree()`'s `hp_name` check (`src/prop/prop_core.c:2779`), not via an explicit named-root tag |
| `nav` | `glw_view_eval.c:831` (`PROP_TAG_NAMED_ROOT`) | the navigator (`$nav.currentpage`, `$nav.pages`) |
| `global` | `src/prop/prop_core.c:2769` (hardcoded) | prop root's absolute global tree; rarely used directly in views |

There is **no `page` scope root** in this tree. Third-party corpus views
(e.g. `~/movian-plugin-tmdb/views/posters.view:6,10`) use `$page.*` by a
different fork's convention; here it resolves to nothing. Views loaded
through the `viewpreview` dev plugin or the flat skin's page cloner must
bind through `$self.model.*` / `$self.args.*` instead (see
`support/devtools/viewpreview/README.md` "How the target view sees the
page model").

## 3. Assignment operators

Source: dispatch table `glw_view_eval.c:2743-2766`; shared implementation
`eval_assign()` (`glw_view_eval.c:934-1082`) parameterized by `how`; link
form `eval_link_assign()` (`glw_view_eval.c:873-927`).

| operator | token | `how` | anchor | semantics |
|---|---|---|---|---|
| `=` | `TOKEN_ASSIGNMENT` | 0 | `glw_view_eval.c:2743-2746` | Normal assignment. If the right side is a property path, it is **resolved through a live subscription** (`token_resolve()`, `glw_view_eval.c:990`) — the RPN re-evaluates whenever the underlying prop changes. Assigning to an attribute flagged `GLW_ATTRIB_FLAG_NO_SUBSCRIPTION` (`self`, `itemModel`, `parentModel`, `tentative` — `glw_view_attrib.c:1494-1501`) skips the subscription and passes the raw property reference instead, `how==0` or not (`glw_view_eval.c:980-982`). |
| `?=` | `TOKEN_COND_ASSIGNMENT` | 1 | `glw_view_eval.c:2748-2751` | Conditional assignment: identical to `=`, except if the resolved right-hand value is `void`, **the assignment is skipped entirely** — the left side keeps its previous value (`glw_view_eval.c:1004-1007`, "Conditional assignment: rvalue of (void) results in doing nothing"). Used for defaulting an attribute only when a prop is actually set. |
| `_=_` | `TOKEN_DEBUG_ASSIGNMENT` | 2 | `glw_view_eval.c:2753-2756` | Debug assignment: functionally identical to `=` for the common (attribute or plain prop) case. It only differs in the rare direct prop-to-prop link case (both sides already `TOKEN_PROPERTY_REF`), where it passes `debug=1` into `prop_link_ex()` (`glw_view_eval.c:970-971`, param name `debug` per `src/prop/prop_core.c:4859-4860`), turning on subscription-debug tracing for that link — the mechanism behind `debug: true`'s `PROP_SUB_DEBUG` tracing (see `SKILL.md` "Widget-local debug tracing"). |
| `:=` | `TOKEN_REF_ASSIGNMENT` | 3 | `glw_view_eval.c:2758-2761` | Reference assignment: the right side is **not** resolved to a value — it stays a property reference (`glw_view_eval.c:980-986`). Assigning a bare property (not an attribute) with `:=` creates a live **prop-symlink** (`prop_set_prop()`, `glw_view_eval.c:1056-1057`) so the left prop continuously mirrors the right. Assigning to a `self`/`args`/`itemModel`/`parentModel`/`tentative` attribute passes the actual `prop_t*` into the class's attribute setter — required for those attributes since they need the property identity, not a snapshotted value. Idiom: `args: { nodes := $self.model.nodes; }` when loading a sub-view. |
| `<-` | `TOKEN_LINK_ASSIGNMENT` | n/a (separate function) | `glw_view_eval.c:2763-2766`, `glw_view_eval.c:873-927` | Link assignment: always creates a one-way, continuously-live `prop_link_ex()` link from the right-hand property to the left (`glw_view_eval.c:923-925`). Distinct from `:=`'s symlink in that the left side may be a bare identifier inside an `args`/block scope (auto-created as a child prop of the current target, `glw_view_eval.c:906-911`) rather than requiring an existing property. |

All four assignment tokens plus `<-` share one more special case: if
**both** sides are already `TOKEN_PROPERTY_REF` at eval time, `eval_assign`
treats it as legacy prop-to-prop linking and logs a `TRACE_INFO`
"Prop linking via assignment is deprecated" (`glw_view_eval.c:963-973`) —
this is dead-idiom territory, not a recommended pattern.

## 4. Other operators

| operator | token | anchor | semantics |
|---|---|---|---|
| `??` | `TOKEN_NULL_COALESCE` | `glw_view_lexer.c:301-305`, eval `glw_view_eval.c:790-810` | `a ?? b`: resolves `a`; if `a` is `void`, evaluates to `b`, else to `a`. Idiom for "fall back to a default only when the prop is genuinely unset" — used constantly for `source:` fallback chains, e.g. `glwskins/flat/universe.view:53-54`. |
| `?:` (ternary) | `TOKEN_QUESTIONMARK`/`TOKEN_COLON` → synthetic `TOKEN_TENARY` | eval `glw_view_eval.c:1087-1100` | `cond ? a : b`: resolves `cond` via `token2bool()`, then (unlike `??`) does **not** eagerly resolve both branches — pushes the unresolved chosen branch (`glw_view_eval.c:1097-1098`). |
| `&&` `\|\|` `^^` | boolean ops | `glw_view_lexer.c:247-287` | Short-circuit-free boolean and/or/xor over `token2bool()` results (this DSL's RPN evaluator resolves both operands before combining — there is no lazy short-circuit at the token level). |
| `==` `!=` `<` `>` | comparisons | `glw_view_lexer.c:289-299,150-151` | Numeric/string comparison via `token_cmp()`-style helpers; mixed int/float compares by value. |
| `+ - * /  %` | arithmetic | `glw_view_lexer.c:142-146` | Numeric; `+` is overloaded for string concatenation when either operand is a string (see `"skin://items/list/" + $self.type + ".view"` in `glwskins/flat/pages/list.view:33`). |

## 5. Preprocessor

Source: `src/ui/glw/glw_view_preproc.c` (full file — no other preprocessor
logic exists in this tree).

- **`#include "path"`** — lexes and splices the target file's tokens in
  place, **every time it's written**, no dedup (`glw_view_preproc.c:295-313`).
- **`#import "path"`** — like `#include`, but deduplicated by resolved
  filename string: a second `#import` of the same path anywhere in the
  same top-level parse is silently skipped (`glw_view_preproc.c:316-346`,
  the `import_list`/`il` tracked across the whole `glw_view_preproc()`
  call). Use `#import` for shared macro/style files (`theme.view`,
  `style_list.view`) that multiple `.view` files pull in; use `#include`
  only when you deliberately want the content re-spliced every time.
- **`#define name(arg1, arg2=default, ...) { body }`** — macro definition
  (`glw_view_preproc.c:349-465`):
  - Arguments are plain identifiers; any argument after the first with a
    default (`=`) makes all subsequent arguments require defaults too
    (`glw_view_preproc.c:406-409`, "Non default arg after default arg").
  - Defaults may be arbitrary token sequences up to the next top-level `,`
    or `)` (paren-depth tracked, `glw_view_preproc.c:386-399`).
  - Body is stored as a token chain; each occurrence of an argument name
    inside the body is tagged (`t->tmp = ma`) for substitution
    (`glw_view_preproc.c:447-455`).
  - Invocation `name(...)` splices a deep copy of the body with each
    tagged argument-occurrence replaced by a deep copy of the
    corresponding actual argument tokens (or the default, if the actual
    was omitted) — `glw_view_preproc.c:228-271`.
  - **Named-argument form** is supported: `name(arg2 = val)` — detected
    when the first actual token is `IDENTIFIER '='`
    (`glw_view_preproc.c:125-126`); mixing named and positional arguments
    in the same call is a hard error (`glw_view_preproc.c:168-172`,
    "Mixing named and unnamed arguments is not supported").
  - Macros are **block-bodied only** (`{ ... }` after the header,
    `glw_view_preproc.c:426-428`) — there is no expression-only macro form.
  - Corpus idiom: `#define ListItemBevel() { ... }` /
    `#define PageHeader(title) { ... }` in `glwskins/flat/theme.view:3-16`.

Macro invocation and `#`-directive scanning both happen in one single
left-to-right pass over the whole (already `#include`/`#import`-expanded)
token stream (`glw_view_preproc0()`, `glw_view_preproc.c:95-473`) — macros
must be `#define`d before their first use in file order; there is no
forward declaration.

## 6. Expression-function table

Source: `funcvec[]`, `src/ui/glw/glw_view_eval.c:7274-7377`, resolved by
name in `glw_view_function_resolve()` (`glw_view_eval.c:7384-7420`, exact
`strcmp` match, no overloading by arity). **90 entries** (89 always built,
1 debug-only — see Coverage & gaps). Counting method: `grep -oE
'\{"[a-zA-Z0-9_]+"' src/ui/glw/glw_view_eval.c` over lines 7274-7377,
deduplicated and diffed for uniqueness (0 duplicates found); reproduced by
`mdev viewdoc --check`.

`nargs` of `-1` means variadic (the function itself validates `argc`).
Anchor given is the `funcvec[]` line (authoritative name/arity source);
implementation line noted separately only where the one-line semantics
needed verifying against non-obvious behavior.

### Fundamentals

| name | nargs | anchor | semantics |
|---|---|---|---|
| `widget` | 1 | `glw_view_eval.c:7278` | `widget(className, { attrs })` — create a child widget of the named `glw_class_t` (resolved by `glw_class_find_by_name()`) and evaluate the block against it (`glwf_widget`, `glw_view_eval.c:2978-3021`). Also reachable as sugar: a bare `container_y({...})`-style call resolves directly to a widget if the name matches a registered class (`glw_view_eval.c:7406-7416`). |
| `cloner` | 3 | `glw_view_eval.c:7279` | `cloner(propDirectory, className, { block })` — for each child of a directory-type property, instantiate one `className` widget evaluating `block` with `$clone` bound to that child. Requires the **parent** widget's class to have `GLW_CAN_HIDE_CHILDS` (aborts otherwise, `glw_view_eval.c:3092-3096`); see widget catalog for which classes qualify. Full mechanism: `glwf_cloner`, `glw_view_eval.c:3059-3143`. |
| `coreAttach` | 3 | `glw_view_eval.c:7280` | Attaches a subtree to a core-service target; has ctor/dtor (stateful). |
| `style` | 2 | `glw_view_eval.c:7281` | `style(StyleName, { block })` — apply a named style block (see `glwskins/flat/universe.view:29-37` `style(NavSelectedText, {...})`) to the current widget. |
| `newstyle` | 2 | `glw_view_eval.c:7282` | Declare a new named style (vs. applying/overriding one via `style`). |
| `space` | 1 | `glw_view_eval.c:7283` | Insert a flexible spacer consuming `n` weight units in the enclosing container (`space(1)` idiom, `glwskins/flat/theme.view:11`). |

### Events

| name | nargs | anchor | semantics |
|---|---|---|---|
| `onEvent` | -1 (2-5) | `glw_view_eval.c:7288`, impl `glw_view_eval.c:3361-3400+` | `onEvent(actionOrExpr, targetExpr, enabled=true, final=true, early=false)` — map an input action (by name string, matched against `src/event.c`'s 71-entry `actionnames[]` table, `src/event.c:109-200`, or an arbitrary expression) on the current widget to evaluating `targetExpr`. |
| `navOpen` | -1 | `glw_view_eval.c:7289` | Navigate/open a URL (typically a `makeUri()` result or `$self.url`), optionally passing `how`, item-model, parent-model props. |
| `playTrackFromSource` | -1 | `glw_view_eval.c:7290` | Start audio playback from a source property. |
| `enqueuetrack` | 1 | `glw_view_eval.c:7291` | Enqueue a track onto the play queue. |
| `selectAudioTrack` | 1 | `glw_view_eval.c:7292` | Select an audio track by id/prop. |
| `selectSubtitleTrack` | 1 | `glw_view_eval.c:7293` | Select a subtitle track by id/prop. |
| `fireEvent` | 1 | `glw_view_eval.c:7294` | Dispatch an already-built event token. |
| `event` | 1 | `glw_view_eval.c:7295` | Build a generic named event (by action-name string) as a value, for use as an `onEvent` target expression. |
| `targetedEvent` | 2 | `glw_view_eval.c:7297` | Build an event targeted at a specific widget/prop rather than the focused one. |
| `deliverEvent` | -1 | `glw_view_eval.c:7298` | Deliver an event to a named destination (widget id or prop). |
| `deliverRef` | 2 | `glw_view_eval.c:7299` | `deliverRef(target, value)` — deliver a value/ref to a target (idiom: `deliverRef($core.clipboard.setFromItem, $self)`, `glwskins/flat/pages/list.view:26`). |
| `currentEvent` | 1 | `glw_view_eval.c:7300` | Access the event currently being routed (inside an event-map context). |
| `onInactivity` | 2 | `glw_view_eval.c:7302` | Fire after N seconds of input inactivity; stateful (ctor/dtor). |
| `changed` | -1 | `glw_view_eval.c:7303` | True for one evaluation cycle right after its argument's value changes; stateful. |
| `iir` | -1 | `glw_view_eval.c:7304` | Infinite-impulse-response low-pass filter over a numeric input — the standard "smooth this value over N frames" idiom (`iir(x, 8)`; ubiquitous in the flat skin for fades). |
| `scurve` | -1 | `glw_view_eval.c:7305` | S-curve-shaped animated transition toward a target value over a given time; stateful. |
| `translate` | -1 (even, >=2) | `glw_view_eval.c:7306`, impl `glw_view_eval.c:4633-4670` | `translate(index, default, k1, v1, k2, v2, ...)` — associative-array lookup: returns the `v` whose `k` matches `index` (via mixed int/float/string comparison), else `default`. |
| `strftime` | 2 | `glw_view_eval.c:7307` | `strftime(unixtime, formatString)` — localtime-formatted string (`glw_view_eval.c:4675-4699`). |
| `isSet` | 1 | `glw_view_eval.c:7308` | True if the argument does not resolve to `void`. |
| `isVoid` | 1 | `glw_view_eval.c:7309` | True if the argument resolves to `void`. |
| `value2duration` | -1 | `glw_view_eval.c:7310` | Format a numeric duration (seconds) as a human string. |
| `value2size` | 1 | `glw_view_eval.c:7311` | Format a numeric byte count as a human string (KB/MB/...). |
| `value2quantity` | 1 | `glw_view_eval.c:7312` | Format a numeric quantity with locale-aware grouping. |
| `createChild` | 1 | `glw_view_eval.c:7313` | Create a child prop under a directory property. |
| `delete` | 1 | `glw_view_eval.c:7314` | Delete a property/child. |

### Widget/focus introspection

| name | nargs | anchor | semantics |
|---|---|---|---|
| `isFocused` | 0 | `glw_view_eval.c:7315`, impl `glw_view_eval.c:4996-5011` | True only if the current widget has GLW keyboard-navigation focus **and** `gr_keyboard_mode` is on (`glw_is_focused(ec->w) && ec->w->glw_root->gr_keyboard_mode`). Keyboard mode is toggled on only by an actual `EVENT_KEYPRESS`-flagged input event (`glw_set_keyboard_mode`, `glw.c:2500-2501`) — synthetic `/api/input/action/<Action>` calls do **not** set this flag, so `isFocused()`/`isNavFocused()` read false even though the widget is the logical focus target. See `movian-plugin-testing/references/debug-flags.md` and this skill's "`--debug-glw`" section for the practical consequence (no visible list cursor from action-only input). |
| `isNavFocused` | 0 | `glw_view_eval.c:7316` | **Same implementation as `isFocused`** — `funcvec[]` maps both names to `glwf_isFocused` (`glw_view_eval.c:7315-7316`). No semantic difference; `isNavFocused` is the name used pervasively in the flat skin's styles (`glwskins/flat/universe.view:34,38`). |
| `isHovered` | 0 | `glw_view_eval.c:7317` | True while the pointer is over the widget. |
| `isPressed` | 0 | `glw_view_eval.c:7318` | True while the widget is pointer/click-pressed. |
| `focusedChild` | 0 | `glw_view_eval.c:7319` | The currently focused direct child widget/prop. |
| `focusedClone` | 0 | `glw_view_eval.c:7320` | The currently focused clone (inside a `cloner()`). |
| `focusedIndex` | 0 | `glw_view_eval.c:7321` | Index of the focused child/clone. |
| `canSelectNext` | 0 | `glw_view_eval.c:7356` | True if a "select next" navigation is currently possible. |
| `canSelectPrevious` | 0 | `glw_view_eval.c:7357` | True if a "select previous" navigation is currently possible. |
| `cloneIndex` | 0 | `glw_view_eval.c:7362` | Index of the current clone within its `cloner()`. |
| `isVisible` | 0 | `glw_view_eval.c:7325` | True if the widget is currently visible (not hidden/occluded per GLW's own visibility tracking). |
| `isPreloaded` | 0 | `glw_view_eval.c:7326` | True if a `loader`/deck child has finished preloading. |
| `canScroll` | 0 | `glw_view_eval.c:7327` | True if the widget (list/array/scroll container) has more content than fits. |
| `isLoading` / `isLoaded` / `isError` | 0 | `glw_view_eval.c:7336-7338` | Widget-status tri-state (loader/backend fetch state). |
| `getCaption` | 1 | `glw_view_eval.c:7322` | Read back a widget's current text caption by id. |
| `getLayer` / `getWidth` / `getHeight` | 0 | `glw_view_eval.c:7346-7348` | Current stacking layer / measured width / measured height of the widget. |
| `suggestFocus` | 1 | `glw_view_eval.c:7340` | Request GLW move focus to the named widget/prop. |
| `focus` | 1 | `glw_view_eval.c:7366` | Force-focus a widget by id/prop. |
| `selectedElement` | 1 | `glw_view_eval.c:7360` | Pick the currently "selected" element of a vector (paired with `vectorize`). |

### Property / prop-tree helpers

| name | nargs | anchor | semantics |
|---|---|---|---|
| `bind` | 1 | `glw_view_eval.c:7323` | Bind the current widget's value to a named property (used by `slider`/scrollbars — `gc_bind_to_id`/`gc_bind_to_property` class hooks). |
| `count` | 1 | `glw_view_eval.c:7341`, impl `glw_view_eval.c:5938-5948` | Live child count of a directory property (`token_resolve_ex(..., GPS_COUNTER)`). |
| `vectorize` | 1 | `glw_view_eval.c:7342`, impl `glw_view_eval.c:5954-5964` | Turn a directory property into a vector token of its children's resolved values (`GPS_VECTORIZER`); typically paired with `selectedElement()` (`glwskins/flat/universe.view:53-54`). |
| `propGrouper` | 2 | `glw_view_eval.c:7343` | `propGrouper(directoryProp, "groupKeyName")` — groups a directory's children by a named sub-property; stateful (ctor/dtor). |
| `propSorter` | -1 | `glw_view_eval.c:7344` | Sort a directory property's children; stateful. |
| `propWindow` | 3 | `glw_view_eval.c:7345` | Windowed/paginated view over a directory property; stateful. |
| `propName` | 1 | `glw_view_eval.c:7364` | The leaf name of a property path. |
| `propSelect` | 1 | `glw_view_eval.c:7365` | Mark/read the "selected" child of a directory. |
| `set` | 2 | `glw_view_eval.c:7361` | `set(prop, value)` — imperative one-shot prop write (vs. declarative `=`); stateful dtor to detach. |
| `lookup` | 2 | `glw_view_eval.c:7370` | Look up a value in a directory property by key; stateful (ctor/dtor). |
| `browse` | 1 | `glw_view_eval.c:7330` | Open a directory-browsing subscription; stateful (ctor/dtor). |
| `isLink` | 1 | `glw_view_eval.c:7331` | True if a property is a symlink. |
| `injectEventsFrom` | 1 | `glw_view_eval.c:7371` | Route another widget's input events through the current one. |
| `eventWithProp` | 2 | `glw_view_eval.c:7368` | Build an event carrying an attached property payload. |

### Math / value helpers

| name | nargs | anchor | semantics |
|---|---|---|---|
| `select` | 3 | `glw_view_eval.c:7328`, impl `glw_view_eval.c:5414-5425` | `select(cond, whenTrue, whenFalse)` — resolves `cond` via `token2bool()`, pushes the **unresolved** chosen branch (ternary-like; the other branch's subscriptions are never created). Ubiquitous idiom, e.g. `glwskins/flat/pages/grid.view:14`. |
| `sin` | 1 | `glw_view_eval.c:7332`, impl `glw_view_eval.c:5646-5660` | `sin(x)` (radians), 0 if input isn't numeric. |
| `sinewave` | 1 | `glw_view_eval.c:7333` | A free-running sine oscillator (time-driven, not a pure function of its argument). |
| `monotime` | 0 | `glw_view_eval.c:7334` | Current monotonic clock reading, for building custom animations. |
| `delay` | 3 | `glw_view_eval.c:7335` | Delay a value's propagation by N seconds; stateful. |
| `delta` | 2 | `glw_view_eval.c:7324` | `delta(target, source)` idiom seen at `glwskins/flat/universe.view:8`: propagate `source`'s value changes onto `target`; stateful. |
| `rand` | 0 | `glw_view_eval.c:7359` | A random number. |
| `abs` | 1 | `glw_view_eval.c:7363` | Absolute value. |
| `clamp` | 3 | `glw_view_eval.c:7350` | `clamp(x, min, max)`. |
| `int` | 1 | `glw_view_eval.c:7349` | Truncate/coerce a value to integer. |
| `toggle` | 1 | `glw_view_eval.c:7367` | Flip a boolean property's value (idiom: `toggle($ui.sysinfo)`, `glwskins/flat/universe.view:10`). |
| `timeAgo` | 1 | `glw_view_eval.c:7369` | Format a unix timestamp as a relative "N minutes ago" string. |
| `RGBToString` | 1 | `glw_view_eval.c:7372` | Format an RGB float vector as a CSS-style color string. |
| `primaryColor` | 0 | `glw_view_eval.c:7339` | The widget's resolved primary color (class-specific, e.g. image dominant color). |

### String helpers

| name | nargs | anchor | semantics |
|---|---|---|---|
| `join` | -1 | `glw_view_eval.c:7351` | Join a variadic argument list into one string (with a separator argument). |
| `fmt` | -1 | `glw_view_eval.c:7352` | `printf`-style string formatting. |
| `_pl` | 3 | `glw_view_eval.c:7353` | Pluralization helper: `_pl(count, singular, plural)`. |
| `multiopt` | -1 | `glw_view_eval.c:7354` | Multi-option chooser idiom (settings-row style); stateful. |
| `makeUri` | 2 | `glw_view_eval.c:7355`, impl `glw_view_eval.c:5621-5640` | `makeUri(title, url)` — builds a `TOKEN_URI` (title+url pair) for use as a `navOpen()` target; `void` if either argument isn't a plain string. |
| `setDefaultFont` | 1 | `glw_view_eval.c:7358` | Set the default font path for the current subtree (idiom: `glwskins/flat/universe.view:1`). |

### Debug

| name | nargs | anchor | semantics |
|---|---|---|---|
| `trace` | 2 | `glw_view_eval.c:7329`, impl `glw_view_eval.c:5432-5460` | `trace(prefix, value)` — `TRACE()`s `value` to the log prefixed by `prefix` (which must be a string); an inline debug probe usable inside any expression. |
| `dumpDynamicStatements` | 0 | `glw_view_eval.c:7375` | **Debug-build only** (`#ifndef NDEBUG`, `glw_view_eval.c:7374-7376`) — dumps the widget's dynamic (per-frame re-evaluated) statement list to the log. Not present in a release/`NDEBUG` build; `mdev viewdoc --check` still expects it documented since this dev tree builds with `NDEBUG` undefined by default (see Coverage & gaps). |

## 7. `#import`ed style/theme convention (idiom, corpus-only)

`(idiom, unverified in core beyond the macro/style mechanism itself)`:
skins conventionally centralize shared macros and `style()`/`newstyle()`
blocks in a `theme.view` imported once per page (`#import
"skin://theme.view"`, `glwskins/flat/pages/list.view:1`) and per-page-type
style files (`#import "skin://styles/style_list.view"`,
`glwskins/flat/pages/list.view:2`). This layout convention itself is not
enforced by any C code — it's a skin-author idiom built entirely on top of
`#import` + `style`/`newstyle` (§5, §6 "Fundamentals").

## Coverage & gaps

Documented **in this file**: lexer tokens/operators, all 5 assignment
operators, `??`/ternary/boolean/comparison/arithmetic operators, the full
preprocessor (`#include`/`#import`/`#define`), the complete 90-entry
expression-function table (`funcvec[]`), and the 9 property scope roots.

Intentionally **not** documented here (tracked elsewhere or out of scope
per issue #88's boundaries):

- The **attribute table** (116 entries, `attribtab[]`) — see
  `glw-widget-catalog.md` §"Global attributes", not duplicated here.
- Per-widget-class attribute semantics and layout algorithms — see
  `glw-widget-catalog.md`.
- The RPN/stack-machine internals of the evaluator (`glw_view_eval_rpn0`
  and friends) — implementation detail, not something a `.view` author
  needs.
- GLW styles' CSS-like cascade/specificity rules beyond `style()`/
  `newstyle()` existing as functions — not fully traced.
- `src/event.c`'s full `actionnames[]` table (71 entries) is used by
  `onEvent()`'s first argument but reproduced in `glw-patterns.md`'s event
  section rather than here, to keep this file to language mechanics.
- Video/media-widget internals (`glw_video_*.c` backends) — see
  `glw-widget-catalog.md`'s `video` entry for the attribute surface only.
- `dumpDynamicStatements()` is a debug-build-only function; this doc does
  not track other `#ifdef`-gated code paths beyond noting this one.
