// Type-checked against generated/movian-api-v1.d.ts TOGETHER with
// generated/movian-api.d.ts -- the pair an apiversion-1 plugin gets, since
// src/plugins.c:712 defaults a manifest without `apiversion` to 1 and
// src/ecmascript/ecmascript.c:913 loads api-v1.js for exactly those.
//
// Every member of the `showtime` global is touched here on purpose. Measured
// before this file existed: 22 of the 26 could be deleted from the artifact
// with every gate still green. `gen.py --check` compares the file byte-wise
// against a fresh render, so it catches a hand-edit and nothing else -- when
// the SCANNER is wrong, the generator and the committed file agree on the same
// false thing. Only a fixture that uses a member can notice its loss.
//
// Kept to name-and-arity, like the main bundle's positive fixture: the
// declarations are `any`-typed, so what this can prove is that a real call
// RESOLVES.

// Values and aliases. `api-v1.js` assigns these from other modules
// (`print: print`, `JSONDecode: JSON.parse`, `deviceId: Core.deviceId`), so
// the scan records that the member exists, not what it aliases.
const version: any = showtime.currentVersionInt;
const versionString: any = showtime.currentVersionString;
const device: any = showtime.deviceId;

// Aliased callables: still callable, just not scanned for parameter names.
showtime.print('example');
showtime.trace('example');
showtime.notify('example', 1);
showtime.message('example', true, false);
showtime.textDialog('example', true, false);
showtime.probe('http://example.test');
showtime.entityDecode('&amp;');
showtime.queryStringSplit('a=1&b=2');
showtime.pathEscape('a b');
showtime.paramEscape('a b');
showtime.durationToString(90);
showtime.basename('/a/b/c.txt');
showtime.getSubtitleLanguages();
const decoded: any = showtime.JSONDecode('{}');
const encoded: any = showtime.JSONEncode({});

// Scanned function literals -- these carry real parameter names and arities,
// so a change to the emission shows up here rather than in somebody's editor.
showtime.httpGet('http://example.test', { a: 1 }, {}, {});
showtime.httpReq('http://example.test', {}, () => { });
showtime.sha1digest('example');
showtime.md5digest('example');
showtime.sleep(1);
showtime.systemIpAddress();
// `RichText` is a constructor in the source (`this.str = x.toString()`), but
// api-v1.js exposes it as a plain member of the object literal, so it is
// declared as a method. Called, not constructed, to match the declaration.
showtime.RichText('example');
// `xmlrpc` declares no formal parameters and reads its tail out of
// `arguments` (api-v1.js). Emitted zero-arity it would reject every real
// call, which is what the variadic detection exists to prevent.
showtime.xmlrpc('http://example.test/rpc', 'sample.method', 'first', 42);

void version; void versionString; void device; void decoded; void encoded;

// The v1 `plugin` object. api-v1.js:102 declares it with a top-level `var`,
// so it is a global binding of that program -- and measured on a running
// instance, `this === plugin` at plugin top level, which is what makes the
// legacy `(function(plugin){...})(this)` wrapper work.
plugin.createService('Example', 'example:v1:', 'other', true, 'icon.png');
plugin.createStore('example');
plugin.addURI('example:v1:(.*)', function() { });
plugin.addSearcher('Example', 'icon.png', function() { });
plugin.createSettings('Example', 'icon.png', 'Description');
plugin.addItemHook({ title: 'Example' });
plugin.addSubtitleProvider(function() { });
plugin.cachePut('stash', 'key', {}, 60);
plugin.getAuthCredentials('Example', 'reason', true, 'example', false);
plugin.addHTTPAuth('.*', function() { });
plugin.copyFile('a', 'b');
plugin.selectView('view');
plugin.getDescriptor();
const cached: any = plugin.cacheGet('stash', 'key');
const pluginPath2: any = plugin.path;
const pluginConfig: any = plugin.config;
const pluginProps: any = plugin.properties;

void cached; void pluginPath2; void pluginConfig; void pluginProps;
