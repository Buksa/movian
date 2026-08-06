# Movian runtime API introspector diff

The committed snapshot in [runtime-api.json](runtime-api.json) is the payload extracted from the log marker `MOVIAN_API_INTROSPECTOR_JSON=`.

## Run and extraction

```text
mdev run -p support/devtools/api-introspector
mdev open introspect:page
mdev log
```

**Opening the route is part of the capture, not an optional extra.** The plugin emits twice: once at load, where the tier3 page and its items have not been attempted, and once from the route callback, where they have. Only the second is complete, and only the second carries `MOVIAN_API_INTROSPECTOR_JSON=` — the load-time payload is marked `MOVIAN_API_INTROSPECTOR_PARTIAL_JSON=` and carries `tier3PageOpened: false`. Both markers were identical until this was corrected, so a run that followed the earlier two-step procedure extracted the partial payload and `gen.py --check` accepted it; it now refuses one that says so.

- Modules required: **52** (the 52 `declare module` blocks in `generated/movian-api.d.ts`).
- Require failures: **none** (`loadErrors` is `{}`).
- `movian/settings.globalSettings(...)` call failure: **none** (`globalSettingsError` is `null`).
- The key comparison below is the union of each module's `Object.keys` and the keys from the one prototype level recorded by the plugin. `export *` declarations are resolved before comparison.

## Key-surface diff before the dynamic call

Every declared module is listed. An em dash means an empty difference.

| Module | Runtime has, bundle lacks | Bundle has, runtime lacks |
| --- | --- | --- |
| `fs` | — | — |
| `http` | — | — |
| `https` | — | — |
| `movian/html` | — | — |
| `movian/http` | — | — |
| `movian/itemhook` | — | — |
| `movian/page` | — | — |
| `movian/popup` | — | — |
| `movian/prop` | — | — |
| `movian/service` | — | — |
| `movian/settings` | — | — |
| `movian/sqlite` | — | — |
| `movian/store` | — | — |
| `movian/subtitles` | — | — |
| `movian/videoscrobbler` | — | — |
| `movian/xml` | — | — |
| `movian/xmlrpc` | — | — |
| `native/crypto` | — | — |
| `native/faprovider` | — | — |
| `native/fs` | — | — |
| `native/gumbo` | — | — |
| `native/hook` | — | — |
| `native/htsmsg` | — | — |
| `native/io` | — | — |
| `native/kvstore` | — | — |
| `native/metadata` | — | — |
| `native/misc` | — | — |
| `native/popup` | — | — |
| `native/prop` | — | — |
| `native/route` | — | — |
| `native/service` | — | — |
| `native/sqlite` | — | — |
| `native/string` | — | — |
| `native/subtitle` | — | — |
| `native/websocket` | — | — |
| `querystring` | — | — |
| `url` | — | — |
| `websocket` | — | — |
| `showtime/html` | — | — |
| `showtime/http` | — | — |
| `showtime/itemhook` | — | — |
| `showtime/page` | — | — |
| `showtime/popup` | — | — |
| `showtime/prop` | — | — |
| `showtime/service` | — | — |
| `showtime/settings` | — | — |
| `showtime/sqlite` | — | — |
| `showtime/store` | — | — |
| `showtime/subtitles` | — | — |
| `showtime/videoscrobbler` | — | — |
| `showtime/xml` | — | — |
| `showtime/xmlrpc` | — | — |

**Result:** all 52 initial runtime key surfaces match the generated bundle; no module has an initial key-only difference.

## `movian/settings` method-induced surface

The required method call was `settings.globalSettings('runtime-api-introspector', 'Runtime API introspector', null, 'Runtime API surface inspection')`. The before/after rows are the same module object; the `showtime/settings` row is an explicit post-call alias dump.

| Module/stage | Runtime has, bundle lacks | Bundle has, runtime lacks |
| --- | --- | --- |
| `movian/settings` before call | — | — |
| `movian/settings` after `globalSettings(...)` | createAction, createBool, createDivider, createInfo, createInt, createMultiOpt, createString, destroy, dump, getvalue, id, nodes, properties, setvalue | — |
| `showtime/settings` after call | — | — |

