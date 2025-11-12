# Repository Structure Guide

This document explains the layout of the Movian repository and the role of each major directory.

## Top-Level Organization

### Build & Configuration
- **`configure`** - Auto-detection wrapper script for platform selection (Linux/macOS)
- **`configure.linux`** - Linux platform configuration script with X11 and OpenGL setup
- **`configure.osx`** - macOS platform configuration with Cocoa and CoreAudio
- **`configure.android`** - Android NDK build configuration
- **`configure.ps3`** - PlayStation 3 PSL1GHT toolchain configuration
- **`configure.rpi`** - Raspberry Pi ARM cross-compilation setup
- **`configure.nacl`** - Google Native Client configuration
- **`configure.sunxi`** - Sunxi ARM SoC configuration
- **`Autobuild.sh`** - Entry point for automated builds (Doozer CI system)
- **`Makefile`** - Root build orchestration
- **`Autobuild/`** - Per-platform automated build scripts

### Source Code
- **`src/`** - Core Movian engine and library code (C)
  - `api/` - HTTP REST API implementation
  - `arch/` - Platform-specific architecture code
  - `audio2/` - Audio pipeline and processing
  - `backend/` - Media backend providers (BitTorrent, HLS, RTMP, HTSP, Icecast, DVD)
  - `db/` - Database operations and persistence
  - `ecmascript/` - ECMAScript engine binding
  - `fileaccess/` - File system and stream access abstractions
  - `htsmsg/` - HTS messaging protocol implementation
  - `image/` - Image processing and decoding
  - `ipc/` - Inter-process communication
  - `media/` - Media playback and format handling
  - `metadata/` - Metadata fetching and management
  - `networking/` - Network operations and protocols
  - `np/` - Native plugin interface
  - `prop/` - Property system (settings and state)
  - `sd/` - Service discovery
  - `subtitles/` - Subtitle handling and rendering
  - `text/` - Text rendering and processing
  - `ui/` - UI framework components
  - `video/` - Video pipeline and rendering
  - `glw/` - GLW (Glorious Looking Widgets) UI framework
  - `libav.c/h` - FFmpeg/libav wrapper
  - `main.c/h` - Application entry point and initialization
  - `navigator.c/h` - Navigation and menu system
  - `playqueue.c/h` - Playlist and queue management
  - `plugins.c/h` - Plugin system core
  - `settings.c/h` - Settings management

### External Dependencies
- **`ext/`** - Bundled external libraries and submodules
  - `libav/` - FFmpeg/libav (submodule, video/audio codec support)
  - `freetype.mk` - FreeType font rendering
  - `duktape/` - Duktape ECMAScript engine
  - `dvd/` - DVD playback library
  - `gumbo-parser/` - HTML5 parser (submodule)
  - `libntfs_ext/` - NTFS file system support (submodule)
  - `libyuv/` - YUV image conversion (submodule)
  - `minilibs/` - Minimal utility libraries
  - `nanosvg/` - SVG rendering
  - `polarssl-1.3/` - TLS/SSL support
  - `rtmpdump/` - RTMP protocol support (submodule)
  - `sqlite/` - SQLite database engine
  - `tlsf/` - Efficient memory allocator
  - `trex/` - Regular expression library
  - `vmir/` - Video mirror/manipulation (submodule)

### User Interface & Themes
- **`glwskins/`** - OpenGL UI skin themes
  - `flat/` - Modern flat design theme with responsive layouts
  - `old/` - Legacy theme for compatibility

### Plugin System
- **`plugin_examples/`** - Example plugins for developers
  - `async_page_load/` - Asynchronous content loading patterns
  - `itemhook/` - Item hook handlers
  - `music/` - Music provider example
  - `settings/` - Settings plugin example
  - `subscriptions/` - Subscription management example
  - `videoscrobbling/` - Video tracking example
  - `webpopupplugin/` - Web popup plugin example
- **`nativeplugin/`** - Native (C) plugin interface
  - `include/` - Public plugin API headers
  - `plugin.mk` - Plugin build rules

### Resources & Assets
- **`res/`** - Application resources
  - `cachedb/` - Cache database schemas
  - `ecmascript/` - ECMAScript runtime resources
  - `fileaccess/` - File access plugins
  - `fonts/` - Font assets
  - `kvstore/` - Key-value store schemas
  - `metadb/` - Metadata database schemas
  - `shaders/` - OpenGL shader programs
  - `showtime/` - Core application resources
  - `speaker_positions/` - Audio speaker configuration
  - `static/` - Static assets and web resources
  - `svg/` - SVG graphics
  - `tvheadend/` - HTSP/Tvheadend integration resources

