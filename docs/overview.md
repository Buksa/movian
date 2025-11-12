# Movian Documentation

## What is Movian?

Movian is a cross-platform multimedia player designed to bring a sophisticated, appliance-style media experience to diverse computing environments. Written primarily in C with a custom autotools-style build system, Movian combines efficient media playback capabilities with an extensible plugin architecture and a modern OpenGL-based user interface.

### Core Capabilities

**Media Playback**
- Universal multimedia playback via FFmpeg/libav integration
- Support for audio, video, and subtitle processing pipelines
- Hardware-accelerated rendering with OpenGL
- Network streaming protocols including HLS, RTMP, HTTP, and more

**Network & Streaming**
- BitTorrent integration for distributed media delivery
- HTSP (Tvheadend) backend support for live TV
- Icecast streaming
- DVD playback and video device handling
- AirPlay support for remote playback

**Extensibility**
- Embedded ECMAScript engine for plugin development
- Plugin system for custom media providers and services
- Native plugin support for platform-specific functionality
- HTTP/REST control API for remote management

**Metadata & Services**
- Integration with TMDB (The Movie Database)
- TVDB (TheTVDB) for TV series information
- Last.fm scrobbling support
- Screenshot capture and management
- Metadata caching via SQLite

**User Interface**
- GLW (Glorious Looking Widgets) - custom OpenGL-based UI framework
- Multiple skin themes (flat and legacy skins included)
- Multi-language support with 20+ localization files
- Settings management with SQLite backend
- Navigation and playlist management

### Supported Platforms

Movian runs on multiple architectures and operating systems:

**Desktop Platforms**
- **Linux** - Full support via X11 and OpenGL ES
- **macOS** - Native support with Cocoa integration
- **Windows** - POSIX compatibility layer support

**Embedded & Appliance Platforms**
- **PlayStation 3** - Native RSX graphics support
- **Raspberry Pi** - OpenGL ES with ARM optimization
- **Sunxi** - ARM-based embedded systems
- **Android** - Mobile platform with Java frontend
- **iOS** - Apple mobile platform with Objective-C/Swift frontend

**Specialized Platforms**
- **Google NaCl** (Native Client) support
- Platform-specific backend providers for each OS

### Architecture Overview

```
Movian Core (C)
├── Media Pipeline (FFmpeg/libav, audio, video, subtitles)
├── Backend Providers (BitTorrent, HLS, RTMP, HTSP, Icecast, DVD)
├── Plugin System (ECMAScript, native plugins)
├── Metadata Services (TMDB, TVDB, Last.fm, AirPlay)
├── Data Persistence (SQLite caches, settings, navigation)
└── User Interface (GLW framework + platform adapters)
    ├── Desktop (Linux/X11, macOS/Cocoa)
    ├── Embedded (PS3/RSX, RPi/GLES)
    └── Mobile (Android/Java, iOS/Swift)
```

## Documentation Structure

This documentation set provides everything needed to build, develop, and extend Movian:

- **[Repository Structure](repository-structure.md)** - Understanding the codebase layout and module organization
- **[Getting Started & Setup](getting-started.md)** - Prerequisites and environment preparation
- **[Build Instructions](build-instructions.md)** - Detailed build procedures for all platforms
- **[Runtime & Configuration](runtime.md)** - Launching builds, settings, and debug options

## Quick Links

- **Project Website**: https://movian.tv/
- **License**: See LICENSE file (proprietary)
- **Version Control**: Git with submodule dependencies

## Contributing

To contribute to Movian, familiarize yourself with:
1. Repository structure and module boundaries
2. Platform-specific build requirements
3. Code style and conventions used throughout the project
4. Plugin development guidelines

Begin with the [Getting Started guide](getting-started.md) and explore the relevant platform build documentation.
