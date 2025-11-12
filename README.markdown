Movian Multimedia Player
=========================

(c) 2006 - 2018 Lonelycoder AB

[![Build status](https://doozer.io/badge/andoma/movian/buildstatus/master)](https://doozer.io/user/andoma/movian)

## Overview

Movian is a cross-platform multimedia player with sophisticated media playback capabilities, an extensible plugin architecture, and a modern OpenGL-based user interface. It supports audio, video, and streaming content across desktop, embedded, and mobile platforms.

**Project Website:** [https://movian.tv/](https://movian.tv/)

## Quick Start

### Linux
```bash
./configure
make -j4
./build.linux/movian
```

### macOS
```bash
./configure
make -j4
./build.osx/Movian.app/Contents/MacOS/movian
```

### Raspberry Pi
```bash
./Autobuild.sh -t rpi -v 5.0.500
```

## Documentation

Complete documentation is available in the `docs/` directory:

- **[Overview](docs/overview.md)** - What Movian is, capabilities, and supported platforms
- **[Repository Structure](docs/repository-structure.md)** - Understanding the codebase layout and module organization
- **[Getting Started](docs/getting-started.md)** - Prerequisites, cloning, environment setup for Linux and macOS
- **[Build Instructions](docs/build-instructions.md)** - Detailed build procedures for all platforms
- **[Runtime Guide](docs/runtime.md)** - Launching, configuration, debugging, and troubleshooting

## Supported Platforms

**Desktop:** Linux, macOS, Windows (POSIX compatibility)  
**Embedded:** PlayStation 3, Raspberry Pi, Sunxi, Android, iOS  
**Specialized:** Google NaCl, custom platforms

## Key Features

- Universal multimedia playback (FFmpeg/libav integration)
- Network streaming (HLS, RTMP, BitTorrent, HTSP, Icecast, DVD)
- Extensible plugin system (ECMAScript + native plugins)
- Metadata integration (TMDB, TVDB, Last.fm, AirPlay)
- Multi-language support (20+ localizations)
- Advanced UI with multiple themes

## Building for Different Targets

See [Build Instructions](docs/build-instructions.md) for:
- **Linux desktop builds** with debug/release options
- **macOS** with framework integration and DMG distribution
- **PlayStation 3** using PSL1GHT toolchain
- **Raspberry Pi** cross-compilation via Autobuild
- **Other platforms** (Android, iOS, Sunxi, NaCl)

## Contributing

To contribute, start with the [Getting Started guide](docs/getting-started.md) and review the [Repository Structure](docs/repository-structure.md) to understand the codebase organization.

## License

See LICENSE file for licensing information.

