// Proves the generated-d.ts gate has teeth. A positive fixture alone goes
// green when the declarations are missing, over-permissive, or the file is
// empty -- this one pins the diagnostics the generated artifact must still
// produce, so a change that makes everything `any` fails here.
//
// Each EXPECT_TSnnnn marker sits on the line tsc must report.

import prop = require('movian/prop');
import xmlrpc = require('movian/xmlrpc');
import http = require('movian/http');
import settings = require('movian/settings');
import page = require('movian/page');
import htmlneg = require('movian/html');

// A module the loader does not have. If this ever stops erroring, the
// declarations have grown a wildcard and TS2307 detection is dead.
import missing = require('movian/no-such-module');  // EXPECT_TS2307

// `export * from 'native/prop'` must not turn movian/prop into an open bag:
// a name that is neither a local export nor an inherited native member stays
// an error.
const absent = prop.noSuchMember;  // EXPECT_TS2339

// Optional parameters still bound the arity above the declared count -- the
// names are emitted optional so fewer arguments are legal, not more.
const tooMany = prop.createRoot('a', 'b');  // EXPECT_TS2554

// The variadic rest on xmlrpc.call must not leak onto its siblings.
const bogus = xmlrpc.noSuchCall();  // EXPECT_TS2339

// The callback form of http.request returns nothing. If the overload split
// collapses back to a single signature promising HttpResponse, this line
// stops erroring and the unsound declaration is back.
const async = http.request('https://e.test', {}, () => { }).toString();  // EXPECT_TS2339

// The settings instance type must stay a real surface: a construct signature
// returning `any` would make this line legal again.
const instance = new settings.globalSettings('id', 'T', 'i.png', 'd');
const nothere = instance.noSuchSetting;  // EXPECT_TS2339

// The two settings initializers install DIFFERENT surfaces: globalSettings
// assigns id and properties, kvstoreSettings does not. One merged instance
// type let this compile and read undefined.
const kv = new settings.kvstoreSettings(null, 'u', 'd');
const noProps = kv.properties;  // EXPECT_TS2339

// NOT pinned here, deliberately: `Page.options` is emitted `options?: any`
// because page.js:195-200 assigns it only under `if(!flat)`, but `?: any`
// still has type `any`, so tsc will not flag an unguarded `p.options.foo`.
// The optional marker is honest documentation and editor signal; it is not
// enforceable while the member type is `any`, and asserting it here would be
// a test that cannot fail. Enforcement arrives with real member types (#134).

// The callback response is `HttpResponse | null` -- http.js:96 passes null on
// the failure path. Unlike Page.options this IS enforceable, because the type
// is a real union rather than `any`.
http.request('https://e.test', {}, (err: any, res) => {
    void err;
    void res.statuscode;  // EXPECT_TS18047
});

// The globals must be real declarations, not an escape hatch: a member that
// does not exist stays an error, and the guarded ones stay optional.
console.nosuchmethod('x');  // EXPECT_TS2339
const unguarded: string = Plugin.path;  // EXPECT_TS2322
// The same pin for the two Core paths. Without these the positive fixture's
// `string | undefined` annotations pass under a REQUIRED emission too, so the
// optionality would be documented and unenforced -- which is what the two
// guards exist to record.
const unguardedLoad: string = Core.loadPath;  // EXPECT_TS2322
const unguardedStorage: string = Core.storagePath;  // EXPECT_TS2322

void missing; void absent; void tooMany; void bogus; void noProps;
void async; void nothere; void unguarded;
// The append methods return `Item`, not `any`. While they returned `any`, a
// plugin could assign any member name and it type-checked: `Item.onSelect`
// shipped in two examples, was never called, and no gate could see it (#177).
// Zero of the nine real plugins use it -- it existed only in examples.
new page.Route('items:(.*)', (p) => {
    p.appendItem('u', 'directory', {}).onSelect = () => { };  // EXPECT_TS2339
    p.appendAction('t', () => { }).onSelect = () => { };  // EXPECT_TS2339
    p.appendPassiveItem('label', {}, {}).onSelect = () => { };  // EXPECT_TS2339
});

void unguardedLoad; void unguardedStorage;

// An array-returning selector must carry its element type. While these were
// `any`, `interface Node` had all eleven members and caught a phantom written
// directly on a node -- but every selector that REACHED a node discarded the
// type, so the same typo one level out was silent. That is not hypothetical:
// plugin_examples/02-intermediate/02-html-parser called `getAttribute` through
// exactly this path at four sites, type-checked clean, and rendered an
// `openerror` until it was fixed by hand (#179).
const viaSelector = htmlneg.parse('<a/>').root
    .getElementsByTagName('a')[0].getAttribute('href');  // EXPECT_TS2551
// The alias resolves to the same method and must inherit the return type.
const viaAlias = htmlneg.parse('<a/>').root
    .getElementsByClassName('c')[0].getAttribute('href');  // EXPECT_TS2551

void viaSelector; void viaAlias;
