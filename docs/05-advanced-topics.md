# Advanced ECMAScript Topics

**Document Version:** 1.0  
**Last Updated:** 2024  
**Scope:** Advanced patterns, performance optimization, error handling, and debugging for Movian ECMAScript plugin development

---

## Table of Contents

1. [Overview](#overview)
2. [Asynchronous Control Flow](#asynchronous-control-flow)
3. [Item Hook Architecture](#item-hook-architecture)
4. [Subscription Patterns](#subscription-patterns)
5. [Performance Optimization](#performance-optimization)
6. [Error Handling and Recovery](#error-handling-and-recovery)
7. [Debugging Strategies](#debugging-strategies)
8. [Real-World Patterns](#real-world-patterns)

---

## Overview

This document covers **advanced patterns** for building robust, performant Movian plugins. Topics include:

- **Async patterns** - setTimeout, async paginators, error retries, race condition avoidance
- **Item hooks** - Dynamic context menus on media items
- **Subscriptions** - Property observables, lifecycle management, memory leaks
- **Performance** - Caching strategies, batching, property subscription optimization
- **Error handling** - Exception propagation, zombie props, graceful degradation
- **Debugging** - Logging, profiling, property tree inspection

**Prerequisites:**
- Familiarity with [ECMAScript API v2 Reference](plugin-dev-api-v2.md)
- Understanding of Movian's property system (`movian/prop`)
- Basic plugin development experience

---

## Asynchronous Control Flow

Movian's ECMAScript environment supports **asynchronous operations** via timers, HTTP requests, and async paginators.

### Timer-Based Async

#### setTimeout and setInterval

Native timer implementation for delayed execution.

**Source:** [`src/ecmascript/es_timer.c`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_timer.c)

**API:**

```javascript
var handle = setTimeout(function() {
  console.log("Executed after 1 second");
}, 1000);

clearTimeout(handle);  // Cancel if needed

var interval = setInterval(function() {
  console.log("Repeated every 500ms");
}, 500);

clearInterval(interval);  // Stop repeating
```

**Implementation Details:**

- **Thread-safe** - Timer thread wakes for nearest expiry
- **Cancellable** - `clearTimeout`/`clearInterval` destroy timer resource
- **Auto-cleanup** - Timers destroyed on plugin unload

**Source:** [`src/ecmascript/es_timer.c#L108-L163`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_timer.c#L108-L163)

**Example: Retry with Exponential Backoff**

```javascript
var http = require('movian/http');

function fetchWithRetry(url, maxRetries, callback) {
  var retries = 0;
  
  function attempt() {
    http.request(url, {}, function(err, response) {
      if(err || response.statuscode >= 400) {
        if(retries++ < maxRetries) {
          var delay = Math.min(1000 * Math.pow(2, retries), 30000);  // Cap at 30s
          console.log("Retry " + retries + " in " + delay + "ms");
          setTimeout(attempt, delay);
        } else {
          callback(new Error("Max retries exceeded"));
        }
      } else {
        callback(null, response);
      }
    });
  }
  
  attempt();
}

// Usage
fetchWithRetry("https://api.example.com/data", 3, function(err, data) {
  if(err) {
    console.error("Failed:", err);
  } else {
    console.log("Success:", data);
  }
});
```

### Async Pagination

Implement infinite scrolling with dynamic data loading.

**Source:** [`plugin_examples/async_page_load/async_page_load.js`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/plugin_examples/async_page_load/async_page_load.js)

**Pattern:**

```javascript
var page = require('movian/page');

new page.Route('myapp:feed:(.*)', function(page, feedId) {
  var offset = 0;
  var hasMore = true;
  
  function loader() {
    if(!hasMore) return;
    
    setTimeout(function() {
      // Fetch next page
      var items = fetchPage(feedId, offset, 20);
      
      if(items.length === 0) {
        hasMore = false;
        page.haveMore(false);  // Signal end of list
        return;
      }
      
      items.forEach(function(item) {
        page.appendItem(item.url, 'video', {
          title: item.title,
          icon: item.thumbnail
        });
      });
      
      offset += items.length;
      page.haveMore(true);  // Signal more available
    }, 100);  // Debounce rapid scroll
  }
  
  page.type = 'directory';
  page.asyncPaginator = loader;  // Assign paginator
  loader();  // Initial load
});
```

**Key Points:**

- **`page.asyncPaginator`** - Called when user scrolls near bottom
- **`page.haveMore(false)`** - Disable "Load more" UI
- **Debouncing** - Add delay to avoid rapid API calls
- **Error handling** - Wrap fetch in try/catch, show error items

**Source:** [`res/ecmascript/modules/movian/page.js#L196-L210`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/page.js)

### Avoiding Race Conditions

**Problem:** Multiple async operations modifying shared state

**Example: Race in search**

```javascript
// BAD: Race condition
var currentQuery = "";

new page.Searcher('MyApp', 'icon.png', function(page, query) {
  currentQuery = query;
  
  setTimeout(function() {
    var results = searchAPI(query);  // Slow API call
    
    // Problem: If user typed again, we're displaying stale results
    results.forEach(function(r) {
      page.appendItem(r.url, 'video', {title: r.title});
    });
  }, 500);
});
```

**Solution: Cancellation tokens**

```javascript
// GOOD: Cancel stale requests
var currentToken = null;

new page.Searcher('MyApp', 'icon.png', function(page, query) {
  var token = currentToken = {};  // Unique token per search
  
  setTimeout(function() {
    if(token !== currentToken) return;  // Cancelled - ignore results
    
    var results = searchAPI(query);
    
    if(token !== currentToken) return;  // Check again after async
    
    results.forEach(function(r) {
      page.appendItem(r.url, 'video', {title: r.title});
    });
    
    page.loading = false;
  }, 500);
});
```

**Alternative: Use cancellation tokens for HTTP requests**

```javascript
var http = require('movian/http');
var currentToken = null;

function search(page, query) {
  var token = currentToken = {};  // New token cancels previous requests
  
  http.request('https://api.example.com/search?q=' + encodeURIComponent(query), {}, function(err, response) {
    if(token !== currentToken) return;  // Cancelled - ignore results
    
    if(err) {
      console.error("Search failed:", err);
      return;
    }
    
    // Process results...
    var data = JSON.parse(response.toString());
    data.results.forEach(function(item) {
      page.appendItem(item.url, 'video', {title: item.title});
    });
  });
}
```

---

## Item Hook Architecture

**Item hooks** add context menu entries to media items throughout Movian's UI.

**Source:** [`res/ecmascript/modules/movian/itemhook.js`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/itemhook.js)

### Creating Item Hooks

**API v2:**

```javascript
var itemhook = require('movian/itemhook');

var hook = itemhook.create({
  title: "Play with MyApp",
  itemtype: "video",  // Optional: filter by content type
  icon: "icon://video",
  handler: function(item, nav) {
    // item: Property object with metadata
    // nav: Navigation object with openURL()
    console.log("Item URL:", item.url);
    console.log("Title:", item.metadata.title);
    console.log("Duration:", item.metadata.duration);
    
    nav.openURL('myapp:play:' + encodeURIComponent(item.url));
  }
});

// Cleanup when plugin unloads
hook.destroy();
```

**Source:** [`res/ecmascript/modules/movian/itemhook.js#L3-L39`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/itemhook.js#L3-L39)

### Item Types

Filter hooks by content type:

```javascript
var itemhook = require('movian/itemhook');

itemhook.create({
  title: "Show audio details",
  itemtype: "audio",
  handler: function(item, nav) {
    var msg = "Title: " + item.metadata.title + "\n" +
              "Artist: " + item.metadata.artist + "\n" +
              "Duration: " + item.metadata.duration;
    console.log(msg);
  }
});
```

**Available types:**
- `"video"` - Video files
- `"audio"` - Audio files
- `"image"` - Images
- `"directory"` - Folders
- `undefined` - All types

**Source:** [`plugin_examples/itemhook/example_itemhook.js#L14-L20`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/plugin_examples/itemhook/example_itemhook.js#L14-L20)

### Navigation Integration

**Opening custom pages:**

```javascript
var itemhook = require('movian/itemhook');
var page = require('movian/page');

itemhook.create({
  title: "Detailed view",
  handler: function(item, nav) {
    // Serialize item metadata into URL
    var data = {
      url: item.url,
      title: item.metadata.title,
      duration: item.metadata.duration
    };
    
    nav.openURL('myapp:details:' + JSON.stringify(data));
  }
});

// Route handler
new page.Route('myapp:details:(.*)', function(page, jsonStr) {
  var data = JSON.parse(jsonStr);
  
  page.type = 'raw';
  page.metadata.title = data.title;
  page.metadata.glwview = Plugin.path + "details.view";  // Custom UI
  
  // Populate page with data...
  page.loading = false;
});
```

**Source:** [`plugin_examples/itemhook/example_itemhook.js#L24-L45`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/plugin_examples/itemhook/example_itemhook.js#L24-L45)

### Lifecycle Management

**Auto-cleanup pattern:**

```javascript
var itemhook = require('movian/itemhook');
var hooks = [];

// Register multiple hooks
hooks.push(itemhook.create({
  title: "Hook 1",
  handler: function(item, nav) { /* ... */ }
}));

hooks.push(itemhook.create({
  title: "Hook 2",
  handler: function(item, nav) { /* ... */ }
}));

// Manual cleanup if needed
function cleanup() {
  hooks.forEach(function(h) { h.destroy(); });
  hooks = [];
}
```

**Note:** Item hooks with `autoDestroy: true` (default) are automatically cleaned up when the plugin is unloaded, so manual cleanup is usually not necessary.

**Source:** [`res/ecmascript/modules/movian/itemhook.js#L15-L33`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/itemhook.js#L15-L33)

---

## Subscription Patterns

**Subscriptions** observe property changes and react to updates.

**Source:** [`src/ecmascript/es_prop.c#L720-L759`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c#L720-L759)

### Basic Subscriptions

```javascript
var prop = require('movian/prop');

var subscription = prop.subscribe(prop.global.clock.unixtime, function(value) {
  console.log("Current time:", value);
});

// Cleanup
subscription.destroy();
```

**Source:** [`plugin_examples/subscriptions/example_subscriptions.js#L9-L11`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/plugin_examples/subscriptions/example_subscriptions.js#L9-L11)

### Subscription Options

```javascript
prop.subscribe(myProp, callback, {
  autoDestroy: true,           // Destroy when callback scope exits
  ignoreVoid: false,           // Receive void/undefined values
  debug: false,                // Log subscription events
  noInitialUpdate: false,      // Skip immediate callback on subscribe
  earlyChildDelete: false,     // Receive delchild before child destroyed
  actionAsArray: false         // Receive actions as arrays
});
```

**Source:** [`src/ecmascript/es_prop.c#L729-L746`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c#L729-L746)

### Page-Level Subscriptions

Monitor page-specific properties:

```javascript
new page.Route('myapp:page', function(page) {
  page.type = 'directory';
  
  // Watch bookmark status
  page.subscribe('page.model.bookmarked', function(isBookmarked) {
    console.log("Bookmark changed:", isBookmarked);
    
    if(isBookmarked) {
      // Add to favorites database
    } else {
      // Remove from favorites
    }
  });
  
  // Watch navigation
  page.subscribe('page.model.url', function(url) {
    console.log("Page URL changed:", url);
  });
  
  page.loading = false;
});
```

**Source:** [`plugin_examples/subscriptions/example_subscriptions.js#L29-L31`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/plugin_examples/subscriptions/example_subscriptions.js#L29-L31)

### Subscription Events

Subscriptions receive different event types:

```javascript
prop.subscribe(myProp, function(type, value, arg2, arg3) {
  switch(type) {
    case 'set':
      console.log("Property set to:", value);
      break;
      
    case 'addchild':
      console.log("Child added:", value);  // Child prop
      break;
      
    case 'delchild':
      console.log("Child deleted:", value);  // Child prop
      break;
      
    case 'movechild':
      console.log("Child moved:", value, "before", arg2);
      break;
      
    case 'action':
      console.log("Action received:", value);  // Action string
      break;
      
    case 'destroyed':
      console.log("Property destroyed");
      break;
  }
}, {
  actionAsArray: false  // Actions as strings instead of arrays
});
```

**Source:** [`src/ecmascript/es_prop.c#L522-L700`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c#L522-L700)

### Auto-Destroy Pattern

**Problem:** Subscription memory leaks

```javascript
// BAD: Subscription never cleaned up
new page.Route('myapp:leak', function(page) {
  prop.subscribe(prop.global.clock.unixtime, function(t) {
    page.metadata.time = t;  // Updates even after page closed
  });
  
  page.loading = false;
});
```

**Solution:** Use autoDestroy

```javascript
// GOOD: Subscription destroyed with page
new page.Route('myapp:clean', function(page) {
  prop.subscribe(prop.global.clock.unixtime, function(t) {
    page.metadata.time = t;
  }, {
    autoDestroy: true  // Destroyed when callback scope exits
  });
  
  page.loading = false;
});
```

**Source:** [`res/ecmascript/modules/movian/itemhook.js#L32`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/itemhook.js#L32)

### Complex Event Handling

**Example: Dynamic list management**

```javascript
function watchPlaylist(playlistProp, onUpdate) {
  var items = [];
  
  prop.subscribe(playlistProp, function(type, value) {
    switch(type) {
      case 'addchild':
        items.push(value);
        onUpdate('add', value);
        break;
        
      case 'delchild':
        var idx = items.indexOf(value);
        if(idx !== -1) {
          items.splice(idx, 1);
          onUpdate('remove', value);
        }
        break;
        
      case 'movechild':
        // value = moved child, arg2 = before target
        var oldIdx = items.indexOf(value);
        var newIdx = items.indexOf(arg2);
        if(oldIdx !== -1) {
          items.splice(oldIdx, 1);
          items.splice(newIdx, 0, value);
          onUpdate('move', value, newIdx);
        }
        break;
    }
  }, {
    autoDestroy: true
  });
}
```

---

## Performance Optimization

### Caching Strategies

**HTTP response caching:**

```javascript
var cache = {};
var cacheExpiry = 5 * 60 * 1000;  // 5 minutes

function fetchCached(url, callback) {
  var now = Date.now();
  
  if(cache[url] && (now - cache[url].timestamp) < cacheExpiry) {
    console.log("Cache hit:", url);
    callback(null, cache[url].data);
    return;
  }
  
  console.log("Cache miss:", url);
  http.request(url).then(function(response) {
    cache[url] = {
      data: response,
      timestamp: now
    };
    callback(null, response);
  }).catch(callback);
}

// Periodic cache cleanup
setInterval(function() {
  var now = Date.now();
  Object.keys(cache).forEach(function(key) {
    if(now - cache[key].timestamp > cacheExpiry) {
      delete cache[key];
    }
  });
}, 60 * 1000);  // Every minute
```

**Persistent caching with Store:**

```javascript
var store = require('movian/store').create('cache');

function fetchPersistentCached(url, maxAge, callback) {
  var cached = store[url];
  var now = Date.now();
  
  if(cached && (now - cached.timestamp) < maxAge) {
    callback(null, cached.data);
    return;
  }
  
  http.request(url).then(function(response) {
    store[url] = {
      data: response,
      timestamp: now
    };
    callback(null, response);
  }).catch(callback);
}
```

**Source:** [`res/ecmascript/modules/movian/store.js`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/res/ecmascript/modules/movian/store.js)

### Property Subscription Optimization

**Problem:** Too many subscriptions

```javascript
// BAD: 100 subscriptions
for(var i = 0; i < 100; i++) {
  var item = page.appendItem('item:' + i, 'video', {title: 'Item ' + i});
  
  prop.subscribe(item, function(type) {
    console.log("Item updated:", type);  // Excessive logging
  });
}
```

**Solution: Batch operations, subscribe to parent**

```javascript
// GOOD: 1 subscription on parent
prop.subscribe(page.items, function(type, child) {
  if(type === 'addchild') {
    console.log("Item added:", child.metadata.title);
  }
});

// Now add items (subscription fires once per add)
for(var i = 0; i < 100; i++) {
  page.appendItem('item:' + i, 'video', {title: 'Item ' + i});
}
```

### Debouncing and Throttling

**Debounce: Execute after silence**

```javascript
function debounce(fn, delay) {
  var timer = null;
  return function() {
    var args = arguments;
    clearTimeout(timer);
    timer = setTimeout(function() {
      fn.apply(null, args);
    }, delay);
  };
}

// Usage: Search input
var debouncedSearch = debounce(function(query) {
  console.log("Searching for:", query);
  // Perform search...
}, 500);

// Simulate rapid input
debouncedSearch("a");
debouncedSearch("ab");
debouncedSearch("abc");  // Only this fires after 500ms
```

**Throttle: Execute at most once per interval**

```javascript
function throttle(fn, interval) {
  var lastCall = 0;
  return function() {
    var now = Date.now();
    if(now - lastCall >= interval) {
      lastCall = now;
      fn.apply(null, arguments);
    }
  };
}

// Usage: Scroll event
var throttledUpdate = throttle(function() {
  console.log("Update UI");
}, 100);

// Simulated rapid scroll
for(var i = 0; i < 50; i++) {
  throttledUpdate();  // Only fires every 100ms
}
```

### Memory Management

**Avoid closure leaks:**

```javascript
// BAD: Closure captures entire page object
new page.Route('myapp:leak', function(page) {
  page.type = 'directory';
  
  setTimeout(function() {
    console.log("Still referencing page:", page.metadata.title);
    // Page can't be GC'd even after user navigates away
  }, 10000);
});

// GOOD: Extract only needed data
new page.Route('myapp:clean', function(page) {
  page.type = 'directory';
  var title = page.metadata.title;  // Copy primitive
  
  setTimeout(function() {
    console.log("Title:", title);
    // Page can be GC'd
  }, 10000);
});
```

**Manual cleanup:**

```javascript
new page.Route('myapp:cleanup', function(page) {
  var subscriptions = [];
  var timers = [];
  
  // Register resources
  subscriptions.push(prop.subscribe(someProp, callback));
  timers.push(setTimeout(doSomething, 1000));
  
  // Cleanup on page close
  page.subscribe('page.model.destroyed', function() {
    subscriptions.forEach(function(s) { s.destroy(); });
    timers.forEach(clearTimeout);
  });
});
```

---

## Error Handling and Recovery

### Exception Propagation

**JavaScript exceptions in callbacks:**

```javascript
prop.subscribe(prop.global.clock.unixtime, function(time) {
  throw new Error("Callback error");  // Caught by es_prop.c
});
// Error logged to console, subscription continues
```

**Source:** [`src/ecmascript/es_prop.c#L706-L709`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c#L706-L709)

**HTTP errors:**

```javascript
http.request(url).then(function(response) {
  console.log("Success:", response);
}).catch(function(error) {
  console.error("HTTP error:", error.message);
  // Handle: retry, show error page, etc.
});
```

### Zombie Props

**Problem:** Accessing destroyed properties

```javascript
var myProp = prop.createRoot();
prop.destroy(myProp);

// BAD: myProp is now a zombie
prop.set(myProp, 'value', 'test');  // Error: "Property is destroyed"
```

**Detection:**

```c
// From es_prop.c - throws error
if(p->hp_flags & PROP_ZOMBIE)
  duk_error(ctx, DUK_ERR_ERROR, "Property is destroyed");
```

**Source:** [`src/prop/prop_core.c`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/prop/prop_core.c) (PROP_ZOMBIE flag)

**Prevention:**

```javascript
// Keep track of prop lifecycle
var props = new WeakMap();  // If available (not in Duktape)

function safeSet(p, key, value) {
  try {
    prop.set(p, key, value);
  } catch(e) {
    if(e.message.indexOf("destroyed") !== -1) {
      console.warn("Attempted to set destroyed prop");
      return false;
    }
    throw e;
  }
  return true;
}
```

### Graceful Degradation

**API failure fallback:**

```javascript
function fetchWithFallback(primaryUrl, fallbackUrl, callback) {
  http.request(primaryUrl).then(callback).catch(function(err) {
    console.warn("Primary API failed, trying fallback:", err.message);
    
    http.request(fallbackUrl).then(callback).catch(function(err2) {
      console.error("Both APIs failed");
      callback(new Error("Service unavailable"));
    });
  });
}
```

**User-visible error pages:**

```javascript
new page.Route('myapp:content:(.*)', function(page, id) {
  page.type = 'directory';
  page.metadata.title = 'Loading...';
  
  fetchContent(id, function(err, items) {
    if(err) {
      page.type = 'raw';
      page.metadata.title = 'Error';
      page.appendPassiveItem('label', null, {
        title: 'Failed to load content: ' + err.message
      });
      page.appendItem('myapp:retry:' + id, 'directory', {
        title: 'Retry'
      });
    } else {
      items.forEach(function(item) {
        page.appendItem(item.url, item.type, item.metadata);
      });
    }
    
    page.loading = false;
  });
});
```

### Validation and Sanitization

**Input validation:**

```javascript
function validateSearchQuery(query) {
  if(typeof query !== 'string') {
    throw new TypeError("Query must be a string");
  }
  
  if(query.length === 0) {
    throw new Error("Query cannot be empty");
  }
  
  if(query.length > 200) {
    throw new Error("Query too long (max 200 chars)");
  }
  
  return query.trim();
}

new page.Searcher('MyApp', 'icon.png', function(page, query) {
  try {
    query = validateSearchQuery(query);
    performSearch(page, query);
  } catch(e) {
    page.error(e.message);
    page.loading = false;
  }
});
```

**URL sanitization:**

```javascript
function sanitizeUrl(url) {
  // Remove potentially dangerous URL schemes
  var dangerous = ['javascript:', 'data:', 'vbscript:'];
  
  for(var i = 0; i < dangerous.length; i++) {
    if(url.toLowerCase().indexOf(dangerous[i]) === 0) {
      throw new Error("Unsafe URL scheme");
    }
  }
  
  return url;
}
```

---

## Debugging Strategies

### Enable Debug Logging

**Environment variables:**

```bash
# Plugin-specific debug
MOVIAN_PLUGIN_DEBUG=1 ./build.linux/movian

# HTTP tracing
MOVIAN_TRACE_HTTP=1 ./build.linux/movian

# Property system debug
MOVIAN_TRACE_PROP=1 ./build.linux/movian

# All traces
MOVIAN_TRACE=*:DEBUG ./build.linux/movian
```

**Source:** [`docs/runtime.md#useful-debug-flags-and-environment-variables`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/docs/runtime.md)

### Property Tree Inspection

**Dump page structure:**

```javascript
new page.Route('myapp:debug', function(page) {
  page.type = 'directory';
  page.metadata.title = 'Debug Page';
  
  page.appendItem('item1', 'video', {title: 'Video 1'});
  page.appendItem('item2', 'audio', {title: 'Audio 1'});
  
  // Dump property tree to console
  prop.print(page.model);
  
  page.loading = false;
});
```

**Output example:**

```
prop (items)
  ├── prop (item) [video]
  │   ├── url = "item1"
  │   └── metadata
  │       └── title = "Video 1"
  └── prop (item) [audio]
      ├── url = "item2"
      └── metadata
          └── title = "Audio 1"
```

**Source:** [`src/ecmascript/es_prop.c#L117-L122`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_prop.c#L117-L122)

### Structured Logging

**Log levels:**

```javascript
function Logger(name) {
  this.name = name;
  
  this.debug = function(msg) {
    console.log("[DEBUG] [" + this.name + "] " + msg);
  };
  
  this.info = function(msg) {
    console.log("[INFO] [" + this.name + "] " + msg);
  };
  
  this.warn = function(msg) {
    console.warn("[WARN] [" + this.name + "] " + msg);
  };
  
  this.error = function(msg, err) {
    console.error("[ERROR] [" + this.name + "] " + msg);
    if(err && err.stack) {
      console.error(err.stack);
    }
  };
}

// Usage
var log = new Logger('MyPlugin');
log.info("Plugin loaded");
log.debug("Fetching data from API");
log.error("API request failed", err);
```

### Performance Profiling

**Measure execution time:**

```javascript
function profile(name, fn) {
  var start = Date.now();
  var result = fn();
  var elapsed = Date.now() - start;
  
  console.log("PROFILE [" + name + "]: " + elapsed + "ms");
  
  if(elapsed > 100) {
    console.warn("SLOW OPERATION: " + name);
  }
  
  return result;
}

// Usage
profile("Load feed", function() {
  var feed = fetchFeed();
  processFeed(feed);
});
```

**Async profiling:**

```javascript
function profileAsync(name, fn, callback) {
  var start = Date.now();
  
  fn(function(err, result) {
    var elapsed = Date.now() - start;
    console.log("PROFILE [" + name + "]: " + elapsed + "ms");
    callback(err, result);
  });
}

// Usage
profileAsync("HTTP request", function(done) {
  http.request(url).then(function(r) {
    done(null, r);
  }).catch(done);
}, function(err, response) {
  // Handle result...
});
```

### Breakpoint Simulation

**Conditional logging:**

```javascript
var DEBUG = true;  // Set to false in production

function breakpoint(condition, message) {
  if(DEBUG && condition) {
    console.log("=== BREAKPOINT ===");
    console.log(message);
    console.log("Stack trace:");
    try {
      throw new Error();
    } catch(e) {
      console.log(e.stack);
    }
  }
}

// Usage
breakpoint(items.length === 0, "No items returned from API");
```

---

## Real-World Patterns

### Complete Example: Robust Feed Loader

```javascript
var page = require('movian/page');
var store = require('movian/store').create('feeds');

var cache = {};

function fetchFeed(url, callback) {
  // Check memory cache
  if(cache[url] && (Date.now() - cache[url].timestamp) < 60000) {
    console.log("Memory cache hit: " + url);
    callback(null, cache[url].data);
    return;
  }
  
  // Check persistent cache
  var stored = store[url];
  if(stored && (Date.now() - stored.timestamp) < 3600000) {
    console.log("Disk cache hit: " + url);
    cache[url] = stored;  // Promote to memory
    callback(null, stored.data);
    return;
  }
  
  // Fetch from network with retry
  console.log("Fetching: " + url);
  fetchWithRetry(url, 3, function(err, response) {
    if(err) {
      console.error("Fetch failed: " + url, err);
      callback(err);
      return;
    }
    
    try {
      var data = JSON.parse(response.toString());
      
      // Cache in memory and disk
      var cached = {
        data: data,
        timestamp: Date.now()
      };
      cache[url] = cached;
      store[url] = cached;
      
      callback(null, data);
    } catch(parseErr) {
      console.error("JSON parse failed", parseErr);
      callback(parseErr);
    }
  });
}

new page.Route('feed:list:(.*)', function(page, feedId) {
  page.type = 'directory';
  page.metadata.title = 'Loading...';
  
  var offset = 0;
  var pageSize = 20;
  var loading = false;
  
  function loadMore() {
    if(loading) return;
    loading = true;
    
    var url = 'https://api.example.com/feed/' + feedId + 
              '?offset=' + offset + '&limit=' + pageSize;
    
    fetchFeed(url, function(err, data) {
      loading = false;
      
      if(err) {
        page.error("Failed to load feed: " + err.message);
        page.loading = false;
        return;
      }
      
      if(data.items.length === 0) {
        page.haveMore(false);
        page.loading = false;
        return;
      }
      
      data.items.forEach(function(item) {
        page.appendItem(item.url, 'video', {
          title: item.title,
          icon: item.thumbnail,
          duration: item.duration
        });
      });
      
      offset += data.items.length;
      page.haveMore(data.hasMore);
      page.loading = false;
    });
  }
  
  page.asyncPaginator = loadMore;
  loadMore();
});
```

### Pattern: State Machine for Complex Flows

```javascript
function StateMachine(initialState, transitions) {
  var currentState = initialState;
  var listeners = [];
  
  function transition(event) {
    var handler = transitions[currentState] && transitions[currentState][event];
    
    if(!handler) {
      console.error("Invalid transition:", currentState, "->", event);
      return false;
    }
    
    var newState = handler();
    
    if(newState) {
      console.log("State:", currentState, "->", newState);
      currentState = newState;
      
      listeners.forEach(function(l) {
        l(newState);
      });
    }
    
    return true;
  }
  
  function onStateChange(listener) {
    listeners.push(listener);
  }
  
  function getState() {
    return currentState;
  }
  
  return {
    transition: transition,
    onStateChange: onStateChange,
    getState: getState
  };
}

// Usage: Video playback states
var playback = StateMachine('stopped', {
  stopped: {
    play: function() {
      startPlayback();
      return 'playing';
    }
  },
  playing: {
    pause: function() {
      pausePlayback();
      return 'paused';
    },
    stop: function() {
      stopPlayback();
      return 'stopped';
    }
  },
  paused: {
    play: function() {
      resumePlayback();
      return 'playing';
    },
    stop: function() {
      stopPlayback();
      return 'stopped';
    }
  }
});

playback.onStateChange(function(state) {
  console.log("Playback state:", state);
});

playback.transition('play');   // stopped -> playing
playback.transition('pause');  // playing -> paused
playback.transition('play');   // paused -> playing
playback.transition('stop');   // playing -> stopped
```

### Pattern: Queue with Concurrency Limit

```javascript
function TaskQueue(concurrency) {
  var queue = [];
  var running = 0;
  
  function process() {
    while(running < concurrency && queue.length > 0) {
      var task = queue.shift();
      running++;
      
      task.fn(function(err, result) {
        running--;
        task.callback(err, result);
        process();  // Process next
      });
    }
  }
  
  function enqueue(fn, callback) {
    queue.push({fn: fn, callback: callback});
    process();
  }
  
  return {
    enqueue: enqueue
  };
}

// Usage: Limit concurrent HTTP requests
var requestQueue = TaskQueue(2);  // Max 2 concurrent

for(var i = 0; i < 10; i++) {
  requestQueue.enqueue(function(done) {
    http.request('https://api.example.com/item/' + i).then(function(r) {
      done(null, r);
    }).catch(done);
  }, function(err, response) {
    if(err) {
      console.error("Request failed:", err);
    } else {
      console.log("Response:", response);
    }
  });
}
```

---

## Summary

**Key Takeaways:**

- **Async:** Use setTimeout for delays, debounce/throttle rapid events, cancel stale operations
- **Item Hooks:** Add context menus to media items, use autoDestroy for cleanup
- **Subscriptions:** Monitor prop changes, use autoDestroy to prevent leaks, batch operations
- **Performance:** Cache aggressively, minimize subscriptions, optimize prop operations
- **Errors:** Validate inputs, catch exceptions, provide fallbacks, show user-friendly errors
- **Debugging:** Enable trace flags, inspect prop trees with `prop.print()`, structured logging

**Best Practices:**

1. **Always clean up resources** - Use autoDestroy, onUnload callbacks
2. **Cache strategically** - Memory + disk, expire policies, invalidation
3. **Handle errors gracefully** - Retry with backoff, fallback APIs, user-visible messages
4. **Profile performance** - Log slow operations, optimize hot paths
5. **Log structured data** - Include timestamps, log levels, context

**Next Steps:**

- Review [ECMAScript API v2 Reference](plugin-dev-api-v2.md) for complete API
- Study plugin examples in `plugin_examples/` directory
- Explore `res/ecmascript/modules/` for implementation patterns
- Read [Native Plugins Guide](04-native-plugins.md) for performance-critical code

---

**Document Status:** Complete  
**Coverage:** Async patterns, item hooks, subscriptions, performance, errors, debugging  
**Last Updated:** 2024
