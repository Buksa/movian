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
const htmlNode = htmlDoc.root.getElementById('test');
const htmlNodes = htmlDoc.root.getElementsByTagName('div');

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

// movian/popup - callable export
popup.notify('Test notification');

// movian/sqlite - constructor with prototype members
const db = new sqlite.DB('test.db');
db.query('SELECT * FROM test');
const row = db.step();
db.upgradeSchema('/path/to/schema');
const lastId = db.lastRowId;
const lastError = db.lastErrorString;
const lastCode = db.lastErrorCode;
db.close();

// movian/subtitles - callback
subtitles.addProvider((req) => {
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

// movian/xmlrpc - callable with variadic
xmlrpc.call();

// fs - callable exports
fs.writeFileSync('/tmp/test.txt', 'test content');
const fileData = fs.readFileSync('/tmp/test.txt');
const dirEntries = fs.readdirSync('/tmp');
fs.unlinkSync('/tmp/test.txt');
fs.mkdirSync('/tmp/testdir');
fs.rmdirSync('/tmp/testdir');

// http - callable with options object
const httpReq = http.request('http://example.test');

// https - re-export
const httpsReq = https.request('https://example.test');
// querystring - callable export
const parsed = querystring.parse('key=value&test=123');

// url - callable with options object
const urlObj = { protocol: 'http:', pathname: '/test', query: { key: 'value' } };
const formatted = url.format(urlObj);
const parsedUrl = url.parse('http://example.test/test?key=value');
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
void htmlNode;
void htmlNodes;
void row;
void lastId;
void lastError;
void lastCode;
void langs;
void xmlProxy;
void fileData;
void dirEntries;
void formatted;
void parsedUrl;
void resolved;
void parsed;
