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
void pluginId; void apiVersion; void versionInt; void deviceId;
void timestamp; void versionString; void pluginUrl; void pluginManifest;
void loadPath; void storagePath; void pluginPath;

void kvstore; void settingsId; void settingsNodes; void rows; void syncBody;
void scrobbler;
