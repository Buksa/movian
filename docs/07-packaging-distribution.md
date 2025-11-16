# Packaging & Distribution

This chapter covers the complete process of packaging, versioning, and distributing Movian plugins, including manifest requirements, repository integration, and deployment strategies.

## Table of Contents

1. [Plugin Package Structure](#plugin-package-structure)
2. [Manifest Requirements](#manifest-requirements)
3. [Version Management](#version-management)
4. [Creating Plugin Packages](#creating-plugin-packages)
5. [Plugin Repositories](#plugin-repositories)
6. [Distribution Methods](#distribution-methods)
7. [Security Considerations](#security-considerations)
8. [Repository Management](#repository-management)

## Plugin Package Structure

Movian plugins are distributed as ZIP archives with a specific internal structure:

```
my-plugin.zip
├── plugin.json          # Required: Plugin manifest
├── main.js              # Main plugin file
├── lib/                 # Optional: Shared libraries
│   ├── api.js
│   └── utils.js
├── pages/               # Optional: Page handlers
│   ├── home.js
│   └── search.js
├── assets/              # Optional: Static assets
│   ├── icon.png
│   └── images/
└── locales/             # Optional: Localization files
    ├── en.json
    └── sv.json
```

### Required Files

**plugin.json** (always required):
```json
{
  "type": "ecmascript",
  "id": "com.example.myplugin",
  "version": "1.0.0",
  "title": "My Plugin",
  "file": "main.js"
}
```

### Optional Files

- **Icon**: `icon.png` or `icon.jpg` (recommended 256x256 pixels)
- **Localization**: `locales/*.json` files for multi-language support
- **Views**: `views/` directory for custom UI views (for GLW plugins)
- **Documentation**: `README.md` for plugin documentation

## Manifest Requirements

The `plugin.json` manifest file defines plugin metadata and compatibility:

### Basic Fields

```json
{
  "type": "ecmascript",
  "id": "com.example.myplugin",
  "version": "1.0.0",
  "title": "My Plugin",
  "description": "A comprehensive plugin description",
  "synopsis": "Short one-line description",
  "author": "Your Name",
  "file": "main.js",
  "icon": "icon.png",
  "category": "video"
}
```

### Compatibility Fields

```json
{
  "showtimeVersion": "5.0.500",
  "apiversion": 2,
  "build": "any",
  "arch": "any"
}
```

### Advanced Fields

```json
{
  "url": "https://github.com/user/myplugin",
  "copyright": "Copyright 2024 Your Name",
  "license": "MIT",
  "control": {
    "fileMagic": [
      {
        "data": "ID3",
        "mask": "ffff",
        "offset": 0,
        "description": "MP3 files"
      }
    ],
    "uriprefixes": [
      "mystream://"
    ]
  },
  "czech": "Název pluginu",
  "swedish": "Mitt plugin"
}
```

### Field Descriptions

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | Yes | Plugin type: "ecmascript", "bitcode", "views" |
| `id` | string | Yes | Unique plugin identifier (reverse domain notation) |
| `version` | string | Yes | Semantic version (e.g., "1.2.3") |
| `title` | string | Yes | Display name for the plugin |
| `file` | string | Yes | Main plugin file path |
| `showtimeVersion` | string | No | Minimum Movian version required |
| `category` | string | No | Plugin category: "tv", "video", "music", "cloud", "other" |
| `description` | string | No | Long description (supports rich text) |
| `synopsis` | string | No | Short description |
| `author` | string | No | Plugin author |
| `icon` | string | No | Icon file path |
| `url` | string | No | Plugin website or repository URL |

## Version Management

### Semantic Versioning

Movian follows semantic versioning for plugins:

```
MAJOR.MINOR.PATCH[-PRERELEASE][+BUILD]
```

Examples:
- `1.0.0` - First stable release
- `1.2.3` - Patch release
- `2.0.0-beta.1` - Beta release
- `1.5.0+build.123` - Development build

### Version Comparison

Movian uses integer-based version comparison:

```c
// From src/version.c:32-44
uint32_t
parse_version_int(const char *str)
{
  int major = 0;
  int minor = 0;
  int commit = 0;
  sscanf(str, "%d.%d.%d", &major, &minor, &commit);

  return
    major * 10000000 +
    minor *   100000 +
    commit;
}
```

### Compatibility Checking

The plugin system checks version compatibility using `showtimeVersion`:

```c
// From src/plugins.c:304-306
int version_dep_ok =
  pl->pl_app_min_version == NULL ||
  parse_version_int(pl->pl_app_min_version) <=
  app_get_version_int();
```

If the plugin requires a newer Movian version:
```c
// From src/plugins.c:313-317
if(!version_dep_ok) {
  status = _("Not installable");
  prop_set(pl->pl_status, "minver", PROP_SET_STRING,
           pl->pl_app_min_version);
}
```

### Version Strategy Guidelines

1. **Major Version**: Breaking changes or API changes
2. **Minor Version**: New features, backward compatible
3. **Patch Version**: Bug fixes, security updates
4. **Prerelease**: Alpha/beta testing versions
5. **Build Metadata**: Development builds, CI builds

## Creating Plugin Packages

### Manual Package Creation

```bash
# Create plugin directory structure
mkdir my-plugin
cd my-plugin

# Create plugin.json
cat > plugin.json << EOF
{
  "type": "ecmascript",
  "id": "com.example.myplugin",
  "version": "1.0.0",
  "title": "My Plugin",
  "file": "main.js",
  "category": "video"
}
EOF

# Create main plugin file
cat > main.js << EOF
var page = require('movian/page');
var service = require('movian/service');

service.create("My Plugin", "myplugin:start", "video");

new page.Route("myplugin:start", function(page) {
  page.type = "directory";
  page.metadata.title = "My Plugin";
  
  page.appendItem("http://example.com/video.mp4", "video", {
    title: "Sample Video"
  });
});
EOF

# Create ZIP package
cd ..
zip -r my-plugin.zip my-plugin/
```

### Automated Package Creation

Create a build script for automated packaging:

```bash
#!/bin/bash
# build.sh - Plugin build script

PLUGIN_NAME="my-plugin"
PLUGIN_ID="com.example.myplugin"
VERSION="1.0.0"

# Clean previous build
rm -rf ${PLUGIN_NAME} ${PLUGIN_NAME}.zip

# Create package directory
mkdir -p ${PLUGIN_NAME}/{lib,pages,assets,locales}

# Copy source files
cp plugin.json ${PLUGIN_NAME}/
cp main.js ${PLUGIN_NAME}/
cp -r lib/* ${PLUGIN_NAME}/lib/ 2>/dev/null || true
cp -r pages/* ${PLUGIN_NAME}/pages/ 2>/dev/null || true
cp -r assets/* ${PLUGIN_NAME}/assets/ 2>/dev/null || true
cp -r locales/* ${PLUGIN_NAME}/locales/ 2>/dev/null || true

# Update version in manifest
sed -i.bak "s/\"version\": \".*\"/\"version\": \"${VERSION}\"/" ${PLUGIN_NAME}/plugin.json

# Create ZIP package
zip -r ${PLUGIN_NAME}-${VERSION}.zip ${PLUGIN_NAME}/

# Generate checksum
sha256sum ${PLUGIN_NAME}-${VERSION}.zip > ${PLUGIN_NAME}-${VERSION}.zip.sha256

echo "Package created: ${PLUGIN_NAME}-${VERSION}.zip"
echo "Checksum: $(cat ${PLUGIN_NAME}-${VERSION}.zip.sha256)"
```

### Package Validation

Validate your plugin package before distribution:

```bash
#!/bin/bash
# validate.sh - Plugin validation script

PLUGIN_ZIP="$1"

if [ -z "$PLUGIN_ZIP" ]; then
  echo "Usage: $0 <plugin.zip>"
  exit 1
fi

# Check if file exists
if [ ! -f "$PLUGIN_ZIP" ]; then
  echo "Error: Plugin file not found: $PLUGIN_ZIP"
  exit 1
fi

# Check ZIP format
if ! file "$PLUGIN_ZIP" | grep -q "Zip archive"; then
  echo "Error: Not a valid ZIP file"
  exit 1
fi

# Extract and validate
TEMP_DIR=$(mktemp -d)
unzip -q "$PLUGIN_ZIP" -d "$TEMP_DIR"

# Check for required files
if [ ! -f "$TEMP_DIR/plugin.json" ]; then
  echo "Error: plugin.json not found"
  rm -rf "$TEMP_DIR"
  exit 1
fi

# Validate JSON syntax
if ! python3 -m json.tool "$TEMP_DIR/plugin.json" > /dev/null 2>&1; then
  echo "Error: Invalid JSON in plugin.json"
  rm -rf "$TEMP_DIR"
  exit 1
fi

# Check required fields
REQUIRED_FIELDS=("type" "id" "version" "title" "file")
for field in "${REQUIRED_FIELDS[@]}"; do
  if ! jq -e ".$field" "$TEMP_DIR/plugin.json" > /dev/null; then
    echo "Error: Required field '$field' missing from plugin.json"
    rm -rf "$TEMP_DIR"
    exit 1
  fi
done

# Check if main file exists
MAIN_FILE=$(jq -r '.file' "$TEMP_DIR/plugin.json")
if [ ! -f "$TEMP_DIR/$MAIN_FILE" ]; then
  echo "Error: Main file '$MAIN_FILE' not found in package"
  rm -rf "$TEMP_DIR"
  exit 1
fi

echo "✓ Plugin validation passed"
rm -rf "$TEMP_DIR"
```

## Plugin Repositories

### Repository Format

Plugin repositories are JSON files listing available plugins:

```json
{
  "title": "My Plugin Repository",
  "url": "https://example.com/plugins/repo.json",
  "plugins": [
    {
      "id": "com.example.myplugin",
      "version": "1.0.0",
      "title": "My Plugin",
      "description": "A great plugin",
      "synopsis": "Short description",
      "author": "Developer Name",
      "category": "video",
      "icon": "https://example.com/plugins/myplugin/icon.png",
      "package": "https://example.com/plugins/myplugin-1.0.0.zip",
      "showtimeVersion": "5.0.500",
      "changelog": "Initial release"
    }
  ]
}
```

### Repository Structure

```
https://example.com/plugins/
├── repo.json           # Repository index
├── myplugin-1.0.0.zip  # Plugin package
├── myplugin-1.0.0.zip.sha256  # Checksum
└── icons/              # Plugin icons
    └── myplugin.png
```

### Creating a Repository

```bash
#!/bin/bash
# create-repo.sh - Repository creation script

REPO_DIR="web-repo"
REPO_URL="https://example.com/plugins"

mkdir -p "$REPO_DIR"

# Start with empty repository
cat > "$REPO_DIR/repo.json" << EOF
{
  "title": "My Plugin Repository",
  "url": "$REPO_URL/repo.json",
  "plugins": []
}
EOF

# Function to add plugin to repository
add_plugin() {
  local plugin_zip="$1"
  local plugin_info="$2"
  
  # Copy plugin to repository
  cp "$plugin_zip" "$REPO_DIR/"
  
  # Generate checksum
  cd "$REPO_DIR"
  sha256sum "$(basename "$plugin_zip")" > "$(basename "$plugin_zip").sha256"
  cd ..
  
  # Add to repository JSON
  jq --argjson plugin "$plugin_info" '.plugins += [$plugin]' "$REPO_DIR/repo.json" > "$REPO_DIR/repo.json.tmp"
  mv "$REPO_DIR/repo.json.tmp" "$REPO_DIR/repo.json"
}

# Example: Add a plugin
PLUGIN_INFO='{
  "id": "com.example.myplugin",
  "version": "1.0.0",
  "title": "My Plugin",
  "description": "A great plugin for Movian",
  "synopsis": "Short description",
  "author": "Developer Name",
  "category": "video",
  "package": "'$REPO_URL'/myplugin-1.0.0.zip",
  "showtimeVersion": "5.0.500"
}'

add_plugin "my-plugin-1.0.0.zip" "$PLUGIN_INFO"

echo "Repository created in $REPO_DIR"
```

## Distribution Methods

### Direct Installation

Users can install plugins directly from ZIP files:

```bash
# Method 1: Command line
./build.linux/movian --install-plugin /path/to/plugin.zip

# Method 2: Through Movian UI
# Navigate to Plugins → Install from file
```

The installation process is handled by `plugin_install()` in `src/plugins.c`:

```c
// From src/plugins.c:1512-1521
static int
plugin_install(plugin_t *pl, const char *package)
{
  // Download plugin package
  buf_t *b = fa_load(package, FA_LOAD_ERRBUF(errbuf, sizeof(errbuf)), NULL);
  
  // Validate ZIP format
  if(b->b_size < 4 ||
     buf[0] != 0x50 || buf[1] != 0x4b || 
     buf[2] != 0x03 || buf[3] != 0x04) {
    // Not a valid ZIP
    return -1;
  }
  
  // Extract and install
  // ... installation logic
}
```

### Repository Subscription

Users can subscribe to plugin repositories:

```bash
# Add repository via command line
./build.linux/movian --plugin-repo https://example.com/plugins/repo.json

# Or through Movian UI
# Navigate to Plugins → Browse available plugins → Subscribe to repository
```

Repository management is handled in `src/plugins.c`:

```c
// From src/plugins.c:1342-1350
static void
plugin_repo_create(const char *url, const char *title, int load)
{
  plugin_repo_t *pr = calloc(1, sizeof(plugin_repo_t));
  LIST_INSERT_HEAD(&plugin_repos, pr, pr_link);
  
  pr->pr_url = strdup(url);
  // ... repository setup
}
```

### Automatic Updates

Plugins can be configured for automatic updates:

```javascript
// In plugin.json
{
  "autoUpgrade": true
}

// Or through Movian settings
// Settings → General → Plugins → Automatically upgrade plugins
```

The auto-upgrade logic is in `plugin_autoupgrade()`:

```c
// From src/plugins.c:1020-1034
static void
plugin_autoupgrade(void)
{
  plugin_t *pl;
  
  LIST_FOREACH(pl, &plugins, pl_link) {
    if(!pl->pl_can_upgrade || !pl->pl_auto_upgrade)
      continue;
    if(plugin_install(pl, NULL))
      continue;
    notify_add(NULL, NOTIFY_INFO, NULL, 5,
               _("Upgraded plugin %s to version %s"), pl->pl_title,
               pl->pl_inst_ver);
  }
}
```

## Security Considerations

### Code Signing

While Movian doesn't enforce code signing, consider these practices:

1. **Checksum Verification**: Always provide SHA256 checksums
2. **HTTPS Distribution**: Serve plugins over HTTPS
3. **Version Validation**: Validate `showtimeVersion` requirements
4. **Input Sanitization**: Sanitize all external inputs

### Plugin Sandboxing

Movian plugins run in a sandboxed environment:

```c
// From src/plugins.c:688-696
if(!strcmp(type, "bitcode")) {
  // Native plugins with memory limits
  int memory_size = htsmsg_get_u32_or_default(ctrl, "memory-size", 4096);
  int stack_size  = htsmsg_get_u32_or_default(ctrl, "stack-size", 64);
  
  r = np_plugin_load(pl->pl_fqid, fullpath, errbuf, errlen, version, 0,
                     memory_size * 1024, stack_size * 1024);
}
```

### Best Practices

1. **Validate External Data**: Always validate API responses
2. **Use HTTPS**: Prefer HTTPS for all external requests
3. **Rate Limiting**: Implement rate limiting for API calls
4. **Error Handling**: Handle network errors gracefully
5. **User Privacy**: Don't collect unnecessary user data

## Repository Management

### Repository Updates

Update repositories programmatically:

```bash
#!/bin/bash
# update-repo.sh - Repository update script

REPO_DIR="web-repo"
REPO_URL="https://example.com/plugins"

# Function to update plugin version
update_plugin() {
  local plugin_id="$1"
  local new_version="$2"
  local plugin_zip="$3"
  
  # Update repository JSON
  jq "(.plugins[] | select(.id == \"$plugin_id\")) |= (.version = \"$new_version\" | .package = \"$REPO_URL/$(basename $plugin_zip)\")" \
     "$REPO_DIR/repo.json" > "$REPO_DIR/repo.json.tmp"
  mv "$REPO_DIR/repo.json.tmp" "$REPO_DIR/repo.json"
  
  # Copy new plugin
  cp "$plugin_zip" "$REPO_DIR/"
  cd "$REPO_DIR"
  sha256sum "$(basename "$plugin_zip")" > "$(basename "$plugin_zip").sha256"
  cd ..
}

# Example usage
update_plugin "com.example.myplugin" "1.1.0" "my-plugin-1.1.0.zip"
```

### Repository Statistics

Track repository usage:

```json
{
  "title": "My Plugin Repository",
  "url": "https://example.com/plugins/repo.json",
  "updated": "2024-01-15T10:00:00Z",
  "plugins": [...],
  "statistics": {
    "totalPlugins": 25,
    "totalDownloads": 15420,
    "categories": {
      "video": 15,
      "music": 5,
      "tv": 3,
      "other": 2
    }
  }
}
```

### Multiple Repositories

Support multiple plugin repositories:

```bash
# Add multiple repositories
./build.linux/movian \
  --plugin-repo https://official.movian.tv/plugins/repo.json \
  --plugin-repo https://community.movian.tv/plugins/repo.json
```

Repository configuration is stored in Movian's settings:

```c
// From src/plugins.c:1201-1211
if((m = htsmsg_store_load("pluginrepos")) != NULL) {
  htsmsg_field_t *f;
  HTSMSG_FOREACH(f, m) {
    htsmsg_t *e;
    if((e = htsmsg_get_map_by_field(f)) == NULL)
      continue;
    const char *url = htsmsg_get_str(e, "url");
    plugin_repo_create(url, NULL, 0);
  }
}
```

## Advanced Packaging

### Conditional Features

Package different variants:

```json
{
  "type": "ecmascript",
  "id": "com.example.myplugin",
  "version": "1.0.0",
  "title": "My Plugin",
  "file": "main.js",
  "variants": {
    "lite": {
      "file": "main-lite.js",
      "description": "Lightweight version"
    },
    "full": {
      "file": "main-full.js",
      "description": "Full-featured version"
    }
  }
}
```

### Plugin Dependencies

Declare plugin dependencies (future feature):

```json
{
  "dependencies": [
    {
      "id": "com.example.common",
      "version": ">=1.0.0"
    }
  ]
}
```

### Plugin Views

Include custom UI views:

```json
{
  "type": "views",
  "views": [
    {
      "name": "myview",
      "file": "views/myview.view"
    }
  ]
}
```

This comprehensive packaging and distribution guide enables you to create professional, secure, and maintainable Movian plugins that can be easily distributed to users through multiple channels.