# ECMAScript Plugin Development - API v2 Introduction

## Overview

This documentation series covers the ECMAScript Plugin Development API v2, the modern framework for extending Movian with custom functionality. Plugins allow you to add new media sources, services, and UI extensions to Movian's core capabilities.

## What is API v2?

API v2 is the current, recommended plugin architecture for Movian. It provides:

- **Modern ECMAScript environment** – Plugins run in the Duktape ECMAScript engine integrated into Movian
- **Simplified module system** – Require-style imports for native and JavaScript modules
- **Rich event handling** – Subscribe to state changes and react to user interactions
- **Native bridges** – Direct access to file systems, HTTP, databases, and UI components via native modules
- **Lifecycle management** – Clear initialization and cleanup patterns

## Plugin System Architecture

### High-Level Overview

```
┌─────────────────────────────────────────┐
│          Movian Core                    │
├─────────────────────────────────────────┤
│  Plugin Manager (plugins.c)             │
│  - Loads plugin.json manifests          │
│  - Manages plugin lifecycle             │
│  - Handles version/compatibility checks │
└────────────┬────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────┐
│  ECMAScript Runtime                     │
│  (src/ecmascript/ecmascript.c)          │
├─────────────────────────────────────────┤
│  - Duktape VM contexts per plugin       │
│  - Module registration & loading        │
│  - Resource lifecycle management        │
└────────────┬────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────┐
│  Plugin JavaScript Entrypoint           │
│  - User-provided .js file               │
│  - Requires 'movian/page', etc.         │
│  - Registers routes & services          │
└────────────┬────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────┐
│  Native Module Bridges                  │
│  - movian/page, movian/http             │
│  - movian/settings, etc.                │
│  (res/ecmascript/modules/movian/*.js)   │
└─────────────────────────────────────────┘
```

### Key Components

**Plugin Manager** — [src/plugins.c](../../src/plugins.c#L48)

The plugin system defines [plugin categories](../../src/plugins.c#L48-L59) (lines 48–59):

- `tv` — Online TV streaming sources
- `video` — Video streaming services
- `music` — Music streaming providers
- `cloud` — Cloud storage/services
- `glwview` — UI view customizations
- `glwosk` — On-screen keyboard extensions
- `audioengine` — Custom audio decoders
- `subtitles` — Subtitle providers
- `other` — Miscellaneous plugins

Each plugin is identified by a fully-qualified ID combining the plugin `id` and `origin` (e.g., `myplugin@repo.example.com`).

**ECMAScript Runtime** — [src/ecmascript/ecmascript.c](../../src/ecmascript/ecmascript.c#L574)

The runtime creates isolated execution contexts for each plugin. [es_context_create()](../../src/ecmascript/ecmascript.c#L575) (line 575) initializes:

- A Duktape VM heap per plugin
- Module lookup and registration infrastructure
- A property system bridge for UI state management
- Resource tracking for cleanup

Plugins execute in their own thread context, isolated from one another.

**Module System** — [src/ecmascript/ecmascript.c](../../src/ecmascript/ecmascript.c#L73)

Built-in modules are registered via `ecmascript_register_module()` (line 73). Native JavaScript facades in `res/ecmascript/modules/movian/` provide convenient APIs:

- `movian/page` — Route definition, page rendering, pagination
- `movian/http` — HTTP client (fetch, JSON APIs)
- `movian/settings` — Plugin configuration storage
- `movian/service` — Service registration (plugins appear in UI)
- `movian/prop` — Property tree manipulation
- And many more...

**Plugin Lifecycle** — [src/ecmascript/ecmascript.c](../../src/ecmascript/ecmascript.c#L874)

[ecmascript_plugin_load()](../../src/ecmascript/ecmascript.c#L874) (line 874) loads a plugin by:

1. Creating an isolated execution context
2. Loading the user's JavaScript file
3. Executing it synchronously (routes/handlers are registered during this phase)
4. Holding the context open for the lifetime of the plugin

[ecmascript_plugin_unload()](../../src/ecmascript/ecmascript.c#L1001) (line 1001) unloads by:

1. Locating the plugin's context
2. Destroying all registered resources (routes, services, etc.)
3. Releasing the context

## Plugin Types and Categories

Plugins declare their type and category in `plugin.json`:

```json
{
  "type": "ecmascript",
  "id": "example_video",
  "file": "example.js",
  "apiversion": 2,
  "category": "video",
  "title": "Example Video Provider",
  "description": "A sample video streaming plugin"
}
```

The `category` field maps to [PLUGIN_CAT_* enum values](../../src/plugins.c#L48-L59) (src/plugins.c, lines 48–59). The category determines where the plugin appears in Movian's UI and what UI affordances are available.

## manifest.json vs plugin.json

Plugin metadata is stored in two places:

1. **plugin.json** — User-provided manifest in the plugin source directory; defines the plugin's metadata (title, description, category, etc.) and entry point
2. **Manifest field** — Stored in the [plugin context](../../src/ecmascript/ecmascript.c#L899) (ecmascript.c, line 899); accessible to the plugin as `Plugin.manifest` containing the full JSON as a string

## Storage and Deployment

Plugins are installed in the following locations:

- **User plugins** — `~/.hts/showtime/plugins/`
- **System plugins** — Bundled with Movian executable
- **Developer mode** — Symlinked or copied from development directory

Plugin settings/data are persisted in:

```
~/.hts/showtime/plugins/{plugin-id}/
```

See [src/ecmascript/ecmascript.c](../../src/ecmascript/ecmascript.c#L881-L882) (lines 881–882) for storage path construction.

## API v1 vs API v2

Movian supports both legacy API v1 and modern API v2 plugins.

- **API v1** — Deprecated legacy system; automatically wrapped via compatibility layer
- **API v2** — Current recommended approach; direct module imports, simpler initialization

New plugins should always use API v2 by setting `"apiversion": 2` in `plugin.json`.

## What You'll Learn

This documentation series guides you through:

1. **[01 - Quick Start](01-quick-start.md)** — Set up your development environment and create a working "Hello World" video listing plugin in under one hour
2. **[02 - Plugin Lifecycle](02-plugin-lifecycle.md)** — Understand initialization, route handling, service registration, and cleanup phases
3. **[03 - Page Routing & UI](03-page-routing-ui.md)** (stub)
4. **[04 - HTTP & Data](04-http-data.md)** (stub)
5. **[05 - Settings & Storage](05-settings-storage.md)** (stub)
6. **[06 - Advanced Patterns](06-advanced-patterns.md)** (stub)
7. **[07 - Debugging & Profiling](07-debugging-profiling.md)** (stub)
8. **[08 - Distribution & Publishing](08-distribution-publishing.md)** (stub)
9. **[09 - API Reference](09-api-reference.md)** (stub)
10. **[10 - Real-World Examples](10-real-world-examples.md)** (stub)

## Prerequisites

- Basic ECMAScript (JavaScript) knowledge
- Movian built and running on your development machine
- Text editor or IDE (VS Code recommended)
- Familiarity with command-line tools

## Getting Help

- Review `plugin_examples/` in the Movian repository for reference implementations
- Check Movian's runtime logs (enable with `MOVIAN_PLUGIN_DEBUG=1`)
- Consult the [API Reference](09-api-reference.md) for module signatures

---

**Next:** [Quick Start Guide](01-quick-start.md)
