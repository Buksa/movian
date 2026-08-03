# Movian runtime API introspector diff

The committed snapshot in [runtime-api.json](runtime-api.json) is the payload extracted from the unique log marker `MOVIAN_API_INTROSPECTOR_JSON=`.

## Run and extraction

```text
mdev run -p support/devtools/api-introspector
mdev log
```

- Modules required: **52** (the 52 `declare module` blocks in `generated/movian-api.d.ts:5-542`).
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

The only key-surface difference is the 14 runtime-only keys on `movian/settings` after the method call: `globalSettings` assigns `this.__proto__ = sp` (`res/ecmascript/modules/movian/settings.js:266-269`), where `sp` defines the setting methods (`:46-259`), then assigns `id`, `nodes`, `getvalue`, `setvalue`, and `properties` (`:274-301`). The generated declaration contains only `globalSettings` and `kvstoreSettings` (`generated/movian-api.d.ts:90-100`).

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

The native declarations and their generated arities occupy `generated/movian-api.d.ts:147-463`; the runtime values are in `runtime-api.json`. CommonJS callable arities have no mismatch in this run. The native mismatch exists because the runtime exposes native functions with Duktape's zero-argument function metadata, while the generated `@arity` is source/metadata-derived.

## Findings

1. **Static key surface:** no initial module differs from the generated declarations when one prototype level and `export *` inheritance are included; `movian/prop` is the important control case (`generated/movian-api.d.ts:72-82`).
2. **Runtime-only dynamic surface:** `movian/settings` gains the 14 keys listed above only after it is invoked as a method. A source-shape-only generator cannot observe that receiver mutation.
3. **Callable metadata:** native function `length` values do not reproduce generated `@arity` values, even though the names are present.
