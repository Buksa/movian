# Real-world Examples

This chapter provides in-depth analysis of real plugin implementations, architectural patterns, and practical solutions to common plugin development challenges using Movian's plugin system.

## Table of Contents

1. [Plugin Architecture Analysis](#plugin-architecture-analysis)
2. [Music Plugin Deep Dive](#music-plugin-deep-dive)
3. [Settings Plugin Patterns](#settings-plugin-patterns)
4. [Async Pagination Implementation](#async-pagination-implementation)
5. [Item Hook Integration](#item-hook-integration)
6. [Subscription Patterns](#subscription-patterns)
7. [Video Scrobbling Integration](#video-scrobbling-integration)
8. [Web Popup Integration](#web-popup-integration)
9. [Common Architectural Patterns](#common-architectural-patterns)
10. [Performance Optimization Examples](#performance-optimization-examples)

## Plugin Architecture Analysis

Movian's plugin ecosystem follows several established patterns. Let's analyze the key architectural components:

### Core Plugin Structure

All plugins follow a similar initialization pattern:

```javascript
// Standard plugin initialization pattern (API v2)
var page = require('movian/page');
var service = require('movian/service');

var BASE_URI = 'myplugin:';

// Service registration - appears on home screen
service.create('My Plugin', BASE_URI + 'start', 'video');

// Route handlers
new page.Route(BASE_URI + 'start', function(page) {
  // Main page logic
});
```

This pattern is evident in all plugin examples in `plugin_examples/`. The service registration creates the entry point on Movian's home screen.

### URI Routing System

Movian uses a URI-based routing system that allows for deep linking:

```javascript
// From plugin_examples/music/example_music.js (updated to API v2)
var page = require('movian/page');

new page.Route('example:music:', function(page) {
  page.type = "directory";
  page.metadata.title = "Music examples";
  // ... page content
});
```

The routing system is implemented in `src/ecmascript/ecmascript.c` and provides:
- Pattern matching with capture groups
- Parameter extraction
- Deep linking support

## Music Plugin Deep Dive

Let's analyze the music plugin example in detail:

### File Structure

```
plugin_examples/music/
├── plugin.json          # Plugin manifest
└── example_music.js     # Main plugin logic
```

### Manifest Analysis

```json
// plugin_examples/music/plugin.json
{
  "type": "ecmascript",
  "id": "example_music",
  "file": "example_music.js"
}
```

This minimal manifest demonstrates the basic requirements:
- `type`: "ecmascript" for JavaScript plugins
- `id`: Unique identifier using reverse domain notation
- `file`: Entry point file

### Implementation Analysis

```javascript
// From plugin_examples/music/example_music.js (updated to API v2)
var page = require('movian/page');
var service = require('movian/service');

var U = "example:music:";

// Register a service (will appear on home page)
service.create("Music example", U, "other");

// Add a responder to the registered URI
new page.Route(U, function(page) {
  page.type = "directory";
  page.metadata.title = "Music examples";

  var B = "http://www.lonelycoder.com/music/";

  page.appendItem(B + "Hybris_Intro-remake.mp3", "audio", {
    title: "Remix of Hybris (The Amiga Game)",
    artist: "Andreas Öman"
  });

  page.appendItem(B + "Russian_Ravers_Rave.mp3", "audio", {
    title: "Russian Ravers Rave",
    artist: "Andreas Öman"
  });

  page.appendItem(B + "spaceships_and_robots_preview.mp3", "audio", {
    title: "Spaceships and Robots",
    artist: "Andreas Öman"
  });

  page.appendItem("example:music:stream", "stream", {
    title: "Shoutcast test stream"
  });
});

new page.Route("example:music:stream", function(page) {
  page.type = "stream";
  page.metadata.title = "Shoutcast stream test";

  page.appendItem("http://mp3.shoutcast.com:8018", "audio", {
    title: "Shoutcast test"
  });
});
```

### Key Architectural Insights

1. **Service Registration**: `service.create()` creates the home screen entry
2. **URI Base Pattern**: Using a base URI (`U`) for consistency
3. **Multiple Routes**: Demonstrates handling different content types
4. **Content Types**: Shows both local audio files and streaming content

### Integration with Movian Core

The plugin integrates with Movian's core systems:

- **Page System**: Uses Movian's page API (`page.type`, `page.metadata`)
- **Item System**: Creates items with proper metadata
- **Audio Playback**: Integrates with Movian's audio engine
- **Streaming**: Supports both local and streaming audio

## Settings Plugin Patterns

The settings example demonstrates plugin configuration management:

### File Structure Analysis

```
plugin_examples/settings/
├── plugin.json
└── settings.js
```

### Settings Implementation

```javascript
// From plugin_examples/settings/example_settings.js (updated to API v2)
var page = require('movian/page');
var service = require('movian/service');
var settings = require('movian/settings');

var U = "example:settings:";

service.create("Settings example", U, "other");

new page.Route(U, function(page) {
  page.type = "directory";
  page.metadata.title = "Settings example";

  // Create settings using API v2 pattern
  var s = new settings.globalSettings("example", "Example settings", null, 
    "Settings for the example plugin");

  // Create a boolean setting
  var boolSetting = s.createBool('v1', 'A boolean setting', true, function(val) {
    print("Bool is now", val);
  });

  // Create a string setting  
  var stringSetting = s.createString('v2', 'A string setting', 'default', function(val) {
    print("String is now", val);
  });

  // Create an integer setting
  var intSetting = s.createInt('v3', 'An integer setting', 42, -50, 50, 1, 'px', function(val) {
    print("Int is now", val);
  });

  // Create a divider
  s.createDivider("Advanced Settings");

  // Create an action
  s.createAction('action', 'Click me', function() {
    print("Action clicked!");
  });

  page.appendItem("dummy", "separator", {
    title: "Settings configured"
  });

  page.appendItem("dummy", "action", {
      title: "Value is: " + stringSetting.value,

      callback: function() {
        stringSetting.value = "hello world";
        page.entries = 0;
        page.flush();
        // In API v2, settings are automatically updated
      }
    });

    page.appendItem("dummy", "action", {
      title: "Open settings for this plugin",
      callback: function() {
        // In API v2, settings are accessible via the global settings tree
        print("Settings configured via global settings tree");
      }
    });
  });
```

### Settings Architecture

The settings system is implemented in `res/ecmascript/modules/movian/settings.js`:

```javascript
// From res/ecmascript/modules/movian/settings.js:5-42
function createSetting(group, type, id, title) {
  var model = group.nodes[id];
  
  model.type = type;
  model.enabled = true;
  model.metadata.title = title;

  var item = {};

  Object.defineProperties(item, {
    model: {
      value: model
    },

    value: {
      get: function() {
        return model.value;
      },

      set: function(v) {
        model.value = v;
      }
    },

    enabled: {
      set: function(val) {
        model.enabled = val;
      },
      get: function() {
        return parseInt(model.enabled) ? true : false;
      }
    }
  });

  return item;
}
```

### Key Settings Patterns

1. **Group Creation**: Settings are organized into logical groups
2. **Property Access**: Settings use JavaScript property getters/setters
3. **Persistence**: Settings are automatically persisted
4. **UI Integration**: Settings appear in Movian's settings interface

## Async Pagination Implementation

The async pagination example demonstrates handling large datasets efficiently:

### Implementation Analysis

```javascript
// From plugin_examples/async_page_load/async_page_load.js
var page = require('movian/page');

new page.Route('asyncPageLoad:test:(.*)', function(page, arg1) {
  var offset = 0;

  function loader() {
    setTimeout(function() {

      if(offset > 100) {
        page.haveMore(false);
        return;
      }

      for(var i = 0; i < 20; i++) {
        page.appendItem('asyncPageLoad:item:' + (offset + i), "directory", {
          title: "Item" + (offset + i)
        });
      }
      offset += 20;
      page.haveMore(true);
    }, 1000);

  }

  page.type = "directory";
  page.asyncPaginator = loader;
  loader();
});
```

### Pagination Architecture

The pagination system integrates with Movian's page system:

1. **Async Loading**: Uses `setTimeout` to simulate async data loading
2. **Progressive Loading**: Loads items in batches (20 at a time)
3. **Have More Signal**: `page.haveMore(true/false)` indicates more content
4. **Paginator Assignment**: `page.asyncPaginator = loader` registers the loader

### Integration with Core Systems

The pagination system hooks into Movian's core page handling:

```javascript
// From res/ecmascript/modules/movian/page.js - Page class implementation
// The page system supports async pagination through the asyncPaginator property
// When user scrolls to bottom, Movian calls the registered paginator
```

### Real-world Application

For actual API integration:

```javascript
function createApiPaginator(page, apiEndpoint) {
  var currentPage = 1;
  var hasMore = true;
  
  function loader() {
    if (!hasMore) {
      page.haveMore(false);
      return;
    }
    
    var url = apiEndpoint + '?page=' + currentPage + '&limit=20';
    
    http.request(url, function(response) {
      if (response.status === 200) {
        var data = JSON.parse(response.data);
        
        data.items.forEach(function(item) {
          page.appendItem(item.url, 'video', {
            title: item.title,
            description: item.description
          });
        });
        
        currentPage++;
        hasMore = data.hasMore;
        page.haveMore(hasMore);
      } else {
        page.haveMore(false);
      }
    });
  }
  
  page.asyncPaginator = loader;
  return loader;
}
```

## Item Hook Integration

Item hooks allow plugins to add context menu items to media items:

### Implementation Analysis

```javascript
// From plugin_examples/itemhook/itemhook.js (updated to API v2)
var page = require('movian/page');
var service = require('movian/service');
var itemhook = require('movian/itemhook');

itemhook.create({
  id: 'com.example.itemhook',
  title: 'Example item hook',
  description: 'Adds an item to the example hook',
  icon: 'itemhook.png',
  handler: function(item) {
    console.log('Item hook called with item:', item);
  }
});

service.create('Item hook example', 'itemhooktest:', 'other');

new page.Route('itemhooktest:', function(page) {
  page.type = 'directory';
  page.metadata.title = 'Item hook test';

  page.appendItem('http://www.lonelycoder.com/music/Hybris_Intro-remake.mp3', 'audio', {
    title: 'Test audio item'
  });

  page.appendItem('http://www.lonelycoder.com/music/Russian_Ravers_Rave.mp3', 'audio', {
    title: 'Test audio item 2'
  });
});
```

### Item Hook Architecture

The item hook system is implemented in `src/ecmascript/es_hook.c`:

```c
// Item hooks are registered through the JavaScript interface
// and integrated with the context menu system
```

### Hook Registration Process

1. **Hook Creation**: `itemhook.create()` registers a new hook
2. **Context Integration**: Hook appears in context menus for relevant items
3. **Handler Execution**: User action triggers the handler function
4. **Item Context**: Handler receives the item object as parameter

### Advanced Hook Patterns

```javascript
// Conditional hooks based on content type
itemhook.create({
  id: 'com.example.video.hook',
  title: 'Video Tools',
  description: 'Tools for video content',
  icon: 'video-tools.png',
  
  // Filter to only show for video items
  filter: function(item) {
    return item.type === 'video';
  },
  
  handler: function(item) {
    // Show video-specific options
    showVideoOptions(item);
  }
});

// Multi-action hooks
itemhook.create({
  id: 'com.example.multi.hook',
  title: 'Multi Actions',
  description: 'Multiple actions in one hook',
  icon: 'multi.png',
  
  handler: function(item) {
    // Show action menu
    showActionMenu([
      {
        title: 'Download',
        action: function() { downloadItem(item); }
      },
      {
        title: 'Share',
        action: function() { shareItem(item); }
      },
      {
        title: 'Info',
        action: function() { showItemInfo(item); }
      }
    ]);
  }
});
```

## Subscription Patterns

The subscription example demonstrates reactive programming patterns:

### Implementation Analysis

```javascript
// From plugin_examples/subscriptions/subscriptions.js
var page = require('movian/page');
var prop = require('movian/prop');

new page.Route('subscriptions:test', function(page) {
  page.type = "directory";
  page.metadata.title = "Subscription test";

  // Create a property to subscribe to
  var p = prop.createRoot();

  p.value = "Initial value";

  // Subscribe to property changes
  var subscription = prop.subscribe(p, function(event, value) {
    console.log("Property changed:", event, value);
    page.entries = 0;
    page.appendItem("dummy", "directory", {
      title: "Value is now: " + value
    });
  });

  page.appendItem("dummy", "action", {
    title: "Change value",
    callback: function() {
      p.value = "Changed at " + new Date().toISOString();
    }
  });

  page.appendItem("dummy", "action", {
    title: "Change value again",
    callback: function() {
      p.value = "Changed again at " + new Date().toISOString();
    }
  });

  page.appendItem("dummy", "action", {
    title: "Destroy subscription",
    callback: function() {
      subscription.destroy();
      page.appendItem("dummy", "directory", {
        title: "Subscription destroyed - no more updates"
      });
    }
  });
});
```

### Property System Architecture

The property system is implemented in `src/ecmascript/es_prop.c`:

```c
// Property system provides reactive bindings
// Properties can be subscribed to for change notifications
// Supports nested property paths and event filtering
```

### Advanced Subscription Patterns

```javascript
// Multi-property subscriptions
function createMultiSubscription(page, properties) {
  var subscriptions = properties.map(function(prop) {
    return prop.subscribe(prop, function(event, value) {
      updateUI();
    });
  });
  
  // Cleanup on page destroy
  page.onDestroy = function() {
    subscriptions.forEach(function(sub) {
      sub.destroy();
    });
  };
  
  return subscriptions;
}

// Conditional subscriptions
function createConditionalSubscription(page, prop, condition, handler) {
  var activeSubscription = null;
  
  function updateSubscription() {
    if (condition()) {
      if (!activeSubscription) {
        activeSubscription = prop.subscribe(prop, handler);
      }
    } else {
      if (activeSubscription) {
        activeSubscription.destroy();
        activeSubscription = null;
      }
    }
  }
  
  updateSubscription();
  
  return {
    update: updateSubscription,
    destroy: function() {
      if (activeSubscription) {
        activeSubscription.destroy();
      }
    }
  };
}

// Debounced subscriptions
function createDebouncedSubscription(page, prop, handler, delay) {
  var timeout = null;
  
  return prop.subscribe(prop, function(event, value) {
    if (timeout) {
      clearTimeout(timeout);
    }
    
    timeout = setTimeout(function() {
      handler(event, value);
    }, delay);
  });
}
```

## Video Scrobbling Integration

The video scrobbling example demonstrates integration with external services:

### Implementation Analysis

```javascript
// From plugin_examples/videoscrobbling/videoscrobbling.js (updated to API v2)
var page = require('movian/page');
var service = require('movian/service');
var scrobbler = require('movian/videoscrobbler');

scrobbler.create({
  id: 'com.example.videoscrobbler',
  title: 'Example video scrobbler',
  description: 'Scrobbles to example.com',
  icon: 'scrobbler.png',

  handler: function(item, progress) {
    console.log('Scrobbling:', item.title, 'Progress:', progress);
  }
});

service.create('Video scrobbler example', 'scrobblertest:', 'other');

new page.Route('scrobblertest:', function(page) {
  page.type = 'directory';
  page.metadata.title = 'Video scrobbler test';

  page.appendItem('http://www.lonelycoder.com/music/Hybris_Intro-remake.mp3', 'audio', {
    title: 'Test audio item 1'
  });

  page.appendItem('http://www.lonelycoder.com/music/Russian_Ravers_Rave.mp3', 'audio', {
    title: 'Test audio item 2'
  });
});
```

### Scrobbler Architecture

The scrobbler system is implemented in `res/ecmascript/modules/movian/videoscrobbler.js`:

```javascript
// From res/ecmascript/modules/movian/videoscrobbler.js
// Scrobblers receive playback progress updates
// Can integrate with external services like Trakt, Last.fm
```

### Advanced Scrobbling Patterns

```javascript
// Service-specific scrobbler
scrobbler.create({
  id: 'com.trakt.scrobbler',
  title: 'Trakt.tv',
  description: 'Scrobble to Trakt.tv',
  icon: 'trakt.png',
  
  // Authentication
  authenticate: function(callback) {
    // OAuth flow for Trakt
    authenticateWithTrakt(callback);
  },
  
  // Progress tracking
  handler: function(item, progress) {
    if (progress === 'start') {
      trakt.startWatching(item);
    } else if (progress === 'stop') {
      trakt.stopWatching(item);
    } else if (typeof progress === 'number') {
      trakt.updateProgress(item, progress);
    }
  },
  
  // Media matching
  matcher: function(item) {
    // Try to match item with Trakt database
    return trakt.search(item.title, item.year);
  }
});

// Batch scrobbling
function batchScrobbler(items) {
  var scrobbler = require('movian/videoscrobbler');
  
  items.forEach(function(item) {
    scrobbler.create({
      id: 'batch.' + item.id,
      title: 'Batch Scrobble',
      handler: function(item, progress) {
        if (progress === 'complete') {
          // Mark as watched in external service
          markAsWatched(item);
        }
      }
    });
  });
}
```

## Web Popup Integration

The web popup example demonstrates web integration:

### Implementation Analysis

```javascript
// From plugin_examples/webpopupplugin/webpopupplugin.js (updated to API v2)
var page = require('movian/page');
var service = require('movian/service');

service.create('Web popup example', 'webpopuptest:', 'other');

new page.Route('webpopuptest:', function(page) {
  page.type = 'directory';
  page.metadata.title = 'Web popup test';

  page.appendItem('webpopuptest:google', 'directory', {
    title: 'Open Google'
  });

  page.appendItem('webpopuptest:github', 'directory', {
    title: 'Open GitHub'
  });
});

new page.Route('webpopuptest:(.*)', function(page, site) {
  page.type = 'directory';
  page.metadata.title = 'Opening ' + site;

    var popup = require('native/popup').webpopup('https://' + site + '.com', 
      site + ' website', 'http://localhost:42000/done');

    if(popup.result == 'trapped') {
      console.log('Popup completed successfully');
    } else {
      console.log('Popup result:', popup.result);
    }
  });
```

### Web Popup Architecture

The web popup system integrates with Movian's web view:

```javascript
// Web popups use Movian's built-in web browser
// Supports JavaScript injection and event handling
// Can be used for OAuth flows and web-based configuration
```

### Advanced Web Integration

```javascript
// OAuth flow example
function authenticateWithService(serviceName) {
  var authUrl = 'https://' + serviceName + '.com/oauth/authorize';
  var redirectUri = 'movian://' + serviceName + '/callback';
  
  var popup = plugin.popup(authUrl + '?redirect_uri=' + encodeURIComponent(redirectUri), {
    title: 'Authenticate with ' + serviceName,
    width: 600,
    height: 400
  });
  
  popup.on('urlchange', function(url) {
    if (url.startsWith(redirectUri)) {
      var code = extractCodeFromUrl(url);
      exchangeCodeForToken(code, function(token) {
        popup.close();
        saveToken(serviceName, token);
      });
    }
  });
}

// Web-based configuration
function showWebConfig(configUrl) {
  var popup = plugin.popup(configUrl, {
    title: 'Plugin Configuration',
    width: 800,
    height: 600
  });
  
  // Inject Movian API for web page
  popup.on('load', function() {
    popup.inject('window.MovianAPI = { saveSettings: function(settings) { /* ... */ } };');
  });
  
  popup.on('message', function(event) {
    if (event.data.type === 'saveSettings') {
      savePluginSettings(event.data.settings);
      popup.close();
    }
  });
}
```

## Common Architectural Patterns

### Service Factory Pattern

```javascript
// Factory for creating consistent services (API v2)
var page = require('movian/page');
var service = require('movian/service');

function createService(name, baseUri, category) {
  return {
    name: name,
    baseUri: baseUri,
    category: category || 'other',
    serviceHandle: null,
    routes: [],
    
    register: function() {
      this.serviceHandle = service.create(this.name, this.baseUri, this.category);
    },
    
    addRoute: function(pattern, handler) {
      var route = new page.Route(this.baseUri + pattern, handler);
      this.routes.push(route);
    },
    
    createPage: function(title, type) {
      return {
        type: type || 'directory',
        metadata: {
          title: title
        }
      };
    },
    
    destroy: function() {
      if (this.serviceHandle) {
        this.serviceHandle.destroy();
      }
      this.routes.forEach(function(r) { r.destroy(); });
    }
  };
}

// Usage
var videoService = createService('My Video Service', 'myservice:', 'video');
videoService.register();

videoService.addRoute('browse', function(page) {
  Object.assign(page, videoService.createPage('Browse Videos'));
  // ... page content
});
```

### API Client Pattern

```javascript
// Reusable API client
function createApiClient(baseUrl, options) {
  var http = require('movian/http');
  
  return {
    baseUrl: baseUrl,
    options: options || {},
    
    request: function(endpoint, params, callback) {
      var url = this.baseUrl + endpoint;
      
      if (params) {
        var queryString = Object.keys(params)
          .map(function(key) {
            return key + '=' + encodeURIComponent(params[key]);
          })
          .join('&');
        url += '?' + queryString;
      }
      
      http.request(url, this.options, callback);
    },
    
    get: function(endpoint, callback) {
      this.request(endpoint, null, callback);
    },
    
    post: function(endpoint, data, callback) {
      http.request(this.baseUrl + endpoint, {
        method: 'POST',
        data: JSON.stringify(data),
        headers: {
          'Content-Type': 'application/json'
        }
      }, callback);
    }
  };
}

// Usage
var api = createApiClient('https://api.example.com/v1/', {
  headers: {
    'User-Agent': 'MyPlugin/1.0'
  }
});

api.get('/movies', function(response) {
  var movies = JSON.parse(response.data);
  // Process movies
});
```

### Cache Manager Pattern

```javascript
// Advanced cache manager
function createCacheManager(options) {
  var cache = {};
  var ttl = options.ttl || 5 * 60 * 1000; // 5 minutes default
  
  return {
    get: function(key) {
      var item = cache[key];
      if (item && Date.now() - item.timestamp < ttl) {
        return item.data;
      }
      return null;
    },
    
    set: function(key, data, customTtl) {
      cache[key] = {
        data: data,
        timestamp: Date.now(),
        ttl: customTtl || ttl
      };
    },
    
    invalidate: function(key) {
      delete cache[key];
    },
    
    clear: function() {
      cache = {};
    },
    
    cleanup: function() {
      var now = Date.now();
      Object.keys(cache).forEach(function(key) {
        var item = cache[key];
        if (now - item.timestamp > item.ttl) {
          delete cache[key];
        }
      });
    }
  };
}

// Usage
var cache = createCacheManager({ ttl: 10 * 60 * 1000 }); // 10 minutes

function loadMovies(callback) {
  var cached = cache.get('movies');
  if (cached) {
    callback(cached);
    return;
  }
  
  api.get('/movies', function(response) {
    var movies = JSON.parse(response.data);
    cache.set('movies', movies);
    callback(movies);
  });
}
```

## Performance Optimization Examples

### Lazy Loading Implementation

```javascript
// Lazy loading for large datasets
function createLazyLoader(page, dataSource, pageSize) {
  pageSize = pageSize || 20;
  var currentOffset = 0;
  var hasMore = true;
  var loading = false;
  
  function loadMore() {
    if (loading || !hasMore) return;
    
    loading = true;
    
    dataSource.load(currentOffset, pageSize, function(items, moreAvailable) {
      loading = false;
      
      items.forEach(function(item) {
        page.appendItem(item.url, item.type, item.metadata);
      });
      
      currentOffset += items.length;
      hasMore = moreAvailable;
      page.haveMore(hasMore);
    });
  }
  
  page.asyncPaginator = loadMore;
  
  // Initial load
  loadMore();
  
  return {
    loadMore: loadMore,
    reset: function() {
      currentOffset = 0;
      hasMore = true;
      page.entries = 0;
      loadMore();
    }
  };
}
```

### Debounced Search

```javascript
// Debounced search to reduce API calls
function createDebouncedSearch(page, searchFunction, delay) {
  delay = delay || 300;
  var timeout = null;
  var lastQuery = null;
  
  return function(query) {
    if (query === lastQuery) return;
    
    lastQuery = query;
    
    if (timeout) {
      clearTimeout(timeout);
    }
    
    timeout = setTimeout(function() {
      if (query.trim() === '') {
        page.entries = 0;
        page.haveMore(false);
        return;
      }
      
      page.appendItem('dummy', 'separator', {
        title: 'Searching for: ' + query
      });
      
      searchFunction(query, function(results) {
        page.entries = 0;
        results.forEach(function(result) {
          page.appendItem(result.url, result.type, result.metadata);
        });
      });
    }, delay);
  };
}
```

### Memory Management

```javascript
// Memory management for large datasets
function createMemoryManager() {
  var resources = [];
  
  return {
    addResource: function(resource) {
      resources.push(resource);
    },
    
    cleanup: function() {
      resources.forEach(function(resource) {
        if (resource.destroy) {
          resource.destroy();
        } else if (resource.release) {
          resource.release();
        }
      });
      resources = [];
    },
    
    getResource: function(index) {
      return resources[index];
    }
  };
}

// Usage in page
function createPageWithMemoryManagement(page) {
  var memoryManager = createMemoryManager();
  
  page.onDestroy = function() {
    memoryManager.cleanup();
  };
  
  return {
    addManagedResource: function(resource) {
      memoryManager.addResource(resource);
      return resource;
    }
  };
}
```

## Error Handling Patterns

### Robust Error Handling

```javascript
// Comprehensive error handling
function safeApiCall(url, options, callback) {
  var http = require('movian/http');
  
  try {
    http.request(url, options, function(response) {
      if (response.status >= 200 && response.status < 300) {
        try {
          var data = JSON.parse(response.data);
          callback(null, data);
        } catch (parseError) {
          callback(new Error('Invalid JSON response'), null);
        }
      } else {
        callback(new Error('HTTP ' + response.status + ': ' + response.statusText), null);
      }
    }).on('error', function(error) {
      callback(error, null);
    });
  } catch (error) {
    callback(error, null);
  }
}

// Usage with user feedback
function loadMovies(page) {
  safeApiCall('https://api.example.com/movies', {}, function(error, movies) {
    if (error) {
      console.error('Failed to load movies:', error);
      page.appendItem('dummy', 'video', {
        title: 'Error loading movies',
        description: error.message,
        icon: 'error'
      });
      return;
    }
    
    movies.forEach(function(movie) {
      page.appendItem(movie.url, 'video', {
        title: movie.title,
        description: movie.description
      });
    });
  });
}
```

### Retry Logic

```javascript
// Exponential backoff retry
function retryOperation(operation, maxRetries, callback) {
  maxRetries = maxRetries || 3;
  var attempt = 0;
  
  function tryOperation() {
    attempt++;
    
    operation(function(error, result) {
      if (!error) {
        callback(null, result);
        return;
      }
      
      if (attempt >= maxRetries) {
        callback(error, null);
        return;
      }
      
      // Exponential backoff
      var delay = Math.pow(2, attempt) * 1000;
      setTimeout(tryOperation, delay);
    });
  }
  
  tryOperation();
}

// Usage
function loadWithRetry(page) {
  retryOperation(function(callback) {
    api.get('/movies', callback);
  }, 3, function(error, movies) {
    if (error) {
      page.appendItem('dummy', 'video', {
        title: 'Failed to load after retries',
        description: error.message
      });
      return;
    }
    
    // Process movies
  });
}
```

These real-world examples demonstrate the practical application of Movian's plugin system, showing how to build robust, user-friendly plugins that integrate seamlessly with the Movian ecosystem. The patterns and architectures presented here provide a solid foundation for developing professional-grade plugins.