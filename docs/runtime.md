# Runtime and Configuration Guide

This guide covers launching Movian builds, runtime configuration, settings management, and debugging options.

## Launching Movian

### Linux
```bash
# Direct execution
./build.linux/movian

# With output to console
./build.linux/movian 2>&1

# In background
./build.linux/movian &

# Run as daemon
nohup ./build.linux/movian > movian.log 2>&1 &
```

### macOS

**Debug Build:**
```bash
./build.osx/Movian.app/Contents/MacOS/movian
```

**Release Build (DMG installed):**
```bash
# If installed from DMG
/Applications/Movian.app/Contents/MacOS/movian

# Or open via Finder
open /Applications/Movian.app
```

**From command line (after installation):**
```bash
movian  # If in PATH
```

### PlayStation 3
Invoke via PS3 menu after package installation or network loading.

### Raspberry Pi
On RPi with installed Movian:
```bash
# If integrated into system
/opt/showtime/movian

# Or check for launch wrapper
showtime
```

## Default Settings Location

Movian stores configuration, cache, and state in a user-specific directory:

### Linux & macOS
```bash
~/.hts/showtime/
```

### Directory Structure
```
~/.hts/showtime/
├── settings                 # Main settings database (SQLite)
├── cache/                   # Metadata and image cache
│   ├── metadb              # Metadata database
│   ├── imagedb             # Image cache
│   └── kvstore             # Key-value stores
├── db/                      # Application databases
│   └── showtime.db         # Main application database
├── plugin_storage/         # Plugin-specific data
├── fonts/                  # Cached font data
└── logs/                   # Debug logs (if enabled)
```

### Platform-Specific Locations

**Android:**
```
/data/data/tv.movian.movian/
```

**iOS:**
```
~/Library/Application Support/tv.movian.movian/
```

**PS3:**
```
/dev_hdd0/game/MVNH00001/usrsettings/
```

**Raspberry Pi:**
```
/opt/showtime/settings/
```

## Runtime Configuration

### Via Settings Menu (GUI)
Access settings in Movian's UI:
1. Open Movian
2. Navigate to Settings
3. Configure preferences
4. Settings auto-save to SQLite database

### Direct Database Access
For advanced users, directly inspect settings:
```bash
# Linux/macOS
sqlite3 ~/.hts/showtime/settings

# View all settings
.mode column
SELECT * FROM settings;

# View specific setting
SELECT * FROM settings WHERE path LIKE '%audio%';
```

### Command-Line Options
Basic command-line flags (platform-dependent):
```bash
# Show help
./build.linux/movian --help

# Verbose output
./build.linux/movian --verbose

# Specify custom settings directory
./build.linux/movian --settingspath ~/.hts/movian-test
```

## Useful Debug Flags and Environment Variables

### Debug Output
```bash
# Enable detailed debug output
MOVIAN_DEBUG=1 ./build.linux/movian

# Full verbose logging
./build.linux/movian 2>&1 | tee movian.log

# Log to file
./build.linux/movian > movian.log 2>&1 &
tail -f movian.log
```

### Development Settings
Enable development settings via UI or direct database modification:
```bash
# Enable development dashboard
sqlite3 ~/.hts/showtime/settings
INSERT OR REPLACE INTO settings (path, value) 
VALUES ('settings/dev', 1);

# Then access via Settings → Dev
```

### Memory Debugging
For builds compiled with Address Sanitizer:
```bash
# Run with ASan
ASAN_OPTIONS=verbosity=2 ./build.linux/movian

# Detect memory leaks
ASAN_OPTIONS=detect_leaks=1 ./build.linux/movian
```

### Video & Rendering Debug
```bash
# Disable GPU acceleration (use software rendering)
export MOVIAN_DISABLE_GPU=1
./build.linux/movian

# Debug OpenGL
export LIBGL_DEBUG=verbose
./build.linux/movian
```

### Network & Plugin Debug
```bash
# Trace HTTP requests
MOVIAN_TRACE_HTTP=1 ./build.linux/movian

# Debug plugins
MOVIAN_PLUGIN_DEBUG=1 ./build.linux/movian
```

### Custom Environment Variables
Common environment variables:
```bash
# Libav debugging
export FFDEBUG=all
./build.linux/movian

# Pulse Audio debugging
export PULSE_DEBUG=4
./build.linux/movian

# GTK debugging
export GTK_DEBUG=all
./build.linux/movian
```

## Stopping Movian

```bash
# Graceful shutdown via UI menu

# Or terminate from command line
killall movian

# Or force kill if needed
pkill -9 movian
```

## Performance Tuning

### CPU Optimization
```bash
# Limit to specific CPU cores
taskset -c 0-3 ./build.linux/movian

# Set nice level (lower = higher priority)
nice -n -5 ./build.linux/movian
```

### Memory Management
Monitor memory usage:
```bash
# Watch memory consumption
watch -n 1 'ps aux | grep movian'

# Or use top/htop
top -p $(pgrep movian)
htop -p $(pgrep movian)
```

For builds with memory issues:
```bash
# Limit memory usage (cgroup on Linux)
echo 1g > /sys/fs/cgroup/memory/movian/memory.limit_in_bytes
./build.linux/movian
```

