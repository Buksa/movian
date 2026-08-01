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

const route = new page.Route('reference:(.*)', (routePage, capture) => {
    const routeCapture: string = capture;
    const appended: page.Item = routePage.appendItem(
        'reference:' + routeCapture,
        'directory',
        { title: routeCapture }
    );
    const itemRoot: prop.Property = appended.root;
    void itemRoot;
});
route.destroy();

const searcher = new page.Searcher(
    'Reference search',
    'skin://icons/search.png',
    (searchPage, query) => {
        const queryPayload: string = query;
        const result: page.Item = searchPage.appendItem(
            'search:' + queryPayload,
            'video',
            { title: queryPayload }
        );
        result.onEvent('Activate', eventType => {
            const actionPayload: string = eventType;
            void actionPayload;
        });
    }
);
searcher.destroy();

declare const fixturePage: page.Page;
const returnedItem: page.Item = fixturePage.appendItem(
    'reference:item',
    'directory',
    { title: 'Item' }
);
returnedItem.disable();

const syncResponse: http.HttpResponse = http.request(
    'https://example.invalid/reference'
);
if (syncResponse.bytes !== undefined) {
    const firstBodyByte: number = syncResponse.bytes[0];
    void firstBodyByte;
    syncResponse.bytes[0] = firstBodyByte;
}

http.request(
    'https://example.invalid/reference',
    { noFail: true },
    (error, response) => {
        const callbackError: string | null = error;
        if (response !== null && response.bytes !== undefined) {
            const callbackBody: http.DuktapeBuffer = response.bytes;
            void callbackBody;
        }
        void callbackError;
    }
);

declare const numberProp: prop.Property<number>;
prop.subscribeValue(numberProp, value => {
    const callbackValue: number | null = value;
    void callbackValue;
});

declare const typed: prop.Property<{
    title: string;
    nested: { count: number };
}>;
const typedTitle: prop.Property<string> = typed.title;
const typedCount: prop.Property<number> = typed.nested.count;
void typedTitle;
void typedCount;

declare const dynamic: prop.Property<Record<string, unknown>>;
const dynamicChild: prop.Property<unknown> = dynamic.runtimeChild;
void dynamicChild;

prop.set(dynamic, 'cleared', undefined);
const namedChild: prop.Property<unknown> = prop.getChild(typed, 'runtimeChild');
const indexedChild: prop.Property<unknown> | undefined =
    prop.getChild(typed, 0);
prop.sendEvent(dynamic, 'redirect', 'page:home');
prop.sendEvent(dynamic, 'openurl', {
    url: 'https://example.invalid/reference',
    view: 'reference',
});

const globalPrefs = new settings.globalSettings(
    'reference',
    'Reference settings',
    'skin://icons/settings.png',
    'Reference declaration fixture'
);
const boolItem: settings.SettingItem<boolean> = globalPrefs.createBool(
    'enabled',
    'Enabled',
    true,
    value => {
        const callbackValue: boolean = value;
        void callbackValue;
    },
    true
);
boolItem.enabled = false;
boolItem.value = true;
const boolValueProperty: prop.Property<boolean> = boolItem.value;

const stringItem: settings.SettingItem<string> = globalPrefs.createString(
    'label',
    'Label',
    'default',
    value => {
        const callbackValue: string = value;
        void callbackValue;
    }
);
stringItem.value = 'changed';

const intItem: settings.SettingItem<number> = globalPrefs.createInt(
    'count',
    'Count',
    1,
    0,
    10,
    1,
    'items',
    value => {
        const callbackValue: number = value;
        void callbackValue;
    }
);
intItem.value = 2;
globalPrefs.createAction('refresh', 'Refresh', () => {});
globalPrefs.createDivider('Advanced');
globalPrefs.createInfo('about', 'skin://icons/info.png', 'Reference');
globalPrefs.createMultiOpt(
    'quality',
    'Quality',
    [['low', 'Low'], [2, 'High', true]],
    value => {
        const selectedId: string = value;
        void selectedId;
    },
    true
);

declare const settingsNodes: prop.Property;
const pagePrefs = new settings.kvstoreSettings(
    settingsNodes,
    'reference:page',
    'plugin'
);
pagePrefs.createBool('page-enabled', 'Enabled', false, value => {
    const callbackValue: boolean = value;
    void callbackValue;
});

const registeredService: service.Service = service.create(
    'Reference',
    'reference:start',
    'video',
    true
);
registeredService.enabled = false;
const currentServiceEnabled: undefined = registeredService.enabled;
registeredService.destroy();

const dynamicStore: store.Store = store.create('reference');
const dynamicStoreValue: unknown = dynamicStore.runtimeKey;
dynamicStore.runtimeKey = { nested: true };

interface ReferenceStoreState {
    token: string;
    retries: number;
}

const typedStore = store.createFromPath<ReferenceStoreState>(
    '/tmp/reference-store'
);
typedStore.token = 'fixture';
typedStore.retries = 2;

// movian/html - callable export
const htmlDoc = html.parse('<div>test</div>');
const htmlRootName: string = htmlDoc.root.nodeName;
const htmlRootType: number = htmlDoc.root.nodeType;
const htmlChildren: html.Node[] = htmlDoc.root.children;
// textContent is undefined for a node with no text fragments (es_gumbo.c
// returns zero values), so the guard is the calibrated shape, not a cast.
const htmlText: string | undefined = htmlDoc.root.textContent;
const htmlEmpty = html.parse('<div></div>');
if (htmlEmpty.root.textContent !== undefined) {
    const htmlNarrowed: string = htmlEmpty.root.textContent;
    void htmlNarrowed;
}
const htmlAttribute = htmlDoc.root.attributes.getNamedItem('class');
const htmlById = htmlDoc.root.getElementById('test');
const htmlByClass = htmlDoc.root.getElementByClassName('reference');
const htmlByClasses = htmlDoc.root.getElementsByClassName('reference');
const htmlByTag = htmlDoc.root.getElementByTagName('div');
const htmlByTags = htmlDoc.root.getElementsByTagName('div');

