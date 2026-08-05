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
const globals = new settings.globalSettings(
    'example', 'Example', 'icon.png', 'Description');
const db = new sqlite.DB('example');

// Legacy aliases resolve to the same surface, local members included.
const legacyRoot = legacyProp.createRoot('legacy');
const legacyRoute = new legacyPage.Route('legacy:(.*)', () => { });

void anonymousRoot; void navigators; void popups; void language;
void proxied; void rpc; void response; void searcher; void route;
void globals; void db; void legacyRoot; void legacyRoute;