### Internationalization
- **`lang/`** - Language/localization files
  - `*.lang` - 20+ language localizations (e.g., cs_CZ.lang, de_DE.lang, zh_CN.lang)
  - Supported languages: Czech, German, Danish, Greek, Finnish, French, Hungarian, Italian, Japanese, Macedonian, Dutch, Polish, Portuguese (BR/PT), Russian, Slovak, Spanish, Swedish, Ukrainian, Chinese

### Platform-Specific Code
- **`android/`** - Android-specific implementation
  - Java frontend and NDK bridge code
- **`ios/`** - iOS-specific implementation
  - Objective-C/Swift frontend and bindings
  - `freetype2-ios/` - iOS-specific FreeType build (submodule)

### Build Support
- **`support/`** - Build utilities and support files
  - `configure.inc` - Common configuration functions
  - `gitver.mk` - Git version extraction
  - `mklicense.mk` - License file generation
  - `mkbundle` - Application bundling utilities
  - `mkdmg` - macOS DMG creation
  - `Movian.app/` - macOS application bundle template
  - `artwork/` - Logo and artwork assets
  - `debian/` - Debian package configuration
  - `fedora/` - Fedora package configuration
  - `gnome/` - GNOME integration files
  - `osx/` - macOS-specific build utilities
  - `ps3/` - PS3-specific build utilities
  - `sunxi/` - Sunxi-specific build utilities
  - `nacl/` - NaCl-specific files
  - `traceprint/` - Debugging utilities
  - `movian.desktop` - Linux .desktop file
  - `snap.yaml` - Snapcraft configuration

### Deployment & Packaging
- **`Manifests/`** - Deployment manifests
  - `rpi.json` - Raspberry Pi deployment configuration
- **`licenses/`** - License files for dependencies and components
- **`man/`** - Manual pages and documentation

### Build Outputs
Build artifacts are generated in platform-specific directories:
- `build.linux/` - Linux build output and executable
- `build.osx/` - macOS build with application bundle
- `build.ps3/` - PlayStation 3 self and package files
- `build.rpi/` - Raspberry Pi squashfs image
- `build.android/` - Android APK and library
- `build.ios/` - iOS application bundle

## Module Interdependencies

### Media Pipeline
- `libav.c` → FFmpeg/libav integration
- `src/audio2/` → Audio processing
- `src/video/` → Video rendering
- `src/subtitles/` → Subtitle handling
- All feed into `src/media/` and playback queues

### Plugin & Extension System
- `src/plugins.c` → Core plugin loader
- `src/ecmascript/` → ECMAScript runtime
- `nativeplugin/` → C plugin interface
- `plugin_examples/` → Reference implementations
- Resources in `res/` support plugin functionality

### UI & Interaction
- `src/glw/` → OpenGL widget framework
- `glwskins/` → Visual themes
- `src/navigator.c` → Navigation state
- `src/playqueue.c` → Playback control
- Platform adapters in `src/arch/`

### Settings & State
- `src/settings.c` → Settings management
- `src/prop/` → Property system
- `res/cachedb/`, `res/kvstore/`, `res/metadb/` → Database schemas
- Stored in `~/.hts/showtime/` on runtime

### Cross-Platform Support
- `src/arch/` → Platform-specific implementations
- `configure*` scripts → Platform detection and setup
- `support/` → Build utilities per platform
- Platform-specific code in `android/`, `ios/`, `src/arch/`

## Key Build Files

- **`Makefile`** - Main orchestration (sources from platform-specific builds)
- **`support/configure.inc`** - Shared configuration logic for all platforms
- **`support/gitver.mk`** - Git version extraction for versioning
- **`support/mklicense.mk`** - License aggregation for distribution

## Resource Management

Resources are compiled into the executable and accessed via the resource system:
- Fonts from `res/fonts/` embedded for UI rendering
- Shaders from `res/shaders/` compiled for OpenGL
- UI layouts from `glwskins/` loaded at startup
- Language files from `lang/` loaded based on locale
- Default plugins and services from `res/`

## Typical Development Workflow

1. **Modify core logic** in `src/` directories
2. **Rebuild** using platform-specific configure + make
3. **Test plugin changes** using examples in `plugin_examples/`
4. **Customize UI** by editing GLW skin files in `glwskins/`
5. **Add translations** to `lang/` files
6. **Platform-specific changes** in `src/arch/` or platform directories

## Getting Started with the Codebase

- For platform builds: Start with relevant `configure*` script
- For plugin development: See `plugin_examples/` and `nativeplugin/`
- For UI customization: Explore `glwskins/flat/` structure
- For backend additions: Review `src/backend/` implementations
- For cross-platform builds: Use `Autobuild.sh` framework
