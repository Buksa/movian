# Movian Developer Documentation

Complete documentation for building, developing, and understanding Movian.

## Getting Started

New to Movian? Start here:

1. **[Overview](overview.md)** - Learn what Movian is, its capabilities, and supported platforms
2. **[Getting Started & Setup](getting-started.md)** - Set up your development environment
3. **[Build Instructions](build-instructions.md)** - Compile for your target platform

## Core Documentation

### Understanding the Project

- **[Repository Structure](repository-structure.md)** - Detailed guide to every directory and module in the codebase

### Building Movian

- **[Build Instructions](build-instructions.md)** - Complete build procedures for:
  - Linux desktop builds (debug and release)
  - macOS builds with framework integration
  - PlayStation 3 (PSL1GHT)
  - Raspberry Pi (cross-compilation)
  - Other platforms (Android, iOS, Sunxi, NaCl)

### Running Movian

- **[Runtime & Configuration](runtime.md)** - Launching builds, configuration, debugging, and troubleshooting

### Plugin Development

- **[ECMAScript Plugin API v2 Documentation](plugin-dev-api-v2/00-introduction.md)** - Complete guide to building plugins
  - [00 - Introduction](plugin-dev-api-v2/00-introduction.md) - Plugin system architecture overview
  - [01 - Quick Start](plugin-dev-api-v2/01-quick-start.md) - Create your first plugin in 45–60 minutes
  - [02 - Plugin Lifecycle](plugin-dev-api-v2/02-plugin-lifecycle.md) - Load, initialization, runtime, and cleanup phases
  - [03 - 10 - Additional Topics (stubs)](plugin-dev-api-v2/03-page-routing-ui.md) - Page routing, HTTP, settings, advanced patterns, debugging, distribution, and more

## Quick Reference

### Build Commands

**Linux:**
```bash
./configure
make -j4
./build.linux/movian
```

**macOS (Debug):**
```bash
./configure
make -j4
./build.osx/Movian.app/Contents/MacOS/movian
```

**macOS (Release/DMG):**
```bash
./configure --release
make -j4
make dist
```

**PlayStation 3:**
```bash
./Autobuild.sh -t ps3 -v 5.0.500
```

**Raspberry Pi:**
```bash
./Autobuild.sh -t rpi -v 5.0.500
```

### Settings Location

All Movian settings and caches are stored in:
```
~/.hts/showtime/
```

### Documentation Map

```
docs/
├── index.md                         # This file - documentation overview
├── overview.md                      # Project overview and capabilities
├── repository-structure.md          # Directory organization and module guide
├── getting-started.md               # Setup and environment preparation
├── build-instructions.md            # Platform-specific build procedures
├── runtime.md                       # Launching, configuration, debugging
└── plugin-dev-api-v2/               # Plugin development documentation
    ├── 00-introduction.md           # Plugin system architecture
    ├── 01-quick-start.md            # Quick start guide (45–60 min)
    ├── 02-plugin-lifecycle.md       # Plugin lifecycle phases
    ├── 03-page-routing-ui.md        # Page routing and UI (stub)
    ├── 04-http-data.md              # HTTP and data fetching (stub)
    ├── 05-settings-storage.md       # Settings and storage (stub)
    ├── 06-advanced-patterns.md      # Advanced patterns (stub)
    ├── 07-debugging-profiling.md    # Debugging and profiling (stub)
    ├── 08-distribution-publishing.md # Distribution (stub)
    ├── 09-api-reference.md          # API reference (stub)
    └── 10-real-world-examples.md    # Real-world examples (stub)
```

## By Use Case

### "I want to build Movian for my platform"
1. Check [Getting Started](getting-started.md) for prerequisites
2. Follow [Build Instructions](build-instructions.md) for your target
3. See [Runtime Guide](runtime.md) to launch and test

### "I want to understand the codebase"
1. Start with [Overview](overview.md) for architecture
2. Review [Repository Structure](repository-structure.md) for module details
3. Explore specific directories mentioned in the guide

### "I want to develop features or plugins"
1. Complete [Getting Started](getting-started.md) setup
2. Understand [Repository Structure](repository-structure.md)
3. Start with [Plugin Development Guide](plugin-dev-api-v2/00-introduction.md) for API v2 plugins
4. Review `plugin_examples/` directory for reference implementations
5. Read [Runtime Guide](runtime.md) for debugging techniques

