# Development Workflow

This chapter covers the complete development workflow for Movian plugins, from initial setup through testing, debugging, and deployment.

## Table of Contents

1. [Development Environment Setup](#development-environment-setup)
2. [Loading Development Plugins](#loading-development-plugins)
3. [Debugging Techniques](#debugging-techniques)
4. [Hot Reload and Iterative Development](#hot-reload-and-iterative-development)
5. [Testing Strategies](#testing-strategies)
6. [Using Plugin Examples](#using-plugin-examples)
7. [Common Development Issues](#common-development-issues)

## Development Environment Setup

### Prerequisites

Before starting plugin development, ensure you have:

1. **Movian Source Code**: Clone the repository with submodules
   ```bash
   git clone --recursive https://github.com/andoma/movian.git
   cd movian
   ```

2. **Build Environment**: Follow the [Build Instructions](build-instructions.md) for your platform

3. **Development Build**: Compile Movian with debug symbols
   ```bash
   ./configure
   make -j4
   ```

### Development Tools

The Movian development workflow includes several built-in tools:

- **Plugin Loader**: Loads plugins from local directories
- **Console Output**: JavaScript logging and debugging
- **Property Inspector**: Examine plugin state and variables
- **Trace System**: Low-level debugging for core functionality

## Loading Development Plugins

### Command Line Method

The primary way to load development plugins is using the `-p` flag:

```bash
# Load a single plugin directory
./build.linux/movian -p /path/to/plugin

# Load multiple plugins
./build.linux/movian -p plugin1 -p plugin2
```

This is implemented in `src/main.c` where the `-p` argument is processed:

```c
// From src/main.c:652-655
} else if(!strcmp(argv[0], "-p") && argc > 1) {
  strvec_addp(&gconf.devplugins, argv[1]);
  argc -= 2; argv += 2;
  continue;
}
```

### Plugin Repository Method

For testing repository integration:

```bash
./build.linux/movian --plugin-repo http://localhost:8000/repo.json
```

### Development Plugin Detection

Development plugins are handled differently from installed plugins:

1. **Storage**: They're loaded directly from source directories
2. **Origin**: Marked with "dev" origin in the plugin system
3. **Auto-reload**: Support hot reloading during development
4. **No Version Checks**: Skip `showtimeVersion` validation

The plugin system tracks development plugins in a separate array:

```c
// From src/plugins.c:78
static char **devplugins;
```

## Debugging Techniques

### Console Logging

Use Movian's built-in console for JavaScript debugging:

```javascript
// Basic logging
console.log("Debug message");
console.error("Error occurred");
console.warn("Warning message");

// Object inspection
var myObject = { title: "Test", url: "http://example.com" };
console.log("Object:", myObject);

// Rich object inspection with JSON
console.log("JSON:", JSON.stringify(myObject, null, 2));
```

Console output is handled by `src/ecmascript/es_console.c`:

```c
// From src/ecmascript/es_console.c:39-50
static const char *
log_concat(duk_context *ctx)
{
  int argc = duk_get_top(ctx);
  if(argc == 0)
    return "";

  duk_push_string(ctx, " ");

  for(int i = 0; i < argc; i++) {
    duk_dup(ctx, i);
    duk_safe_call(ctx, make_printable, 1, 1);
    // ... concatenation logic
  }
}
```

### Property Inspection

Movian's property system allows runtime inspection:

```javascript
// Inspect page properties
console.log("Page type:", page.type);
console.log("Page metadata:", page.metadata);

// Inspect item properties
item.dump(); // Available on Item objects

// Monitor property changes
var prop = require('movian/prop');
var subscription = prop.subscribe(page.root, function(event, value) {
  console.log("Property changed:", event, value);
});
```

### Native Debugging

For deeper debugging, Movian provides trace flags:

```bash
# General plugin debugging
./build.linux/movian --trace plugins

# HTTP request debugging
./build.linux/movian --trace http

# ECMAScript debugging
./build.linux/movian --trace ecmascript

# Property system debugging
./build.linux/movian --trace prop
```

### Breakpoints and Step Debugging

While Movian doesn't have a built-in JavaScript debugger, you can simulate breakpoints:

```javascript
function debugBreak() {
  if (typeof console !== 'undefined') {
    console.trace("Debug breakpoint reached");
    // Add conditional logic here
  }
}

// Usage in your code
if (someCondition) {
  debugBreak();
}
```

## Hot Reload and Iterative Development

### Automatic Plugin Reloading

Development plugins support hot reloading through the `plugins_reload_dev_plugin()` function:

```c
// From src/plugins.c:1454-1467
void
plugins_reload_dev_plugin(void)
{
  char errbuf[200];
  if(devplugins == NULL)
    return;

  hts_mutex_lock(&plugin_mutex);

  const char *path;
  for(int i = 0; (path = devplugins[i]) != NULL; i++) {
    if(plugin_load(path, "dev", errbuf, sizeof(errbuf),
                   PLUGIN_LOAD_FORCE | PLUGIN_LOAD_DEBUG | PLUGIN_LOAD_BY_USER))
      TRACE(TRACE_ERROR, "plugins",
            "Unable to reload development plugin: %s\n%s", path, errbuf);
  }
  hts_mutex_unlock(&plugin_mutex);
}
```

### Manual Reload

You can trigger reloads programmatically:

```javascript
// Add a reload button to your plugin during development
if (typeof Movian !== 'undefined' && Movian.devMode) {
  page.appendItem('dummy', 'separator', {
    title: 'Development Tools'
  });
  
  page.appendItem('dummy', 'action', {
    title: 'Reload Plugin',
    callback: function() {
      // Trigger plugin reload (implementation depends on your setup)
      console.log("Plugin reload requested");
    }
  });
}
```

### File Watching

For external file watching (not built into Movian):

```bash
# Using inotify-tools on Linux
while inotifywait -r -e modify /path/to/plugin/; do
  echo "Plugin files changed, reload in Movian"
done
```

## Testing Strategies

### Unit Testing Approach

While Movian doesn't have a formal testing framework, you can create test pages:

```javascript
// test.js - Create a test route for your plugin (API v2)
var page = require('movian/page');

// Test route for various plugin functions
new page.Route('myplugin:test', function(page) {
    page.type = 'directory';
    page.metadata.title = 'Plugin Tests';
    
    // Test basic item creation
    page.appendItem('http://example.com/video.mp4', 'video', {
      title: 'Test Video Item'
    });
    
    // Test HTTP requests
    testHTTPRequests(page);
    
    // Test property manipulation
    testProperties(page);
  });
  
  function testHTTPRequests(page) {
    var http = require('movian/http');
    
    page.appendItem('dummy', 'separator', {
      title: 'HTTP Tests'
    });
    
    // Test GET request
    http.request('https://httpbin.org/get', function(response) {
      if (response.status === 200) {
        page.appendItem('dummy', 'video', {
          title: '✓ GET request successful',
          description: 'Status: ' + response.status
        });
      } else {
        page.appendItem('dummy', 'video', {
          title: '✗ GET request failed',
          description: 'Status: ' + response.status
        });
      }
    });
  }
  
  function testProperties(page) {
    var prop = require('movian/prop');
    
    page.appendItem('dummy', 'separator', {
      title: 'Property Tests'
    });
    
    // Test property creation and modification
    var testProp = prop.create();
    testProp.value = 'initial';
    
    page.appendItem('dummy', 'video', {
      title: 'Property test: ' + testProp.value
    });
  }
});
```

### Integration Testing

Test your plugin with different Movian configurations:

```bash
# Test with different UI skins
./build.linux/movian -p /path/to/plugin --skin flat
./build.linux/movian -p /path/to/plugin --skin old

# Test with different cache configurations
./build.linux/movian -p /path/to/plugin --cache /tmp/cache

# Test with debug logging
./build.linux/movian -p /path/to/plugin --trace plugins,http,prop
```

### Performance Testing

Monitor plugin performance using built-in timing:

```javascript
// Performance testing utilities
var perf = {
  timers: {},
  
  start: function(name) {
    this.timers[name] = Date.now();
  },
  
  end: function(name) {
    if (this.timers[name]) {
      var duration = Date.now() - this.timers[name];
      console.log('Timer [' + name + ']: ' + duration + 'ms');
      delete this.timers[name];
      return duration;
    }
  }
};

// Usage example
perf.start('http-request');
http.request(url, function(response) {
  perf.end('http-request');
  // Process response
});
```

## Using Plugin Examples

Movian provides several example plugins in `plugin_examples/`:

### Music Plugin Example

The `plugin_examples/music/` directory demonstrates basic plugin structure:

```json
// plugin_examples/music/plugin.json
{
  "type": "ecmascript",
  "id": "example_music",
  "file": "example_music.js"
}
```

```javascript
// plugin_examples/music/example_music.js
(function(plugin) {
  var U = "example:music:";

  // Register a service (will appear on home page)
  plugin.createService("Music example", U, "other", true);

  // Add a responder to the registered URI
  plugin.addURI(U, function(page) {
    page.type = "directory";
    page.metadata.title = "Music examples";

    var B = "http://www.lonelycoder.com/music/";

    page.appendItem(B + "Hybris_Intro-remake.mp3", "audio", {
      title: "Remix of Hybris (The Amiga Game)",
      artist: "Andreas Öman"
    });
    
    // ... more items
  });
})(this);
```

### Loading Examples

Load example plugins for development:

```bash
# Load the music example
./build.linux/movian -p plugin_examples/music

# Load multiple examples
./build.linux/movian \
  -p plugin_examples/music \
  -p plugin_examples/settings \
  -p plugin_examples/async_page_load
```

### Example Plugin Structure

Each example demonstrates different aspects:

1. **music/**: Basic service creation and item handling
2. **settings/**: Settings management and persistence
3. **async_page_load/**: Asynchronous pagination patterns
4. **itemhook/**: Context menu integration
5. **subscriptions/**: Property subscription patterns
6. **videoscrobbbling/**: Integration with external services

## Common Development Issues

### Plugin Not Loading

**Symptoms**: Plugin doesn't appear in Movian

**Debugging Steps**:

1. Check plugin.json syntax:
   ```bash
   # Validate JSON
   python3 -m json.tool plugin.json
   ```

2. Verify file paths and permissions:
   ```bash
   ls -la plugin.json
   cat plugin.json
   ```

3. Check Movian logs:
   ```bash
   ./build.linux/movian -p /path/to/plugin 2>&1 | grep -i plugin
   ```

4. Verify plugin structure matches expected format

### HTTP Request Failures

**Common Issues**:

1. **CORS Problems**: External APIs may block requests
   ```javascript
   // Add user agent or other headers if needed
   http.request(url, {
     headers: {
       'User-Agent': 'Movian-Plugin/1.0'
     }
   }, callback);
   ```

2. **SSL Certificate Issues**:
   ```javascript
   // For development only - disable SSL verification
   process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0';
   ```

3. **Timeout Issues**:
   ```javascript
   // Increase timeout for slow APIs
   http.request(url, {
     timeout: 30000 // 30 seconds
   }, callback);
   ```

### Memory Leaks

**Prevention Strategies**:

1. **Clean up subscriptions**:
   ```javascript
   var subscriptions = [];
   
   function cleanup() {
     subscriptions.forEach(function(sub) {
       sub.destroy();
     });
     subscriptions = [];
   }
   
   // Call cleanup when page is destroyed
   page.onDestroy = cleanup;
   ```

2. **Avoid circular references**:
   ```javascript
   // Bad: Circular reference
   page.item = item;
   item.page = page;
   
   // Good: Use weak references or cleanup
   page.item = item;
   // item references page through callback only
   ```

### Performance Issues

**Optimization Techniques**:

1. **Debounce expensive operations**:
   ```javascript
   function debounce(func, wait) {
     var timeout;
     return function() {
       var context = this, args = arguments;
       clearTimeout(timeout);
       timeout = setTimeout(function() {
         func.apply(context, args);
       }, wait);
     };
   }
   
   var debouncedSearch = debounce(performSearch, 300);
   ```

2. **Cache HTTP responses**:
   ```javascript
   var cache = {};
   
   function cachedRequest(url, callback) {
     if (cache[url]) {
       callback(cache[url]);
       return;
     }
     
     http.request(url, function(response) {
       cache[url] = response;
       callback(response);
     });
   }
   ```

## Development Best Practices

### Code Organization

```
my-plugin/
├── plugin.json          # Plugin manifest
├── main.js              # Main plugin entry point
├── lib/                 # Shared utilities
│   ├── api.js          # API wrapper functions
│   └── utils.js        # Helper functions
├── pages/              # Page handlers
│   ├── home.js         # Home page
│   └── search.js       # Search functionality
└── assets/             # Static assets
    └── icon.png        # Plugin icon
```

### Error Handling

```javascript
// Robust error handling patterns
function safeRequest(url, callback) {
  try {
    http.request(url, function(response) {
      if (response.status >= 200 && response.status < 300) {
        callback(null, response);
      } else {
        callback(new Error('HTTP ' + response.status), null);
      }
    }).on('error', function(err) {
      callback(err, null);
    });
  } catch (err) {
    callback(err, null);
  }
}

// Usage
safeRequest(url, function(err, response) {
  if (err) {
    console.error('Request failed:', err);
    page.appendItem('dummy', 'video', {
      title: 'Error: ' + err.message
    });
    return;
  }
  
  // Process successful response
});
```

### Logging Strategy

```javascript
// Structured logging for better debugging
var logger = {
  debug: function(msg, data) {
    if (typeof console !== 'undefined') {
      console.log('[DEBUG]', msg, data || '');
    }
  },
  
  info: function(msg, data) {
    if (typeof console !== 'undefined') {
      console.log('[INFO]', msg, data || '');
    }
  },
  
  error: function(msg, error) {
    if (typeof console !== 'undefined') {
      console.error('[ERROR]', msg, error);
      if (error && error.stack) {
        console.error(error.stack);
      }
    }
  }
};

// Usage
logger.info('Loading plugin data');
logger.error('API request failed', error);
```

This development workflow provides a comprehensive foundation for building, testing, and debugging Movian plugins efficiently. The combination of built-in tools, example plugins, and best practices enables rapid iteration and robust plugin development.