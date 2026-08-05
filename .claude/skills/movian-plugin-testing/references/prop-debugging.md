# Prop debugging: core source anchors

`/api/prop` usage, the named-path mutation trap, `prop.print` and the
ECMAScript stats/GC recipes moved to the `movian:verify` skill
(`references/prop-debugging.md`) in movian-plugin-sdk. What stays here anchors
into this repo's C sources, which only a core developer can act on.

## Subscriber Source Anchors

Subscriber source recording is enabled when `NDEBUG` is not defined:

```text
src/prop/prop_defs.h
#ifndef NDEBUG
#define PROP_DEBUG
#define PROP_SUB_RECORD_SOURCE
#endif
```

C subscriptions capture the call site through:

```text
src/prop/prop.h:192-195
prop_subscribe(...) -> prop_subscribe_ex(__FILE__, __LINE__, ...)
```

`prop_subscribe_ex()` stores the values in `hps_file`/`hps_line` at
`src/prop/prop_core.c:3195-3196`.

GLW replaces the native evaluator location with the original `.view` token
source using `PROP_TAG_SOURCE` (`src/ui/glw/glw_view_eval.c`). This is why
output can point directly to a skin line:

```text
Value Subscribers:
.//glwskins/flat/pages/list.view:74
src/navigator.c:698
Canonical Subscribers:
.//glwskins/flat/pages/list.view:74
src/navigator.c:698
```

Interpretation:

- Canonical prop: resolved path without following symlinks.
- Value prop: actual value source after following symlinks.
- When no symlink separates them, the same subscription appears in both
  lists.
- A prop with no subscribers may be dead for the current view, but it may
  still be consumed later or by another page. Treat absence as evidence, not
  proof.

Required reporting shape:

```text
Runtime: title has list.view:74 and navigator.c:698 subscribers
Source: list.view:74 reads $self.model.metadata.title
Source: navigator.c:698 installs nav_page_title_set callback
Effect: title drives PageHeader and bookmark metadata
```