### Cache Configuration
Adjust cache size in settings:
- Settings → Cache → Maximum cache size
- Default: Varies by platform
- Range: 10 MB to 10 GB

## Logs and Debugging

### Accessing Logs

**During Execution:**
```bash
# Console output captures logs
./build.linux/movian 2>&1 | tee debug.log

# Search for errors
grep -i "error\|warning" debug.log

# Tail live
tail -f debug.log
```

**Stored Logs:**
```bash
# Linux/macOS log location
ls -la ~/.hts/showtime/logs/
cat ~/.hts/showtime/logs/movian.log

# Or check system journal (systemd systems)
journalctl -u movian -n 100
```

### Common Log Patterns

**Video codec issues:**
```
grep "video\|codec\|decoder" debug.log
```

**Audio problems:**
```
grep "audio\|alsa\|pulse" debug.log
```

**Plugin errors:**
```
grep "plugin\|script\|ecmascript" debug.log
```

**Network issues:**
```
grep "http\|network\|dns" debug.log
```

## Backing Up Settings

### Manual Backup
```bash
# Backup settings directory
tar -czf movian-backup.tar.gz ~/.hts/showtime/

# Backup specific database
cp ~/.hts/showtime/settings ~/.hts/showtime/settings.backup
```

### Restore from Backup
```bash
# Restore entire settings
tar -xzf movian-backup.tar.gz -C ~/

# Or restore just settings database
cp ~/.hts/showtime/settings.backup ~/.hts/showtime/settings
```

## Settings Migration

### Between Machines
```bash
# On source machine
tar -czf movian-config.tar.gz ~/.hts/showtime/

# Transfer file to destination
scp movian-config.tar.gz user@destination:~/

# On destination machine
tar -xzf movian-config.tar.gz -C ~/
```

### Between Movian Versions
Usually compatible, but for major version changes:
```bash
# Backup first
tar -czf movian-old-backup.tar.gz ~/.hts/showtime/

# Delete cache to force refresh
rm -rf ~/.hts/showtime/cache/*

# Run new version
./build.linux/movian
```

## Troubleshooting Runtime Issues

### Crashes on Startup
```bash
# Check if settings database is corrupt
sqlite3 ~/.hts/showtime/settings ".integrity_check"

# Rebuild or reset settings if corrupt
mv ~/.hts/showtime/settings ~/.hts/showtime/settings.corrupt
./build.linux/movian  # Will recreate default settings

# Check logs for errors
tail -f ~/.hts/showtime/logs/movian.log
```

### Audio Issues
```bash
# List available audio devices
./build.linux/movian 2>&1 | grep -i "audio\|alsa\|pulse\|coreaudio"

# Disable specific audio backend
./configure --disable-pulse    # Disable PulseAudio
make -j4
./build.linux/movian
```

### Video Playback Issues
```bash
# Check hardware acceleration
./build.linux/movian 2>&1 | grep -i "vdpau\|vaapi\|opengl"

# Disable GPU acceleration if problematic
MOVIAN_DISABLE_GPU=1 ./build.linux/movian
```

### Network/Streaming Issues
```bash
# Enable network tracing
MOVIAN_TRACE_HTTP=1 ./build.linux/movian 2>&1 | grep -i "http\|dns"

# Check connectivity
ping 8.8.8.8
nslookup movian.tv
```

### Plugin Loading Issues
```bash
# Check plugin directory
ls -la ~/.hts/showtime/plugin_storage/

# Clear plugin cache and reload
rm -rf ~/.hts/showtime/cache/*
./build.linux/movian

# Check plugin logs
MOVIAN_PLUGIN_DEBUG=1 ./build.linux/movian 2>&1 | grep plugin
```

## Performance Monitoring

### Real-Time Metrics
```bash
# CPU and memory usage
watch 'ps aux | grep "[m]ovian"'

# More detailed with htop
htop -p $(pgrep movian)

# Network activity
iftop -p
nethogs

# Disk I/O
iotop
```

### Profiling (with debug builds)
```bash
# CPU profiling (if compiled with profiling support)
perf record ./build.linux/movian
perf report

# Memory profiling
valgrind ./build.linux/movian
```

## Platform-Specific Runtime Notes

### Linux
- X11 is required for GUI (Wayland support varies)
- ALSA, PulseAudio, or pipewire for audio
- OpenGL or OpenGL ES for rendering
- Automatic udev for USB device support

### macOS
- CoreAudio for audio
- Metal or OpenGL for graphics
- Cocoa framework integration
- Can run as menu bar application or fullscreen

### PlayStation 3
- System memory divided between OS and application
- Audio through HDMI or optical
- Controller via wireless or USB
- Cannot access PS3 settings during Movian runtime

### Raspberry Pi
- Memory allocation to GPU affects performance
- CEC for remote control via TV
- Audio through jack or HDMI
- SSH access recommended for remote configuration

## Related Documentation

- [Build Instructions](build-instructions.md) - How to compile
- [Repository Structure](repository-structure.md) - Code organization
- [Getting Started](getting-started.md) - Initial setup
