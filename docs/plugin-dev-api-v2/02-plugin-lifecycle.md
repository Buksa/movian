# Plugin Lifecycle Fundamentals

This document describes the complete lifecycle of an ECMAScript API v2 plugin from loading through unloading, including initialization, route handling, and cleanup.

## Lifecycle Overview

A plugin's lifetime follows these phases:

```
User Installs Plugin
         ↓
  [LOAD PHASE]
    Load plugin.json manifest
    Validate schema and compatibility
    Create ECMAScript execution context
         ↓
  [INITIALIZATION PHASE]
    Execute user's JavaScript file
    Module registration (movian/page, etc.)
    Route creation
    Service registration
         ↓
  [RUNTIME PHASE]
    Context runs idle, waiting for user interaction
    Routes fire when URLs are accessed
    Services visible in UI
         ↓
  [UNLOAD PHASE]
    User disables/uninstalls plugin
    Destroy all registered resources
    Release execution context
    Clean up storage (if uninstalling)
```

## Phase 1: Load Phase

### 1.1 Plugin Discovery

Movian scans plugin directories during startup:

- `~/.hts/showtime/plugins/` (user plugins)
- Built-in plugin locations

For each directory, Movian looks for `plugin.json`.

See [plugins.c lines 505–543](../../src/plugins.c#L505-L543) for manifest parsing.

### 1.2 Manifest Validation

The manifest is parsed and validated:

```javascript
// plugin.json is read and parsed as JSON
{
  "type": "ecmascript",        // Must be "ecmascript"
  "id": "my_plugin",           // Must match [a-zA-Z0-9_]+
  "file": "my_plugin.js",      // Path relative to plugin directory
  "apiversion": 2,             // 1 or 2; v2 recommended
  "category": "video",         // Plugin category from enum
  "title": "My Plugin",        // Display name
  "description": "...",        // Description
  "version": "1.0.0"           // Version string
}
```

Invalid manifests cause the plugin to be marked as failed. See [plugins.c lines 61–71](../../src/plugins.c#L61-L71) for category enum definition.

### 1.3 ECMAScript Context Creation

When the plugin loads, Movian calls [ecmascript_plugin_load()](../../src/ecmascript/ecmascript.c#L874) (line 874).

This function:

1. **Creates a new context** via [es_context_create()](../../src/ecmascript/ecmascript.c#L575) (line 575):
   - Initializes a Duktape VM heap
   - Sets up memory tracking
   - Creates property dispatch group
   - Allocates storage path: `~/.hts/showtime/plugins/{plugin-id}/`

2. **Prepares the global environment** via [es_create_env()](../../src/ecmascript/ecmascript.c#L620) (line 620):
   - Registers built-in modules (movian/page, movian/http, etc.)
   - Installs the module loader
   - Sets up console object

3. **Injects Plugin metadata** (lines 889–910):
   - Sets `Plugin.id` – plugin identifier
   - Sets `Plugin.url` – path to main JavaScript file
   - Sets `Plugin.manifest` – full manifest JSON as string
   - Sets `Plugin.apiversion` – API version (1 or 2)
   - Sets `Plugin.path` – directory containing the plugin

### 1.4 Context Structure

The execution context (`es_context_t`) defined in [ecmascript.h](../../src/ecmascript/ecmascript.h) contains:

- `ec_duk` – Duktape VM heap
- `ec_id` – Plugin identifier
- `ec_path` – Plugin directory path
- `ec_storage` – Plugin storage directory
- `ec_resources_permanent` – List of rooted resources (routes, services, hooks)
- `ec_mutex` – Thread synchronization lock
- `ec_refcount` – Reference counting for cleanup
- Memory tracking fields (`ec_mem_active`, `ec_mem_peak`)

## Phase 2: Initialization Phase

### 2.1 File Load and Execution

After context creation, Movian loads and executes the user's JavaScript file. For API v2, the flow is:

1. Load the file from disk (see [es_load_and_compile()](../../src/ecmascript/ecmascript.c#L823), line 823)
2. Compile it to Duktape bytecode
3. Execute synchronously via protected call

See [ecmascript_plugin_load() lines 951–953](../../src/ecmascript/ecmascript.c#L951-L953).

### 2.2 Module Imports

During execution, the plugin requires modules:

```javascript
var page = require('movian/page');
var http = require('movian/http');
```

The module system searches:

1. Built-in modules (`movian/*`)
2. User modules (in plugin directory or standard locations)

When `require('movian/page')` is called, Movian loads the movian/page module and caches it.

### 2.3 Route Registration

Routes are registered via `new page.Route()`. This creates a [page.Route object](../../res/ecmascript/modules/movian/page.js#L384) that:

1. Extracts the route pattern (regex string)
2. Calls the native `require('native/route').create()` function
3. Returns a rooted resource that persists until plugin unload

Internally, this invokes [es_route_create()](../../src/ecmascript/es_route.c#L105) (line 105):

- Compiles the regex pattern
- Inserts the route into a global route list (protected by mutex)
- Registers it as a rooted resource (see [es_root_register()](../../src/ecmascript/es_root.c#L27), line 27)

### 2.4 Service Registration

Services are created via `new service.Service()`, which calls [es_service_create()](../../src/ecmascript/es_service.c#L108) (line 108):

- Creates a service object visible in Movian's UI
- Links it to the plugin
- Enables/disables via service settings
- On plugin uninstall, the service triggers plugin removal (see [es_service_delete_req()](../../src/ecmascript/es_service.c#L95), line 95)

### 2.5 End of Initialization

Once the JavaScript file finishes executing:

- All route handlers are registered
- All services are visible in UI
- All subscriptions are active
- The context remains alive, ready for runtime requests

## Phase 3: Runtime Phase

### 3.1 Route Matching and Execution

When a user navigates to a URL, Movian's backend calls [ecmascript_openuri()](../../src/ecmascript/es_route.c#L189) (line 189):

1. Iterates through registered routes (sorted by priority)
2. Tests each regex against the URL
3. On first match:
   - Captures regex groups (passed as arguments)
   - Acquires the execution context lock
   - Retrieves the route's callback function (via [es_push_root()](../../src/ecmascript/es_root.c#L73), line 73)
   - Calls the function with the page object and captured arguments
   - Waits for completion or error

This happens synchronously for blocking UI updates.

### 3.2 Page Object Lifecycle

When a route callback is invoked, it receives a Page proxy object (line 150) that:

- Wraps Movian's property tree for UI state
- Provides methods to add items: `page.appendItem()`, `page.appendAction()`
- Supports pagination: `page.paginator`, `page.asyncPaginator`
- Handles events: `page.onEvent()`

The page object is destroyed when the route handler completes or when the user navigates away.

### 3.3 Asynchronous Operations

Some operations happen asynchronously via callbacks:

```javascript
page.asyncPaginator = function() {
  setTimeout(function() {
    page.appendItem(...);
    page.haveMore(true);
  }, 1000);
};
```

See [page.js lines 208–211](../../res/ecmascript/modules/movian/page.js#L208-L211) for async pagination handling.

### 3.4 Resource Lifecycle During Runtime

Resources created during runtime:

- **Hooks** – Registered via `require('native/hook').register()`; live until explicitly destroyed or plugin unloads
- **Services** – Created at initialization; live until unload or user disables plugin
- **Timers** – Created via `setTimeout()`; cleaned up when they fire or on plugin unload
- **Subscriptions** – Property subscriptions via `prop.subscribe()`; cleaned up when they fire or on unload

## Phase 4: Unload Phase

### 4.1 Plugin Unload Initiation

Unload can be triggered by:

1. User disables the plugin in settings
2. Plugin is uninstalled
3. Movian shuts down
4. Plugin encounters a fatal error

### 4.2 Resource Destruction

When [ecmascript_plugin_unload()](../../src/ecmascript/ecmascript.c#L1001) (line 1001) is called:

1. The plugin's context is located in the global context list
2. The context is unlinked from the list
3. All permanent resources are destroyed via es_resource_destroy():
   - Routes are unregistered and removed from the global route list
   - Services are destroyed
   - Subscriptions are unlinked
   - Hooks are deregistered
   - Memory is freed

See [es_route_destroy()](../../src/ecmascript/es_route.c#L50) (line 50) for example resource cleanup.

### 4.3 Context Release

After resources are destroyed, [es_context_release()](../../src/ecmascript/ecmascript.c#L640) (line 640):

1. Decrements the reference count
2. When refcount reaches zero:
   - Destroys the Duktape heap
   - Frees the context structure
   - Releases storage path and identity

### 4.4 Storage Cleanup

Plugin data persists in `~/.hts/showtime/plugins/{plugin-id}/` even after unload. This is intentional:

- Settings are preserved if the user temporarily disables the plugin
- User data is not lost on plugin update
- Uninstall may trigger explicit cleanup (database removal, etc.)

See [plugins.c line 882](../../src/plugins.c#L882) for storage path construction.

## Error Handling

### 4.1 Compilation Errors

If the JavaScript file has syntax errors:

```javascript
// ❌ Bad: Missing closing brace
var page = require('movian/page');
{
```

The [es_load_and_compile()](../../src/ecmascript/ecmascript.c#L823) function (line 823) detects this and logs a TRACE_ERROR. The plugin remains loaded but non-functional.

### 4.2 Runtime Errors

If a route handler throws an exception:

```javascript
new page.Route('example:test', function(page) {
  throw new Error("Something went wrong!");  // ❌ Uncaught
});
```

The error is caught by protected call. In [ecmascript_openuri()](../../src/ecmascript/es_route.c#L244-L253) (lines 244–253):

```c
int rc = duk_pcall(ctx, 3);
if(rc) {
  if(duk_is_string(ctx, -1)) {
    nav_open_error(page, duk_to_string(ctx, -1));  // Shows error to user
  }
}
```

The user sees an error page, but the plugin continues running.

### 4.3 Memory Errors

Memory usage is tracked per context (see [es_mem_alloc()](../../src/ecmascript/ecmascript.c#L523), line 523 and related functions):

- `ec_mem_active` – Current heap size
- `ec_mem_peak` – Peak memory used
- `ec_mem_limit` – Configurable memory ceiling

If memory limits are exceeded, the Duktape heap signals an error, and the route fails.

## Best Practices

### 5.1 Resource Cleanup

Always clean up resources in your plugin:

```javascript
// ✅ Good: Destroy resources when done
var service = new service.Service(...);
service.destroy();  // Unregisters from UI

var route = new page.Route(...);
route.destroy();    // Unregisters route handler
```

### 5.2 Error Handling

Wrap asynchronous operations in try-catch:

```javascript
// ✅ Good: Handle errors gracefully
new page.Route('example:test', function(page) {
  try {
    var http = require('movian/http');
    var data = http.httpGet(url);
    // Process data
  } catch(e) {
    page.error("Failed to load data: " + e);
  }
});
```

### 5.3 Module Imports

Import modules once at the top level:

```javascript
// ✅ Good: Import once
var page = require('movian/page');
var http = require('movian/http');

new page.Route(..., function(page) {
  // Use page and http here
});
```

### 5.4 Timeout and Pagination

For long-running operations, use async pagination:

```javascript
// ✅ Good: Async pagination for large datasets
new page.Route('example:browse', function(page) {
  page.type = 'directory';
  
  var offset = 0;
  page.asyncPaginator = function() {
    setTimeout(function() {
      // Fetch next batch
      for(var i = 0; i < 20; i++) {
        page.appendItem(...);
      }
      offset += 20;
      page.haveMore(offset < total);
    }, 100);
  };
});
```

## Context and Threading

### 6.1 Thread Safety

Each plugin context runs in its own thread, isolated from others. The context is protected by a mutex (`ec_mutex`):

- See [ecmascript_context_lockmgr()](../../src/ecmascript/ecmascript.c#L43) (line 43) for synchronization
- Routes are called serially within the same context
- Multiple plugins can run routes simultaneously in different contexts

### 6.2 Context Begin/End

Routes are bracketed by [es_context_begin()](../../src/ecmascript/ecmascript.c) and [es_context_end()](../../src/ecmascript/ecmascript.c):

- `es_context_begin()` – Acquires lock, returns Duktape context
- User code runs within the lock
- `es_context_end()` – Releases lock, allows other operations

This ensures thread-safe access to the plugin's state.

## Reference

| Component | Location | Lines |
|-----------|----------|-------|
| Plugin load entry | src/ecmascript/ecmascript.c | 874–961 |
| Context creation | src/ecmascript/ecmascript.c | 575–631 |
| Environment setup | src/ecmascript/ecmascript.c | 620 |
| Route creation | src/ecmascript/es_route.c | 105–157 |
| Route execution | src/ecmascript/es_route.c | 189–261 |
| Service creation | src/ecmascript/es_service.c | 108–143 |
| Plugin unload | src/ecmascript/ecmascript.c | 1001–1027 |
| Resource cleanup | src/ecmascript/es_route.c | 50–66 |

---

**Next:** [Page Routing & UI](03-page-routing-ui.md) (stub)

For detailed module API signatures, see [API Reference](09-api-reference.md).
