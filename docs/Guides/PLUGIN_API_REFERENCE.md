# Movian Plugin API Reference

This reference describes the plugin-facing ECMAScript API available in this
checkout. It is based on:

- `src/ecmascript/ecmascript.c`, which creates plugin globals and resolves
  modules.
- `res/ecmascript/modules/`, which contains the public CommonJS modules.
- `res/ecmascript/legacy/api-v1.js`, which provides the legacy `showtime` and
  `plugin` API wrapper.
- `src/ecmascript/es_*.c`, which registers low-level `native/*` modules.

Prefer the `movian/*` modules and the legacy wrapper for plugin code. The
`native/*` modules are available, but they are closer to internal bindings and
usually need manual resource cleanup with `Core.resourceDestroy()`.

## Runtime Model

Plugin files run on Duktape with CommonJS-style `require()`.

Module lookup order:

- `require('native/name')` loads a native module registered from C.
- `require('showtime/name')` is treated as `require('movian/name')`.
- Relative or plugin-local modules are loaded from the plugin directory.
- Built-in modules are loaded from `res/ecmascript/modules/name.js`.

For `apiversion: 2`, the plugin entry file runs directly.

For legacy API v1, `res/ecmascript/legacy/api-v1.js` is loaded first and the
plugin entry file is called with `this` bound to the generated `plugin` object.
Old examples usually use:

```js
(function(plugin) {
  plugin.createService('Example', 'example:start', 'other', true);
})(this);
```

## Plugin Manifest

Plugins are loaded from a directory or archive containing `plugin.json`.

Minimal API v2 plugin:

```json
{
  "type": "ecmascript",
  "id": "example_plugin",
  "file": "example.js",
  "apiversion": 2
}
```

Recognized fields used by the current loader and plugin UI:

- `type` - required. Use `ecmascript` for JavaScript plugins. `views` is a
  view-only package type. `bitcode` is only available when VMIR is enabled.
- `id` - required plugin id.
- `file` - required for `ecmascript` and `bitcode`; path is relative to the
  plugin directory.
- `apiversion` - optional, defaults to `1`. Use `2` for the direct CommonJS
  API described in this document.
- `title` - display title.
- `version` - plugin version string.
- `icon` - absolute HTTP(S) URL or plugin-relative icon path.
- `synopsis` - short summary shown in plugin metadata.
- `description` - longer plugin metadata text.
- `debug` - truthy value enables ECMAScript debug logging for the plugin.
- `entitlements.bypassFileACLRead` - allow broader file reads.
- `entitlements.bypassFileACLWrite` - allow broader file writes.
- `glwviews` - optional list of bundled view definitions.

`glwviews` entries may contain:

- `uitype` - optional, defaults to `standard`.
- `class`
- `title`
- `file`
- `select` - optional flag controlling immediate selection.

Compatibility note: some old examples use `type: "javascript"`. The current
local loader branch for JavaScript plugins is `type: "ecmascript"`.

## Quick Start

```js
var page = require('movian/page');
var service = require('movian/service');

var PREFIX = 'example_plugin:';

service.create('Example Plugin', PREFIX + 'start', 'other', true);

new page.Route(PREFIX + 'start', function(page) {
  page.type = 'directory';
  page.metadata.title = 'Example Plugin';

  page.appendItem('https://example.test/video.mp4', 'video', {
    title: 'Example Video'
  });

  page.loading = false;
});
```

## Globals

### `Plugin`

The `Plugin` global is created for every plugin:

- `Plugin.id` - plugin id from the manifest.
- `Plugin.url` - entry script URL.
- `Plugin.manifest` - raw manifest JSON string.
- `Plugin.apiversion` - numeric API version.
- `Plugin.path` - plugin directory path when known.

### `Core`

`Core` contains runtime helpers and build/device metadata:

- `Core.currentVersionInt`
- `Core.currentVersionString`
- `Core.deviceId`
- `Core.loadPath`
- `Core.storagePath`
- `Core.compile(path)` - load and compile an ECMAScript file, returning a
  callable function.
- `Core.resourceDestroy(resource)` - explicitly destroy a native resource.
- `Core.sleep(seconds)` - blocking sleep.
- `Core.timestamp()` - high-resolution timestamp from the platform clock.
- `Core.randomBytes(length)` - returns a Duktape buffer; `length` is capped at
  65536.

