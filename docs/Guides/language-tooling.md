# Movian language tooling

`movian-lsp` supplies diagnostics, hover information, document symbols, and
`#include`/`#import` definitions for GLW `.view` files. It also supplies
JavaScript syntax diagnostics and `require()` definitions for `.js` files. It
runs from a Movian checkout, so each configuration below assumes the editor
and checkout share a Linux or WSL environment.

## Prepare the checkout

From the repository root, build the analyzer that the server delegates syntax
and semantic checks to, then run the preflight:

```sh
./support/configure-linux-debug.sh
make BUILD=debug -j$(nproc) movian-analyze
./support/devtools/mdev lsp doctor
```

The doctor requires Python 3.10 or newer, confirms that
`generated/movian-metadata.json` is fresh, performs a framed stdio LSP
`initialize`/`shutdown` exchange, and probes JavaScript diagnostics plus a
`require('movian/page')` definition. Re-run it after changing the build or
metadata inputs.

## JavaScript type declarations (movian-api.d.ts)

`generated/movian-api.d.ts` provides TypeScript declarations for Movian's
ES5.1 CommonJS plugin API. All types are `any` in v1; the honest signal is
exact arity encoded via `@arity` JSDoc tags on native ES_MODULE functions.
Constructor-style CommonJS exports carry construct signatures derived from
the same metadata.
Other CommonJS exports remain `any` because the metadata does not distinguish
callable exports from property values.

The file is generated from the same `js.*` metadata as
`movian-metadata.json` by `support/devtools/metadata/gen.py`:

```sh
python3 support/devtools/metadata/gen.py
```

This writes both `generated/movian-metadata.json` and
`generated/movian-api.d.ts` in one pass. Drift-check covers both artifacts:

```sh
python3 support/devtools/metadata/gen.py --check
```

### Editor wiring

To enable editor completion and hover for `require('movian/page')` etc.,
add a `jsconfig.json` to the project root (or copy the worked example from
`support/devtools/lsp/editors/jsconfig.json`):

```json
{
  "compilerOptions": {
    "target": "es5",
    "module": "commonjs",
    "typeRoots": ["./generated"]
  },
  "include": [
    "**/*.js",
    "generated/**/*.d.ts"
  ],
  "exclude": [
    "node_modules",
    "build.*"
  ]
}
```

With this configuration, VS Code's built-in TypeScript language service
resolves `require('movian/page')` to the generated declarations and
provides completion for exported functions. No tsc or Node.js is required
for the declarations to work; the optional tsc validation lane below is
purely for catching regressions in the generated file.

### Optional tsc validation

If `tsc` is already on PATH, you can validate the generated file:

```sh
if command -v tsc >/dev/null 2>&1; then
  tsc --noEmit --strict false generated/movian-api.d.ts
fi
```

If `tsc` is not installed, this step is silently skipped. The absence of
`tsc` must never fail any repository guard.

## JavaScript support and limits

For `.js` documents inside the workspace or flat-skin root, `movian-lsp` sends
the current editor buffer to `movian-analyze --js` after open and save
notifications; full-text changes update the saved buffer but do not run the
analyzer until save. The analyzer compiles without executing the
file using Movian's vendored Duktape, reports at most the first syntax error,
and labels that diagnostic source `duktape`. Its accepted JavaScript syntax is
therefore the syntax supported by Movian's Duktape, not the syntax of a current
browser or Node.js runtime.

Definition works only when the cursor is on the quoted string literal of a
direct `require('module')` call. Generated `js.modules` metadata resolves
Movian IDs such as `movian/page` and `native/fs`; `./` and `../` targets resolve
only to existing `.js` files that remain inside the workspace or skin root.
Dynamic arguments, escaped string literals, package discovery, JavaScript AST
features, completion, type declarations, and plugin-manifest validation are
outside this server's scope.

The project `.lsp.json` associates `movian-lsp` with both `.view` and `.js`.
For other editors, add JavaScript to the client-side file-type association if
you want this server alongside (or instead of) another JavaScript language
server.

## Oh My Pi (OMP)

