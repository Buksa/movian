# Prop and ECMAScript Diagnostics

Read this reference when inspecting `/api/prop`, subscriber source anchors,
`prop.print`, page dumps, or ECMAScript memory/resources.

## Surface Safety

| Surface | Effect |
|---|---|
| `GET /api/ecmascript/stats` | Read-only context/resource snapshot |
| `GET /api/ecmascript/gc` | Runs GC in every ECMAScript context |
| `GET /api/prop/<path>` | Reads a prop, but a mistyped named path can materialize void props |
| `POST /api/prop/<path> action=...` | Sends an external event |
| `POST /api/prop/<path> debug=on/off` | Mutates `PROP_DEBUG_THIS`; the audited branch has no log consumer |
| `prop.print(prop)` | Recursively writes prop contents to stderr |

Do not call `gc`, POST actions, or debug toggles while claiming a read-only
audit. Treat `prop.print` output and stats paths as local/private artifacts
until sanitized.

## /api/prop Source Flow

Registration:

```text
src/prop/prop_http.c:274
http_path_add("/api/prop", NULL, hc_prop, 0)
```

GET resolution:

```text
hc_prop()
  -> prop_from_path()
  -> strvec_split(path, "/")
  -> prop_get_by_name(..., follow_symlinks=1)
  -> prop_subfind(..., allow_indexing=1)
  -> emit_value() or directory listing
  -> subscriber source lists
```

Anchors verified in this checkout (`src/prop/prop_http.c`, 280 lines total):

- `:28` `prop_from_path()` — slash-path conversion (`strvec_split` + `prop_get_by_name`).
- `:48` `emit_value()` — value serialization by `hp_type`.
- `:94` `hc_prop()` — HTTP handler entry point.
- `:121` `HTTP_CMD_POST` branch — `action=`/`debug=` handling.
- `:146` `HTTP_CMD_GET` branch.
- `:164` directory-listing loop — unnamed children get the `*N` alias.
- `:214` `#ifdef PROP_SUB_RECORD_SOURCE` — Value/Canonical Subscribers dump.
- `src/prop/prop_core.c:2682` `prop_subfind()` — symlink/path/index traversal.
- `src/prop/prop_core.c:2792` `prop_get_by_name0()` / `:2796` `prop_get_by_name()` — root resolution.
- `src/prop/prop_core.c:2912` `prop_subscribe_ex()` — subscription construction.

Always re-check lines in the target checkout before publishing them —
`prop_http.c` above matched the previously-recorded anchors exactly at every
line cited (re-verified for this issue).

### Named paths can mutate the tree

`prop_from_path()` enables indexing and follows symlinks. In
`prop_subfind()`, an absent named child can be created through `prop_create0`.
Therefore a typo such as `global/navigators/curent/...` is not guaranteed to
be a harmless 404.

Safe inspection order:

1. Read the known parent.
2. Copy the child name from the response.
3. Descend one segment at a time.
4. Use `*N` only after enumerating the current parent because indices move
   when children are added, removed, or reordered.

### Output types

| Text | Meaning |
|---|---|
| `is a 0` / `is a 1` | Integer or boolean |
| `is a 3.140000` | Float |
| `is a value` | String |
| `title uri` | URI value |
| `(void)` | Prop exists without a value |
| `(zombie)` | Deleted/stale prop |
| `(proxy)` | Proxy prop |
| `directory` | Prop has children |

Plain-text directory output lists child names. With `Accept: text/html`, the
handler emits links and inline values.

### POST modes

```bash
curl -X POST -d 'action=Activate' \
  "$BASE/api/prop/global/navigators/current/currentpage/model/nodes/*0/eventSink"

curl -X POST -d 'debug=on' \
  "$BASE/api/prop/global/navigators/current/currentpage/model/loading"

curl -X POST -d 'debug=off' \
  "$BASE/api/prop/global/navigators/current/currentpage/model/loading"
```