### Timers And Logging

The global object also provides:

- `print(...)`
- `console.log(...)`
- `console.error(...)`
- `console.warn(...)`
- `setTimeout(fn, ms)`
- `setInterval(fn, ms)`
- `clearTimeout(timer)`
- `clearInterval(timer)`

## Public CommonJS Modules

### `require('movian/page')`

Route and search integration.

Exports:

- `new page.Route(pattern, callback)`
- `Route#destroy()`
- `new page.Searcher(title, icon, callback)`
- `Searcher#destroy()`

Route callback signature:

```js
new page.Route('example:start:(.*)', function(page, arg1) {
  page.type = 'directory';
  page.metadata.title = 'Example';
  page.loading = false;
});
```

`Page` properties:

- `page.type`
- `page.metadata`
- `page.loading`
- `page.source`
- `page.entries`
- `page.paginator`
- `page.asyncPaginator`
- `page.reorderer`
- `page.options`

`Page` methods:

- `page.haveMore(boolean)`
- `page.error(message)`
- `page.getItems()`
- `page.appendItem(url, type, metadata)`
- `page.appendAction(title, callback, subtype)`
- `page.appendPassiveItem(type, data, metadata)`
- `page.dump()`
- `page.flush()`
- `page.redirect(url)`
- `page.onEvent(type, callback)`

`Item` objects returned by `appendItem()`, `appendAction()`, and
`appendPassiveItem()` expose:

- `item.root`
- `item.page`
- `item.bindVideoMetadata(object)`
- `item.unbindVideoMetadata()`
- `item.dump()`
- `item.enable()`
- `item.disable()`
- `item.destroyOption(prop)`
- `item.addOptAction(title, callback, subtype)`
- `item.addOptURL(title, url, subtype)`
- `item.addOptSeparator(title)`
- `item.destroy()`
- `item.moveBefore(beforeItem)`
- `item.onEvent(type, callback)`

### `require('movian/http')`

HTTP client wrapper.

```js
var http = require('movian/http');

var res = http.request('https://example.test/data.json', {
  args: { q: 'test' },
  headers: { Accept: 'application/json' },
  method: 'GET'
});

print(res.statuscode);
print(res.toString());
```

Exports:

- `http.request(url, ctrl)`
- `http.request(url, ctrl, callback)`

Async callback signature:

```js
http.request(url, ctrl, function(err, response) {});
```

`HttpResponse` properties and methods:

- `response.statuscode`
- `response.bytes`
- `response.allheaders`
- `response.headers`
- `response.headers_lc`
- `response.multiheaders`
- `response.multiheaders_lc`
- `response.contenttype`
- `response.toString()`
- `response.convertFromEncoding(encoding)`

`ctrl` fields recognized by the native HTTP layer:

- `debug`
- `noFollow`
- `compression`
- `noAuth`
- `noFail`
- `verifySSL`
- `headRequest`
- `cacheTime`
- `caching`
- `headers`
- `postdata` - buffer, object, or string. Objects are sent as form data.
- `args` - query parameters.
- `method`

### `require('movian/html')`

HTML parser wrapper over Gumbo.

```js
var html = require('movian/html');
var doc = html.parse(source);
var title = doc.root.getElementsByTagName('title')[0].textContent;
```

Exports:

- `html.parse(source)`

Returned object:

- `document`
- `root`

Node properties:

- `node.nodeName`
- `node.nodeType`
- `node.children`
- `node.textContent`
- `node.attributes`

Node methods:

- `node.getElementById(id)`
- `node.getElementByClassName(className)`
- `node.getElementsByClassName(className)`
- `node.getElementByTagName(tagName)`
- `node.getElementsByTagName(tagName)`

The class and tag helpers return arrays.

### `require('movian/prop')`

Property tree wrapper. It exports proxied properties for normal plugin code and
also inherits all functions from `native/prop`.

Exports:

- `prop.global`
- `prop.makeProp(nativeProp)`
- `prop.createRoot(name)`
- `prop.subscribeValue(prop, callback, ctrl)`

Common inherited native helpers:

- `prop.print(prop)`
- `prop.release(prop)`
- `prop.create(name)`
- `prop.getValue(prop)`
- `prop.getName(prop)`
- `prop.getChild(prop, child)`
- `prop.set(prop, key, value)`
- `prop.setRichStr(prop, key, richString)`
- `prop.setParent(prop, parent)`
- `prop.subscribe(prop, callback, ctrl)`
- `prop.haveMore(prop, boolean)`
- `prop.makeUrl(prop)`
- `prop.enumerate(prop)`
- `prop.has(prop, key)`
- `prop.deleteChild(prop, key)`
- `prop.deleteChilds(prop)`
- `prop.destroy(prop)`
- `prop.select(prop)`
- `prop.link(prop, target)`
- `prop.unlink(prop)`
- `prop.sendEvent(prop, type, payload)`
- `prop.isValue(prop)`
- `prop.atomicAdd(prop, number)`
- `prop.isSame(a, b)`
- `prop.moveBefore(prop, before)`
- `prop.unloadDestroy(prop)`
- `prop.isZombie(prop)`
- `prop.setClipRange(prop, min, max)`
- `prop.tagSet(prop, tag, value)`
- `prop.tagClear(prop, tag)`
- `prop.tagGet(prop, tag)`
- `prop.nodeFilterCreate(root, out)`
- `prop.nodeFilterAddPred(filter, path, compare, value, reserved, mode)`
- `prop.nodeFilterDelPred(filter, predId)`

Proxied props allow natural property access:

```js
var prop = require('movian/prop');
var root = prop.createRoot('example');
root.metadata.title = 'Title';
```

### `require('movian/settings')`

Settings group helpers.

Exports:

- `new settings.globalSettings(id, title, icon, description)`
- `new settings.kvstoreSettings(nodes, url, domain)`

Settings group methods:

- `group.destroy()`
- `group.dump()`
- `group.createBool(id, title, defaultValue, callback, persistent)`
- `group.createString(id, title, defaultValue, callback, persistent)`
- `group.createInt(id, title, defaultValue, min, max, step, unit, callback, persistent)`
- `group.createDivider(title)`
- `group.createInfo(id, icon, description)`
- `group.createAction(id, title, callback)`
- `group.createMultiOpt(id, title, options, callback, persistent)`

Setting items expose:

- `item.model`
- `item.value`
- `item.enabled`

### `require('movian/service')`

Home-screen service registration.

Exports:

- `service.create(title, url, type, enabled, icon)`

Returned `Service`:

- `service.id`
- `service.enabled`
- `service.destroy()`

### `require('movian/store')`

JSON-backed plugin storage.

Exports:

- `store.create(name)`
- `store.createFromPath(path)`

Returned stores are proxied objects. Assigning a key schedules a JSON write.
Pending writes are flushed by a Duktape finalizer.

### `require('movian/sqlite')`

SQLite wrapper.

Exports:

- `new sqlite.DB(dbname)`

`DB` methods and properties:

- `db.close()`
- `db.query(sql, ...args)`
- `db.step()`
- `db.upgradeSchema(path)`
- `db.lastRowId`
- `db.lastErrorString`
- `db.lastErrorCode`

### `require('movian/itemhook')`

Context/action hooks for browsed items.

Exports:

- `itemhook.create(conf)`

`conf` fields:

- `title`
- `itemtype`
- `icon`
- `handler(item, nav)`

Returned object:

- `destroy()`

### `require('movian/subtitles')`

Subtitle provider helpers.

Exports:

- `subtitles.addProvider(fn)`
- `subtitles.getLanguages()`

Provider callback receives a request object with query fields and:

- `request.addSubtitle(url, title, language, format, source, score)`

### `require('movian/videoscrobbler')`

Video playback event hooks.

Exports:

- `new videoscrobbler.VideoScrobbler()`

Optional callbacks:

- `onstart(data, prop, origin)`
- `onstop(data, prop, origin)`
- `onpause(data, prop, origin)`
- `onresume(data, prop, origin)`

Methods:

- `destroy()`

### `require('movian/xml')`

XML and htsmsg wrapper.

Exports:

- `xml.parse(source)`
- `xml.htsmsg(nativeMessage)`

Returned XML objects are proxied. Useful members:

- field access by name or index
- `length`
- `dump()`
- `filterNodes(name)`

### `require('movian/xmlrpc')`

XML-RPC helper.

Exports:

- `xmlrpc.call(url, method, ...args)`

