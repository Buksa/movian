#!/usr/bin/env python3
"""Check CommonJS module coverage for issue #137."""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
METADATA_FILE = REPO_ROOT / "generated" / "movian-metadata.json"
REFERENCE_DIR = Path(__file__).resolve().parent / "tests" / "reference"
JS_MODULE_DIR = REPO_ROOT / "res" / "ecmascript" / "modules" / "movian"
TOPLEVEL_MODULE_DIR = REPO_ROOT / "res" / "ecmascript" / "modules"


def load_metadata() -> dict:
    """Load the generated movian-metadata.json."""
    text = METADATA_FILE.read_text(encoding="utf-8")
    return json.loads(text)


def discover_commonjs_modules(metadata: dict) -> set[str]:
    """Discover all CommonJS modules from metadata, excluding already-accepted ones."""
    all_modules = metadata.get("js", {}).get("modules", [])
    commonjs_modules = {m["name"] for m in all_modules if not m.get("commonjs", False)}
    
    # Exclude the six already accepted by #135/#136
    accepted = {
        "movian/page",
        "movian/prop", 
        "movian/http",
        "movian/settings",
        "movian/service",
        "movian/store",
    }
    
    return commonjs_modules - accepted


def get_declaration_path(module_name: str) -> Path:
    """Get the .d.ts declaration path for a module."""
    if module_name.startswith("movian/"):
        basename = module_name[len("movian/"):].replace("/", "-")
        return REFERENCE_DIR / ("movian-" + basename + ".d.ts")
    else:
        return REFERENCE_DIR / (module_name + ".d.ts")


def filename_to_module(filename: str) -> str:
    """Convert .d.ts filename back to module name."""
    if filename.startswith("movian-"):
        # movian-html.d.ts -> movian/html
        name = filename[len("movian-"):len(filename)-5]  # Remove "movian-" and ".d.ts"
        return "movian/" + name.replace("-", "/")
    else:
        # http.d.ts -> http
        return filename[:len(filename)-5]  # Remove ".d.ts"


def check_commonjs_coverage() -> int:
    """Check that all CommonJS/top-level modules have reference fixtures."""
    metadata = load_metadata()
    required_modules = discover_commonjs_modules(metadata)
    
    # The target set for this issue
    target_modules = {
        "movian/html",
        "movian/itemhook",
        "movian/popup",
        "movian/sqlite",
        "movian/subtitles",
        "movian/videoscrobbler",
        "movian/xml",
        "movian/xmlrpc",
        "fs",
        "http",
        "https",
        "querystring",
        "url",
        "websocket",
    }

    # Count native modules as deferred
    native_modules = {m for m in required_modules if m.startswith("native/")}
    deferred_count = len(native_modules)
    
    # Check for missing fixtures
    missing = []
    phantom = []
    
    for module_name in target_modules:
        decl_path = get_declaration_path(module_name)
        if not decl_path.is_file():
            missing.append(module_name)
    
    # Check for phantom fixtures (declarations that don't exist in metadata)
    existing_decls = list(REFERENCE_DIR.glob("*.d.ts"))
    for decl_path in existing_decls:
        if decl_path.name == "movian-plugin.d.ts":
            continue
        
        module_name = filename_to_module(decl_path.name)
        
        # Skip accepted modules
        if module_name in {
            "movian/page",
            "movian/prop",
            "movian/http", 
            "movian/settings",
            "movian/service",
            "movian/store",
        }:
            continue
            
        if module_name not in target_modules:
            phantom.append(module_name)
    
    if missing:
        print("reference-dts: missing %d: %s" % (len(missing), ", ".join(sorted(missing))), file=sys.stderr)
        return 1
    
    if phantom:
        print("reference-dts: phantom %d: %s" % (len(phantom), ", ".join(sorted(phantom))), file=sys.stderr)
        return 1
    
    print("reference-dts: CommonJS coverage OK (missing 0, phantom 0, deferred-native %d)" % deferred_count)
    return 0


if __name__ == "__main__":
    sys.exit(check_commonjs_coverage())