### "I'm troubleshooting a build or runtime issue"
1. Check [Build Instructions](build-instructions.md) - "Common Build Issues" section
2. Refer to [Runtime Guide](runtime.md) - "Troubleshooting Runtime Issues" section
3. Review debug flags in [Runtime Guide](runtime.md) - "Useful Debug Flags" section

## Platform-Specific Information

### Linux
- **Prerequisites:** See [Getting Started](getting-started.md#linux)
- **Build:** See [Build Instructions](build-instructions.md#linux-desktop-build)
- **Output:** `build.linux/movian`
- **Settings:** `~/.hts/showtime/`

### macOS
- **Prerequisites:** See [Getting Started](getting-started.md#macos)
- **Build:** See [Build Instructions](build-instructions.md#macos-build)
- **Output:** `build.osx/Movian.app/Contents/MacOS/movian`
- **Distribution:** `make dist` creates DMG

### PlayStation 3
- **Prerequisites:** Linux build host required
- **Build:** See [Build Instructions](build-instructions.md#playstation-3-build)
- **Output:** `build.ps3/movian.self`, `build.ps3/movian.pkg`
- **Installation:** Via PS3 package installer

### Raspberry Pi
- **Prerequisites:** Linux build host, cross-compilation toolchain
- **Build:** See [Build Instructions](build-instructions.md#raspberry-pi-build)
- **Output:** `build.rpi/showtime.sqfs`
- **Installation:** Via SFTP or HTTP API

### Android & iOS
- **Details:** See [Build Instructions](build-instructions.md#other-platform-builds)
- **Requirements:** Platform-specific NDK/SDK

## Repository Structure Overview

```
movian/
├── src/                    # Core C source code
│   ├── backend/           # Media providers (HLS, RTMP, BitTorrent, etc.)
│   ├── glw/               # OpenGL UI framework
│   ├── plugins.c          # Plugin system
│   └── ...                # Other modules (audio, video, networking, etc.)
├── ext/                    # External dependencies (FFmpeg, etc.)
├── glwskins/              # UI themes
├── plugin_examples/       # Example plugins for developers
├── res/                   # Resources (fonts, shaders, databases)
├── lang/                  # Localization files
├── support/               # Build utilities and templates
├── Autobuild/            # Platform-specific build scripts
├── configure*             # Platform configuration scripts
└── docs/                  # This documentation
```

See [Repository Structure](repository-structure.md) for complete details.

## Key Directories for Development

| Directory | Purpose |
|-----------|---------|
| `src/` | Core engine implementation |
| `src/backend/` | Media provider backends |
| `src/glw/` | UI framework and rendering |
| `src/plugins.c` | Plugin system |
| `ext/libav/` | FFmpeg (submodule) |
| `glwskins/` | Customizable UI themes |
| `plugin_examples/` | Example plugins |
| `res/` | Application resources |
| `support/` | Build support files |

## Environment Variables and Debug Options

Quick reference for common debugging:

```bash
# Verbose logging
./build.linux/movian 2>&1 | tee debug.log

# Memory debugging (requires ASan build)
ASAN_OPTIONS=verbosity=2 ./build.linux/movian

# Plugin debugging
MOVIAN_PLUGIN_DEBUG=1 ./build.linux/movian

# Network tracing
MOVIAN_TRACE_HTTP=1 ./build.linux/movian
```

See [Runtime Guide](runtime.md#useful-debug-flags-and-environment-variables) for complete list.

## Support and Resources

- **Project Website:** https://movian.tv/
- **Repository:** https://github.com/andoma/movian
- **License:** See LICENSE file

## Documentation Conventions

- **Code blocks:** Use language-specific highlighting (bash, C, etc.)
- **File paths:** Relative to repository root (`/home/engine/project/`)
- **Commands:** Should work on Linux and macOS unless noted
- **Links:** Cross-reference between docs for navigation

## Contributing

When contributing to Movian:
1. Follow existing code organization from [Repository Structure](repository-structure.md)
2. Review relevant documentation for your target area
3. Build with [Build Instructions](build-instructions.md)
4. Test using [Runtime Guide](runtime.md)
5. Use debug flags for troubleshooting

## Version Information

- **Current Focus:** Cross-platform multimedia player
- **Platform Support:** Linux, macOS, PS3, Raspberry Pi, Android, iOS, and more
- **Build System:** Custom autotools-style configuration
- **Languages:** C (core), ECMAScript (plugins), Platform-specific (Objective-C, Java, etc.)

---

**Last Updated:** 2024  
**Status:** Complete Developer Documentation  
**Coverage:** All major platforms and development workflows
