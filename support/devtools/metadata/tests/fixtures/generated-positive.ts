// Type-checked against generated/movian-api.d.ts -- NOT the hand-written
// reference under tests/reference/. Every line below is a call site copied
// from a plugin that ships today, with the originating file named, so a
// regression in the generated artifact fails here instead of failing in
// somebody's editor.
//
// This is a shape check, not a calibration: the generated declarations are
// deliberately `any`-typed, so what it can prove is that a real call RESOLVES
// -- the member is exported, and the signature accepts the arity the plugin
// passes. That is exactly what the two shipped defects broke.

import prop = require('movian/prop');
import xmlrpc = require('movian/xmlrpc');
import http = require('movian/http');
import page = require('movian/page');
import service = require('movian/service');
import settings = require('movian/settings');
import sqlite = require('movian/sqlite');
import videoscrobbler = require('movian/videoscrobbler');

// Legacy spelling: the loader rewrites showtime/* to movian/* at require
// time, so plugins written against the old names must resolve too.
import legacyProp = require('showtime/prop');
import legacyPage = require('showtime/page');

// movian/prop -- the module inherits native/prop through
// `exports.__proto__ = np`, and its own members have to survive that.
// movian-plugin-HDRezka/service.js:81, movian-plugin-trakt/src/auth.js:32
const root = prop.createRoot('example');
// movian-plugin-HDRezka/utils/resume/nativePlayInfo.js:84 -- called with no
// argument, which is why the parameters are emitted optional.
const anonymousRoot = prop.createRoot();
// m7-jellyfin/src/navigator.js:10, movian-plugin-trakt/src/auth.js:44,
// m7-jellyfin/src/i18n.js:13 -- `prop.global` is an own property holding a
// proxied prop object; the inherited `native/prop.global` is a function, and
// resolving to that one made every property access below an error.
const navigators = prop.global.navigators;
const popups = prop.global.popups;
const language = prop.global.i18n.iso639_1.toString();
// movian-plugin-HDRezka/service.js:60 -- assignment through the proxy.
prop.global.example.gridColumns = 3;
// movian-plugin-HDRezka/utils/ui.js:75
prop.subscribeValue(root, (value: any) => { void value; }, null);
// m7-jellyfin/libs/movian/prop.js.examples.js
const proxied = prop.makeProp(root);
// Inherited from native/prop, and still reachable after the local members are
// exported explicitly.
prop.setParent(root, prop.global.popups);
prop.destroy(root);

// movian/xmlrpc -- declares no formal parameters and reads arguments[0],
// arguments[1] and the variadic tail from index 2.
// m7-jellyfin/libs/movian/xmlrpc.js.examples.js:5
const rpc = xmlrpc.call(
    'https://example.test/xmlrpc',
    'sample.method',
    'first',
    42);

// The remaining modules pin the ordinary CommonJS emission so a change to the
// signature shape cannot pass unnoticed either.
const response = http.request('https://example.test/api');
const searcher = new page.Searcher('Example', 'icon.png', () => { });
const route = new page.Route('example:(.*)', () => { });
service.create('Example', 'example:start', 'video', true, 'icon.png');
// movian/settings -- `this.__proto__ = sp` serves both call forms, so both
// have to be declared. Constructed is what the two in-repo callers do
// (res/ecmascript/legacy/api-v1.js:140, res/ecmascript/modules/movian/
// page.js:197); the instance carries the shared surface plus what the
// initializer assigns onto the receiver.
const globals = new settings.globalSettings(
    'example', 'Example', 'icon.png', 'Description');
const kvstore = new settings.kvstoreSettings(null, 'example:url', 'domain');
globals.createBool('flag', 'Flag', false, () => { }, true);
globals.createString('name', 'Name', '', () => { }, true);
const settingsId = globals.id;
const settingsNodes = kvstore.nodes;
// Called plainly it mutates the module receiver instead, which is why the
// same members are also reachable off the module itself.
settings.globalSettings('example', 'Example', 'icon.png', 'Description');
settings.createBool('module-flag', 'Flag', false, () => { }, true);

// movian/sqlite -- `DB.prototype.query` names no parameter and forwards
// everything in `arguments` (sqlite.js:12-19), so a zero-argument method
// rejected every real query.
const db = new sqlite.DB('example');
const rows = db.query('SELECT ? AS value', 1);
db.query('SELECT 1');

