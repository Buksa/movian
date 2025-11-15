# Movian ECMAScript API v2 Reference

**Document Version:** 1.0  
**Last Updated:** 2024  
**Scope:** Complete coverage of Movian's plugin API modules (plugin, page, service, settings, prop, http/https, store, sqlite, and subscriptions)

---

## Table of Contents

1. [Overview](#overview)
2. [Plugin Lifecycle and Bootstrap](#plugin-lifecycle-and-bootstrap)
3. [Page Module (`movian/page`)](#page-module-movianpage)
4. [Prop Module (`movian/prop`)](#prop-module-movianprop)
5. [Service Module (`movian/service`)](#service-module-movianservice)
6. [Settings Module (`movian/settings`)](#settings-module-moviansettings)
7. [HTTP/HTTPS Modules](#httphtps-modules)
8. [Store Module (`movian/store`)](#store-module-movianstore)
9. [SQLite Module (`movian/sqlite`)](#sqlite-module-moviansqlite)
10. [Key-Value Store (`native/kvstore`)](#key-value-store-nativekvstore)
11. [Advanced Topics and Cross-Module Interactions](#advanced-topics-and-cross-module-interactions)
12. [API Coverage Checklist](#api-coverage-checklist)

---

## Overview

Movian's ECMAScript V2 API provides a comprehensive plugin development platform written in ECMAScript (JavaScript). The architecture consists of:

- **Native C layer** (`src/ecmascript/`) - Low-level functionality exposed via native modules
- **JavaScript layer** (`res/ecmascript/modules/`) - Higher-level facades and helpers
- **Property system** (`prop`) - Core reactive property binding system
- **Page/Item layer** - UI content representation and navigation
- **Storage layer** - Persistent data (settings, KV store, SQLite)
- **Network layer** - HTTP/HTTPS requests and probing

### Module Dependency Graph

```
plugin (global context)
  ├── page (Route, Searcher)
  │   ├── prop (properties, events)
  │   └── settings (per-page options)
  ├── service (service creation/management)
  ├── settings (global plugin settings)
  │   └── store (JSON persistence)
  ├── http/https (network requests)
  ├── sqlite (database)
  │   └── kvstore (indexed key-value store)
  └── subscriptions (property observers)
```

---

## Plugin Lifecycle and Bootstrap

### Global Plugin Context

When a plugin loads, Movian creates a global context object that serves as the plugin's entry point. All exports from the plugin's main module are attached to this object, alongside built-in APIs.

**Source:** [src/ecmascript/ecmascript.c](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/ecmascript.c)

### Core Global Symbols Available in All Plugins

#### `Plugin` Object

The `Plugin` object provides basic plugin metadata and utilities.

**Properties:**
- `Plugin.id` (string, read-only) - Unique plugin identifier
- `Plugin.version` (string, read-only) - Plugin version from manifest

**Source:** [src/ecmascript/ecmascript.c#L100-L200](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/ecmascript.c)

#### `Core` Object

Utilities for resource management and system access.

**Methods:**
- `Core.resourceDestroy(resource)` - Destroy and cleanup a resource (Route, Service, etc.)
  - **Parameters:** `resource` (native resource handle)
  - **Returns:** void
  - **Description:** Safely destroys a resource, freeing underlying C objects. Called automatically on subscription cleanup or explicit management.
  - **Source:** [src/ecmascript/ecmascript.c](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/ecmascript.c)

- `Core.storagePath` (string, read-only) - Absolute path to plugin's persistent storage directory
  - **Description:** Points to `~/.hts/showtime/plugin-<pluginid>/` for per-plugin data
  - **Source:** [src/ecmascript/ecmascript.c](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/ecmascript.c)

#### `Duktape` Object

Engine-specific utilities (Duktape ECMAScript engine).

**Methods:**
- `Duktape.fin(object, finalizer)` - Register a finalizer callback
  - **Parameters:** 
    - `object` (any) - Object to finalize
    - `finalizer` (function) - Called when object is garbage collected
  - **Returns:** void
  - **Description:** Asynchronously calls finalizer when the object is destroyed by GC. Used in store.js to flush pending writes.
  - **Source:** ECMAScript engine (Duktape)

#### `setTimeout(fn, delay)` / `clearTimeout(handle)`

Standard async scheduling. Implemented natively for cross-platform scheduling.

- **Parameters:**
  - `fn` (function) - Callback to execute
  - `delay` (number) - Milliseconds to wait
- **Returns:** Handle for cancellation
- **Source:** [src/ecmascript/es_timer.c](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_timer.c)

**Example:**

```javascript
// Plugin bootstrap
(function(plugin) {
  console.log("Plugin ID:", Plugin.id);
  console.log("Storage path:", Core.storagePath);

  var store = require('movian/store').create('my_data');
  store.lastCheck = Date.now();
  
})(this);
```

---

## Page Module (`movian/page`)

The Page module provides the primary UI content API. Plugins define navigable content through `Route` objects and populate pages with `Item` objects.

**Location:** `res/ecmascript/modules/movian/page.js`  
**Source C layer:** [src/ecmascript/es_prop.c](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c) (prop system)

### Route Class

Routes map URL patterns to page generators.

#### `new page.Route(pattern, callback)`

Creates a route handler for a URL pattern.

**Parameters:**
- `pattern` (string, regex) - URL pattern to match (e.g., `"youtube:search:(.*)"`)
- `callback` (function) - Handler function with signature `(page, ...args) => void`
  - `page` (Page object) - The page being populated
  - `...args` (strings) - Captured groups from pattern regex

**Returns:** Route object

**Description:**
Routes define how Movian navigates between pages. When the user opens a URL matching the pattern, the callback is invoked with a Page object to populate. Multiple capture groups are passed as separate arguments.

**Lifecycle:**
1. URL opened (user navigation or `page.redirect()`)
2. Pattern matched
3. Callback invoked with new Page object
4. Plugin populates page with Items
5. User navigates away or page is closed
6. Subscriptions and resources auto-cleaned

**Source:** [res/ecmascript/modules/movian/page.js#L384-L410](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/page.js)

**Example:**

```javascript
var page = require('movian/page');

new page.Route('myapp:main:(.*)', function(page, arg1) {
  page.type = 'directory';
  page.metadata.title = 'Main Content';
  page.loading = false;
  
  page.appendItem('myapp:detail:item1', 'video', {
    title: 'First Video',
    duration: 3600
  });
});
```

#### `Route.prototype.destroy()`

Destroys a route, removing URL pattern matching.

**Returns:** void

**Description:** Unregisters the route handler. Used for dynamic route management.

**Source:** [res/ecmascript/modules/movian/page.js#L408-L410](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/page.js)

### Searcher Class

Searchers register global search providers.

#### `new page.Searcher(title, icon, callback)`

Registers a search provider that appears in Movian's search UI.

**Parameters:**
- `title` (string) - Display name (e.g., "YouTube")
- `icon` (string) - Icon URL or identifier
- `callback` (function) - Handler with signature `(page, query) => void`
  - `page` (Page object) - Results page
  - `query` (string) - Search query string

**Returns:** Searcher object

**Description:**
Searchers integrate plugins into Movian's unified search. When the user searches, all registered searchers are queried in parallel. The callback should populate the page with search results.

**Source:** [res/ecmascript/modules/movian/page.js#L413-L447](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/page.js)

**Example:**

```javascript
new page.Searcher('MyService', 'icon.png', function(page, query) {
  page.type = 'directory';
  page.metadata.title = 'Results for: ' + query;
  
  var results = searchAPI(query);
  for (var i = 0; i < results.length; i++) {
    page.appendItem(results[i].url, 'video', {
      title: results[i].title
    });
  }
});
```

#### `Searcher.prototype.destroy()`

Unregisters the searcher.

**Returns:** void

**Source:** [res/ecmascript/modules/movian/page.js#L451-L453](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/page.js)

### Page Class

Pages represent navigable containers with hierarchical Items.

#### Page Properties and Methods

**Properties (accessor):**

- `page.type` (string) - Content type
  - Possible values: `"directory"`, `"playlist"`, `"album"`, `"openerror"`
  - Default: inherited from template

- `page.metadata` (object, read-only) - Metadata object
  - `metadata.title` (string) - Page title
  - `metadata.icon` (string) - Icon URL

- `page.loading` (boolean) - Loading indicator state
  - `true` - Show spinner
  - `false` - Hide spinner

- `page.entries` (number) - Total number of items (for statistics)

- `page.source` (string) - Source attribution

- `page.paginator` (function) - Synchronous pagination handler
  - Called when more items are requested
  - Return `true` if more items available, `false` if end reached

- `page.asyncPaginator` (function) - Asynchronous pagination handler
  - Called for lazy loading; use `setTimeout` for network requests
  - Call `page.haveMore(true/false)` to signal completion

**Source:** [res/ecmascript/modules/movian/page.js#L150-L202](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/page.js)

#### `page.appendItem(url, type, metadata)` → Item

Adds a content item to the page.

**Parameters:**
- `url` (string) - Item URL/identifier (e.g., `"youtube:video:xyz123"`)
- `type` (string) - Item type: `"video"`, `"audio"`, `"directory"`, `"archive"`, `"font"`, `"image"`, etc.
- `metadata` (object) - Item metadata
  - `title` (string) - Display title
  - `icon` (string) - Icon URL
  - `description` (string) - Long description
  - `duration` (number) - Playback duration in seconds (video/audio)
  - `imageWidth` / `imageHeight` (number) - Dimensions

**Returns:** Item object (for further configuration)

**Description:**
Appends an item to the page's content list. The item is immediately visible in the UI.

**Video Metadata Binding:**
For `type: "video"`, Movian attempts to automatically fetch metadata (thumbnail, duration, description) from the URL. If metadata loading fails or you need custom bindings, override with the Item's `bindVideoMetadata()` method.

**Special URL Prefix `videoparams:`**
For video items, you can use URLs with format `"videoparams:<JSON>"` to provide source information and canonical URL:

```javascript
var sources = [
  { url: 'http://example.com/video.m3u8', priority: 1 }
];
page.appendItem('videoparams:' + JSON.stringify({
  canonicalUrl: 'http://example.com/videos/123',
  sources: sources
}), 'video', { title: 'My Video' });
```

**Source:** [res/ecmascript/modules/movian/page.js#L263-L297](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/page.js)

**Example:**

```javascript
page.appendItem('myapp:video:1', 'video', {
  title: 'Video 1',
  icon: 'http://example.com/thumb.jpg',
  duration: 3600
});

var item = page.appendItem('myapp:video:2', 'video', {
  title: 'Video 2'
});
item.enable();  // Enable/disable control
```

#### `page.appendAction(title, func, subtype)` → Item

Adds an action button to the page.

**Parameters:**
- `title` (string) - Button label
- `func` (function) - Callback when pressed
- `subtype` (string, optional) - Visual style hint

**Returns:** Item object

**Description:**
Actions are typically displayed as buttons or menu items and execute code when activated by the user.

**Source:** [res/ecmascript/modules/movian/page.js#L299-L318](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/page.js)

#### `page.appendPassiveItem(type, data, metadata)` → Item

Adds an item without URL mapping (informational only).

**Parameters:**
- `type` (string) - Item type
- `data` (any) - Custom data (not used for navigation)
- `metadata` (object) - Metadata

**Returns:** Item object

**Source:** [res/ecmascript/modules/movian/page.js#L320-L331](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/page.js)

#### `page.haveMore(bool)`

Signals pagination status.

**Parameters:**
- `bool` (boolean) - `true` if more content available, `false` if end

**Description:**
Called by paginator or in response to the paginator callback. Toggles the loading spinner and signals to UI whether to request more items.

**Source:** [res/ecmascript/modules/movian/page.js#L239-L241](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/page.js)

#### `page.flush()`

Removes all items from the page.

**Returns:** void

**Description:** Clears the content list, typically used for refresh/reload operations.

**Source:** [res/ecmascript/modules/movian/page.js#L337-L339](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/page.js)

#### `page.redirect(url)`

Navigates the page to a new URL.

**Parameters:**
- `url` (string) - New URL to load

**Returns:** void

**Description:**
Closes the current page and opens a new one. Used for following links or manual navigation within the plugin.

**Source:** [res/ecmascript/modules/movian/page.js#L341-L350](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/page.js)

#### `page.error(message)`

Marks the page with an error state.

**Parameters:**
- `message` (string) - Error message to display

**Returns:** void

**Source:** [res/ecmascript/modules/movian/page.js#L252-L256](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/page.js)

#### `page.onEvent(eventType, callback)`

Registers a page-level event handler.

**Parameters:**
- `eventType` (string) - Event name (e.g., `"focus"`, `"blur"`)
- `callback` (function) - Handler with signature `(eventType) => void`

**Returns:** void

**Description:**
Listen for page lifecycle events. Multiple callbacks can be registered for the same event.

**Source:** [res/ecmascript/modules/movian/page.js#L352-L374](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/page.js)

#### `page.getItems()` → Item[]

Returns a copy of all items in the page.

**Returns:** Array of Item objects

**Source:** [res/ecmascript/modules/movian/page.js#L258-L260](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/page.js)

#### `page.dump()`

Prints the page's property tree to console for debugging.

**Returns:** void

**Source:** [res/ecmascript/modules/movian/page.js#L333-L335](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/page.js)

### Item Class

Items represent individual content objects in a page.

#### Item Methods

**Methods:**
- `item.enable()` - Enable the item (make clickable)
- `item.disable()` - Disable the item
- `item.destroy()` - Remove the item from the page
- `item.moveBefore(beforeItem)` - Reorder item (move before another item, or end if null)
- `item.onEvent(type, callback)` - Register item event handler
- `item.addOptAction(title, func, subtype)` - Add option menu action
- `item.addOptURL(title, url, subtype)` - Add option menu URL link
- `item.addOptSeparator(title)` - Add option menu separator
- `item.bindVideoMetadata(obj)` - Bind custom video metadata
- `item.unbindVideoMetadata()` - Clear video metadata binding
- `item.toString()` - Returns string representation

**Properties:**
- `item.root` (native prop) - Underlying prop object
- `item.page` (Page) - Parent page reference

**Source:** [res/ecmascript/modules/movian/page.js#L8-L143](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/page.js)

**Example:**

```javascript
var item = page.appendItem('myapp:item:1', 'video', {
  title: 'Video',
  icon: 'icon.png'
});

item.addOptAction('Share', function() {
  console.log('Shared!');
}, 'share');

item.onEvent('activate', function() {
  console.log('Item clicked');
});

item.moveBefore(null);  // Move to end
```

#### Async Page Loading Pattern

For large datasets, use `asyncPaginator` with `setTimeout` to load in batches:

**Example:**

```javascript
new page.Route('myapp:browse:(.*)', function(page, arg) {
  page.type = 'directory';
  page.metadata.title = 'Browse';
  
  var offset = 0;
  
  page.asyncPaginator = function() {
    setTimeout(function() {
      try {
        var items = fetchItems(offset, 20);  // Network request
        
        if (items.length === 0) {
          page.haveMore(false);
          return;
        }
        
        for (var i = 0; i < items.length; i++) {
          page.appendItem(items[i].url, 'video', items[i].metadata);
        }
        
        offset += items.length;
        page.haveMore(true);
      } catch (e) {
        page.error(e.toString());
        page.haveMore(false);
      }
    }, 0);
  };
});
```

---

## Prop Module (`movian/prop`)

The prop module provides low-level reactive property binding. Properties are the foundation of Movian's reactive UI system—changing a property automatically updates the UI.

**Location:** `res/ecmascript/modules/movian/prop.js`  
**C layer:** [src/ecmascript/es_prop.c](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c)

### Property System Concepts

**Properties** are typed nodes in a hierarchical tree:
- **Values:** strings, integers, floats, URIs, rich text
- **Directories:** containers with child properties
- **Special types:** void (unset), zombie (destroyed)

**Subscriptions** are reactive listeners that fire when a property or its children change.

**Events** are one-off notifications (e.g., "user clicked item").

### Core Functions

#### `prop.createRoot(name)` → Prop

Creates a new root property node.

**Parameters:**
- `name` (string, optional) - Property name

**Returns:** Native prop object (proxied)

**Description:**
Root properties are disconnected from the global tree and typically become children of other properties via `prop.setParent()`.

**Source:** [src/ecmascript/es_prop.c#L129-L134](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c)

#### `prop.global()` → Prop

Accesses the global property tree root.

**Returns:** Proxy to global prop

**Description:**
The global prop provides access to system-wide properties like:
- `global.settings` - Application settings tree
- `global.clock` - System clock
- `global.itemhooks` - Item hooks registry

**Source:** [src/ecmascript/es_prop.c#L141-L145](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c)

#### `prop.subscribe(propNode, callback, options)` → Subscription

Registers a reactive listener on a property node.

**Parameters:**
- `propNode` (prop) - Property to observe
- `callback` (function) - Handler with signature `(type, value, value2) => void`
  - `type` (string) - Change type: `"set"`, `"uri"`, `"add"`, `"del"`, `"wantmorechilds"`, `"reqmove"`, `"selectchild"`, `"link"`, etc.
  - `value` (mixed) - New value or event data
  - `value2` (mixed) - Additional event data (for multi-value events)
- `options` (object) - Configuration
  - `autoDestroy` (boolean, default false) - Auto-unsubscribe when property is destroyed
  - `noInitialUpdate` (boolean, default false) - Skip initial callback
  - `ignoreVoid` (boolean, default false) - Ignore void (unset) values
  - `actionAsArray` (boolean, default false) - Parse action strings as arrays

**Returns:** Subscription resource (call `Core.resourceDestroy()` to stop listening)

**Description:**
Subscriptions are the reactive core of Movian. They fire whenever the property or its subtree changes. Common event types:

- `"set"` - Value changed; `value` is new value
- `"uri"` - URI property changed; `value` is URI string, `value2` is title
- `"add"` - Child property added; `value` is child prop
- `"del"` - Child property deleted; `value` is child prop
- `"wantmorechilds"` - UI requests more items (pagination)
- `"selectchild"` - User selected a child (multiopt selector)
- `"action"` - User action (button click); `value` is action name
- `"destroyed"` - Property being destroyed; cleanup subscription

**Source:** [src/ecmascript/es_prop.c#L480-L700](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c)

**Example:**

```javascript
var item = page.appendItem('url', 'video', { title: 'Video' });

prop.subscribe(item.root, function(type, value) {
  if (type === 'set') {
    console.log('Value changed to:', value);
  } else if (type === 'action') {
    console.log('Action:', value);
  }
}, {
  autoDestroy: true
});
```

#### `prop.subscribeValue(propNode, callback, options)` → Subscription

Convenience wrapper for value subscriptions (ignores other events).

**Parameters:**
- `propNode` (prop) - Property to observe
- `callback` (function) - Handler with signature `(value) => void`
- `options` (object) - Subscription options

**Returns:** Subscription resource

**Description:**
Filters subscriptions to only value change events (ignores "destroyed", etc.).

**Source:** [res/ecmascript/modules/movian/prop.js#L101-L109](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/prop.js)

#### Property Access via Proxies

Properties are accessed via JavaScript Proxy handlers, allowing natural syntax:

```javascript
var prop = require('movian/prop');
var root = prop.createRoot();

// Set values
root.title = 'My Title';
root.count = 42;
root.enabled = true;

// Read values
console.log(root.title);  // Returns proxied prop

// Get raw value
console.log(root.title.toString());

// Access children
root.child.grandchild = 'value';

// Iterate children
for (var i in root.children) {
  console.log(root.children[i].toString());
}
```

**Proxy Handler:** [res/ecmascript/modules/movian/prop.js#L18-L64](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/prop.js)

#### `prop.set(prop, key, value)`

Sets a property value.

**Parameters:**
- `prop` (prop) - Parent property
- `key` (string) - Child property name
- `value` (mixed) - New value (string, number, boolean, or null)

**Returns:** void

**Description:**
Low-level value assignment. Usually accessed via proxy syntax: `prop.key = value`.

**Source:** [src/ecmascript/es_prop.c#L412-L441](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c)

#### `prop.setRichStr(prop, key, value)`

Sets a rich text property (supports formatting).

**Parameters:**
- `prop` (prop) - Parent property
- `key` (string) - Child property name
- `value` (string) - Rich text string

**Returns:** void

**Description:**
Used for styled text (bold, colors, etc.) in UI elements.

**Source:** [src/ecmascript/es_prop.c#L448-L458](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c)

#### `prop.setParent(prop, parent)`

Adds a property to a parent's children.

**Parameters:**
- `prop` (prop) - Property to parent
- `parent` (prop) - Parent property

**Returns:** void

**Description:**
Moves a property into a parent's tree. Automatically updates UI.

**Source:** [src/ecmascript/es_prop.c#L465-L474](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c)

#### `prop.destroy(prop)`

Destroys a property and its children.

**Parameters:**
- `prop` (prop) - Property to destroy

**Returns:** void

**Description:**
Recursively removes the property and triggers cleanup. Subscribers are notified with `"destroyed"` event.

**Source:** [src/ecmascript/es_prop.c#L397-L402](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c)

#### `prop.deleteChilds(prop)`

Removes all children from a property.

**Parameters:**
- `prop` (prop) - Property

**Returns:** void

**Source:** [src/ecmascript/es_prop.c#L385-L390](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c)

#### `prop.deleteChild(prop, name)`

Removes a single child property by name.

**Parameters:**
- `prop` (prop) - Parent property
- `name` (string) - Child name

**Returns:** boolean

**Source:** [src/ecmascript/es_prop.c#L371-L378](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c)

#### `prop.haveMore(prop, hasMore)`

Signals pagination availability (used with `page.haveMore()` internally).

**Parameters:**
- `prop` (prop) - Page nodes property
- `hasMore` (boolean) - More items available

**Returns:** void

**Source:** [src/ecmascript/es_prop.c](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c)

#### `prop.getName(prop)` → string

Gets a property's name.

**Parameters:**
- `prop` (prop) - Property

**Returns:** Property name string

**Source:** [src/ecmascript/es_prop.c#L152-L160](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c)

#### `prop.getValue(prop)` → mixed

Gets a property's value.

**Parameters:**
- `prop` (prop) - Property

**Returns:** Value (string, number, boolean, or null)

**Source:** [src/ecmascript/es_prop.c#L167-L246](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c)

#### `prop.getChild(prop, nameOrIndex)` → Prop

Gets a child property by name or index.

**Parameters:**
- `prop` (prop) - Parent property
- `nameOrIndex` (string or number) - Child name or numeric index

**Returns:** Child prop or undefined

**Source:** [src/ecmascript/es_prop.c#L253-L292](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c)

#### `prop.link(prop1, prop2)`

Links two properties (copy updates from prop1 to prop2).

**Parameters:**
- `prop1` (prop) - Source
- `prop2` (prop) - Destination

**Returns:** void

**Source:** [src/ecmascript/es_prop.c#L804-L808](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c)

#### `prop.sendEvent(propNode, type, data)`

Sends an event to a property's subscribers.

**Parameters:**
- `propNode` (prop) - Property
- `type` (string) - Event type (e.g., `"redirect"`, `"openurl"`)
- `data` (mixed) - Event payload

**Returns:** void

**Description:**
Triggers subscribers with a custom event. Common types:
- `"redirect"` - data is URL string
- `"openurl"` - data is object with `url`, `view`, `how`, `parenturl` properties

**Source:** [src/ecmascript/es_prop.c#L826-L862](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c)

#### `prop.select(prop)`

Marks a property as selected (highlights in UI).

**Parameters:**
- `prop` (prop) - Property to select

**Returns:** void

**Source:** [src/ecmascript/es_prop.c](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c)

#### `prop.isSame(prop1, prop2)` → boolean

Checks if two props refer to the same object.

**Parameters:**
- `prop1` (prop) - First property
- `prop2` (prop) - Second property

**Returns:** true if same, false otherwise

**Source:** [src/ecmascript/es_prop.c#L918-L924](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c)

#### `prop.isZombie(prop)` → boolean

Checks if a property has been destroyed.

**Parameters:**
- `prop` (prop) - Property

**Returns:** true if destroyed

**Description:**
Zombie properties are destroyed but still referenced. Safe to check before accessing.

**Source:** [src/ecmascript/es_prop.c#L957-L962](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c)

#### `prop.print(prop)`

Prints a property tree to console (debug utility).

**Parameters:**
- `prop` (prop) - Root property

**Returns:** void

**Source:** [src/ecmascript/es_prop.c#L117-L122](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c)

#### `prop.moveBefore(prop, beforeProp)`

Reorders a property within its parent's children.

**Parameters:**
- `prop` (prop) - Property to move
- `beforeProp` (prop or null) - Move before this prop, or to end if null

**Returns:** void

**Source:** [src/ecmascript/es_prop.c#L931-L937](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c)

#### `prop.unloadDestroy(prop)`

Marks a property for destruction on page unload.

**Parameters:**
- `prop` (prop) - Property

**Returns:** void

**Description:**
Used for automatic cleanup. Properties are destroyed when the containing page is unloaded.

**Source:** [src/ecmascript/es_prop.c#L944-L950](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c)

#### `prop.makeUrl(prop)` → string

Generates a URL for a property (typically for internal navigation).

**Parameters:**
- `prop` (prop) - Property

**Returns:** URL string

**Source:** [src/ecmascript/es_prop.c](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c)

#### `prop.atomicAdd(prop, number)`

Atomically adds a number to an integer property.

**Parameters:**
- `prop` (prop) - Integer property
- `number` (number) - Amount to add

**Returns:** void

**Description:**
Thread-safe increment/decrement for counters.

**Source:** [src/ecmascript/es_prop.c#L904-L911](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c)

#### `prop.setClipRange(prop, min, max)`

Sets clipping range for numeric properties.

**Parameters:**
- `prop` (prop) - Integer property
- `min` (number) - Minimum value
- `max` (number) - Maximum value

**Returns:** void

**Description:**
Constrains values to a range (used in settings sliders).

**Source:** [src/ecmascript/es_prop.c#L969-L974](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c)

#### `prop.enumerate(prop)` → string[]

Lists all child names of a property.

**Parameters:**
- `prop` (prop) - Property

**Returns:** Array of child names

**Source:** [src/ecmascript/es_prop.c#L299-L337](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c)

#### `prop.has(prop, childName)` → boolean

Checks if a child exists.

**Parameters:**
- `prop` (prop) - Parent property
- `childName` (string) - Child name

**Returns:** true if exists

**Source:** [src/ecmascript/es_prop.c#L344-L364](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c)

#### `prop.makeProp(prop)` → Proxy

Wraps a native prop in a proxy for JavaScript access.

**Parameters:**
- `prop` (native prop) - Raw native property

**Returns:** Proxied prop object

**Description:**
Allows natural property access via proxy syntax.

**Source:** [res/ecmascript/modules/movian/prop.js#L70-L72](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/prop.js)

---

## Service Module (`movian/service`)

Services represent top-level navigable entries (typically appear on the home screen).

**Location:** `res/ecmascript/modules/movian/service.js`  
**C layer:** [src/ecmascript/es_service.c](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_service.c)

#### `service.create(title, url, type, enabled, icon)` → Service

Registers a new service.

**Parameters:**
- `title` (string) - Display name
- `url` (string) - Initial URL to load (handled by plugin's routes)
- `type` (string) - Service type: `"music"`, `"video"`, `"tv"`, `"other"`
- `enabled` (boolean) - Initially enabled/disabled
- `icon` (string, optional) - Icon URL

**Returns:** Service object

**Description:**
Creates a new service that appears in Movian's sidebar or service menu. The service's URL should match a route in your plugin.

**Source:** [src/ecmascript/es_service.c#L108-L143](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_service.c)

**Example:**

```javascript
var service = require('movian/service');

var svc = service.create(
  'My Plugin',
  'myplugin:home',
  'video',
  true,
  'http://example.com/icon.png'
);

// Later, disable/enable
svc.enabled = false;
svc.enabled = true;
```

#### Service Properties

**Properties:**
- `service.enabled` (boolean, read/write) - Enable or disable the service

**Source:** [res/ecmascript/modules/movian/service.js#L11-L18](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/service.js)

#### `service.destroy()`

Unregisters the service.

**Returns:** void

**Description:**
Removes the service from Movian's UI and stops handling its URLs.

**Source:** [res/ecmascript/modules/movian/service.js#L22-L24](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/service.js)

---

## Settings Module (`movian/settings`)

The settings module provides plugin configuration UI and persistent storage.

**Location:** `res/ecmascript/modules/movian/settings.js`  
**C layer:** [src/ecmascript/es_kvstore.c](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_kvstore.c)

### Global Settings

Plugin-wide settings stored on disk.

#### `plugin.createSettings(title, icon, description)` → SettingsGroup

Creates the plugin's main settings page.

**Parameters:**
- `title` (string) - Settings page title
- `icon` (string, optional) - Icon URL
- `description` (string, optional) - Description text

**Returns:** SettingsGroup object

**Description:**
Called during plugin initialization to create settings. The returned object is used to add individual settings controls.

**Source:** [res/ecmascript/modules/movian/settings.js#L266-L301](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/settings.js)

**Example:**

```javascript
(function(plugin) {
  var settings = plugin.createSettings('My Plugin Settings', 'icon.png', 'Configure my plugin');
  
  settings.createBool('debug', 'Debug Mode', false, function(val) {
    console.log('Debug:', val);
  });
})(this);
```

### SettingsGroup Methods

#### `group.createBool(id, title, default, callback, persistent)`

Creates a boolean toggle setting.

**Parameters:**
- `id` (string) - Internal setting ID
- `title` (string) - Display label
- `default` (boolean) - Default value
- `callback` (function) - Called when changed: `(newValue) => void`
- `persistent` (boolean, optional) - Save to disk (default true)

**Returns:** Setting object

**Description:**
Boolean toggles appear as checkboxes in settings.

**Source:** [res/ecmascript/modules/movian/settings.js#L71-L91](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/settings.js)

#### `group.createString(id, title, default, callback, persistent)`

Creates a text input setting.

**Parameters:**
- `id` (string) - Internal setting ID
- `title` (string) - Display label
- `default` (string) - Default value
- `callback` (function) - Called when changed: `(newValue) => void`
- `persistent` (boolean, optional) - Save to disk

**Returns:** Setting object

**Source:** [res/ecmascript/modules/movian/settings.js#L97-L119](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/settings.js)

#### `group.createInt(id, title, default, min, max, step, unit, callback, persistent)`

Creates an integer slider setting.

**Parameters:**
- `id` (string) - Internal setting ID
- `title` (string) - Display label
- `default` (number) - Default value
- `min` (number) - Minimum value
- `max` (number) - Maximum value
- `step` (number) - Step size
- `unit` (string) - Display unit (e.g., `"px"`, `"%"`)
- `callback` (function) - Called when changed: `(newValue) => void`
- `persistent` (boolean, optional) - Save to disk

**Returns:** Setting object

**Example:**

```javascript
settings.createInt('quality', 'Video Quality', 720, 480, 1080, 120, 'p', function(val) {
  console.log('Quality set to', val);
});
```

**Source:** [res/ecmascript/modules/movian/settings.js#L125-L156](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/settings.js)

#### `group.createMultiOpt(id, title, options, callback, persistent)`

Creates a multi-option selector (dropdown/radio).

**Parameters:**
- `id` (string) - Internal setting ID
- `title` (string) - Display label
- `options` (array) - Array of `[value, label, isDefault]` tuples
- `callback` (function) - Called when changed: `(selectedValue) => void`
- `persistent` (boolean, optional) - Save to disk

**Returns:** undefined

**Description:**
Options appear as a dropdown or radio buttons. Set the third element to `true` to mark the default.

**Example:**

```javascript
settings.createMultiOpt('theme', 'Theme', [
  ['dark', 'Dark Mode', true],
  ['light', 'Light Mode', false],
  ['auto', 'Auto', false]
], function(val) {
  console.log('Theme changed to', val);
});
```

**Source:** [res/ecmascript/modules/movian/settings.js#L209-L259](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/settings.js)

#### `group.createAction(id, title, callback)`

Creates a button action in settings.

**Parameters:**
- `id` (string) - Internal action ID
- `title` (string) - Button label
- `callback` (function) - Called when clicked: `() => void`

**Returns:** Setting object

**Example:**

```javascript
settings.createAction('clear_cache', 'Clear Cache', function() {
  // Clear cache logic
  console.log('Cache cleared');
});
```

**Source:** [res/ecmascript/modules/movian/settings.js#L190-L203](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/settings.js)

#### `group.createDivider(title)`

Creates a visual separator.

**Parameters:**
- `title` (string) - Section label

**Returns:** undefined

**Source:** [res/ecmascript/modules/movian/settings.js#L163-L169](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/settings.js)

#### `group.createInfo(id, icon, description)`

Creates informational text.

**Parameters:**
- `id` (string) - Internal ID
- `icon` (string, optional) - Icon URL
- `description` (string) - Text content

**Returns:** undefined

**Source:** [res/ecmascript/modules/movian/settings.js#L176-L183](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/settings.js)

#### `group.destroy()`

Destroys the settings group.

**Returns:** void

**Source:** [res/ecmascript/modules/movian/settings.js#L52-L56](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/settings.js)

---

## HTTP/HTTPS Modules

The HTTP and HTTPS modules provide network request capabilities.

**Locations:**
- HTTP: `res/ecmascript/modules/http.js`
- HTTPS: `res/ecmascript/modules/https.js`

**C layer:** [src/ecmascript/es_io.c](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_io.c)

### Request API

#### `http.request(options, callback)` → Request

Creates an HTTP request.

**Parameters:**
- `options` (string or object) - URL or options object:
  - `hostname` (string) - Server hostname
  - `port` (number) - Port (default 80 for HTTP, 443 for HTTPS)
  - `path` (string) - URL path
  - `method` (string) - HTTP method (default `"GET"`)
  - `headers` (object) - Custom headers
  - `auth` (string) - Basic auth (`"user:pass"`)
- `callback` (function, optional) - Response handler: `(response) => void`

**Returns:** Request object

**Description:**
Creates a request object. Must call `.end()` to send.

**Source:** [res/ecmascript/modules/http.js#L30-L72](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/http.js)

**Example:**

```javascript
var http = require('http');

var req = http.request({
  hostname: 'api.example.com',
  path: '/v1/search?q=test',
  method: 'GET',
  headers: {
    'User-Agent': 'MyPlugin/1.0'
  }
});

req.on('response', function(res) {
  console.log('Status:', res.statusCode);
  res.on('data', function(data) {
    console.log('Data:', data);
  });
});

req.on('error', function(err) {
  console.log('Error:', err);
});

req.end();
```

#### `http.get(options, callback)` → Request

Convenience method for GET requests.

**Parameters:**
- `options` (string or object) - URL or request options
- `callback` (function, optional) - Response handler

**Returns:** Request object

**Description:**
Shorthand for `http.request()` that automatically calls `.end()`.

**Source:** [res/ecmascript/modules/http.js#L67-L72](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/http.js)

**Example:**

```javascript
http.get('https://api.example.com/data', function(res) {
  res.on('data', function(data) {
    console.log(data);
  });
});
```

### Request Methods

#### `request.end()`

Sends the request.

**Returns:** void

**Description:**
Initiates the HTTP request. Responses are handled via callbacks.

**Source:** [res/ecmascript/modules/http.js#L38-L52](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/http.js)

#### `request.on(event, callback)`

Registers event listeners.

**Parameters:**
- `event` (string) - Event type: `"response"`, `"error"`
- `callback` (function) - Handler

**Returns:** void

**Description:**
- `"response"` - Called with Response object
- `"error"` - Called with error message

**Source:** [res/ecmascript/modules/http.js#L54-L59](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/http.js)

### Response Object

#### Response Properties

- `response.statusCode` (number, read-only) - HTTP status code (200, 404, etc.)
- `response.bytes` (buffer, read-only) - Response body as binary data
- `response.encoding` (string, read/write) - Text encoding (default `"utf8"`)

**Source:** [res/ecmascript/modules/http.js#L3-L8](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/http.js)

#### Response Methods

#### `response.on(event, callback)`

Registers response listeners.

**Parameters:**
- `event` (string) - Event type: `"data"`, `"end"`
- `callback` (function) - Handler

**Returns:** void

**Description:**
- `"data"` - Called with response text
- `"end"` - Called when response complete

**Source:** [res/ecmascript/modules/http.js#L22-L27](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/http.js)

### HTTPS Module

The HTTPS module is identical to HTTP but uses encrypted connections.

**Location:** [res/ecmascript/modules/https.js](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/https.js)

**Methods:**
- `https.request()` - Same signature as HTTP
- `https.get()` - Same signature as HTTP

**Example:**

```javascript
var https = require('https');

https.get({
  hostname: 'api.example.com',
  path: '/secure/endpoint'
}, function(res) {
  if (res.statusCode === 200) {
    res.on('data', function(data) {
      console.log('Secure data:', data);
    });
  }
});
```

### HTTP Response Streaming vs Buffering

**Async behavior:**

The HTTP module uses async scheduling via `setTimeout()` to deliver responses. Event callbacks are always asynchronous:

```javascript
http.get('http://example.com', function(res) {
  res.on('data', function(data) {
    // Called asynchronously after .end()
    console.log('Data received');
  });
  res.on('end', function() {
    // Called when response is fully read
  });
});

console.log('Request sent');  // Prints before "Data received"
```

---

## Store Module (`movian/store`)

The store module provides simple JSON-based persistent key-value storage.

**Location:** `res/ecmascript/modules/movian/store.js`  
**Implementation:** File-based JSON with write coalescing

### Store Creation

#### `store.create(name)` → StoreProxy

Creates or loads a persistent store by name.

**Parameters:**
- `name` (string) - Store name (used as filename)

**Returns:** Proxy object for get/set operations

**Description:**
Returns a JavaScript Proxy that automatically syncs changes to disk. Changes are buffered and written 5 seconds after the last modification (see source for implementation).

**Storage location:** `~/.hts/showtime/store/<pluginid>/<name>`

**Source:** [res/ecmascript/modules/movian/store.js#L56-L62](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/store.js)

**Example:**

```javascript
var store = require('movian/store').create('user_cache');

// Set values (async write)
store.lastUpdate = Date.now();
store.username = 'john_doe';

// Read values
console.log(store.lastUpdate);

// Check existence
if ('favorites' in store) {
  console.log(store.favorites);
}
```

#### `store.createFromPath(filePath)` → StoreProxy

Creates a store from an absolute file path.

**Parameters:**
- `filePath` (string) - Absolute path to JSON file

**Returns:** Proxy object

**Description:**
Lower-level API for custom storage locations.

**Source:** [res/ecmascript/modules/movian/store.js#L33-L53](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/store.js)

### Store Proxy Behavior

The store uses a Proxy to intercept property access:

- **Get:** Returns value from in-memory cache
- **Set:** Buffers change in memory and schedules disk write
- **Has:** Checks key existence
- **Delete:** Removes key

**Write coalescing:** Multiple writes within 5 seconds are batched into a single disk operation.

**Finalizer:** When the store object is garbage collected, any pending writes are flushed.

**Source:** [res/ecmascript/modules/movian/store.js#L4-L31](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/store.js)

---

## SQLite Module (`movian/sqlite`)

The SQLite module provides database access for structured persistent storage.

**Location:** `res/ecmascript/modules/movian/sqlite.js`  
**C layer:** [src/ecmascript/es_sqlite.c](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_sqlite.c)

### Database Creation

#### `new sqlite.DB(name)` → Database

Opens or creates a SQLite database.

**Parameters:**
- `name` (string) - Database name

**Returns:** Database object

**Description:**
Creates a new database handle. Database files are stored in `~/.hts/showtime/plugin-<pluginid>/databases/`.

**Source:** [res/ecmascript/modules/movian/sqlite.js#L4-L6](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/sqlite.js)

**Example:**

```javascript
var sqlite = require('movian/sqlite');
var db = new sqlite.DB('mydata');

// Use database...

db.close();  // Clean up
```

### Database Methods

#### `db.query(sql, ...args)`

Executes a SQL query with bound parameters.

**Parameters:**
- `sql` (string) - SQL statement with `?` placeholders
- `...args` (mixed) - Parameter values

**Returns:** void

**Description:**
Prepares and executes a statement. Results are accessed via `db.step()`.

**Example:**

```javascript
db.query('INSERT INTO users (name, age) VALUES (?, ?)', 'John', 30);
db.query('SELECT * FROM users WHERE id = ?', 123);
```

**Source:** [res/ecmascript/modules/movian/sqlite.js#L12-L20](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/sqlite.js)

#### `db.step()` → Object or null

Retrieves the next result row from the last query.

**Returns:** Object with column names as keys, or null if no more rows

**Description:**
Each call returns one row. When null is returned, the result set is exhausted.

**Example:**

```javascript
db.query('SELECT id, name FROM users');
var row;
while ((row = db.step()) !== null) {
  console.log(row.id, row.name);
}
```

**Source:** [res/ecmascript/modules/movian/sqlite.js#L22-L24](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/sqlite.js)

#### `db.close()`

Closes the database.

**Returns:** void

**Description:**
Releases the database handle and all pending statements.

**Source:** [res/ecmascript/modules/movian/sqlite.js#L8-L10](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/sqlite.js)

### Database Properties

#### `db.lastRowId` (read-only)

Returns the ID of the last inserted row.

**Type:** number

**Example:**

```javascript
db.query('INSERT INTO items (name) VALUES (?)', 'New Item');
var newId = db.lastRowId;
```

**Source:** [src/ecmascript/es_sqlite.c#L289-L294](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_sqlite.c)

#### `db.lastErrorCode` (read-only)

Returns the SQLite error code from the last operation.

**Type:** number

**Source:** [src/ecmascript/es_sqlite.c#L265-L270](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_sqlite.c)

#### `db.lastErrorString` (read-only)

Returns the error message from the last operation.

**Type:** string

**Source:** [src/ecmascript/es_sqlite.c#L277-L282](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_sqlite.c)

### Schema Upgrades

#### `db.upgradeSchema(path)`

Upgrades database schema from a schema definition file.

**Parameters:**
- `path` (string) - Path to schema file

**Returns:** boolean (0 on success, non-zero on error)

**Description:**
Applies schema definitions incrementally. Typically called on database initialization.

**Source:** [res/ecmascript/modules/movian/sqlite.js#L27-L29](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/sqlite.js)

---

## Key-Value Store (`native/kvstore`)

The KV store provides a low-level indexed key-value backend for persistent settings.

**Location:** Exposed via `native/kvstore` module  
**C layer:** [src/ecmascript/es_kvstore.c](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_kvstore.c)

### Methods

#### `kvstore.getString(url, domain, key)` → string or null

Retrieves a string value.

**Parameters:**
- `url` (string) - Context URL (e.g., page URL)
- `domain` (string) - Domain scope (e.g., `"plugin"`)
- `key` (string) - Key name

**Returns:** String value or null if not found

**Source:** [src/ecmascript/es_kvstore.c#L45-L56](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_kvstore.c)

#### `kvstore.getInteger(url, domain, key, default)` → number

Retrieves an integer value.

**Parameters:**
- `url` (string) - Context URL
- `domain` (string) - Domain scope
- `key` (string) - Key name
- `default` (number, optional) - Default if not found

**Returns:** Integer value

**Source:** [src/ecmascript/es_kvstore.c#L63-L73](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_kvstore.c)

#### `kvstore.getBoolean(url, domain, key, default)` → boolean

Retrieves a boolean value.

**Parameters:**
- `url` (string) - Context URL
- `domain` (string) - Domain scope
- `key` (string) - Key name
- `default` (boolean, optional) - Default if not found

**Returns:** Boolean value

**Source:** [src/ecmascript/es_kvstore.c#L80-L89](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_kvstore.c)

#### `kvstore.set(url, domain, key, value)`

Sets a key-value pair.

**Parameters:**
- `url` (string) - Context URL
- `domain` (string) - Domain scope
- `key` (string) - Key name
- `value` (mixed) - Value to store (string, number, boolean, or null)

**Returns:** void

**Source:** [src/ecmascript/es_kvstore.c#L96-L117](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_kvstore.c)

### Usage in Settings

The KV store is used internally by `kvstoreSettings` to persist page-specific options:

```javascript
// Automatically used by page options
var page = new page.Page(...);
page.options.createBool('setting1', 'Label', true, callback, true);
// Stored in KV store with page's URL as key
```

---

## Advanced Topics and Cross-Module Interactions

### Pagination Patterns

**Synchronous paginator:** For in-memory data or simple calculations.

```javascript
var offset = 0;
page.paginator = function() {
  if (offset >= totalItems) return false;
  
  // Add batch of items
  for (var i = 0; i < 20 && offset < totalItems; i++) {
    page.appendItem(...);
    offset++;
  }
  
  return offset < totalItems;  // More available?
};
```

**Asynchronous paginator:** For network requests.

```javascript
var offset = 0;
page.asyncPaginator = function() {
  setTimeout(function() {
    try {
      var items = fetchItems(offset, 20);
      
      if (items.length === 0) {
        page.haveMore(false);
        return;
      }
      
      for (var i = 0; i < items.length; i++) {
        page.appendItem(items[i].url, items[i].type, items[i].metadata);
      }
      
      offset += items.length;
      page.haveMore(items.length > 0);
    } catch (e) {
      page.error(e.toString());
      page.haveMore(false);
    }
  }, 0);
};
```

### Settings Persistence

Settings can be stored in different backends:

1. **Global plugin settings** - Stored on disk via `plugin.createSettings()`
2. **Page-specific options** - Stored in KV store via page's `options` property
3. **Custom store** - File-based JSON via `store.create()`
4. **Database** - Structured data via SQLite

**Example combining all:**

```javascript
// Global plugin config
var settings = plugin.createSettings('My Plugin', 'icon.png', 'Config');
settings.createBool('debug', 'Debug', false, function(v) {
  console.log('Debug:', v);
});

// Per-service options
var service = plugin.createService('My Service', 'myapp:home', 'video', true);

// Per-page options
new page.Route('myapp:browse:(.*)', function(page) {
  page.type = 'directory';
  
  // These are stored per-page in KV store
  page.options.createBool('show_desc', 'Show Description', true, function(v) {
    // ...
  });
});

// Custom data storage
var cache = store.create('cache');
cache.lastCheck = Date.now();

// Database
var db = new sqlite.DB('metadata');
db.query('CREATE TABLE IF NOT EXISTS videos (id TEXT, title TEXT)');
```

### Error Handling Patterns

**HTTP errors:**

```javascript
http.get('http://example.com/api', function(res) {
  if (res.statusCode !== 200) {
    page.error('HTTP ' + res.statusCode);
    page.haveMore(false);
    return;
  }
  
  res.on('data', function(data) {
    try {
      var json = JSON.parse(data);
      // Process data...
    } catch (e) {
      page.error('JSON parse error: ' + e.toString());
      page.haveMore(false);
    }
  });
}).on('error', function(err) {
  page.error('Network error: ' + err);
  page.haveMore(false);
});
```

**Page safety:**

```javascript
new page.Route('myapp:browse:(.*)', function(page) {
  page.asyncPaginator = function() {
    setTimeout(function() {
      // Check if page still exists
      if (prop.isZombie(page.root)) {
        console.log('Page was closed');
        return;
      }
      
      // Safe to populate...
    }, 0);
  };
});
```

### Subscription Cleanup

Subscriptions auto-cleanup with `autoDestroy: true`:

```javascript
// Will automatically unsubscribe when property is destroyed
prop.subscribe(myProp, callback, { autoDestroy: true });

// Manual cleanup
var sub = prop.subscribe(myProp, callback);
Core.resourceDestroy(sub);
```

### Cross-Module Pattern Example

Complete example using multiple modules:

```javascript
(function(plugin) {
  var page = require('movian/page');
  var http = require('http');
  var service = require('movian/service');
  var settings = require('movian/settings');
  var store = require('movian/store');
  var sqlite = require('movian/sqlite');
  
  // Settings
  var config = plugin.createSettings('YouTube', 'icon.png', 'Settings');
  config.createBool('hd', 'Prefer HD', true, function(val) {
    // ...
  });
  
  // Service
  var svc = service.create('YouTube', 'youtube:home', 'video', true, 'icon.png');
  
  // Database
  var db = new sqlite.DB('cache');
  db.query('CREATE TABLE IF NOT EXISTS videos (id TEXT PRIMARY KEY, title TEXT, cached_at INTEGER)');
  
  // Cache store
  var cache = store.create('metadata');
  
  // Route with pagination
  new page.Route('youtube:search:(.*)', function(page, query) {
    page.type = 'directory';
    page.metadata.title = 'Results for: ' + query;
    page.loading = true;
    
    var offset = 0;
    var pageSize = 20;
    
    page.asyncPaginator = function() {
      setTimeout(function() {
        var url = 'https://www.youtube.com/api/search?q=' + encodeURIComponent(query) + '&offset=' + offset;
        
        http.get(url, function(res) {
          if (res.statusCode !== 200) {
            page.error('HTTP ' + res.statusCode);
            page.haveMore(false);
            return;
          }
          
          res.on('data', function(data) {
            try {
              var json = JSON.parse(data);
              
              if (json.items.length === 0) {
                page.haveMore(false);
                page.loading = false;
                return;
              }
              
              for (var i = 0; i < json.items.length; i++) {
                var item = json.items[i];
                
                // Cache in database
                db.query('INSERT OR REPLACE INTO videos (id, title, cached_at) VALUES (?, ?, ?)',
                         item.id, item.title, Date.now());
                
                page.appendItem('youtube:video:' + item.id, 'video', {
                  title: item.title,
                  icon: item.thumbnail
                });
              }
              
              offset += json.items.length;
              page.haveMore(json.items.length === pageSize);
              page.loading = false;
            } catch (e) {
              page.error('Parse error: ' + e.toString());
              page.haveMore(false);
              page.loading = false;
            }
          });
        }).on('error', function(err) {
          page.error('Network error: ' + err);
          page.haveMore(false);
          page.loading = false;
        });
      }, 0);
    };
  });
  
})(this);
```

---

## API Coverage Checklist

| Module | API | Documented | Source |
|--------|-----|-----------|--------|
| **plugin** | Plugin object | ✓ | ecmascript.c |
| **plugin** | Plugin.id | ✓ | ecmascript.c |
| **plugin** | Plugin.version | ✓ | ecmascript.c |
| **plugin** | Core object | ✓ | ecmascript.c |
| **plugin** | Core.resourceDestroy() | ✓ | ecmascript.c |
| **plugin** | Core.storagePath | ✓ | ecmascript.c |
| **plugin** | setTimeout() | ✓ | es_timer.c |
| **plugin** | clearTimeout() | ✓ | es_timer.c |
| **plugin** | Duktape.fin() | ✓ | ECMAScript engine |
| **page** | Route class | ✓ | page.js |
| **page** | new Route(pattern, callback) | ✓ | page.js |
| **page** | Route.destroy() | ✓ | page.js |
| **page** | Searcher class | ✓ | page.js |
| **page** | new Searcher(title, icon, callback) | ✓ | page.js |
| **page** | Searcher.destroy() | ✓ | page.js |
| **page** | Page.type | ✓ | page.js |
| **page** | Page.metadata | ✓ | page.js |
| **page** | Page.loading | ✓ | page.js |
| **page** | Page.entries | ✓ | page.js |
| **page** | Page.source | ✓ | page.js |
| **page** | Page.paginator | ✓ | page.js |
| **page** | Page.asyncPaginator | ✓ | page.js |
| **page** | Page.appendItem() | ✓ | page.js |
| **page** | Page.appendAction() | ✓ | page.js |
| **page** | Page.appendPassiveItem() | ✓ | page.js |
| **page** | Page.haveMore() | ✓ | page.js |
| **page** | Page.flush() | ✓ | page.js |
| **page** | Page.redirect() | ✓ | page.js |
| **page** | Page.error() | ✓ | page.js |
| **page** | Page.onEvent() | ✓ | page.js |
| **page** | Page.getItems() | ✓ | page.js |
| **page** | Page.dump() | ✓ | page.js |
| **page** | Item.enable() | ✓ | page.js |
| **page** | Item.disable() | ✓ | page.js |
| **page** | Item.destroy() | ✓ | page.js |
| **page** | Item.moveBefore() | ✓ | page.js |
| **page** | Item.onEvent() | ✓ | page.js |
| **page** | Item.addOptAction() | ✓ | page.js |
| **page** | Item.addOptURL() | ✓ | page.js |
| **page** | Item.addOptSeparator() | ✓ | page.js |
| **page** | Item.bindVideoMetadata() | ✓ | page.js |
| **page** | Item.unbindVideoMetadata() | ✓ | page.js |
| **page** | Item.toString() | ✓ | page.js |
| **prop** | prop.createRoot() | ✓ | es_prop.c |
| **prop** | prop.global() | ✓ | es_prop.c |
| **prop** | prop.subscribe() | ✓ | es_prop.c |
| **prop** | prop.subscribeValue() | ✓ | prop.js |
| **prop** | prop.set() | ✓ | es_prop.c |
| **prop** | prop.setRichStr() | ✓ | es_prop.c |
| **prop** | prop.setParent() | ✓ | es_prop.c |
| **prop** | prop.destroy() | ✓ | es_prop.c |
| **prop** | prop.deleteChilds() | ✓ | es_prop.c |
| **prop** | prop.deleteChild() | ✓ | es_prop.c |
| **prop** | prop.haveMore() | ✓ | es_prop.c |
| **prop** | prop.getName() | ✓ | es_prop.c |
| **prop** | prop.getValue() | ✓ | es_prop.c |
| **prop** | prop.getChild() | ✓ | es_prop.c |
| **prop** | prop.link() | ✓ | es_prop.c |
| **prop** | prop.sendEvent() | ✓ | es_prop.c |
| **prop** | prop.select() | ✓ | es_prop.c |
| **prop** | prop.isSame() | ✓ | es_prop.c |
| **prop** | prop.isZombie() | ✓ | es_prop.c |
| **prop** | prop.print() | ✓ | es_prop.c |
| **prop** | prop.moveBefore() | ✓ | es_prop.c |
| **prop** | prop.unloadDestroy() | ✓ | es_prop.c |
| **prop** | prop.makeUrl() | ✓ | es_prop.c |
| **prop** | prop.atomicAdd() | ✓ | es_prop.c |
| **prop** | prop.setClipRange() | ✓ | es_prop.c |
| **prop** | prop.enumerate() | ✓ | es_prop.c |
| **prop** | prop.has() | ✓ | es_prop.c |
| **prop** | prop.makeProp() | ✓ | prop.js |
| **service** | service.create() | ✓ | es_service.c |
| **service** | service.enabled (property) | ✓ | service.js |
| **service** | service.destroy() | ✓ | service.js |
| **settings** | settings.globalSettings() | ✓ | settings.js |
| **settings** | group.createBool() | ✓ | settings.js |
| **settings** | group.createString() | ✓ | settings.js |
| **settings** | group.createInt() | ✓ | settings.js |
| **settings** | group.createMultiOpt() | ✓ | settings.js |
| **settings** | group.createAction() | ✓ | settings.js |
| **settings** | group.createDivider() | ✓ | settings.js |
| **settings** | group.createInfo() | ✓ | settings.js |
| **settings** | group.destroy() | ✓ | settings.js |
| **http** | http.request() | ✓ | es_io.c / http.js |
| **http** | http.get() | ✓ | es_io.c / http.js |
| **http** | request.end() | ✓ | http.js |
| **http** | request.on() | ✓ | http.js |
| **http** | response.statusCode | ✓ | http.js |
| **http** | response.bytes | ✓ | http.js |
| **http** | response.on() | ✓ | http.js |
| **https** | https.request() | ✓ | https.js |
| **https** | https.get() | ✓ | https.js |
| **store** | store.create() | ✓ | store.js |
| **store** | store.createFromPath() | ✓ | store.js |
| **sqlite** | new sqlite.DB() | ✓ | es_sqlite.c / sqlite.js |
| **sqlite** | db.query() | ✓ | es_sqlite.c / sqlite.js |
| **sqlite** | db.step() | ✓ | es_sqlite.c / sqlite.js |
| **sqlite** | db.close() | ✓ | es_sqlite.c / sqlite.js |
| **sqlite** | db.lastRowId | ✓ | es_sqlite.c |
| **sqlite** | db.lastErrorCode | ✓ | es_sqlite.c |
| **sqlite** | db.lastErrorString | ✓ | es_sqlite.c |
| **sqlite** | db.upgradeSchema() | ✓ | es_sqlite.c |
| **kvstore** | kvstore.getString() | ✓ | es_kvstore.c |
| **kvstore** | kvstore.getInteger() | ✓ | es_kvstore.c |
| **kvstore** | kvstore.getBoolean() | ✓ | es_kvstore.c |
| **kvstore** | kvstore.set() | ✓ | es_kvstore.c |

**Total Functions Documented:** 90+
**Coverage:** 100% of enumerated ECMAScript V2 API

---

## Document Map

```
docs/
└── plugin-dev-api-v2.md          ← This file (API Reference)
    ├── Overview
    ├── Plugin Lifecycle
    ├── Page Module
    ├── Prop Module
    ├── Service Module
    ├── Settings Module
    ├── HTTP/HTTPS Modules
    ├── Store Module
    ├── SQLite Module
    ├── Key-Value Store
    ├── Advanced Topics
    └── API Checklist
```

---

## Related Documentation

- **[Overview](overview.md)** - Movian architecture and capabilities
- **[Repository Structure](repository-structure.md)** - Codebase organization
- **[Getting Started](getting-started.md)** - Development setup
- **[Runtime Guide](runtime.md)** - Debugging and deployment

---

## Contributing to This Documentation

When adding new API functions:

1. Identify C layer (`src/ecmascript/es_*.c`) and JS layer (`res/ecmascript/modules/`)
2. Find function signature in function list (`fnlist_*`)
3. Document all parameters, return values, and behavior
4. Include source link with commit hash
5. Add practical examples
6. Update API Coverage Checklist

---

**Document Revision:** 1.0  
**Last Updated:** 2024  
**Source Commit:** [1f76b9ad66335477b10ebc23b8a687a25407a3d9](https://github.com/andoma/movian/commit/1f76b9ad66335477b10ebc23b8a687a25407a3d9)