The only key-surface difference is the 14 runtime-only keys on `movian/settings` after the method call: `globalSettings` assigns `this.__proto__ = sp` (`res/ecmascript/modules/movian/settings.js`), where `sp` defines the setting methods, then assigns `id`, `nodes`, `getvalue`, `setvalue`, and `properties`.

**This is the capture, not the current state.** The rows above record what the bundle looked like when the plugin ran, and that gap is what the capture existed to find. The bundle now declares all 14 — hoisted onto the module for the plain-call form, and gathered into the `sp` instance interface for the constructed form, since `this.__proto__ = sp` serves both and both in-repo callers construct.

The comparison is no longer maintained by hand here. `gen.py --check` recomputes it against `runtime-api.json` on every run and fails on drift, so this document is a narrative of one capture rather than the authority on agreement:

```text
RUNTIME ORACLE CROSS-CHECK OK
counts: match 242, drift 0, missing-modules 0, plugin-supplied 2, oracle-unreachable 35
```

These counts are recomputed from the committed inputs, not transcribed: an earlier revision of this document quoted `oracle-unreachable 31` and no plugin-supplied bucket, which no run had produced since the bucket was added. The 35 unreachable members are members no tier could construct.

`missing-modules` closes the largest part of #166. Every other comparison walks the *artifact's* module list, so a module the runtime observed and the artifact dropped entirely was invisible — deleting `fs` from the artifact left the check reporting `ok`, and an artifact with **no modules at all** passed against the full oracle. The check now resolves `showtime/x` to its `movian/x` alias block and fails on any oracle module the artifact cannot account for: dropping `fs` reports 1, dropping `movian/prop` reports 2 (the alias goes with it), emptying the list reports 52. What remains open under #166 is the member-level direction: a large unreachable set still passes.

## Function-length observations

The snapshot also records the runtime function `length` for every callable key. Comparing generated `@arity` annotations with the flattened runtime surface finds **188** mismatches across **20** modules; each mismatch is a native callable whose runtime `length` is `0`, while the generated annotation is the value shown below. This is callable metadata, not an add/remove key difference.