// movian/http -- the synchronous form returns the response; the callback
// form dispatches and returns nothing (http.js:93-101). Only the first may
// be dereferenced, which the negative fixture pins.
const syncBody = http.request('https://example.test/api', {}).toString();
// Error-first: the module calls `callback(err, null)` on failure and
// `callback(null, new HttpResponse(res))` on success, so the response is the
// SECOND argument. Typing it first made this callback's `err` a response.
// The response is `HttpResponse | null` -- http.js:96 calls
// `callback(err, null)` on the failure path. A plugin must check it, and this
// fixture must not model the crash it is meant to prevent.
http.request('https://example.test/api', {}, (err: any, res) => {
    if (err || res === null) { return; }
    void res.statuscode;
});
// Pins the ORDER rather than the presence. The rest parameter types every
// position after the response as `any`, so dereferencing the second argument
// passes under either ordering -- annotating the FIRST one is what
// discriminates: under the old response-first signature `number` is not
// assignable to `HttpResponse` and strictFunctionTypes rejects it.
http.request('https://example.test/api', {}, (code: number, res: any) => {
    void code; void res;
});

// Slots a plugin fills and the module only reads through a
// `typeof this.X === 'function'` guard. Optional on purpose: nothing forces a
// plugin to set them, so requiring them would invent errors the runtime does
// not have. From plugin_examples/async_page_load/async_page_load.js:27 and
// plugin_examples/videoscrobbling/videoscrobbling_example.js:53-70.
// `paged` is deliberately un-annotated: the Route callback is narrowed to
// `Page`, and annotating it `any` here would make this block pass without
// testing the interface at all.
new page.Route('paged:(.*)', (paged) => {
    paged.asyncPaginator = () => { };
    paged.reorderer = (item: any, before: any) => { void item; void before; };
    // `paginator` is a real accessor from Object.defineProperties, not a
    // guard-only hook, so it must NOT have become optional.
    paged.paginator = () => true;
});
// The append methods return a real `Item`, so its own members resolve --
// this is what makes an invented one an error rather than a silent no-op.
new page.Route('appended:(.*)', (p) => {
    p.appendItem('u', 'directory', {}).addOptAction('t', () => { }, 's');
    p.appendPassiveItem('label', {}, {}).disable();
    p.appendAction('t', () => { }).enable();
});
// plugin_examples/videoscrobbling/videoscrobbling_example.js:51 -- no arguments.
const scrobbler = new videoscrobbler.VideoScrobbler();
scrobbler.onstart = (data: any) => { void data; };
scrobbler.onpause = (data: any) => { void data; };
scrobbler.onresume = (data: any) => { void data; };
scrobbler.onstop = (data: any) => { void data; };

// Legacy aliases resolve to the same surface, local members included.
const legacyRoot = legacyProp.createRoot('legacy');
const legacyRoute = new legacyPage.Route('legacy:(.*)', () => { });

void anonymousRoot; void navigators; void popups; void language;
void proxied; void rpc; void response; void searcher; void route;
void globals; void db; void legacyRoot; void legacyRoute;
// Globals. `console` and the timer family are installed on the global object
// by es_create_env (src/ecmascript/ecmascript.c), and `Plugin` by
// ecmascript_plugin_load -- none of them were declared, so 24 uses across the
// example corpus reported a false "cannot find name". See #169.
console.log('example', 1);
console.error('example');
console.warn('example');
const timer = setTimeout(() => { }, 1000);
clearTimeout(timer);
clearInterval(setInterval(() => { }, 1000));
const pluginId: string = Plugin.id;
const apiVersion: number = Plugin.apiversion;
const versionInt: number = Core.currentVersionInt;
const deviceId: string = Core.deviceId;
// The remaining members of the two global objects. Declaring a global and
// never touching it leaves it deletable with every gate green -- measured:
// removing Core.sleep, Core.currentVersionString, Core.loadPath or
// Core.storagePath from the artifact left the whole checker at exit 0, while
// removing console.log failed it. Half the surface this commit added was
// ungated until these lines existed.
Core.compile('1 + 1');
Core.sleep(1);
Core.randomBytes(16);
Core.resourceDestroy(null);
const timestamp: number = Core.timestamp();
const versionString: string = Core.currentVersionString;
const pluginUrl: string = Plugin.url;
const pluginManifest: string = Plugin.manifest;
// `loadPath` and `storagePath` are assigned under `if(loaddir != NULL)` and
// `if(storage != NULL)` (ecmascript.c), so they are emitted optional. The
// annotation is what pins that: under a required emission `string | undefined`
// is still assignable, but the negative fixture's mirror line stops erroring.
// Read together they pin the optionality in both directions, the way
// Plugin.path already was.
const loadPath: string | undefined = Core.loadPath;
const storagePath: string | undefined = Core.storagePath;
// Same shape for Plugin.path (`if(ec->ec_path)`). Its optionality was already
// pinned by the negative fixture, but nothing here referenced it, so the
// member could be deleted outright and only the negative side would notice.
const pluginPath: string | undefined = Plugin.path;
// Duktape's own globals, not Movian's. `print` is the one the plugin_examples
// audit got wrong: 16 uses were filed as example rot because no module
// exports it. This repo's own api-introspector calls it at introspector.js:985
// to emit the capture gen.py --check diffs against, so the corpus was right
// and the audit was wrong. See #169.
print('example');
print('example', 1, {});
// The call form, not the `import x = require(...)` syntax above: that is
// TypeScript syntax and resolves without any `require` declaration at all, so
// the imports do not exercise this global. Plugins written for the runtime
// call it plainly.
require('movian/prop');
void pluginId; void apiVersion; void versionInt; void deviceId;
void timestamp; void versionString; void pluginUrl; void pluginManifest;
void loadPath; void storagePath; void pluginPath;