### `require('movian/popup')`

Small popup wrapper.

Exports:

- `popup.notify(text, delay, icon)`

Use `native/popup` or the legacy `showtime` wrapper for message dialogs,
authentication prompts, and web popups.

## Node-Style Compatibility Modules

### `require('fs')`

Minimal synchronous filesystem helper:

- `fs.writeFileSync(filename, data, opts)`
- `fs.readFileSync(filename, opts)`
- `fs.readdirSync(path)`
- `fs.unlinkSync(filename)`
- `fs.mkdirSync(path)`
- `fs.rmdirSync(path)`

### `require('http')` / `require('https')`

Minimal Node-style request/get helpers:

- `http.request(opts, callback)`
- `http.get(opts, callback)`
- `https.request(opts, callback)`
- `https.get(opts, callback)`

The returned `Request` supports:

- `request.end()`
- `request.on('response', callback)`
- `request.on('error', callback)`

The `Response` supports:

- `response.statusCode`
- `response.bytes`
- `response.setEncoding(encoding)`
- `response.on('data', callback)`
- `response.on('end', callback)`

For Movian plugins, prefer `require('movian/http')` unless a Node-style
compatibility shape is useful.

### `require('url')`

- `url.format(object)`
- `url.parse(url, parseQueryString)`
- `url.resolve(base, url)`

### `require('querystring')`

- `querystring.parse(source)`

### `require('websocket')`

W3C-like client wrapper:

- `new websocket.w3cwebsocket(url, protocol)`
- `socket.send(data)`
- `socket.close()`
- `socket.onopen`
- `socket.oninput`
- `socket.onclose`

## Legacy API v1

When API v1 emulation is active, plugins receive a `plugin` object as `this`
and a global `showtime` object.

### `showtime`

- `showtime.print(...)`
- `showtime.trace(...)`
- `showtime.JSONDecode(source)`
- `showtime.JSONEncode(value)`
- `showtime.httpGet(url, args, headers, ctrl)`
- `showtime.httpReq(url, ctrl, callback)`
- `showtime.currentVersionInt`
- `showtime.currentVersionString`
- `showtime.deviceId`
- `showtime.entityDecode(source)`
- `showtime.queryStringSplit(source)`
- `showtime.pathEscape(source)`
- `showtime.paramEscape(source)`
- `showtime.durationToString(duration)`
- `showtime.message(message, ok, cancel)`
- `showtime.textDialog(message, ok, cancel)`
- `showtime.notify(text, delay, icon)`
- `showtime.probe(url, timeout)`
- `showtime.basename(path)`
- `showtime.sha1digest(source)`
- `showtime.md5digest(source)`
- `new showtime.RichText(value)`
- `showtime.systemIpAddress()`
- `showtime.getSubtitleLanguages()`
- `showtime.xmlrpc(url, method, ...args)`
- `showtime.sleep(seconds)`

### Legacy `plugin`

- `plugin.createService(title, url, type, enabled, icon)`
- `plugin.createStore(name)`
- `plugin.addURI(pattern, callback)`
- `plugin.addSearcher(title, icon, callback)`
- `plugin.path`
- `plugin.getDescriptor()`
- `plugin.getAuthCredentials(source, reason, query, id, forceTemporary)`
- `plugin.addHTTPAuth(url, callback, async)`
- `plugin.copyFile(from, to)`
- `plugin.selectView(filename)`
- `plugin.createSettings(title, icon, description)`
- `plugin.cachePut(stash, key, object, maxage)`
- `plugin.cacheGet(stash, key)`
- `plugin.config`
- `plugin.properties`
- `plugin.addItemHook(conf)`
- `plugin.addSubtitleProvider(fn)`

Compatibility note: some old examples refer to `plugin.subscribe()` and
`page.subscribe()`. Those methods are not exported by the current
`res/ecmascript/legacy/api-v1.js`; use `require('movian/prop')` subscription
helpers instead.

## Native Modules

Native modules are loaded with `require('native/name')`.

### `native/crypto`

- `hashCreate(algo)`
- `hashUpdate(hash, data)`
- `hashFinalize(hash)`

Supported algorithms are `md5`, `sha1`, `sha256`, and `sha512`.

### `native/faprovider`