// movian/itemhook - callable with options object
const itemHook = itemhook.create({
    itemtype: 'video',
    title: 'Test',
    icon: 'skin://icons/test.png',
    handler: (item, nav) => {
        nav.openURL('page:home');
    }
});
itemHook.destroy();

// movian/popup - callable export with exact tuple
popup.notify('Test notification', 5000, 'skin://icons/test.png');

// movian/sqlite - constructor, variadic method, methods, and accessors
const db = new sqlite.DB('test.db');
db.query('SELECT * FROM test WHERE id = ?', 1);
const dbRow: unknown = db.step();
const upgraded: unknown = db.upgradeSchema('/tmp/reference-schema');
const lastRowId: number = db.lastRowId;
const lastErrorString: string = db.lastErrorString;
const lastErrorCode: number = db.lastErrorCode;
db.close();

// movian/subtitles - callback
subtitles.addProvider((req) => {
    // the request inherits the native query object, so a provider can read
    // the search metadata it was called with
    if (req.title !== undefined) {
        const searchTitle: string = req.title;
        void searchTitle;
    }
    if (req.season !== undefined && req.episode !== undefined) {
        const ep: number = req.season * 100 + req.episode;
        void ep;
    }
    void req.imdb;
    void req.opensubhash;
    req.addSubtitle('http://example.test/sub.vtt', 'Test', 'en', 'vtt', 'test', 100);
});
const langs = subtitles.getLanguages();

// movian/videoscrobbler - constructor with callbacks
const scrobbler = new videoscrobbler.VideoScrobbler();
scrobbler.onstart = (data, prop, origin) => {};
scrobbler.onpause = (data, prop, origin) => {};
scrobbler.onresume = (data, prop, origin) => {};
scrobbler.onstop = (data, prop, origin) => {};
scrobbler.destroy();

// movian/xml - callable export
const xmlData = xml.parse('<root><item>test</item></root>');
const xmlProxy = xml.htsmsg(xmlData);

// movian/xmlrpc - two required arguments plus a variadic tail
xmlrpc.call(
    'https://example.test/xmlrpc',
    'reference.method',
    'argument',
    42
);

// fs - callable exports
fs.writeFileSync('/tmp/test.txt', 'test content');
const fileData = fs.readFileSync('/tmp/test.txt');
const dirEntries = fs.readdirSync('/tmp');
fs.unlinkSync('/tmp/test.txt');
fs.mkdirSync('/tmp/testdir');
fs.rmdirSync('/tmp/testdir');

// top-level http - callable with string and options-object signatures.
//
// `port` is deliberately absent from this positive case. It is typed as a
// number (native/string.parseURL pushes it with duk_push_int,
// src/ecmascript/es_string.c:319), but a port-bearing options object cannot
// actually run: url.js:14 tests `d.port` and then concatenates the bare
// identifier `port`, which is not defined —
//     var host = d.host || (d.hostname + (d.port ? (':' + port) : ''));
// so `get({hostname, port, ...})` raises ReferenceError before reaching the
// request. A compile-only fixture must not bless a shape the runtime cannot
// execute; the `d.port`/`port` typo is a separate runtime defect.
const httpReq = toplevelHttp.request('http://example.test');
const httpGet = toplevelHttp.get({
    protocol: 'http:',
    hostname: 'example.test',
    pathname: '/reference'
});

// http - the request/response callback families. Request.on('response')
// yields a Response; Response.on('data') yields a decoded string and
// on('end') takes no argument.
httpReq.on('response', (response) => {
    const status: number = response.statusCode;
    response.setEncoding('utf8');
    response.on('data', (chunk) => {
        const text: string = chunk;
        void text;
    });
    response.on('end', () => {
        void status;
    });
});
httpReq.on('error', (error) => {
    void error;
});
httpReq.end();
const requestHeaders: unknown[] = httpReq.headers;
void requestHeaders;

// https - two-argument wrappers reusing HTTP types
const httpsReq = https.request('https://example.test');
const httpsGet = https.get('https://example.test/reference');
httpsReq.on('response', (response) => {
    void response.statusCode;
});

// querystring - callable export
const parsed = querystring.parse('key=value&test=123');

// url - callable with options object
const urlObj = { protocol: 'http:', pathname: '/test', query: { key: 'value' } };
const formatted = url.format(urlObj);
const parsedUrl = url.parse('http://example.test/test?key=value', false);
const resolved = url.resolve('http://example.test/', 'test');

// websocket - constructor with callbacks
const ws = new websocket.w3cwebsocket('ws://example.test');
ws.onopen = () => {};
ws.oninput = (data) => {};
ws.onclose = () => {};
ws.send('test message');
ws.close();

const pluginId: string = Plugin.id;
const pluginUrl: string = Plugin.url;
const pluginManifest: string = Plugin.manifest;
const pluginApiVersion: number = Plugin.apiversion;
const pluginPath: string | undefined = Plugin.path;

void namedChild;
void indexedChild;
void dynamicStoreValue;
void currentServiceEnabled;
void pluginId;
void pluginUrl;
void pluginManifest;
void pluginApiVersion;
void pluginPath;
void boolValueProperty;
void htmlDoc;
void db;
void langs;
void xmlProxy;
void fileData;
void dirEntries;
void formatted;
void parsedUrl;
void resolved;
void parsed;
