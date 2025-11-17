# Build Instructions for Movian

Complete build procedures for all supported platforms. Ensure you have completed [Getting Started](getting-started.md) before proceeding.

## Linux Desktop Build

### Prerequisites Check
```bash
# Verify dependencies are installed
pkg-config --cflags --libs freetype2
pkg-config --cflags --libs fontconfig
pkg-config --cflags --libs x11
pkg-config --cflags --libs libwebp
```

### Standard Debug Build

```bash
# Configure for Linux (autodetects on Linux systems)
./configure

# Build with parallel jobs (-j4 recommended for 4 cores)
make -j4

# Build completes when you see the full path to the binary
```

### Output Location
```bash
# Binary location after successful build
./build.linux/movian

# Run directly
./build.linux/movian
```

### Build Options

**Disable specific features:**
```bash
./configure --disable-webkit      # Disable WebKit renderer
./configure --disable-vdpau       # Disable NVIDIA VDPAU
./configure --disable-libcec       # Disable CEC support
./configure --disable-pulse       # Disable PulseAudio
```

**Enable debugging:**
```bash
./configure --enable-bughunt       # Enable bug hunting features
./configure --cc=gcc-5             # Use specific compiler version
./configure --optlevel=g           # Compile with debug symbols (-g flag)
```

**Full development build with Address Sanitizer:**
```bash
./configure \
  --cc=gcc-5 \
  --extra-cflags=-fno-omit-frame-pointer \
  --optlevel=g \
  --sanitize=address \
  --enable-bughunt

make -j4
```

This build includes:
- ASan (Address Sanitizer) for memory error detection
- Frame pointers for better debugging
- Full debug symbols
- Bug hunting features for development

### Incremental Builds
After initial build, code changes only require:
```bash
make -j4
./build.linux/movian
```

### Clean Build
To start fresh:
```bash
# Remove build artifacts but keep configuration
rm -rf build.linux/

# Or configure from scratch
./configure --cleanbuild
make -j4
```

### Installation (Optional)
```bash
# Install to system directories (requires make target)
sudo make install

# Or manually install built binary
sudo cp build.linux/movian /usr/local/bin/
```

## macOS Build

### Prerequisites
Ensure Xcode and Command Line Tools are installed:
```bash
xcode-select --install
brew install yasm
brew install webp
```

### Debug Build

```bash
# Configure for macOS
./configure

# Build
make -j4

# Binary output
./build.osx/Movian.app/Contents/MacOS/movian
```

### Release Build (Optimized)

```bash
./configure --release

make -j4

# Binary output (optimized)
./build.osx/Movian.app/Contents/MacOS/movian
```

### macOS Application Bundle

**For local development:**
The debug build creates a self-contained bundle:
```bash
./build.osx/Movian.app/Contents/MacOS/movian
```

**For distribution (DMG creation):**
```bash
./configure --release
make -j4
make dist
# Generates: Movian.dmg
```

The DMG file can be distributed and will properly load all resources when installed.

### Architecture-Specific Builds

**For Apple Silicon (ARM64):**
```bash
./configure --arch=arm64
make -j4
```

**For Intel (x86_64):**
```bash
./configure --arch=x86_64
make -j4
```

**For Universal Binary (if supported):**
Check your Xcode version for universal binary support.

### macOS Build Options
```bash
# Specify macOS deployment target
./configure --macosxsdk=/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk

# Use specific compiler
./configure --cc=clang
```

## PlayStation 3 Build

PS3 builds use the Autobuild system with PSL1GHT toolchain. Builds must run on a compatible Linux system.

### Prerequisites
- Linux host system (Ubuntu 16.04 LTS recommended)
- Git and build tools
- Internet connection for toolchain download

### Building for PS3

```bash
# Create working directory if needed
mkdir -p /var/tmp/showtime-autobuild

# Build PS3 binary and package
./Autobuild.sh -t ps3 -v 5.0.500

# Optional: Specify parallel jobs
./Autobuild.sh -t ps3 -v 5.0.500 -j4

# Optional: Custom working directory
./Autobuild.sh -t ps3 -v 5.0.500 -w /custom/path
```

### Build Process
The Autobuild system:
1. Downloads and extracts PSL1GHT toolchain
2. Runs `./configure.ps3` with appropriate flags
3. Compiles native code to PS3 SELF format
4. Packages into installable PKG format

### Output Artifacts
```bash
build.ps3/
├── movian.self              # Executable (SELF format)
├── movian.pkg               # Installable package
└── movian_geohot.pkg        # Alternative package
```

### Configuration Details
The PS3 build automatically:
- Downloads required toolchain from Movian.tv
- Detects and uses ccache if available for speed
- Builds SELF (Signed ELF) executable
- Generates installable PKG files

### Installation on PS3
1. Enable dev mode or use compatible PS3 firmware
2. Copy PKG to USB drive or deliver via network
3. Install through PS3 package installer

### Debugging PS3 Builds
```bash
# Check toolchain status
./Autobuild.sh -t ps3 -v 5.0.500 -w /var/tmp/ps3-debug

# Verbose output
set -x
./Autobuild.sh -t ps3 -v 5.0.500
set +x
```