- `register(name, handlers)`
- `openRespond(handle, ok, value)`
- `readRespond(handle, size)`
- `closeRespond(handle)`
- `statRespond(handle, ok, size, type, mtime)`
- `redirectRespond(handle, ok, url)`
- `setSize(handle, size)`

### `native/fs`

- `open(filename, flags, reserved)`
- `read(handle, buffer, offset, length, position)`
- `write(handle, buffer, offset, length, position)`
- `fsize(handle)`
- `ftruncate(handle, size)`
- `ftrunctae(handle, size)` - legacy compatibility alias for the old misspelling.
- `rename(from, to)`
- `mkdirs(path, reserved)`
- `unlink(path)`
- `rmdir(path)`
- `readdir(path)`
- `dirname(path)`
- `basename(path)`
- `copyfile(from, to)`

### `native/gumbo`

- `parse(source)`
- `nodeType(node)`
- `nodeName(node)`
- `nodeChilds(node, includeAll)`
- `nodeAttributes(node)`
- `nodeTextContent(node)`
- `findById(node, id)`
- `findByTagName(node, tag)`
- `findByClassName(node, className)`

### `native/hook`

- `register(type, callback)`

### `native/htsmsg`

- `createFromXML(source)`
- `get(message, keyOrIndex)`
- `enumerate(message)`
- `length(message)`
- `getName(message, index)`
- `print(message)`

### `native/io`

- `httpReq(url, ctrl, callback)`
- `httpInspectorCreate(url, callback, async)`
- `probe(url, timeout)`
- `xmlrpc(url, method, ...args)`

HTTP inspector objects expose:

- `fail(reason)`
- `proceed()`
- `ignore()`
- `setHeader(key, value)`
- `setCookie(key, value)`

### `native/kvstore`

- `getString(url, domain, key)`
- `getInteger(url, domain, key, defaultValue)`
- `getBoolean(url, domain, key, defaultValue)`
- `set(url, domain, key, value)`

### `native/metadata`

- `videoMetadataBind(rootProp, url, object)`
- `bindPlayInfo(rootProp, url)`

### `native/misc`

- `cachePut(stash, key, bufferOrString, maxage)`
- `cacheGet(stash, key)`
- `systemIpAddress()`
- `selectView(filename)`

### `native/popup`

- `webpopup(url, title, trapUrl)`
- `getAuthCredentials(source, reason, query, id, forceTemporary)`
- `message(message, ok, cancel)`
- `textDialog(message, ok, cancel)`
- `notify(text, delay, icon)`

### `native/prop`

See `require('movian/prop')`; the public wrapper inherits the native module.

### `native/route`

- `create(pattern, callback)`
- `backendOpen(rootProp, url, sync)`
- `test(url)`

### `native/service`

- `create(id, title, url, type, enabled, icon)`
- `enable(service, enabled)`

### `native/sqlite`

- `create(name)`
- `query(db, sql, ...args)`
- `changes(db)`
- `step(db)`
- `lastErrorCode(db)`
- `lastErrorString(db)`
- `lastRowId(db)`
- `upgradeSchema(db, path)`

### `native/string`

- `isUtf8(value)`
- `utf8FromBytes(buffer, encoding)`
- `entityDecode(source)`
- `queryStringSplit(source)`
- `pathEscape(source)`
- `paramEscape(source)`
- `durationToString(duration)`
- `parseTime(source)`
- `parseURL(url, parseQueryString)`
- `resolveURL(base, url)`

### `native/subtitle`

- `addProvider(callback, id, title)`
- `addItem(root, url, title, language, format, source, score, autosel)`
- `getLanguages()`

### `native/websocket`

- `clientCreate(url, protocol, callbacks)`
- `clientSend(socket, data)`
- `serverCreate(port, callbacks)`

## Duktape-Specific API

Plugins can use the Duktape built-ins available in the embedded runtime:

- `Duktape.version`
- `Duktape.Buffer`
- `Duktape.enc(format, value)`
- `Duktape.dec(format, value)`
- `Duktape.fin(object, finalizer)`
- `Duktape.gc()`
- `Duktape.compact(object)`
- `Duktape.modLoaded`
- `Duktape.modSearch`

This branch currently embeds Duktape 1.8.0, so `Duktape.Buffer` and the
Duktape 1.x built-in CommonJS loader are still available.
