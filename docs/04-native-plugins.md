# Native Plugin Development

**Document Version:** 1.0  
**Last Updated:** 2024  
**Scope:** Native plugin development using VMIR (Virtual Machine Intermediate Representation), LLVM bitcode compilation, and C/C++ integration with Movian's core engine

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Native Plugin API](#native-plugin-api)
4. [Build System and Toolchain](#build-system-and-toolchain)
5. [Plugin Lifecycle](#plugin-lifecycle)
6. [Module Registration](#module-registration)
7. [Use Cases](#use-cases)
8. [Debugging and Troubleshooting](#debugging-and-troubleshooting)
9. [Advanced Topics](#advanced-topics)

---

## Overview

### What Are Native Plugins?

Native plugins extend Movian using compiled C/C++ code instead of ECMAScript. They provide:

- **Performance-critical functionality** - Audio/video decoding, file format parsing, cryptographic operations
- **Low-level system access** - Direct file I/O, hardware interfaces, OS-specific features
- **Binary library integration** - Leverage existing C/C++ codebases without JavaScript wrappers
- **Custom file access providers** - Implement custom protocols (e.g., proprietary streaming formats)

Unlike ECMAScript plugins, native plugins compile to **LLVM bitcode** and run in Movian's **VMIR** (Virtual Machine Intermediate Representation) sandbox.

**Key Differences:**

| Feature | ECMAScript Plugins | Native Plugins |
|---------|-------------------|----------------|
| Language | JavaScript | C/C++ |
| Execution | Duktape interpreter | VMIR VM (LLVM bitcode) |
| Performance | Moderate | High |
| Safety | Sandboxed | Sandboxed (VMIR) |
| Use Case | UI logic, API integration | Codecs, file formats, low-level I/O |

### When to Use Native Plugins

Choose native plugins for:

- **File format probing** - Detect media types from binary headers
- **Custom decoders** - Audio/video codecs not supported by FFmpeg
- **Protocol handlers** - Custom network protocols or file systems
- **Performance-critical algorithms** - DSP, encryption, compression
- **Legacy code integration** - Reuse existing C/C++ libraries

Use ECMAScript plugins for everything else (UI, API calls, business logic).

---

## Architecture

### VMIR Virtual Machine

Movian's native plugin system is built on **VMIR** (Virtual Machine Intermediate Representation), a custom LLVM bitcode interpreter.

**Source:** [`ext/vmir/src/vmir.h`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/ext/vmir/src/vmir.h)

**Key Components:**

1. **ir_unit_t** - Virtual machine instance (one per native plugin)
2. **Memory isolation** - Each plugin has a dedicated heap (configurable size)
3. **Stack isolation** - Separate call stack (default: 128KB)
4. **Function resolver** - Maps function calls to host implementations
5. **File descriptor abstraction** - Virtual FDs for props, metadata, file handles

**Architecture Diagram:**

```
┌─────────────────────────────────────────────────────────────┐
│ Movian Core (C)                                              │
│  ├── Plugin Manager (src/plugins.c)                          │
│  ├── Native Plugin Loader (src/np/np.c)                      │
│  └── VMIR Runtime (ext/vmir/)                                │
│       ├── ir_unit_t (VM instance per plugin)                 │
│       ├── Memory Manager (isolated heap)                     │
│       └── Function Resolver (host API bridge)                │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               │ LLVM Bitcode (.bc)
                               │
┌──────────────────────────────▼──────────────────────────────┐
│ Native Plugin (.opt file)                                    │
│  ├── C/C++ Source → Clang → LLVM IR → Optimizer → .opt      │
│  ├── np_file_probe() - File format detection                 │
│  ├── np_audio_open/play/close() - Audio decoding             │
│  └── Custom algorithms (codec, crypto, parsing)              │
└─────────────────────────────────────────────────────────────┘
```

### Plugin Context Structure

Each native plugin runs in an `np_context_t`:

**Source:** [`src/np/np.h#L14-L32`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/np/np.h#L14-L32)

```c
typedef struct np_context {
  lockmgr_t np_lockmgr;           // Thread-safe reference counting
  LIST_ENTRY(np_context) np_link; // Global plugin list
  struct np_resource_list np_resources; // Allocated resources (props, fds)
  
  char *np_id;                    // Plugin ID (e.g., "audiocodec-opus")
  char *np_storage;               // Storage path (~/.hts/showtime/plugins/<id>)
  char *np_path;                  // Load path (for relative imports)
  
  ir_unit_t *np_unit;             // VMIR VM instance
  void *np_mem;                   // Heap memory block
  
  struct backend *np_backend;     // Backend integration (if applicable)
} np_context_t;
```

### Native Object Bridge

Native plugins interact with Movian's prop system via **native objects** - opaque handles that bridge JavaScript and C.

**Source:** [`src/ecmascript/es_native_obj.c`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_native_obj.c)

**Key Functions:**

- `es_push_native_obj(ctx, class, ptr)` - Push C pointer as JS object
- `es_get_native_obj(ctx, idx, class)` - Extract C pointer (with type checking)
- `ES_NATIVE_CLASS(name, release_fn)` - Define native object type

**Example from File Access Provider:**

```c
// Define native class for file handle
ES_NATIVE_CLASS(fah, &fah_release);

// Push to JavaScript
es_push_native_obj(ctx, &es_native_fah, fah_retain(fah));

// Retrieve in callback
es_fa_handle_t *fah = es_get_native_obj(ctx, 0, &es_native_fah);
```

**Source:** [`src/ecmascript/es_faprovider.c#L157-L221`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_faprovider.c#L157-L221)

---

## Native Plugin API

### Header File

All native plugins must include:

```c
#include <nativeplugin.h>
```

**Location:** [`nativeplugin/include/nativeplugin.h`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/nativeplugin/include/nativeplugin.h)

### Core Data Types

#### Metadata Constants

```c
#define NP_METADATA_CONTENT_TYPE 1
#define NP_METADATA_TITLE        2
#define NP_METADATA_ALBUM        3
#define NP_METADATA_ARTIST       4
#define NP_METADATA_DURATION     5
#define NP_METADATA_REDIRECT     6
```

**Source:** [`nativeplugin/include/nativeplugin.h#L12-L17`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/nativeplugin/include/nativeplugin.h#L12-L17)

#### Content Types

```c
#define NP_CONTENT_UNKNOWN      0
#define NP_CONTENT_DIR          1
#define NP_CONTENT_FILE         2
#define NP_CONTENT_ARCHIVE      3
#define NP_CONTENT_AUDIO        4
#define NP_CONTENT_VIDEO        5
#define NP_CONTENT_PLAYLIST     6
#define NP_CONTENT_DVD          7
#define NP_CONTENT_IMAGE        8
#define NP_CONTENT_ALBUM        9
#define NP_CONTENT_PLUGIN       10
#define NP_CONTENT_FONT         11
#define NP_CONTENT_SHARE        12
#define NP_CONTENT_DOCUMENT     13
```

**Source:** [`nativeplugin/include/nativeplugin.h#L19-L32`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/nativeplugin/include/nativeplugin.h#L19-L32)

#### Audio Buffer Structure

```c
typedef struct {
  int64_t timestamp;  // Presentation timestamp (microseconds)
  int64_t duration;   // Frame duration (microseconds)
  int samples;        // Number of samples in buffer
  int channels;       // Channel count (1=mono, 2=stereo, etc.)
  int samplerate;     // Sample rate (Hz)
} np_audiobuffer_t;
```

**Source:** [`nativeplugin/include/nativeplugin.h#L36-L43`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/nativeplugin/include/nativeplugin.h#L36-L43)

### Host API Functions

These functions are **provided by Movian** and callable from native plugins:

#### `np_metadata_set(int metadata_handle, int which, ...)`

Set metadata for a file probe result.

**Parameters:**
- `metadata_handle` - Metadata handle from probe callback
- `which` - Metadata field (NP_METADATA_* constant)
- `...` - Value (type depends on `which`)

**Example:**

```c
int np_file_probe(int fd, const uint8_t *buf0, size_t len,
                  int metadata_handle, const char *url) {
  // Detect MP3 file
  if(len >= 3 && buf0[0] == 0xFF && (buf0[1] & 0xE0) == 0xE0) {
    np_metadata_set(metadata_handle, NP_METADATA_CONTENT_TYPE, NP_CONTENT_AUDIO);
    np_metadata_set(metadata_handle, NP_METADATA_TITLE, "Detected MP3");
    return 0; // Success
  }
  return -1; // Not recognized
}
```

**Source:** [`src/np/np.c#L160-L194`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/np/np.c#L160-L194)

#### `np_register_uri_prefix(const char *prefix)`

Register a URL prefix that this plugin handles.

**Parameters:**
- `prefix` - URI prefix (e.g., "mycodec://")

**Usage:**
Typically called from plugin initialization to claim URL schemes.

**Source:** Header declaration only

#### Property System Functions

```c
typedef int prop_t; // Property handle (file descriptor)

prop_t np_prop_create(prop_t parent, const char *name);
prop_t np_prop_create_root(void);
void np_prop_set(prop_t p, const char *key, int how, ...);
void np_prop_append(prop_t parent, prop_t child);
#define np_prop_release(p) close(p)
```

**Source:** [`nativeplugin/include/nativeplugin.h#L61-L72`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/nativeplugin/include/nativeplugin.h#L61-L72)

**Property Set Constants:**

```c
#define NP_PROP_SET_STRING 1
#define NP_PROP_SET_INT    2
#define NP_PROP_SET_FLOAT  3
```

**Example:**

```c
prop_t root = np_prop_create_root();
prop_t title = np_prop_create(root, "title");
np_prop_set(title, NULL, NP_PROP_SET_STRING, "My Title");
np_prop_release(title);
np_prop_release(root);
```

**Source:** [`src/np/np_prop.c`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/np/np_prop.c)

### Plugin Exports (Callbacks)

Native plugins **export** these functions for Movian to call:

#### `int np_file_probe(int fd, const uint8_t *buf0, size_t len, int metadata_handle, const char *url)`

Probe a file to detect its type.

**Parameters:**
- `fd` - File descriptor (VMIR virtual FD)
- `buf0` - Initial buffer (read-ahead, typically 4-16KB)
- `len` - Buffer size
- `metadata_handle` - Handle for setting metadata
- `url` - File URL

**Returns:**
- `0` - File recognized, metadata set
- `-1` - File not recognized

**Called by:** Movian's file access layer when opening unknown files

**Example:**

```c
int np_file_probe(int fd, const uint8_t *buf0, size_t len,
                  int metadata_handle, const char *url) {
  // Check FLAC signature
  if(len >= 4 && memcmp(buf0, "fLaC", 4) == 0) {
    np_metadata_set(metadata_handle, NP_METADATA_CONTENT_TYPE, NP_CONTENT_AUDIO);
    // Parse FLAC metadata blocks...
    return 0;
  }
  return -1;
}
```

**Source:** Invoked from [`src/np/np.c#L117-L156`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/np/np.c#L117-L156)

#### `void *np_audio_open(const char *url, prop_t mp_prop_root)`

Open an audio file for playback.

**Parameters:**
- `url` - File URL to open
- `mp_prop_root` - Media player property root (for status updates)

**Returns:** Opaque context pointer (passed to play/close)

**Source:** [`nativeplugin/include/nativeplugin.h#L81`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/nativeplugin/include/nativeplugin.h#L81)

#### `void *np_audio_play(void *ctx, np_audiobuffer_t *nab)`

Decode next audio frame.

**Parameters:**
- `ctx` - Context from `np_audio_open()`
- `nab` - Audio buffer structure to fill

**Returns:** Updated context pointer (or NULL on EOF/error)

**Source:** [`nativeplugin/include/nativeplugin.h#L85`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/nativeplugin/include/nativeplugin.h#L85)

#### `void np_audio_close(void *ctx)`

Close audio decoder and free resources.

**Parameters:**
- `ctx` - Context from `np_audio_open()`

**Source:** [`nativeplugin/include/nativeplugin.h#L83`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/nativeplugin/include/nativeplugin.h#L83)

---

## Build System and Toolchain

### Prerequisites

Native plugins require an **LLVM toolchain** capable of generating **PNaCl-compatible bitcode**:

- **Clang** (LLVM C/C++ compiler)
- **llvm-link** (LLVM bitcode linker)
- **opt** (LLVM optimizer)
- **PNaCl sysroot** (standard C library headers/libs)

**Typical Installation:**

```bash
# Ubuntu/Debian
sudo apt-get install clang llvm

# macOS
brew install llvm

# Verify
clang --version
llvm-link --version
opt --version
```

### Environment Setup

Native plugin builds require these environment variables:

```bash
export LLVM_TOOLCHAIN=/usr/bin/      # Path to LLVM tools (clang, opt, etc.)
export NPSDK=/path/to/movian/nativeplugin  # Native plugin SDK path
```

The SDK path should point to `movian/nativeplugin/` in the Movian source tree.

**Source:** [`nativeplugin/plugin.mk#L2-L11`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/nativeplugin/plugin.mk#L2-L11)

### Build Makefile

Native plugins use a standard Makefile template:

**Example `Makefile`:**

```makefile
PROG = mycodec  # Output filename (mycodec.opt)

SRCS = codec.c \
       decoder.c \
       utils.c

include $(NPSDK)/plugin.mk
```

**Source:** [`nativeplugin/plugin.mk`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/nativeplugin/plugin.mk)

### Compilation Pipeline

The build system performs these steps:

**1. Compile C/C++ to LLVM IR (.bc files)**

```bash
clang -emit-llvm -target le32-unknown-nacls -ffreestanding \
      --sysroot=${SYSROOT} -I${NPSDK}/include \
      -c codec.c -o build/codec.bc
```

**Flags:**
- `-emit-llvm` - Generate LLVM bitcode instead of native code
- `-target le32-unknown-nacls` - PNaCl little-endian 32-bit target
- `-ffreestanding` - No hosted environment (no main(), no stdlib assumptions)
- `--sysroot` - Minimal libc headers

**Source:** [`nativeplugin/plugin.mk#L36-L38`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/nativeplugin/plugin.mk#L36-L38)

**2. Link bitcode modules**

```bash
llvm-link -o build/mycodec build/codec.bc build/decoder.bc build/utils.bc
```

**Source:** [`nativeplugin/plugin.mk#L22-L23`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/nativeplugin/plugin.mk#L22-L23)

**3. Optimize bitcode**

```bash
opt -O3 -adce -argpromotion -constmerge -globaldce -globalopt \
    -disable-slp-vectorization -disable-loop-vectorization \
    -o build/mycodec.opt build/mycodec
```

**Optimizations:**
- `-O3` - Aggressive optimization
- `-adce` - Aggressive dead code elimination
- `-globaldce` - Remove unused globals
- `-disable-*-vectorization` - Disable unsupported SIMD

**Source:** [`nativeplugin/plugin.mk#L25-L26`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/nativeplugin/plugin.mk#L25-L26)

**4. Output artifact**

Final file: `build/mycodec.opt` (LLVM bitcode, ready for VMIR)

### Complete Build Example

**Directory structure:**

```
mycodec/
├── Makefile
├── plugin.json
├── codec.c
└── decoder.h
```

**Makefile:**

```makefile
PROG = mycodec
SRCS = codec.c
include $(NPSDK)/plugin.mk
```

**codec.c:**

```c
#include <nativeplugin.h>

int np_file_probe(int fd, const uint8_t *buf0, size_t len,
                  int metadata_handle, const char *url) {
  if(len >= 4 && buf0[0] == 'M' && buf0[1] == 'Y') {
    np_metadata_set(metadata_handle, NP_METADATA_CONTENT_TYPE, NP_CONTENT_AUDIO);
    return 0;
  }
  return -1;
}
```

**Build:**

```bash
export LLVM_TOOLCHAIN=/usr/bin/
export NPSDK=/path/to/movian/nativeplugin
cd mycodec
make
```

**Output:**

```
build/mycodec.opt  # 10-50KB LLVM bitcode
```

### Common Build Issues

#### Issue: `clang: error: unknown target 'le32-unknown-nacls'`

**Solution:** Use older LLVM (3.8-5.0) or install PNaCl SDK

#### Issue: `undefined reference to 'malloc'`

**Solution:** VMIR provides `malloc` - ensure sysroot includes `stdlib.h`

#### Issue: Linker errors for C++ standard library

**Solution:** Native plugins must use `-nostdlib++` and avoid STL (use C99 instead)

---

## Plugin Lifecycle

### Loading Sequence

**1. Plugin manifest discovery**

Movian scans plugin directories for `plugin.json`:

```json
{
  "id": "mycodec",
  "version": "1.0",
  "type": "native",
  "file": "mycodec.opt",
  "memory": 67108864,
  "stack": 131072
}
```

**Source:** [`src/plugins.c`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/plugins.c)

**2. Context creation**

```c
np_context_t *np = calloc(1, sizeof(np_context_t));
np->np_id = strdup("mycodec");
np->np_mem = malloc(memory_size);  // e.g., 64MB
np->np_unit = vmir_create(np->np_mem, memory_size, 0, stack_size, np);
```

**Source:** [`src/np/np.c#L289-L326`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/np/np.c#L289-L326)

**3. Bitcode loading**

```c
buf_t *buf = fa_load("mycodec.opt", ...);
vmir_load(np->np_unit, buf_data(buf), buf_size(buf));
```

**Source:** [`src/np/np.c#L336-L340`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/np/np.c#L336-L340)

**4. Global initializers**

```c
vmir_run(np->np_unit, NULL, 0, NULL);  // Execute __attribute__((constructor))
```

**Source:** [`src/np/np.c#L348`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/np/np.c#L348)

**5. Function resolution**

VMIR resolves symbols like `np_metadata_set`, `malloc`, `memcpy` to host implementations.

**Source:** [`src/np/np.c#L249-L261`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/np/np.c#L249-L261)

### Unloading Sequence

**1. Module unload callbacks**

```c
LIST_FOREACH(m, &np_modules, link) {
  if(m->unload)
    m->unload(np);  // Cleanup registered resources
}
```

**Source:** [`src/np/np.c#L368-L371`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/np/np.c#L368-L371)

**2. Context destruction**

```c
vmir_destroy(np->np_unit);
free(np->np_mem);
free(np);
```

**Source:** [`src/np/np.c#L20-L29`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/np/np.c#L20-L29)

### Memory Management

Native plugins have **isolated heaps**:

- **Heap size:** Configurable in `plugin.json` (default: 64MB)
- **Allocation:** Standard `malloc/free` (provided by VMIR)
- **Stack size:** Configurable (default: 128KB)
- **Leak isolation:** Plugin memory leaks don't affect Movian core

**Memory Limits:**

```c
np_plugin_load("mycodec", "mycodec.opt",
               errbuf, sizeof(errbuf),
               1, 0,
               64 * 1024 * 1024,  // 64MB heap
               128 * 1024);       // 128KB stack
```

**Source:** [`src/np/np.c#L400-L415`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/np/np.c#L400-L415)

---

## Module Registration

Native plugins can export **API modules** that other native plugins or Movian can call.

### NP_MODULE Macro

```c
#define NP_MODULE(nam, fn, ctxini, unloadr)
```

**Parameters:**
- `nam` - Module name (string)
- `fn` - Function table (array of vmir_function_tab_t)
- `ctxini` - Per-context init callback (or NULL)
- `unloadr` - Per-context unload callback (or NULL)

**Source:** [`src/np/np.h#L95-L104`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/np/np.h#L95-L104)

### Example Module

```c
// Function implementations
static int my_add(void *ret, const void *regs, struct ir_unit *iu) {
  int a = vmir_vm_arg32(&regs);
  int b = vmir_vm_arg32(&regs);
  vmir_vm_ret32(ret, a + b);
  return 0;
}

static int my_multiply(void *ret, const void *regs, struct ir_unit *iu) {
  int a = vmir_vm_arg32(&regs);
  int b = vmir_vm_arg32(&regs);
  vmir_vm_ret32(ret, a * b);
  return 0;
}

// Function table
static const vmir_function_tab_t math_funcs[] = {
  {"add",      &my_add},
  {"multiply", &my_multiply},
};

// Register module
NP_MODULE("math", math_funcs, NULL, NULL);
```

**Usage:**

Other native plugins can call:

```c
extern int add(int a, int b);
extern int multiply(int a, int b);

int result = add(5, 3) * multiply(2, 4);
```

### Built-in Modules

Movian provides these host modules:

**"global" module:**

```c
static const vmir_function_tab_t np_funcs[] = {
  {"np_metadata_set",      &np_metadata_set},
  {"time",                 &np_time},
  {"rand",                 &np_rand},
  {"localtime_r",          &np_localtime_r},
};
NP_MODULE("global", np_funcs, NULL, NULL);
```

**Source:** [`src/np/np.c#L230-L237`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/np/np.c#L230-L237)

**Other modules:**

- **"prop"** - Property system access ([`src/np/np_prop.c`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/np/np_prop.c))
- **"fs"** - File system operations ([`src/np/np_fs.c`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/np/np_fs.c))
- **"stats"** - Statistics reporting ([`src/np/np_stats.c`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/np/np_stats.c))

---

## Use Cases

### File Format Probing

Detect file types from binary headers.

**Example: FLAC detector**

```c
#include <nativeplugin.h>
#include <string.h>

int np_file_probe(int fd, const uint8_t *buf0, size_t len,
                  int metadata_handle, const char *url) {
  // Check FLAC signature "fLaC" (4 bytes)
  if(len < 4 || memcmp(buf0, "fLaC", 4) != 0)
    return -1;
  
  np_metadata_set(metadata_handle, NP_METADATA_CONTENT_TYPE, NP_CONTENT_AUDIO);
  
  // Parse metadata blocks
  const uint8_t *p = buf0 + 4;
  while(p + 4 < buf0 + len) {
    int is_last = (p[0] & 0x80) != 0;
    int type = p[0] & 0x7F;
    int size = (p[1] << 16) | (p[2] << 8) | p[3];
    p += 4;
    
    if(type == 4 && size > 0) {  // VORBIS_COMMENT block
      // Parse vorbis comments for title, artist, etc.
      // ...
    }
    
    if(is_last)
      break;
    p += size;
  }
  
  return 0;
}
```

**Performance Note:** Probing runs synchronously on file open - keep it under 10ms.

### Audio Decoder

Implement custom audio codec.

**Example skeleton:**

```c
#include <nativeplugin.h>
#include <stdlib.h>

typedef struct {
  int samplerate;
  int channels;
  // Decoder state...
} decoder_ctx_t;

void *np_audio_open(const char *url, prop_t mp_prop_root) {
  decoder_ctx_t *ctx = malloc(sizeof(decoder_ctx_t));
  
  // Open file, parse headers, initialize decoder
  // ...
  
  ctx->samplerate = 44100;
  ctx->channels = 2;
  
  return ctx;
}

void *np_audio_play(void *opaque, np_audiobuffer_t *nab) {
  decoder_ctx_t *ctx = opaque;
  
  // Decode next frame
  // Fill nab->samples, nab->channels, nab->samplerate
  // ...
  
  nab->timestamp = 0;  // Calculate PTS
  nab->duration = (nab->samples * 1000000LL) / nab->samplerate;
  
  return ctx;  // Return NULL on EOF
}

void np_audio_close(void *opaque) {
  decoder_ctx_t *ctx = opaque;
  // Cleanup
  free(ctx);
}
```

### Custom Protocol Handler

Register a URL scheme (e.g., `myproto://`).

**Implementation uses ECMAScript `faprovider` module:**

```c
// Native side - register prefix
void my_init() {
  np_register_uri_prefix("myproto");
}

// ECMAScript side - implement file operations
var faprovider = require('native/faprovider');

faprovider.register('myproto', {
  open: function(handle, url) {
    // Open connection
    handle.setSize(file_size);
    handle.openRespond(true);
  },
  read: function(handle, root, buffer, size, offset) {
    // Read data into buffer
    var bytesRead = readFromNetwork(buffer, size);
    handle.readRespond(bytesRead);
  },
  close: function(handle, root) {
    // Cleanup
    handle.closeRespond();
  }
});
```

**Source:** [`src/ecmascript/es_faprovider.c`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/ecmascript/es_faprovider.c)

---

## Debugging and Troubleshooting

### Enable Debug Logging

Set environment variable:

```bash
MOVIAN_TRACE=np:DEBUG ./build.linux/movian
```

This logs:
- Plugin loading/unloading
- VMIR function calls
- Memory allocations
- Module registrations

**Source:** [`src/np/np.c#L265-L284`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/np/np.c#L265-L284)

### Common Runtime Errors

#### Error: "Unable to parse bitcode"

**Cause:** Incompatible LLVM version or corrupted .opt file

**Solution:**
- Rebuild with matching LLVM version (3.8-5.0 recommended)
- Check `clang --version` matches Movian's VMIR expectations

#### Error: "Undefined symbol: <function>"

**Cause:** Function not provided by host or wrong signature

**Solution:**
- Check function is exported by an NP_MODULE
- Verify function signature matches (use `vmir_vm_arg32` accessors)

#### Crash: Segmentation fault in plugin

**Cause:** Memory corruption (buffer overflow, use-after-free)

**Solution:**
- Native plugins are **sandboxed** - crashes are contained
- Check VMIR logs for stack traces
- Use AddressSanitizer during development:

```bash
clang -fsanitize=address -g -O0 -c codec.c -o codec.bc
```

### Performance Profiling

**Measure probe time:**

```c
int np_file_probe(int fd, const uint8_t *buf0, size_t len,
                  int metadata_handle, const char *url) {
  int64_t start = arch_get_ts();  // Microseconds
  
  // Probe logic...
  
  int64_t elapsed = arch_get_ts() - start;
  if(elapsed > 10000)
    fprintf(stderr, "WARNING: Probe took %d ms\n", (int)(elapsed / 1000));
  
  return 0;
}
```

**Note:** `arch_get_ts()` may not be available in VMIR - use native logging instead.

### Debugging with LLVM Disassembler

Inspect generated bitcode:

```bash
llvm-dis build/mycodec.opt -o mycodec.ll
less mycodec.ll
```

**Example output:**

```llvm
define i32 @np_file_probe(i32 %fd, i8* %buf0, i64 %len, i32 %metadata_handle, i8* %url) {
entry:
  %0 = icmp ult i64 %len, 4
  br i1 %0, label %return, label %check_sig
  
check_sig:
  %1 = load i8, i8* %buf0
  %2 = icmp eq i8 %1, 102  ; 'f'
  ; ...
}
```

---

## Advanced Topics

### Thread Safety

Native plugins run on Movian's **main thread** by default. For async operations:

**Option 1: Use callbacks**

VMIR supports callback-based async:

```c
void async_operation(void (*callback)(void *arg), void *arg) {
  // Queue work on background thread
  // When done, invoke callback on main thread
}
```

**Option 2: Task queue (from ECMAScript)**

ECMAScript plugins can call native functions via `task_run()`:

```javascript
var native = require('native/mycodec');
setTimeout(function() {
  native.processAsync();
}, 0);
```

### Interoperability with ECMAScript

Native plugins can expose functions callable from ECMAScript:

**1. Register native module**

```c
static int my_encode(void *ret, const void *regs, struct ir_unit *iu) {
  const char *input = vmir_vm_ptr(&regs, iu);
  // Encode...
  char *output = strdup(result);
  vmir_vm_ret32(ret, (uint32_t)output);  // Return pointer
  return 0;
}

static const vmir_function_tab_t codec_funcs[] = {
  {"encode", &my_encode},
};
NP_MODULE("mycodec", codec_funcs, NULL, NULL);
```

**2. Call from ECMAScript**

```javascript
var mycodec = require('native/mycodec');
var encoded = mycodec.encode("Hello World");
```

**Note:** Direct ECMAScript ↔ VMIR calls are **not supported** in standard Movian. Use prop system for data exchange.

### Security Considerations

**Sandboxing:**
- VMIR provides **memory isolation** - plugins can't corrupt Movian's heap
- **No direct syscalls** - all I/O goes through host APIs
- **File access restrictions** - plugins can only access allowed paths

**Attack Surface:**
- Buffer overflows in native code are contained by VMIR
- Malicious plugins can exhaust memory (limited by heap size)
- Host API bugs may expose vulnerabilities

**Best Practices:**
- Validate all inputs from file headers
- Limit probe execution time (timeout after 100ms)
- Use `malloc` bounds checking during development

### Performance Optimization

**1. Minimize allocations**

```c
// Bad: Allocate per-call
char *process() {
  char *buf = malloc(1024);
  // ...
  return buf;
}

// Good: Reuse buffer
static char buf[1024];
char *process() {
  // ...
  return buf;
}
```

**2. Avoid string operations**

```c
// Bad: strcmp in hot loop
for(int i = 0; i < 1000000; i++) {
  if(strcmp(type, "audio") == 0) { }
}

// Good: Integer comparison
#define TYPE_AUDIO 1
for(int i = 0; i < 1000000; i++) {
  if(type == TYPE_AUDIO) { }
}
```

**3. Batch prop updates**

```c
// Bad: Multiple prop sets
for(int i = 0; i < 100; i++) {
  prop_t p = np_prop_create(root, items[i].name);
  np_prop_set(p, NULL, NP_PROP_SET_STRING, items[i].value);
  np_prop_release(p);
}

// Good: Create hierarchy, then populate
prop_t root = np_prop_create_root();
for(int i = 0; i < 100; i++) {
  prop_t p = np_prop_create(root, items[i].name);
  np_prop_set(p, NULL, NP_PROP_SET_STRING, items[i].value);
  np_prop_release(p);
}
np_prop_append(parent, root);
```

---

## Summary

Native plugins provide **high-performance extensions** to Movian using compiled C/C++ code:

- **Architecture:** LLVM bitcode running in VMIR sandbox
- **API:** File probing, audio decoding, prop system, protocol handlers
- **Build:** Clang → LLVM IR → Optimizer → .opt file
- **Lifecycle:** Load → Init → Function calls → Unload
- **Safety:** Isolated memory, limited syscalls, contained crashes

**Next Steps:**

- Review [`nativeplugin/include/nativeplugin.h`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/nativeplugin/include/nativeplugin.h) for complete API
- Study [`src/np/np.c`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/src/np/np.c) for loading internals
- Examine [`nativeplugin/plugin.mk`](https://github.com/andoma/movian/blob/1f76b9ad66335477b10ebc23b8a687a25407a3d9/nativeplugin/plugin.mk) for build system
- Explore VMIR source in `ext/vmir/` for VM internals

---

**Document Status:** Complete  
**API Coverage:** Core native plugin system  
**Last Updated:** 2024