## Raspberry Pi Build

Raspberry Pi builds use cross-compilation via Autobuild with specialized ARM toolchain.

### Prerequisites
- Linux host system (Ubuntu 16.04 LTS 64-bit recommended)
- Dependencies installed:
```bash
sudo apt-get install \
  git-core \
  build-essential \
  autoconf \
  bison \
  flex \
  libelf-dev \
  libtool \
  pkg-config \
  texinfo \
  libncurses5-dev \
  libz-dev \
  python-dev \
  libssl-dev \
  libgmp3-dev \
  ccache \
  zip \
  squashfs-tools
```

### Building for Raspberry Pi

```bash
# Basic build
./Autobuild.sh -t rpi -v 5.0.500

# With parallel jobs
./Autobuild.sh -t rpi -v 5.0.500 -j4

# Custom working directory
./Autobuild.sh -t rpi -v 5.0.500 -w /custom/path
```

### Build Process
The Autobuild system:
1. Downloads and extracts ARM cross-compilation toolchain
2. Runs `./configure.rpi` with cross-compiler settings
3. Compiles for ARM architecture
4. Creates squashfs image (read-only filesystem)

### Output Artifacts
```bash
build.rpi/
└── showtime.sqfs            # Compressed squashfs image
```

### Installation on Raspberry Pi

**Via SFTP/SSH:**
```bash
# Copy to RPi
scp build.rpi/showtime.sqfs pi@<rpi_ip>:/tmp/

# SSH into RPi and install
ssh pi@<rpi_ip>
sudo /opt/showtime/updater.sh /tmp/showtime.sqfs
```

**Via HTTP API (requires Binreplace in settings:dev):**
```bash
# Enable Binreplace in RPi settings:dev first, then:
curl --data-binary @build.rpi/showtime.sqfs http://<rpi_ip>:42000/api/replace
```

### RPi Build Configuration
Key features enabled:
- OpenGL ES for embedded graphics
- ARM-specific optimizations
- Connman for network management
- libcec for HDMI-CEC remote support
- Avahi for service discovery

### Cross-Compilation Notes
- Builds target 32-bit ARM (armhf)
- Squashfs image is read-only after deployment
- Update mechanism via HTTP API or file replacement
- Toolchain includes proper sysroot for compatibility

## Other Platform Builds

### Android Build
```bash
./configure.android [options]
make -j4
# Output: build.android/
```

### iOS Build
```bash
./configure.ios [options]
make -j4
# Output: build.ios/
```

### Sunxi (ARM) Build
```bash
./configure.sunxi [options]
make -j4
# Output: build.sunxi/
```

### Google NaCl Build
```bash
./configure.nacl [options]
make -j4
# Output: build.nacl/
```

## Common Build Issues

### Out of Memory During Build
If the build process runs out of memory:
```bash
# Reduce parallel jobs
make -j2

# Or compile with single core
make -j1
```

### Missing Dependencies
If configure reports missing libraries:
```bash
# Check all pkg-config issues
./configure 2>&1 | grep -i "error\|missing\|not found"

# Install specific package and retry
./configure --disable-<feature>
```

### Incomplete Submodules
If build fails on external libraries:
```bash
# Update all submodules
git submodule update --init --recursive

# Or specific submodule
git submodule update --init ext/libav
```

### Compiler Cache Issues (ccache)
If ccache causes problems:
```bash
# Clear ccache
ccache -C

# Or disable ccache
./configure --no-ccache
```

### Permission Errors
```bash
# Ensure build directory is writable
chmod 755 build.* 2>/dev/null || true

# Clean and reconfigure if needed
rm -rf build.linux/ config.mak
./configure
```

## Build Performance Tips

### Parallel Compilation
```bash
# Recommended: 1-2x number of CPU cores
nproc                     # Show CPU count
make -j$(($(nproc) + 1)) # Add one extra

# For 4-core system
make -j5
```

### Using ccache
If installed, ccache automatically accelerates rebuilds:
```bash
ccache -s  # Show statistics
ccache -C  # Clear cache if needed
```

### Incremental Compilation
```bash
# Only recompile changed files
touch src/navigator.c
make -j4

# Faster than full rebuild
```

## Verification After Build

### Check Binary Exists
```bash
# Linux
file ./build.linux/movian

# macOS
file ./build.osx/Movian.app/Contents/MacOS/movian

# Should show ELF 64-bit (Linux) or Mach-O 64-bit (macOS)
```

### Check Linked Libraries
```bash
# Linux
ldd ./build.linux/movian | head -10

# macOS
otool -L ./build.osx/Movian.app/Contents/MacOS/movian | head -10
```

### Binary Size
```bash
# Should be several MB
ls -lh build.linux/movian
du -sh build.osx/Movian.app/
```

## Next Steps

After successful build:
1. See [Runtime Guide](runtime.md) for launching and configuration
2. Check [Repository Structure](repository-structure.md) for code organization
3. Explore plugin development in `plugin_examples/`
4. Customize UI themes in `glwskins/`