OMP v17 discovers a project-root `.lsp.json` before its lower-priority LSP
locations. This checkout includes a ready-to-use template at
[`/.lsp.json`](../../.lsp.json): keep it at the root of the Movian checkout,
then open that checkout with `omp` and open a `.view` or `.js` file. The
relative server path is intentionally resolved from the project root.

```json
{
  "servers": {
    "movian-lsp": {
      "command": "python3",
      "args": ["support/devtools/movian-lsp", "--stdio"],
      "fileTypes": [".view", ".js"],
      "rootMarkers": [".git"]
    }
  }
}
```

Use OMP and the checkout in the same WSL distribution; this template does not
provide Windows-to-WSL URI mapping. OMP's current [LSP configuration
guide](https://github.com/can1357/oh-my-pi/blob/0f9fceeea483caad531a32b050ac38558516cb5c/docs/lsp-config.md)
documents the project config precedence and schema.

## Optional — requires a third-party generic client (VS Code Remote WSL)

VS Code does not attach an arbitrary stdio language server through settings
alone, so this optional path uses the maintained third-party [Generic LSP
Proxy](https://marketplace.visualstudio.com/items?itemName=mjmorales.generic-lsp-proxy)
extension (`mjmorales.generic-lsp-proxy`). At the 2026-07-17 check it had a
2026-06-28 v2.1.1 Marketplace publication and its public repository had no
open issues. This is not a Movian extension; review that extension's current
maintenance status before relying on it.

1. Open this checkout with **Remote - WSL**, trust the workspace, and install
   Generic LSP Proxy into the WSL remote extension host (not only the local
   Windows host).
2. From the repository root in WSL, run
   `realpath support/devtools/movian-lsp`. Copy the resulting absolute WSL
   path into the proxy configuration. The proxy does not set its server
   process's working directory, so a relative script path is not portable.
3. Create `.vscode/settings.json` with this settings snippet:

   ```json
   {
     "genericLspProxy.configPath": ".vscode/lsp-proxy.json"
   }
   ```

4. Create `.vscode/lsp-proxy.json` with the following object, substituting
   your path from step 2:

   ```json
   {
     "languageId": "movian-view",
     "command": "python3",
     "args": [
       "/absolute/WSL/path/to/movian/support/devtools/movian-lsp",
       "--stdio"
     ],
     "fileExtensions": [".view"],
     "transport": "stdio"
   }
   ```

The proxy starts this configuration by `.view` extension even if VS Code shows
the file as Plain Text. Its [configuration documentation](https://github.com/mjmorales/vscode-generic-lsp-proxy#configuration)
describes the workspace config file and fields. Movian does not ship or package
a VS Code extension.

## Neovim

Add this to `init.lua` (or an equivalent Lua file). It registers `.view` and
starts one `movian-lsp` client rooted at the checkout containing `.git`:

```lua
vim.filetype.add({ extension = { view = "movian-view" } })

vim.api.nvim_create_autocmd("FileType", {
  pattern = "movian-view",
  callback = function(event)
    local root = vim.fs.root(event.buf, { ".git" })
    if not root then
      return
    end
    vim.lsp.start({
      name = "movian-lsp",
      cmd = { "python3", root .. "/support/devtools/movian-lsp", "--stdio" },
      root_dir = root,
    }, { bufnr = event.buf })
  end,
})
```

This uses Neovim's built-in [`vim.lsp.start`](https://neovim.io/doc/user/lsp.html#vim.lsp.start())
API; no Neovim LSP plugin is required.

## Helix

Create `.helix/languages.toml` in the checkout (or merge the same tables into
your user `languages.toml`). First obtain the absolute path with
`realpath support/devtools/movian-lsp`, then substitute it below:

```toml
[language-server.movian-lsp]
command = "python3"
args = ["/absolute/WSL/or/Linux/path/to/movian/support/devtools/movian-lsp", "--stdio"]

[[language]]
name = "movian-view"
scope = "source.movian-view"
file-types = ["view"]
roots = [".git"]
language-servers = ["movian-lsp"]
comment-tokens = "//"
block-comment-tokens = { start = "/*", end = "*/" }
```

Helix merges a project's `.helix/languages.toml` with user and built-in
configuration; its [language configuration documentation](https://docs.helix-editor.com/languages.html)
defines the `language-server`, `file-types`, `roots`, and `language-servers`
fields used here.
