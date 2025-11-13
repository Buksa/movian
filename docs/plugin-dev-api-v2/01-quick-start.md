# Quick Start: Create Your First Plugin

**Estimated Time:** 45–60 minutes  
**Skill Level:** Beginner  
**Goal:** Build a working video listing plugin that displays sample content

This guide walks you from environment setup to a running plugin in Movian.

## Prerequisites

Before starting, ensure you have:

1. **Movian built and running** on your development machine
   - See [Getting Started](../getting-started.md) and [Build Instructions](../build-instructions.md)
   - Verify with `~/.hts/showtime/` directory existing (contains Movian's settings)

2. **A text editor** (VS Code, Sublime Text, Vim, or similar)

3. **Basic shell access** and familiarity with command-line

## Step 1: Create Plugin Directory (5 minutes)

Create a new directory for your plugin. We'll name it `hello_video`:

```bash
mkdir -p ~/my_plugins/hello_video
cd ~/my_plugins/hello_video
```

## Step 2: Create plugin.json (5 minutes)

Create a file named `plugin.json` in your plugin directory. This manifest tells Movian about your plugin.

**File:** `~/my_plugins/hello_video/plugin.json`

```json
{
  "type": "ecmascript",
  "id": "hello_video",
  "file": "hello_video.js",
  "apiversion": 2,
  "category": "video",
  "title": "Hello Video",
  "description": "A beginner's example video plugin",
  "author": "You",
  "version": "1.0.0"
}
```

### Schema Reference

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `type` | Yes | string | Must be `"ecmascript"` for JavaScript plugins |
| `id` | Yes | string | Unique plugin identifier (alphanumeric + underscore) |
| `file` | Yes | string | Relative path to the main JavaScript file |
| `apiversion` | Yes | number | Must be `2` for modern plugins |
| `category` | No | string | Plugin category: `tv`, `video`, `music`, `cloud`, `glwview`, `glwosk`, `subtitles`, `audioengine`, `other` |
| `title` | No | string | Display name in Movian UI |
| `description` | No | string | Short description of plugin purpose |
| `author` | No | string | Plugin author name |
| `version` | No | string | Semantic version string |

See [src/plugins.c lines 61–71](../../src/plugins.c#L61-L71) for category definitions and manifest field parsing.

## Step 3: Create the JavaScript Entrypoint (10 minutes)

Create the main plugin file. This code runs when Movian loads the plugin.

**File:** `~/my_plugins/hello_video/hello_video.js`

```javascript
/**
 * Hello Video - A beginner's video plugin
 * 
 * This plugin demonstrates:
 * - Loading the movian/page module
 * - Creating a route
 * - Building a page with video items
 * - Basic page lifecycle
 */

var page = require('movian/page');
var http = require('movian/http');

// Define a URL scheme for this plugin's pages
var BASE_URL = 'helloVideo:';

// Create a route handler
// The route pattern is a regex that matches URLs this plugin handles
new page.Route(BASE_URL + 'start', function(page) {
  
  // Set the page type and title
  page.type = 'directory';
  page.metadata.title = 'Hello Video';
  
  // Add some example video items
  page.appendItem(BASE_URL + 'item1', 'video', {
    title: 'Sample Video 1',
    description: 'This is the first sample video'
  });
  
  page.appendItem(BASE_URL + 'item2', 'video', {
    title: 'Sample Video 2',
    description: 'This is the second sample video'
  });
  
  page.appendItem(BASE_URL + 'item3', 'video', {
    title: 'Sample Video 3',
    description: 'This is the third sample video'
  });
  
  // Indicate there are no more items
  page.loading = false;
});

// Create handlers for individual video items
new page.Route(BASE_URL + 'item(\\d+)', function(page, itemNumber) {
  page.type = 'video';
  
  // In a real plugin, you'd fetch actual video URLs from an API
  // For now, we use a test video URL
  page.source = 'http://commondatastorage.googleapis.com/gtv-videos-library/' +
                'sample/BigBucksBunny.mp4';
  
  page.metadata.title = 'Sample Video ' + itemNumber;
  page.metadata.description = 'This is sample video number ' + itemNumber;
  page.loading = false;
});

// Register a service so the plugin appears in Movian's UI
// See src/ecmascript/es_service.c for service creation
var service = require('movian/service');

new service.Service('helloVideoService', 'Hello Video', BASE_URL + 'start', 'video');
```

### Code Walkthrough

1. **Module Imports** (lines 11–12)
   - `movian/page` – Page creation and routing (see [page.js](../../res/ecmascript/modules/movian/page.js#L384))
   - `movian/http` – HTTP requests (imported but not used in this simple example)

2. **Route Registration** (lines 16–47)
   - `new page.Route(pattern, callback)` registers a URL pattern handler
   - When Movian navigates to a URL matching the pattern, the callback executes
   - The `page` object is a [Page proxy](../../res/ecmascript/modules/movian/page.js#L150) that controls UI rendering
   - `page.appendItem()` adds items to the page (see [page.js line 263](../../res/ecmascript/modules/movian/page.js#L263))

3. **Video Item Handlers** (lines 49–60)
   - Routes can capture regex groups and pass them as arguments
   - `(\\d+)` captures digit sequences; passed to callback as `itemNumber`

4. **Service Registration** (line 135)
   - Registers the plugin as a discoverable service in Movian's UI
   - See [es_service.c](../../src/ecmascript/es_service.c#L108) for service creation details

## Step 4: Deploy to Movian (10 minutes)

### Option A: Copy to Plugin Directory (Recommended for Testing)

```bash
mkdir -p ~/.hts/showtime/plugins
cp -r ~/my_plugins/hello_video ~/.hts/showtime/plugins/
```

### Option B: Symlink (Recommended for Development)

This lets you edit files and see changes without recopying:

```bash
ln -s ~/my_plugins/hello_video ~/.hts/showtime/plugins/hello_video
```

### Enable Developer Mode (Optional but Helpful)

Set this environment variable to enable debug output:

```bash
export MOVIAN_PLUGIN_DEBUG=1
```

## Step 5: Launch Movian and Test (5 minutes)

1. **Start Movian** with the plugin:

```bash
# From the Movian repo root:
./build.linux/movian  # or ./build.osx/Movian.app/Contents/MacOS/movian on macOS
```

**Note:** On first run, Movian scans `~/.hts/showtime/plugins/` and loads plugins. If you copied the plugin after starting Movian, you may need to restart.

2. **Find your plugin in the UI:**
   - Navigate to the **Plugins** section in Movian's menu
   - You should see "Hello Video" listed
   - Select it to open the main page

3. **Verify functionality:**
   - The page should display three video items
   - Selecting an item should load the video player

## Step 6: Debug and Troubleshoot (5–15 minutes)

### Check Plugin Loading

Look for your plugin in Movian's settings:

```bash
# On Linux:
~/.hts/showtime/plugins/hello_video/

# Check for any error files:
tail -f ~/.hts/showtime/plugins/hello_video/*.log  # if any exist
```

### Enable Verbose Logging

Restart Movian with debug flags:

```bash
MOVIAN_PLUGIN_DEBUG=1 LOGLEVEL=trace ./build.linux/movian 2>&1 | tee movian.log
```

Search the log for your plugin ID:

```bash
grep -i "hello_video" movian.log
```

### Common Issues

| Problem | Solution |
|---------|----------|
| Plugin doesn't appear | Check `plugin.json` syntax (use JSON validator) |
| Routes not firing | Ensure regex pattern matches the URL scheme; test with `new page.Route(...).route.test(url)` in console |
| Items don't load | Check that `page.loading = false` is set at the end of route handler |
| Page shows but no items | Verify `page.appendItem()` calls are executed; add `console.log()` statements |

### Log Rotation

Movian doesn't create plugin logs by default. To capture stderr, redirect it:

```bash
./build.linux/movian 2>&1 | tee movian_$(date +%Y%m%d_%H%M%S).log
```

## Next Steps

Congratulations! You've created and deployed your first Movian plugin. 

**To continue learning:**

1. **[Plugin Lifecycle Guide](02-plugin-lifecycle.md)** — Understand initialization, cleanup, and event handling
2. **Extend your plugin:**
   - Add HTTP requests to fetch real video metadata
   - Implement pagination for large video lists
   - Add search functionality
3. **Review the Plugin Examples:**
   - `plugin_examples/async_page_load` — Asynchronous page loading with pagination
   - `plugin_examples/music` — Music plugin structure
   - `plugin_examples/settings` — Settings persistence

## Reference

- **[Page API](../../res/ecmascript/modules/movian/page.js)** – Full page module documentation
- **[Service Registration](../../src/ecmascript/es_service.c)** – Service creation
- **[Plugin Lifecycle](02-plugin-lifecycle.md)** – Detailed initialization and cleanup
- **[Debugging & Profiling](07-debugging-profiling.md)** — Advanced debugging techniques

---

**Time Estimate Summary:**
- Step 1: 5 min (directory setup)
- Step 2: 5 min (manifest)
- Step 3: 10 min (code)
- Step 4: 10 min (deployment)
- Step 5: 5 min (testing)
- Step 6: 5–15 min (debugging)
- **Total: 40–50 minutes** (+ time for Movian to build/run)

If you encounter issues, check [Troubleshooting](07-debugging-profiling.md#troubleshooting) or review source code citations provided in this guide.

---

**Next:** [Plugin Lifecycle Fundamentals](02-plugin-lifecycle.md)
