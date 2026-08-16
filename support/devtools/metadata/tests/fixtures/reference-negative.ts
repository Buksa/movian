import page = require('movian/page');
import prop = require('movian/prop');
import http = require('movian/http');
import settings = require('movian/settings');
import service = require('movian/service');
import store = require('movian/store');

import html = require('movian/html');
import itemhook = require('movian/itemhook');
import popup = require('movian/popup');
import sqlite = require('movian/sqlite');
import subtitles = require('movian/subtitles');
import videoscrobbler = require('movian/videoscrobbler');
import xml = require('movian/xml');
import xmlrpc = require('movian/xmlrpc');
import fs = require('fs');
import https = require('https');
import querystring = require('querystring');
import url = require('url');
import websocket = require('websocket');
import toplevelHttp = require('http');

new page.Route(42, () => {}); // EXPECT_TS2345
new page.Route(/foo/, () => {}); // EXPECT_TS2345
new page.Searcher('Search', 'icon', (_page, query) => {
    const queryNumber: number = query; // EXPECT_TS2322
    void queryNumber;
});

declare const fixturePage: page.Page;
const returnedString: string = fixturePage.appendItem( // EXPECT_TS2322
    'reference:item',
    'directory',
    { title: 'Item' }
);

const bodyString: string = http.request( // EXPECT_TS2322
    'https://example.invalid/reference'
).bytes;
const bodyBytes = http.request('https://example.invalid/reference').bytes;
if (bodyBytes !== undefined) {
    bodyBytes[0] = 'x'; // EXPECT_TS2322
}

declare const numberProp: prop.Property<number>;
prop.subscribeValue(numberProp, value => {
    const callbackText: string = value; // EXPECT_TS2322
    void callbackText;
});

declare const typed: prop.Property<{ title: string }>;
const wrongTypedChild: prop.Property<number> = typed.title; // EXPECT_TS2322

declare const dynamic: prop.Property<Record<string, unknown>>;
const overNarrowDynamic: prop.Property<string> = dynamic.runtimeChild; // EXPECT_TS2322
prop.release(prop.createRoot()); // EXPECT_TS2345
const wronglyPresent: prop.Property<unknown> = // EXPECT_TS2322
    prop.getChild(typed, 0);
prop.sendEvent(dynamic, 'redirect', {}); // EXPECT_TS2345

const badSettings = new settings.globalSettings(
    'bad',
    'Bad settings',
    'icon',
    'description'
);
badSettings.createBool('enabled', 'Enabled', true, value => {
    const callbackText: string = value; // EXPECT_TS2322
    void callbackText;
});
badSettings.createInt(
    'count',
    'Count',
    1,
    0,
    10,
    1,
    42, // EXPECT_TS2345
    () => {}
);
badSettings.createMultiOpt(
    'quality',
    'Quality',
    [['low', 'Low']],
    value => {
        const selectedNumber: number = value; // EXPECT_TS2322
        void selectedNumber;
    }
);

const badService = service.create('Bad', 'bad:start', 'video', true);
const serviceEnabledBoolean: boolean = badService.enabled; // EXPECT_TS2322
badService.enabled = 'yes'; // EXPECT_TS2322

const untypedStore = store.create('bad');
const narrowedStoreValue: string = untypedStore.key; // EXPECT_TS2322
const typedStore = store.create<{ count: number }>('typed');
typedStore.count = 'one'; // EXPECT_TS2322

const missingPluginField = Plugin.missing; // EXPECT_TS2339
const pluginVersionText: string = Plugin.apiversion; // EXPECT_TS2322

// movian/html - wrong parameter type
const htmlNumber = html.parse(42); // EXPECT_TS2345

// movian/itemhook - missing required field
const badItemHook = itemhook.create({ // EXPECT_TS2345
    itemtype: 'video',
    title: 'Test'
});

// movian/popup - text is the one mandatory argument and it must be a string;
// omitting delay/icon is valid (es_notify tolerates 0 / NULL), so the invalid
// call is a wrong-typed text, not a short one.
popup.notify(42); // EXPECT_TS2345

