# Getting Started with Movian

This guide walks you through setting up Movian for development and building from source.

## Prerequisites

### All Platforms
- **Git** - For cloning the repository and submodule management
- **Bash** - Build scripts require bash shell
- **Build Tools** - Platform-specific toolchains (see below)

### Linux

#### Ubuntu 16.04 LTS and later
```bash
sudo apt-get update
sudo apt-get install \
  build-essential \
  git \
  autoconf \
  pkg-config \
  yasm \
  libfreetype6-dev \
  libfontconfig1-dev \
  libxext-dev \
  libgl1-mesa-dev \
  libasound2-dev \
  libgtk2.0-dev \
  libxss-dev \
  libxxf86vm-dev \
  libxv-dev \
  libvdpau-dev \
  libpulse-dev \
  libssl-dev \
  curl \
  libwebkitgtk-dev \
  libsqlite3-dev \
  libavahi-client-dev
```

#### Debian/other distributions
Use equivalent packages for your distribution manager (apt, yum, pacman, etc.)

#### Optional for development
```bash
# Address sanitizer for memory debugging
sudo apt-get install libasan-dev

# Additional codecs and protocols
sudo apt-get install libavahi-compat-libdnssd-dev
```

### macOS

**Requirements:**
- macOS 10.9 or later
- Xcode (install from Mac App Store)
- Xcode Command Line Tools

**Installation:**
```bash
# Install Xcode if not already installed
xcode-select --install

# Install Homebrew if needed
/usr/bin/ruby -e "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/master/install)"

# Install yasm for assembly
brew install yasm

# Optional: Additional tools
brew install pkg-config
```

## Cloning the Repository

### Basic Clone
```bash
git clone https://github.com/andoma/movian.git
cd movian
```

### Initialize Submodules
Movian uses git submodules for external dependencies (FFmpeg, Duktape, etc.):

```bash
# Initialize and fetch all submodules
git submodule update --init --recursive

# Or in one command during clone:
git clone --recursive https://github.com/andoma/movian.git
```

### Submodule Components
Key submodules include:
- `ext/libav/` - FFmpeg for codec support
- `ext/rtmpdump/` - RTMP protocol support
- `ext/libntfs_ext/` - NTFS file system support
- `ext/gumbo-parser/` - HTML5 parsing
- `ext/libyuv/` - YUV image conversion
- `ext/vmir/` - Video effects
- `ios/freetype2-ios/` - iOS font support

After cloning, verify submodules are present:
```bash
git submodule status
# Should show each submodule with a commit hash, not a minus sign
```

## Environment Preparation

### Linux

Create a working directory and prepare:
```bash
mkdir -p ~/movian-build
cd ~/movian-build

# Optional: Create virtual environment or use system Python
python3 -m venv venv
source venv/bin/activate
```

### macOS

Additional environment setup:
```bash
# Set SDK path (if not auto-detected)
export MACOSX_DEPLOYMENT_TARGET=10.9

# For Intel Macs (check your architecture)
uname -m  # Should show x86_64 or arm64
```

### Verify Toolchain
Check that required tools are available:

```bash
# Check compiler
gcc --version  # or clang --version on macOS

# Check build tools
make --version
pkg-config --version
yasm --version

# Check critical libraries
pkg-config --cflags --libs freetype2
pkg-config --cflags --libs fontconfig
```

## Initial Build Configuration

### Linux: Standard Build
```bash
./configure
```

If any dependencies are missing, configure will inform you. You can disable features:
```bash
./configure --disable-webkit     # Disable WebKit if unavailable
./configure --disable-vdpau      # Disable VDPAU GPU support
./configure --disable-libcec      # Disable CEC support
```

### Linux: Development Build with Debugging
```bash
./configure \
  --cc=gcc-5 \
  --extra-cflags=-fno-omit-frame-pointer \
  --optlevel=g \
  --sanitize=address \
  --enable-bughunt
```

This enables:
- Frame pointer preservation for better backtraces
- Address Sanitizer for memory error detection
- Additional debug information
- Bug hunting features

### macOS: Debug Build
```bash
./configure
```

### macOS: Release Build
```bash
./configure --release
```

## Verifying Installation

After configuration, verify the setup:
```bash
# Check that configuration was successful
ls -la config.mak

# Inspect configuration
grep "PLATFORM\|VERSION" config.mak

# Verify key features were enabled
grep "ENABLE" config.mak | head -20
```

## Project Layout for Development

After setup, your directory structure looks like:
```
movian/
├── src/                      # Core C source code
├── ext/                      # External dependencies (submodules)
├── glwskins/                 # UI themes
├── plugin_examples/          # Example plugins
├── configure*                # Platform configuration scripts
├── Makefile                  # Build orchestration
└── build.linux/              # (Generated after make)
    └── movian                # (Generated executable)
```

## Troubleshooting Setup

### Missing pkg-config entries
If `pkg-config` can't find a library:
```bash
# Add PKG_CONFIG_PATH for custom installations
export PKG_CONFIG_PATH=/usr/local/lib/pkgconfig:$PKG_CONFIG_PATH
./configure
```

### Submodule Issues
If submodules fail to clone:
```bash
# Update submodules individually
git submodule update --init ext/libav
git submodule update --init ext/duktape
# etc.

# Or remove and re-clone
rm -rf ext/libav
git submodule update --init ext/libav
```

### Configure Script Failures
```bash
# Run with verbose output to see what's failing
./configure --verbose

# Check the configure log
tail -f configure.log
```

### Permission Errors
If you get permission denied:
```bash
# Make configure scripts executable
chmod +x configure configure.* Autobuild.sh

# Ensure proper permissions on your build directory
chmod 755 .
```

## Next Steps

Once environment is set up:

1. **Read [Build Instructions](build-instructions.md)** for platform-specific build commands
2. **Review [Repository Structure](repository-structure.md)** to understand code organization
3. **Consult [Runtime Guide](runtime.md)** to launch and configure Movian
4. **Explore [plugin_examples/](../plugin_examples)** to understand plugin development

## Building for Specific Platforms

Quick reference for your platform:

### Linux
```bash
./configure
make -j4
./build.linux/movian
```

### macOS (Debug)
```bash
./configure
make -j4
./build.osx/Movian.app/Contents/MacOS/movian
```

### macOS (Release/DMG)
```bash
./configure --release
make -j4
make dist
# Output: Movian.dmg
```

### Raspberry Pi (via Autobuild)
```bash
./Autobuild.sh -t rpi -v 5.0.500 -j4
# Output: build.rpi/showtime.sqfs
```

See [Build Instructions](build-instructions.md) for complete details on each platform.

## Getting Help

- Check existing configuration failures with `./configure --help`
- Review `support/configure.inc` for advanced options
- Examine platform-specific `configure.*` scripts for target-specific settings
- Consult README.markdown for quick reference
