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

void missing; void absent; void tooMany; void bogus; void noProps;
void async; void nothere;
