import page = require('movian/page');
import prop = require('movian/prop');
import http = require('movian/http');
import settings = require('movian/settings');
import service = require('movian/service');
import store = require('movian/store');

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

void returnedString;
void bodyString;
void wrongTypedChild;
void overNarrowDynamic;
void wronglyPresent;
void serviceEnabledBoolean;
void narrowedStoreValue;
void missingPluginField;
void pluginVersionText;
