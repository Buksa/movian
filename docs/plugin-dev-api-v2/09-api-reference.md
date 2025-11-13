# API Reference

*This module is a stub and will be populated with detailed content.*

## Overview

Complete API documentation for all Movian plugin modules, native functions, and object types.

### Module Reference

#### Core Modules

- **movian/page** – Page rendering and routing
  - Source: `res/ecmascript/modules/movian/page.js`
  - Classes: `Route`, `Page`, `Item`, `Searcher`

- **movian/service** – Service registration
  - Source: `res/ecmascript/modules/movian/service.js`
  - Classes: `Service`

- **movian/settings** – Configuration management
  - Source: `res/ecmascript/modules/movian/settings.js`
  - Classes: `Settings`, `kvstoreSettings`

- **movian/http** – HTTP client
  - Source: `res/ecmascript/modules/movian/http.js`
  - Functions: `httpGet()`, `httpPost()`, etc.

- **movian/sqlite** – SQLite database
  - Source: `res/ecmascript/modules/movian/sqlite.js`
  - Classes: `Database`

- **movian/prop** – Property tree manipulation
  - Source: `res/ecmascript/modules/movian/prop.js`
  - Functions: `createRoot()`, `subscribe()`, etc.

#### Utility Modules

- **movian/html** – HTML parsing (`res/ecmascript/modules/movian/html.js`)
- **movian/xml** – XML parsing (`res/ecmascript/modules/movian/xml.js`)
- **movian/itemhook** – Item hooks (`res/ecmascript/modules/movian/itemhook.js`)
- **movian/videoscrobbler** – Scrobbling integration
- **movian/store** – Key-value store
- **movian/subtitles** – Subtitle handling

### Global Objects

- **Plugin** – Plugin metadata
  - `Plugin.id` – Plugin identifier
  - `Plugin.url` – Main file path
  - `Plugin.manifest` – Manifest JSON
  - `Plugin.apiversion` – API version
  - `Plugin.path` – Plugin directory

### Topics to Cover

- Complete function signatures
- Parameter descriptions and types
- Return values and error codes
- Usage examples for each API
- Compatibility notes
- Deprecated APIs

---

**Navigation:** [← Distribution](08-distribution-publishing.md) | [Examples →](10-real-world-examples.md) | [Index](../index.md)
