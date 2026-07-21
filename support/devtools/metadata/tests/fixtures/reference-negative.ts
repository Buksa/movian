import page = require('movian/page');
import prop = require('movian/prop');
import http = require('movian/http');

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

void returnedString;
void bodyString;
void wrongTypedChild;
void overNarrowDynamic;