| Module | Generated `@arity` → runtime `length` |
| --- | --- |
| `movian/prop` | atomicAdd (2→0), create (1→0), deleteChild (2→0), deleteChilds (1→0), destroy (1→0), enumerate (1→0), getChild (2→0), getName (1→0), getValue (1→0), has (2→0), haveMore (2→0), isSame (2→0), isValue (1→0), isZombie (1→0), link (2→0), makeUrl (1→0), moveBefore (2→0), nodeFilterAddPred (6→0), nodeFilterCreate (2→0), nodeFilterDelPred (2→0), print (1→0), release (1→0), select (1→0), sendEvent (3→0), set (3→0), setClipRange (3→0), setParent (2→0), setRichStr (3→0), subscribe (3→0), tagClear (2→0), tagGet (2→0), tagSet (3→0), unlink (1→0), unloadDestroy (1→0) |
| `native/crypto` | hashCreate (1→0), hashFinalize (1→0), hashUpdate (2→0) |
| `native/faprovider` | closeRespond (1→0), openRespond (3→0), readRespond (2→0), redirectRespond (3→0), register (2→0), setSize (2→0), statRespond (5→0) |
| `native/fs` | basename (1→0), copyfile (2→0), dirname (1→0), fsize (1→0), ftruncate (2→0), ftrunctae (2→0), mkdirs (2→0), open (3→0), read (5→0), readdir (1→0), rename (2→0), rmdir (1→0), unlink (1→0), write (5→0) |
| `native/gumbo` | findByClassName (2→0), findById (2→0), findByTagName (2→0), nodeAttributes (1→0), nodeChilds (2→0), nodeName (1→0), nodeTextContent (1→0), nodeType (1→0), parse (1→0) |
| `native/hook` | register (2→0) |
| `native/htsmsg` | createFromXML (1→0), enumerate (1→0), get (2→0), getName (2→0), length (1→0), print (1→0) |
| `native/io` | httpInspectorCreate (3→0), httpReq (3→0), probe (2→0), xmlrpc (-1→0) |
| `native/kvstore` | getBoolean (4→0), getInteger (4→0), getString (3→0), set (4→0) |
| `native/metadata` | bindPlayInfo (2→0), videoMetadataBind (3→0) |
| `native/misc` | cacheGet (2→0), cachePut (4→0), selectView (1→0) |
| `native/popup` | getAuthCredentials (5→0), message (3→0), notify (3→0), textDialog (3→0), webpopup (3→0) |
| `native/prop` | atomicAdd (2→0), create (1→0), deleteChild (2→0), deleteChilds (1→0), destroy (1→0), enumerate (1→0), getChild (2→0), getName (1→0), getValue (1→0), has (2→0), haveMore (2→0), isSame (2→0), isValue (1→0), isZombie (1→0), link (2→0), makeUrl (1→0), moveBefore (2→0), nodeFilterAddPred (6→0), nodeFilterCreate (2→0), nodeFilterDelPred (2→0), print (1→0), release (1→0), select (1→0), sendEvent (3→0), set (3→0), setClipRange (3→0), setParent (2→0), setRichStr (3→0), subscribe (3→0), tagClear (2→0), tagGet (2→0), tagSet (3→0), unlink (1→0), unloadDestroy (1→0) |
| `native/route` | backendOpen (3→0), create (2→0), test (1→0) |
| `native/service` | create (6→0), enable (2→0) |
| `native/sqlite` | changes (1→0), create (1→0), lastErrorCode (1→0), lastErrorString (1→0), lastRowId (1→0), query (-1→0), step (1→0), upgradeSchema (2→0) |
| `native/string` | durationToString (1→0), entityDecode (1→0), isUtf8 (1→0), paramEscape (1→0), parseTime (1→0), parseURL (2→0), pathEscape (1→0), queryStringSplit (1→0), resolveURL (2→0), utf8FromBytes (2→0) |
| `native/subtitle` | addItem (8→0), addProvider (3→0) |
| `native/websocket` | clientCreate (3→0), clientSend (2→0), serverCreate (2→0) |
| `showtime/prop` | atomicAdd (2→0), create (1→0), deleteChild (2→0), deleteChilds (1→0), destroy (1→0), enumerate (1→0), getChild (2→0), getName (1→0), getValue (1→0), has (2→0), haveMore (2→0), isSame (2→0), isValue (1→0), isZombie (1→0), link (2→0), makeUrl (1→0), moveBefore (2→0), nodeFilterAddPred (6→0), nodeFilterCreate (2→0), nodeFilterDelPred (2→0), print (1→0), release (1→0), select (1→0), sendEvent (3→0), set (3→0), setClipRange (3→0), setParent (2→0), setRichStr (3→0), subscribe (3→0), tagClear (2→0), tagGet (2→0), tagSet (3→0), unlink (1→0), unloadDestroy (1→0) |

The native declarations carry the generated arities; the runtime values are in `runtime-api.json`. CommonJS callable arities have no mismatch in this run. The native mismatch exists because the runtime exposes native functions with Duktape's zero-argument function metadata, while the generated `@arity` is source/metadata-derived.

## Findings

1. **Static key surface:** no initial module differs from the generated declarations when one prototype level and `export *` inheritance are included; `movian/prop` is the important control case: it inherits `native/prop` through `exports.__proto__` and shadows `global` with an own property.
2. **Runtime-only dynamic surface:** `movian/settings` gains the 14 keys listed above only after it is invoked as a method. A source-shape-only generator cannot observe that receiver mutation — which is why this capture exists. The generator now scans the `this.__proto__ = sp` idiom and declares the surface for both call forms, so the gap this row records is closed; the capture remains the evidence that it was real.
3. **Callable metadata:** native function `length` values do not reproduce generated `@arity` values, even though the names are present.