void kvstore; void settingsId; void settingsNodes; void rows; void syncBody;
void scrobbler;

// The other half of the #179 pin in the negative fixture: giving a selector a
// real element type must not reject what the runtime allows. `Node[]` has to
// behave as an array -- length, indexing, and the iteration a scraping plugin
// actually writes -- or the declaration trades a silent hole for invented
// errors.
import htmlpos = require('movian/html');
const anchors = htmlpos.parse('<a href="x">y</a>').root.getElementsByTagName('a');
const anchorCount: number = anchors.length;
const firstAnchor = anchors[0];
const nestedAnchors = firstAnchor.getElementsByClassName('c');
anchors.forEach(function (anchor) { void anchor.textContent; });
const anchorTexts = anchors.map(function (anchor) { return anchor.textContent; });

void anchorCount; void firstAnchor; void nestedAnchors; void anchorTexts;

// The other half of the #207 pins in the negative fixture. Bounding native
// arity must not reject what the runtime accepts: fewer arguments than `nargs`
// is legal (Duktape pads with `undefined`), and a variadic native — `nargs`
// -1, DUK_VARARGS — really does take anything.
import natfspos = require('native/fs');
import natio = require('native/io');
import natsqlite = require('native/sqlite');

const exactArity = natfspos.basename('/a/b');
const fewerThanArity = natfspos.copyfile('/a/b');
const noneAtAll = natfspos.basename();
const variadicNative = natio.xmlrpc('a', 'b', 'c', 'd', 'e', 'f');
const variadicQuery = natsqlite.query('SELECT 1', 1, 2, 3);

void exactArity; void fewerThanArity; void noneAtAll;
void variadicNative; void variadicQuery;

// `native/fs` exports BOTH `ftruncate` and `ftrunctae`, at
// src/ecmascript/es_fs.c:473-474, bound to the same C function. The second is
// a typo that reached the public native surface, and it is callable at
// runtime.
//
// Decision (#207): keep emitting it. The artifact describes the runtime, and
// dropping the alias would make a call that works today an error in the type
// checker. This line records the choice where it can be checked — if a future
// round decides to remove it from the emitted surface, this fixture fails and
// forces that to be a decision rather than a silent narrowing.
//
// The first argument is a file handle from `native/fs.open`, not a path:
// es_file_ftruncate reads slot 0 with es_fd_get(). #208 wrote a path string
// here, which type-checked while every native parameter was `any` and would
// have thrown at runtime. The signature derivation caught it.
const truncFd = natfspos.open('/a/b', 'w', 420);
natfspos.ftrunctae(truncFd, 0);
natfspos.ftruncate(truncFd, 0);

// #207, second half: the inverse of the negative fixture's handle and
// primitive pins. Everything the C body actually permits must still compile,
// or the derivation has narrowed the surface rather than described it.
import natsqlitepos = require('native/sqlite');
import natproppos = require('native/prop');
import natstringpos = require('native/string');

// A handle comes out of the native that pushes it, and the return derivation
// now names the class, so the round trip is checked on both sides rather than
// resting on `any`.
const nativeDb = natsqlitepos.create('mydb');
const nativeRows: number = natsqlitepos.changes(nativeDb);

const nativeRoot = natproppos.create('root');
natproppos.destroy(nativeRoot);

