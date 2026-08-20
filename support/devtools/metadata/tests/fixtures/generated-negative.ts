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
//
// TS2551 rather than TS2339 because `Node` declares `attributes`, which is
// within tsc's spelling-suggestion distance of `getAttribute`. That coupling is
// the point -- the same suggestion is what makes the diagnostic useful to a
// plugin author -- but it means renaming `attributes` turns these two pins into
// TS2339 and fails an otherwise-correct tree. Repin, do not delete: the pin is
// then telling the truth about a real change to the surface.
const viaSelector = htmlneg.parse('<a/>').root
    .getElementsByTagName('a')[0].getAttribute('href');  // EXPECT_TS2551
// The alias resolves to the same method and must inherit the return type.
const viaAlias = htmlneg.parse('<a/>').root
    .getElementsByClassName('c')[0].getAttribute('href');  // EXPECT_TS2551

void viaSelector; void viaAlias;

// Native modules declare an arity and, until #207, did not enforce it: every
// export was emitted `(...args: any[])`, so `@arity 1` was a comment beside a
// signature that accepted anything. The CommonJS half of this same artifact has
// bounded arity since the beginning, which is what made the gap visible.
//
// Optional parameters bound the call from above only — passing FEWER arguments
// stays legal, matching Duktape, which pads the missing ones with `undefined`.
// The positive fixture pins that half.
import natfs = require('native/fs');
import natprop = require('native/prop');

const tooManyForNative = natfs.basename('a', 'b');  // EXPECT_TS2554
const tooManyForNative2 = natfs.copyfile('a', 'b', 'c');  // EXPECT_TS2554
// @arity 0 means zero: `native/prop.global()` takes nothing.
const tooManyForZeroArg = natprop.global('x');  // EXPECT_TS2554

void tooManyForNative; void tooManyForNative2; void tooManyForZeroArg;

// #207, second half: `nargs` was never all the C had to say. The table's
// second field names the C function, and that body shows which Duktape reader
// it applies to each argument index. An argument read through
// es_get_native_obj() or es_resource_get() is a wrapped C pointer whose class
// is written at the call site, so it gets an opaque handle type rather than
// `any` -- which is what stops a prop handle being passed where a database
// handle belongs.
import natsqlite = require('native/sqlite');
import natstring = require('native/string');
import nathtsmsg = require('native/htsmsg');

// Both sides of the boundary are derived, so a handle is obtained the way a
// plugin obtains one -- from the native that pushes it. `native/prop.create`
// pushes through es_push_native_obj(&es_native_prop) and
// `native/htsmsg.createFromXML` through es_push_native_obj(&es_native_htsmsg).
const someProp = natprop.create('root');
const someHtsmsg = nathtsmsg.createFromXML('<a/>');

// es_file_ftruncate reads slot 0 with es_fd_get() -> es_resource_get(ctx, idx,
// &es_resource_fd). A path is not a file handle, and passing one throws at
// runtime -- this line type-checked until the derivation landed.
const pathIsNotAHandle = natfs.ftruncate('/a/b', 0);  // EXPECT_TS2345

// Handle classes are distinct types, not one shared opaque blob.
const propIsNotADatabase = natsqlite.changes(someProp);  // EXPECT_TS2345
const htsmsgIsNotAProp = natprop.destroy(someHtsmsg);  // EXPECT_TS2345

// A primitive read out of the C body is enforced like any other parameter.
const numberIsNotAPath = natfs.readdir(42);  // EXPECT_TS2345
const numberIsNotATimestamp = natstring.parseTime(42);  // EXPECT_TS2345

// A native whose every `return` is 0 leaves nothing on the stack, so the call
// evaluates to `undefined`. Emitting `void` is what makes using it as a value
// an error instead of silently `any`.
const voidHasNoMembers = natprop.destroy(someProp).toString();  // EXPECT_TS2339

void pathIsNotAHandle; void propIsNotADatabase; void htsmsgIsNotAProp;
void numberIsNotAPath; void numberIsNotATimestamp; void voidHasNoMembers;

// The index signature admits UNKNOWN keys; it does not weaken a known one.
// `debug` is read with es_prop_is_true, so it is a boolean.
import natio2 = require('native/io');

const wrongOptionType = natio2.httpReq('http://e.test/', { debug: 'yes' });  // EXPECT_TS2322
// `cacheTime` is read with es_prop_to_int.
const wrongOptionType2 = natio2.httpReq('http://e.test/', { cacheTime: '60' });  // EXPECT_TS2322
// The union on sendEvent's third argument is string-or-object, not anything.
const notInTheUnion = natprop.sendEvent(someProp, 'openurl', 42);  // EXPECT_TS2345

void wrongOptionType; void wrongOptionType2; void notInTheUnion;
