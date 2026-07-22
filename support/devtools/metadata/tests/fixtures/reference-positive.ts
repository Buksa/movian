import page = require('movian/page');
import prop = require('movian/prop');
import http = require('movian/http');

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
void namedChild;
void indexedChild;
