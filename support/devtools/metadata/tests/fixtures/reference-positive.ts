import page = require('movian/page');
import prop = require('movian/prop');
import http = require('movian/http');
import settings = require('movian/settings');
import service = require('movian/service');
import store = require('movian/store');

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