// Derived return types are real: a string result has string members.
const upper: string = natfspos.basename('/a/b.txt').toUpperCase();
const nameLength: number = natproppos.getName(nativeRoot).length;
const seconds: number = natstringpos.parseTime('2026-08-20T00:00:00Z');

// Fewer arguments than nargs stays legal -- Duktape pads with `undefined`.
const someUrl: string = natstringpos.resolveURL('http://e.test/');

void nativeRows; void upper; void nameLength; void seconds; void someUrl;

// Review of #209 found three signatures that narrowed a legal call. All three
// were the same mistake: a read the derivation cannot spell was ignored
// instead of recorded, so a primitive reader in one branch spoke for the whole
// slot. These lines are the calls that used to fail.
import natws = require('native/websocket');
import natmeta = require('native/metadata');

// es_websocket.c tries duk_get_buffer_data(ctx, 1) first and falls back to
// duk_to_string: a buffer is a first-class binary send, which
// tests/reference/websocket.d.ts:42-53 already states.
const someSocket = natws.clientCreate('ws://e.test/', 'proto');
declare const binaryFrame: Uint8Array;
natws.clientSend(someSocket, binaryFrame);
natws.clientSend(someSocket, 'a text frame');

// es_prop.c:834-841 reads argument 2 as an options object for `openurl`, and
// res/ecmascript/modules/movian/itemhook.js:23-25 calls it that way.
natproppos.sendEvent(nativeRoot, 'openurl', { url: 'x', view: 'y' });
natproppos.sendEvent(nativeRoot, 'redirect', 'http://e.test/');

// es_get_native_obj_nothrow accepts anything and answers NULL. isValue is a
// predicate over arbitrary values; moveBefore takes null, as page.js:105 does.
const notAProp: boolean = natproppos.isValue('anything at all');
natproppos.moveBefore(nativeRoot, null);

// es_metadata.c reads seven keys off argument 2.
natmeta.videoMetadataBind(nativeRoot, 'http://e.test/v.mkv',
                          { title: 'T', year: 2026, season: 1, episode: 2 });

void notAProp;

// #207 residue: an argument the C reads by named property is an options
// object, and the keys it reads are the shape. Every key here is a literal in
// es_io.c / es_prop.c, not a guess.


natio.httpReq('http://e.test/', {
  debug: true, noFollow: true, compression: false, verifySSL: true,
  headRequest: false, caching: true, cacheTime: 60,
  method: 'POST', postdata: { a: 1 }, headers: { 'X-A': 'b' },
});

// The index signature is the point, not an oversight. The native reads the
// keys it knows and ignores the rest, so an unknown key is not an error at
// runtime and must not become one here.
natio.httpReq('http://e.test/', { debug: true, somethingNewer: 'ok' });

// es_prop.c:834-841 reads argument 2 as a string for `redirect` and as an
// options object for `openurl`. That is a union, not a conflict, and both
// halves have to compile.
natproppos.sendEvent(nativeRoot, 'redirect', 'http://e.test/');
natproppos.sendEvent(nativeRoot, 'openurl',
                     { url: 'x', view: 'v', how: 'h', parenturl: 'p' });

// `Duktape` is an interpreter global: Duktape installs it, Movian only reaches
// into it (ecmascript.c:502), so es_create_env never names it and the C
// scanner cannot see it. Both forms below are what the core modules do.
const dukBuf = new Duktape.Buffer(16);
Duktape.fin({}, function() { });
const finalizer = Duktape.fin({});
// The index signature is the admission that Duktape 1.8.0 keeps its builtin
// membership in a bit-packed blob no scanner here can read. Declaring only
// the three anchored members and stopping would report a real builtin as an
// error this repository invented.
const encoded = Duktape.enc('hex', dukBuf);

void dukBuf; void finalizer; void encoded;

// Buffers. `duk_require_buffer_data` demands one; `duk_to_buffer` coerces, and
// the string is the coercion that matters -- tests/reference/fs.d.ts:33-37
// records that declaring the buffer alone rejects the file-copy round trip.
import natfsbuf = require('native/fs');
import natmisc = require('native/misc');

const copyBuf = new Duktape.Buffer(64);
const rfd = natfsbuf.open('/a/b', 'r');
natfsbuf.read(rfd, copyBuf.valueOf(), 0, copyBuf.length, 0);
const wfd = natfsbuf.open('/a/c', 'w');
natfsbuf.write(wfd, copyBuf, 0, copyBuf.length, 0);
natfsbuf.write(wfd, 'a text payload', 0, null, 0);
natmisc.cachePut('stash', 'key', 'a string is coerced too', 60);