// movian/sqlite - wrong constructor parameter type
const badDb = new sqlite.DB(42); // EXPECT_TS2345

// movian/sqlite - query without SQL: native es_sqlite_query rejects argc < 2
const emptyDb = new sqlite.DB('test.db');
emptyDb.query(); // EXPECT_TS2555

// movian/subtitles - wrong callback parameter
subtitles.addProvider((req) => {
    req.addSubtitle(42, 'Test', 'en', 'vtt', 'test', 100); // EXPECT_TS2345
});

// movian/xml - wrong parameter type
xml.parse(42); // EXPECT_TS2345

// movian/xmlrpc - wrong required URL and missing method
xmlrpc.call(42); // EXPECT_TS2555

// top-level http - wrong options type
toplevelHttp.request(42); // EXPECT_TS2345

// https - wrong parameter type
https.request(42); // EXPECT_TS2345

// querystring - wrong parameter type
querystring.parse(void 0); // EXPECT_TS2345

// url - `host` and `slashes` are format inputs; es_parseURL never emits them,
// so reading them off a parse result must not type-check
const parsedNoHost = url.parse('http://example.test/');
void parsedNoHost.host; // EXPECT_TS2339
void parsedNoHost.slashes; // EXPECT_TS2339

// url - wrong parameter types
url.format(42); // EXPECT_TS2345
url.parse(42); // EXPECT_TS2345
url.resolve(42, 'test'); // EXPECT_TS2345

// websocket - a plain array structurally resembles a buffer but is not one:
// duk_get_buffer_data does not recognise it, so the value would go out
// stringified as a text frame. The brand must keep it out of the binary branch.
const brandWs = new websocket.w3cwebsocket('wss://example.test');
brandWs.send([1, 2, 3]); // EXPECT_TS2345

// websocket - wrong constructor parameter
const badWs = new websocket.w3cwebsocket(42); // EXPECT_TS2345

// movian/videoscrobbler - wrong callback signature
const badScrobbler = new videoscrobbler.VideoScrobbler();
badScrobbler.onstart = (data, prop, origin) => {
    const wrongType: string = data; // EXPECT_TS2322
};

// fs - wrong parameter type
fs.writeFileSync(42, 'test'); // EXPECT_TS2345

// fs - wrong data type: native/fs.write coerces with duk_to_buffer, so the
// declared payload is a string, not an arbitrary value
fs.writeFileSync('/tmp/reference', 42); // EXPECT_TS2345

// http - `port` is not offered on the format-input shape at all: url.js reads
// it through the broken `':' + port` branch, so a port-bearing options object
// either crashes or is ignored. Supplying one must not type-check, whatever
// its type.
toplevelHttp.request({
    protocol: 'http:',
    hostname: 'example.test',
    port: 80, // EXPECT_TS2353
    pathname: '/reference'
});

const badHttpRequest = toplevelHttp.request('http://example.test');

// http - unknown event name on the response-callback family
badHttpRequest.on('finished', () => undefined); // EXPECT_TS2769

// http - response and data callback parameters used at the wrong types
badHttpRequest.on('response', (response) => {
    const wrongStatus: string = response.statusCode; // EXPECT_TS2322
    void wrongStatus;
    response.on('data', (chunk) => {
        const wrongChunk: number = chunk; // EXPECT_TS2322
        void wrongChunk;
    });
});

// https - the callback family is inherited from http, so it falsifies alike
const badHttpsRequest = https.request('https://example.test');
badHttpsRequest.on('response', (response) => {
    const wrongStatus: boolean = response.statusCode; // EXPECT_TS2322
    void wrongStatus;
});

void returnedString;
void bodyString;
void wrongTypedChild;
void overNarrowDynamic;
void wronglyPresent;
void serviceEnabledBoolean;
void narrowedStoreValue;
void missingPluginField;
void pluginVersionText;