An action response proves only that the event was accepted. Re-read the
target props to prove the effect. Any `debug` value other than `on` clears
`PROP_DEBUG_THIS` (`src/prop/prop_http.c:132-142`).

Source search in this tree finds `PROP_DEBUG_THIS` only in the HTTP toggle
(`prop_http.c:135,137`) and its definition — there is no reader that emits a
change log when it is set. Runtime mutation after `debug=on` produces no
prop-change log. Use STPP `watch`, subscriber anchors, or the narrow dump
helpers below for actual diagnostics; do not treat `OK` as proof that prop
logging is active.

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

## prop.print and dump wrappers

The ECMAScript binding is registered in `src/ecmascript/es_prop.c`:

```text
prop.print -> es_prop_print_duk (es_prop.c:117) -> prop_print_tree(prop, 1) (es_prop.c:120)
```

Implementation anchors:

- `src/ecmascript/es_prop.c:117` — Duktape binding (`es_prop_print_duk`).
- `src/ecmascript/es_prop.c:1089` — `{ "print", es_prop_print_duk, 1 }` registration.
- `src/prop/prop_core.c:5895-5896` — `prop_print_tree0()`, the recursive printer.
- `src/prop/prop_core.c:6000-6001` — `prop_print_tree()`, the locked entrypoint.

The output goes to stderr and includes name and pointer, refcount/xref,
multi-subscription flags, symlink/target information, and recursive child
values and types.

Flag `1` follows links. Subscriber details require internal flags `2` and
`4`, which the JS binding does not expose. Use `/api/prop` for subscriber
anchors.

Convenience wrappers already exist:

- `Item.dump()` in `res/ecmascript/modules/movian/page.js`.
- `Page.dump()` in the same module.
- settings group `dump()` in `res/ecmascript/modules/movian/settings.js`.

Dump only the narrow page/item/settings subtree under investigation. Never
dump `prop.global` casually: output can be very large and may contain
credentials, tokens, URLs, or user data.

## ECMAScript stats and GC

Registration in `src/ecmascript/es_stats.c`:

```text
/api/ecmascript/stats -> dumpstats()   (es_stats.c:94, registered es_stats.c:153)
/api/ecmascript/gc    -> dogc()        (es_stats.c:121, registered es_stats.c:154)
```

Stats reports per context: load path; current and peak memory; rooted
ECMAScript objects; active native instances by class; permanent resources
such as routes, hooks, subscriptions, inspectors, and services.

GC obtains every context, locks it, calls `duk_gc()`, and returns `OK`.

### Leak-check recipe

1. Capture stats.
2. Exercise the target route/action N times.
3. Capture stats again.
4. Invoke GC only when authorized.
5. Capture post-GC stats.
6. Repeat multiple cycles.
7. Compare current memory, rooted object count, native instance counts, and
   permanent resource count.

Do not use peak memory as a leak assertion; peak is not expected to decrease.
One small post-GC memory change is not conclusive.

### Structured snapshot

The scripts ported from the Codex skill now live in
`support/devtools/mdevlib/` and run as modules from `support/devtools/`
(they are not yet wired into a dedicated `mdev` subcommand):

```bash
cd support/devtools

# broad agent-oriented inspection (HTTP surface + STPP)
python3 -m mdevlib.movian_agent snapshot \
  --base-url "http://127.0.0.1:$port" \
  --http-surface --stpp \
  --output /tmp/movian-agent/snapshot.json

# lightweight prop/ECMAScript-only snapshot
python3 -m mdevlib.movian_diag_snapshot \
  --base-url "http://127.0.0.1:$port" \
  --output /tmp/movian-diag.json

python3 -m mdevlib.movian_diag_snapshot \
  --base-url "http://127.0.0.1:$port" \
  --gc \
  --output /tmp/movian-diag-after-gc.json
```

`$port` is whatever `mdev run`/`mdev props`/`mdev log` reports for your
instance (or read it from `/tmp/mdev/<name>/state.json`). These scripts
record structured page props, subscriber anchors, context counts, and
optional GC deltas without embedding full raw logs or response bodies.
