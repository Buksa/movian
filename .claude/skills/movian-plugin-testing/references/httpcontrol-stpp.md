# HTTP Control and STPP Diagnostics

Read this reference for a broad HTTP Control/STPP audit, live WebSocket
diagnostics, or agent-style inspection of a running Movian instance.

## Contents

- [Agent CLI](#agent-cli)
- [Safety model](#safety-model)
- [HTTP routing](#http-routing)
- [HTTP Control matrix](#http-control-matrix)
- [Event delivery differences](#event-delivery-differences)
- [STPP lifecycle and discovery](#stpp-lifecycle-and-discovery)
- [JSON STPP](#json-stpp)
- [Binary STPP](#binary-stpp)
- [Known protocol defects](#known-protocol-defects)
- [Prop debug reality](#prop-debug-reality)
- [Runtime audit recipe](#runtime-audit-recipe)
- [Source anchors](#source-anchors)

## Agent CLI

The scripts ported from the Codex skill live in `support/devtools/mdevlib/`
and run as modules from `support/devtools/` — they are the Movian equivalent
of an agent-oriented browser driver, keeping read/diagnostic commands
separate from explicit UI mutations and emitting structured JSON/JSONL.

Broad snapshot:

```bash
cd support/devtools
python3 -m mdevlib.movian_agent snapshot \
  --base-url "http://127.0.0.1:$port" \
  --full \
  --output /tmp/movian-agent/snapshot.json
```

Live prop events:

```bash
python3 -m mdevlib.movian_agent watch \
  --base-url "http://127.0.0.1:$port" \
  --seconds 10 \
  app.systemname \
  navigators.current.currentpage.model.metadata.title \
  navigators.current.currentpage.model.nodes
```

Explicit control commands:

```bash
python3 -m mdevlib.movian_agent open \
  --base-url "http://127.0.0.1:$port" \
  settings:network --expect-url settings:network

python3 -m mdevlib.movian_agent action \
  --base-url "http://127.0.0.1:$port" Home

python3 -m mdevlib.movian_agent prop \
  --base-url "http://127.0.0.1:$port" \
  global/navigators/current/currentpage/model/metadata/title
```

For ordinary launch/open/shot/stop, prefer `mdev` (see `movian-run`) — reach
for `movian_agent.py` when you need STPP watch, structured snapshots, or
explicit prop-debug toggling that `mdev` does not expose yet.

The collector intentionally exposes no restart, binary replacement, log
upload, or screenshot-upload command. Treat snapshot files and screenshots as
private until paths, URLs, credentials, cookies, and tokens are reviewed.

## Safety model

Classify every call before sending it:

| Class | Meaning | Examples |
|---|---|---|
| Read | Reads local state | `/api/diag`, static files |
| Active read | May touch file/network/cache backends | `/api/image` |
| Diagnostic mutation | Temporary debug/runtime bookkeeping | STPP connection, GC, prop debug flag |
| UI mutation | Changes current UI/application state | open, action, UTF-8, notify |
| Sensitive read | Can expose private data | logfile bodies, broad prop dumps |
| External write | Uploads data outside Movian | logfile pastebin, screenshot upload |
| Destructive | Stops or replaces the process | restart, replace |

Default to read-only snapshots. Use `--stpp` knowing that a connection
temporarily increments `global/stpp/remoteControlled`. Use `--gc` only on an
isolated or explicitly authorized instance.

Never call these during a capability probe:

- `/api/restart`;
- `/api/replace`;
- `/api/logfile/N?mode=pastebin`;
- `/api/screenshot` without `raw`.

## HTTP routing

HTTP handlers are registered with `http_path_add(path, ..., leaf)`
(`src/api/httpcontrol.c:678-699`). For GET/HEAD, a true leaf rejects a
remaining suffix. POST resolution does not repeat the same leaf check before
dispatch. Do not infer identical routing semantics across methods.

Most HTTP Control handlers ignore the method argument. The HTTP server
accepts GET, HEAD, and POST; POST requires `Content-Length`, buffers at most
16 MiB, and parses form fields only for
`application/x-www-form-urlencoded`.

A 200 or redirect proves transport only. Always verify the terminal effect
via props, screenshot, log, filesystem state, or process state.

### `/api/open` is GET-only in practice

`hc_open()` (`src/api/httpcontrol.c:49-65`) reads `url` via
`http_arg_get_req(hc, "url")`, which resolves query-string args regardless of
method — but the HTTP server resets the connection when a POST carries a
body here in practice. `mdev open`/`support/devtools/mdevlib/harness.py:
open_and_wait()` always issue `GET /api/open?url=<urlencoded>`; do the same
by hand rather than POSTing a form body to this endpoint.

## HTTP Control matrix

| Endpoint | Effect | Safety and verification |
|---|---|---|
| `/api/done` | Returns `OK`; does not exit | Read/no-op |
| `/api/image[?url=...]` | Runs backend image loader and returns coded image | Active read; verify type/hash |
| `/api/open?url=...` | Sends OPENURL to UI event sink (GET only — see above) | UI mutation; verify current page URL/title |
| `/api/openparameterized/<scheme>` | Builds `scheme:<JSON args>` and dispatches OPENURL | UI mutation; verify resulting URL/error |
| `/api/input/action/<Action>` | Sends action to UI event sink | UI mutation; verify prop/screenshot effect |
| `/api/input/utf8?str=...` | Sends Unicode events; byte 8 becomes Backspace/NavBack | UI mutation; needs focused field for effect proof |
| `/api/notifyuser` | Adds info/warning/error notification | UI mutation; verify notification prop/screenshot |
| `/api/diag` | Returns version and logfile links | Read |
| `/api/logfile/<N>` | Reads cached Movian log | Sensitive read; prefer HEAD or sanitized local log |
| `/api/logfile/<N>?mode=download` | Same log with attachment header | Sensitive read |
| `/api/logfile/<N>?mode=pastebin` | Posts whole log to `http://sprunge.us` | External exfiltration; never automate |
| `/api/replace` | Unlinks/replaces executable, mode 0777, then shutdown 13 | Destructive; never probe |
| `/api/ws/echo` | Echoes WebSocket opcode and bytes | Safe protocol test |
| `/` | Diagnostics/static root; old root can dispatch `?url=` | Read unless URL argument supplied |
| `/favicon.ico` | Reads dataroot icon | Read |
| `/api/static/<path>` | Reads `dataroot://res/static/<path>`; rejects `..` | Read |
| `/api/restart` | Calls `app_shutdown(13)`; conditionally registered | Destructive; never probe |

Screenshot endpoints are registered separately. Prefer `/api/screenshot/raw`
(`mdev shot`) or `/api/screenshot?raw=1`; do not call upload mode in CI or
private environments.

## Event delivery differences

`/api/open`, `/api/input/action`, and `/api/input/utf8` call `event_to_ui()`
(`src/event.c:541`). That sends only to:

```text
global/userinterfaces/ui/eventSink
```

The old root URL form and `/api/openparameterized` call `event_dispatch()`
(`src/event.c:553`). It first sends to `global/eventSink`, then routes
navigation/open events to `global/navigators/current/eventSink`; media,
playqueue, and volume actions take separate branches.

This difference explains why two OPENURL-looking endpoints can behave
differently during early UI startup. Wait for the UI and navigator props
before sending `/api/open`.

## STPP lifecycle and discovery

The WebSocket endpoint is `/api/stpp` (`src/api/stpp.c:1265`). Connection
initialization returns 403 when "Allow remote control" is disabled. The
setting defaults to enabled and is stored under `stpp/enablecontrollee`
(`src/api/stpp.c:1754`).

Each accepted connection (`stpp_init()` at `src/api/stpp.c:1213`):

1. allocates connection-local subscription/propref state;
2. increments `global/stpp/remoteControlled` (`stpp.c:1223`);
3. destroys subscriptions and exported proprefs on disconnect;
4. decrements `remoteControlled` (`stpp.c:1253`).

Propref `0` means the global root. Non-zero proprefs are exported for one
connection and usually one subscription. Do not cache them across child
deletion, unsubscribe, reconnect, or process restart.

Discovery uses STPP version 3 over IPv4 multicast `239.255.255.250:42000`.
Announcements include device id, role, system name, system type, and the
actual HTTP port. A controllee announces about every 20 seconds; discovered
devices expire after about 60 seconds.

## JSON STPP

JSON STPP is a text-frame protocol and does not perform HELLO. Sending
`[0,3,0]` as text is silently ignored.

Supported client commands are only:

| Command | Shape | Effect |
|---|---|---|
| Subscribe | `[1, id, propref, "dot.path"]` | Creates subscription |
| Unsubscribe | `[2, id]` | Destroys subscription and its exported proprefs |
| Set | `[3, propref, "dot.path", value]` | Sets int/string/float values |

Paths are dot-separated relative to propref `0`; omit `global.`. HTTP `*N`
aliases are not STPP path components. Subscribe to the parent directory,
recover child proprefs from add events, then address fields relative to
those proprefs.

JSON SET accepts only message field types mapped to signed integer, string,
and double. It does not expose binary SET void/toggle semantics.

Server notification shapes:

| Message | Meaning |
|---|---|
| `[4,id,value]` | Scalar/void value |
| `[4,id,["uri",a,b]]` | URI value |
| `[4,id,["dir"]]` | Directory value |
| `[5,id,before,[proprefs...]]` | Child add |
| `[6,id,[propref]]` | Child delete |
| `[7,id,propref,before]` | Child move |

Use a tolerant raw-frame recorder. A strict JSON decoder alone loses exported
proprefs when the vector-add defect below is triggered.

## Binary STPP

Binary STPP requires HELLO before all non-HELLO commands (`src/api/stpp.c:1017`
checks `cmd == STPP_CMD_HELLO`). Send bytes `00 03 00`. A valid server
response is a 19-byte binary frame:

```text
command=0, version=3, running_instance[16], flags=0
```

Command ids from `src/api/stpp.h`:

| ID | Command |
|---:|---|
| 0 | HELLO |
| 1 | SUBSCRIBE |
| 2 | UNSUBSCRIBE |
| 3 | SET |
| 4 | NOTIFY |
| 5 | EVENT |
| 6 | REQ_MOVE |
| 7 | WANT_MORE_CHILDS |
| 8 | SELECT |
| 9 | IMAGE_LOAD |
| 10 | IMAGE_REPLY |
| 11 | IMAGE_FAIL |
| 12 | IMAGE_CANCEL |

Binary SET supports string, int, float, toggle-int, and void. Binary EVENT
supports action vectors, OPENURL, PLAYTRACK, dynamic actions, and
audio/subtitle track selection. Binary-only commands also cover move,
pagination demand, selection, and image load/cancel.

Do not fuzz binary STPP against a user instance. Use an isolated debug
process and retain the exact frame plus crash/backtrace artifacts.

## Known protocol defects

### Malformed JSON vector add

`stpp_sub_json_add_childs()` (`src/api/stpp.c:182`) builds `"]]"` but appends
length `1`. Single-child adds use `snprintf` and are valid.

Runtime-verified examples:

```text
services             -> [5,1,0,[1,2,3,4]
settings.apps.nodes   -> [5,2,0,[5]
current page nodes    -> [5,3,0,[6]]   # valid single add
```

The agent collector records `raw`, `json_valid=false`, and the parse error.

### Binary parser result discarded

`stpp_binary()` (`src/api/stpp.c:1004`) returns `-1` for malformed/unknown
messages, but `stpp_input()` (`src/api/stpp.c:1185`) ignores the return and
always returns `0`. The WebSocket layer therefore cannot act on the parser
failure through this callback result.

### Assert on remote length data

`decode_string_vector()` (`src/api/stpp.c:679`) subtracts untrusted length
bytes and asserts that the remaining length is non-negative. A malformed
binary frame can abort a debug build. Treat this as a core hardening issue,
not a probe recipe.

## Prop debug reality

`POST /api/prop/<path> debug=on/off` toggles `PROP_DEBUG_THIS` and returns
`OK` (`src/prop/prop_http.c:132-142`). In this tree, source search finds the
flag only in `prop_http.c` and its definition; prop core has no reader that
emits change logs. Runtime navigation after `debug=on` produced no
prop-debug trace.

Therefore:

- treat `OK` as flag-write acceptance, not proof of logging;
- use STPP `watch` for value/child changes;
- use `/api/prop` subscriber anchors for consumers;
- use narrow `prop.print`, `Page.dump()`, or `Item.dump()` for tree state;
- use `--debug-glw` for GLW focus/event routing.

The agent CLI keeps `prop-debug` explicit because it is a diagnostic
mutation, but callers must not promise log output on this branch.

## Runtime audit recipe

1. Check for existing Movian processes (basename-anchored check — see
   `movian-run`).
2. Launch the target checkout from its root with an isolated profile/cache
   (`mdev run`).
3. Parse the actual port from the log (`mdev` does this automatically).
4. Capture a baseline snapshot without GC.
5. Open a reversible core route such as `settings:network` (`mdev open`).
6. Capture a snapshot with `--http-surface --stpp`.
7. Use `watch` on scalar, vector-backed, and page-node directories.
8. Test binary HELLO and WebSocket echo.
9. Test image proxy with a known local PNG.
10. Use HEAD for logfile capability; do not print the body.
11. Test notification with a short timeout and screenshot before/after.
12. Run GC only when intended, then capture post-GC deltas.
13. Return to `page:home`, stop only the test-owned instance (`mdev stop`),
    and preserve sanitized artifacts.

Do not mark an endpoint runtime-verified when policy forbids invoking its
effect. Record `blocked-by-policy` for restart, replace, pastebin, and
upload surfaces.

## Source anchors

Re-checked against this checkout for this issue (all resolve at the cited
line ±5 unless noted otherwise):

- `src/api/httpcontrol.c:49-65`: `hc_open` (open);
- `src/api/httpcontrol.c:678-699`: `httpcontrol_init` registrations;
- `src/networking/http_server.c`: GET/POST routing (method table at
  `:130-132`, POST body handling around `:771-855`);
- `src/event.c:541`: `event_to_ui`; `:553`: `event_dispatch`;
- `src/api/stpp.c:182`: `stpp_sub_json_add_childs`;
- `src/api/stpp.c:679`: `decode_string_vector`;
- `src/api/stpp.c:1004`: `stpp_binary`; `:1017`: HELLO check; `:1185`:
  `stpp_input`;
- `src/api/stpp.c:1213`: `stpp_init`; `:1223`/`:1253`: `remoteControlled`
  inc/dec; `:1265`: `/api/stpp` registration;
- `src/api/stpp.c:1754`: `enablecontrollee` setting;
- `src/prop/prop_http.c:132-142`: prop POST/debug flag.
