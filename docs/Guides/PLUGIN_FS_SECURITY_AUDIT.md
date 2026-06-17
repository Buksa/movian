# Plugin Filesystem Security Audit

This note audits plugin-facing filesystem boundaries after SMB2 gained writable
fileaccess operations.

## Scope

The audited path is JavaScript plugin code calling `native/fs` or the CommonJS
`fs` wrapper, then reaching generic fileaccess protocols such as local `file://`,
`smb://`, `smb2://`, and `ntfs://`.

This does not cover OS process sandboxing. JavaScript plugins run inside the
Movian process; the plugin sandbox here is an application-level URL/path ACL.

## Current Enforcement Points

- `src/ecmascript/es_fs.c:get_filename()` is the main plugin filesystem ACL.
  It rejects filenames containing parent references and allows access only when
  the requested URL begins with `ec->ec_storage` or `ec->ec_path`.
- `ec->ec_storage` is created per plugin under
  `gconf.persistent_path/plugins/<plugin-id>`.
- `ec->ec_path` is the parent directory of the plugin entry file. Default
  plugins can therefore access their persistent storage and their load
  directory.
- `entitlements.bypassFileACLRead` and `entitlements.bypassFileACLWrite` in
  `plugin.json` bypass the read or write path ACL for that plugin.
- `--bypass-ecmascript-acl` disables JavaScript filesystem ACL checks globally.
- Command-line `--ecmascript` scripts are created with read and write bypass
  flags; treat them as a development/admin surface, not as sandboxed plugins.

## SMB And SMB2 Write Surface

SMB1 currently exposes destructive operations (`unlink`, `rmdir`) but not
`fap_write`, so `native/fs.open("smb://...", "w")` is rejected by
`fa_open_ex()` as a filesystem without write support.

SMB2 now exposes:

- `fap_write`
- `fap_ftruncate`
- `fap_unlink`
- `fap_rmdir`
- `fap_rename`
- `fap_makedir`
- `fap_fsinfo`

That means an SMB2 URL is writable whenever JavaScript filesystem ACL checks
allow the URL to reach `fa_open_ex()` or the path operation wrappers.

For normal installed plugins without bypass entitlements, `smb2://...` is not
inside the default local storage path, so direct SMB2 writes are blocked. The
exception is a plugin loaded from an SMB2 URL: in that case the plugin load
directory can itself be an SMB2 URL, and writes under that directory match
`ec->ec_path`.

For plugins with `entitlements.bypassFileACLWrite`, SMB2 is writable anywhere
the share credentials and server permissions allow. This is consistent with the
current broad meaning of the entitlement, but it is a wider practical effect
now that SMB2 implements writes.

## Findings

## Runtime Smoke Evidence

A temporary development plugin with no `entitlements` block was tested against a
reserved SMB2 URL (`smb2://192.0.2.10/...`) using an isolated profile. The URL
uses a non-routable documentation address so a failed ACL cannot damage a real
share; a passing ACL blocks before any SMB2 connection is attempted.

The plugin route exercised these destructive/write paths:

- `native/fs.open(url, "w")`
- `native/fs.open(url, "a")`
- `fs.writeFileSync(url, data)`
- `native/fs.mkdirs(url)`
- `fs.mkdirSync(url)`
- `native/fs.unlink(url)`
- `fs.unlinkSync(url)`
- `native/fs.rmdir(url)`
- `fs.rmdirSync(url)`
- `native/fs.rename(smb2Old, smb2New)`
- `native/fs.rename(scopedLocal, smb2New)`
- `native/fs.open(url, "r")` followed by `native/fs.ftruncate(fd, 0)`

All destructive/write SMB2 cases threw:

```text
Bad filename smb2://... -- Access not allowed
```

The stack for each denial reached `src/ecmascript/es_fs.c:get_filename()` before
fileaccess opened the SMB2 URL. The log contained no SMB2 connection, session
setup, or keyring prompt signals for these denied cases.

The same smoke also ran `native/fs.copyfile(outsideReadUrl, storageName)`.
That call completed without an ACL error, confirming the separate read-scope
finding below.

### P1: `copyfile(from, to)` bypasses read path scoping

`native/fs.copyfile(from, to)` does not call `get_filename()` for `from`.
Instead, it sanitizes `to`, constructs a destination under plugin storage, and
calls `fa_copy(storage_copy_path, from)`.

Impact: a plugin without read bypass can ask fileaccess to read an arbitrary
source URL into its own storage, then read the copied file from storage. This is
an existing read-scope issue independent of SMB2 write support.

Compatibility research: the HDRezka development plugin uses this behavior for
its updater. `utils/updater.js:getRemoteManifest()` calls:

```js
nativeFs.copyfile('zip://' + downloadURL + '/plugin.json',
                  'remote_plugin.json')
```

The plugin manifest sets `downloadURL` to a remote package URL. A runtime probe
with HDRezka loaded without any `entitlements` confirmed that Movian logs:

```text
Copying file from 'zip://http://.../plugin.json'
  to '<plugin-storage>/copy/remote_plugin.json'
```

and the remote `plugin.json` lands in plugin storage. This is a legitimate
remote-package import use case, so a simple `get_filename(ctx, 0, ec, 0)` on the
source would break existing updater behavior.

Recommended fix: do not turn `copyfile` into a pure scoped local copy without a
replacement import API. Either replace it with two explicit APIs:

- scoped local copy, enforcing read ACL on `from`;
- remote download/import, limited to explicitly allowed protocols such as
  `http://` and `https://`.

Or keep `copyfile` as the import API but add source policy:

- allow sources that pass normal read ACL;
- allow `http://` and `https://`;
- allow `zip://http://...` and `zip://https://...` for remote package metadata;
- reject local and share-backed sources that do not pass read ACL, including
  `file://`, bare local paths, `smb://`, `smb2://`, `ntfs://`, and nested forms
  such as `zip://file://...` or `zip://smb2://...`.

### Standard plugin archive install path

Movian core already supports installing a plugin from a direct archive URL.
The normal navigation path is:

1. Open `search:<archive-url>`.
2. The search backend strips the `search:` prefix and lets fileaccess probe the
   URL.
3. ZIP probing reads `zip://<archive-url>/plugin.json`.
4. A ZIP with plugin metadata is classified as `CONTENT_PLUGIN`.
5. `plugin_open_file()` reads the archive manifest and calls
   `plugin_install(pl, url)`.
6. `plugin_install()` downloads the archive with `fa_load()`, validates ZIP
   magic, and stores it under `persistent/mrp/installed/<plugin-id>.zip`.

The repository upgrade path is also already archive-URL based:

1. `plugins_upgrade_check()` loads configured repositories.
2. `plugin_load_repo()` reads each plugin entry and resolves `downloadURL`
   relative to the repo URL.
3. `plugin_autoupgrade()` calls `plugin_install(pl, NULL)`.
4. `plugin_install()` uses the resolved package URL stored on the plugin.

Runtime evidence: opening a public plugin archive through `search:<archive-url>`
in an isolated profile produced the expected core install logs:

```text
Opening search:<archive-url>
Downloading plugin <id>@local from <archive-url>
Plugin <id>@local valid ZIP archive <n> bytes
```

The installed ZIP was written under the isolated profile and contained
`plugin.json`.

This means HDRezka-style update code does not need `copyfile()` for the actual
install or upgrade action. Its current use of `copyfile(zip://.../plugin.json,
...)` is only for a pre-install remote manifest/version check.

### Self-update without a plugin repository

The runtime tests found a no-core-change path for plugins that cannot publish
to the shared Movian repository: ship a small `repo.json` inside the plugin ZIP
and subscribe to it as `zip://<archive-url>/repo.json`. The embedded repository
entry should use an absolute HTTP(S) `downloadURL` for the archive.

This reuses the standard repository install and upgrade flow instead of adding
a new manifest-read API or using `native/fs.copyfile()` to copy
`zip://.../plugin.json` into plugin storage. The installed plugin identity is
repository-scoped, so upgrades work when the plugin was installed through the
same feed URL. A direct `search:<archive-url>` install remains `@local` and is
not upgraded by that feed because the origins differ.

The `search:<archive-url>` path is still a core install path, not a filesystem
bypass:

- it probes the archive through fileaccess;
- it classifies valid plugin ZIPs as `CONTENT_PLUGIN`;
- it reads `plugin.json` through `plugin_open_file()`;
- it calls `plugin_install(pl, url)`;
- it downloads and stores the ZIP under `persistent/mrp/installed/`.

A fully silent plugin-initiated update would still need a separate core API such
as `native/plugin.selfUpdate(url)`. That API should validate that the archive
manifest id matches the calling plugin before install, and it should schedule
the install after the current JavaScript callback returns so the running plugin
context is not unloaded while native code is still executing inside it.

### Repository update reuse research

The built-in repository path is useful reference material, but it is not the
target path for plugins that must self-update without publishing a repository.
It is currently a core/UI feature rather than a JavaScript plugin API.

Repository format:

```json
{
  "version": 1,
  "title": "Example Plugin Repository",
  "plugins": [
    {
      "type": "ecmascript",
      "id": "example_plugin",
      "title": "Example Plugin",
      "version": "1.2.3",
      "category": "video",
      "downloadURL": "example-plugin.zip"
    }
  ]
}
```

The repository loader:

1. loads the repository JSON through `fa_load()` with compression and auth
   disabled;
2. requires repository `version` to be `1`;
3. walks the `plugins` list;
4. skips unsupported plugin types and blacklisted versions;
5. resolves each `downloadURL` relative to the repository URL;
6. creates a plugin entry whose origin is an MD5-derived hash of the resolved
   package URL;
7. sets `availableVersion`, install/upgrade status, metadata, optional
   `showtimeVersion`, and optional `control` autoplugin triggers.

Runtime evidence with an isolated local HTTP repository:

- repo v1 exposed one plugin item with `canInstall=1` and
  `availableVersion=1.0.0`;
- sending the normal plugin item action `install:` downloaded the ZIP from the
  repository `downloadURL` and wrote
  `persistent/mrp/installed/<id>@<origin>.zip`;
- after restarting with the same persistent profile and repo v2, the same item
  showed `installed=1`, `installedVersion=1.0.0`, `availableVersion=2.0.0`,
  and `canUpgrade=1`;
- sending the normal plugin item action `upgrade` downloaded the v2 ZIP through
  the same `plugin_install(pl, NULL)` path.

Important limitations for reuse:

- Repository subscription is not exposed to plugin JavaScript. Repositories can
  be added through the core settings popup, the `--plugin-repo` command-line
  option, or the saved `pluginrepos` store, but `plugin_repo_create()` and
  `plugins_upgrade_check()` are static core functions.
- A direct plugin ZIP URL is not accepted as a repository feed today.
  `repo_get()` reads the feed URL with `fa_load()` and deserializes the bytes as
  JSON, so an archive URL fails with "Malformed JSON in repository".
- A repository JSON stored inside an archive does work when the feed URL is
  written explicitly as `zip://<archive-url>/repo.json`. Runtime smoke verified
  that Movian loads this URL and creates the repo plugin item. Use an absolute
  HTTP(S) `downloadURL` inside that embedded `repo.json`; relative URLs from a
  `zip://.../repo.json` base are not a good compatibility contract.
- The installed plugin identity includes the origin hash. A plugin installed
  through direct archive open is `id@local`; the same plugin discovered through
  a repository is `id@<hash-of-package-url>`. To get clean repo upgrades, the
  plugin should be installed from the repository entry at least once, otherwise
  the repo item and the existing local item can coexist as separate plugin
  origins.
- Manual install and manual upgrade through the repository UI path work.
- The automatic upgrade path needs more work before relying on it:
  `plugin_autoupgrade()` checks `pl->pl_auto_upgrade`, but current code only
  stores the repository setting in `pr->pr_autoupgrade`; no assignment from the
  repository setting to plugin entries was found in this pass.

Reuse options:

- Best fit without core changes: put `repo.json` in the plugin archive and ask
  users to subscribe to `zip://<archive-url>/repo.json`; runtime tests confirmed
  `plugin:start` and `plugin:repo` show installed and available versions through
  the standard repository model.
- Best fit for maintained plugins that can publish a feed: publish a small
  repository JSON beside the archive and have users subscribe once. After that,
  the core repository UI can show available versions and perform installs or
  upgrades without plugin-side `copyfile()`.
- If only one public archive URL is available, the archive can contain
  `repo.json` and users can subscribe to
  `zip://<archive-url>/repo.json`. For better UX, core could adapt
  `repo_get()` so a direct archive URL automatically falls back to
  `zip://<archive-url>/repo.json` before reporting malformed JSON.
- If plugin-side repository subscription is desired, add a narrow core API such
  as `native/plugin.addRepository(url)` / `native/plugin.checkUpgrades()` rather
  than granting filesystem bypass or general arbitrary-source reads.

### P2: path scope uses raw prefix matching

`get_filename()` uses `mystrbegins(filename, ec->ec_storage)` and
`mystrbegins(filename, ec->ec_path)`. `mystrbegins()` is a plain byte-prefix
check.

Impact: a scope such as `/path/plugin` also matches `/path/plugin-extra` unless
the scope string is guaranteed to end with `/` and the requested URL is
canonicalized consistently. For URL protocols, the same boundary issue applies
to strings such as `smb2://server/share/plugin` and
`smb2://server/share/plugin-extra`.

Recommended fix: replace the raw prefix check with a helper that allows only:

- exact scope match;
- child paths where the next byte after the scope is `/`;
- scopes normalized to a single trailing slash before comparison.

### P2: ACL is checked before protocol-specific parsing/decoding

`get_filename()` checks the raw JavaScript string. Later, fileaccess protocol
handlers may normalize, redirect, or parse that string differently.

Impact: encoded path traversal or protocol redirects could become security
relevant if a protocol decodes `%2e%2e`, rewrites URLs, or treats equivalent
paths differently after the JavaScript ACL check. No concrete bypass was proven
in this pass, but the boundary should be tested before relying on string-level
ACLs for network protocols.

Recommended fix: introduce a protocol-aware ACL function that validates a
canonical URL/path form and either disables redirects during ACL validation or
revalidates after redirect.

### P3: write bypass entitlement is all-protocol, not local-filesystem-only

`entitlements.bypassFileACLWrite` currently bypasses the entire JavaScript write
ACL. It is not limited to local plugin storage, local filesystem paths, or a
specific protocol family.

Impact: any plugin granted this entitlement can write through every writable
fileaccess protocol available in the build, including SMB2.

Recommended fix: document this as a high-trust entitlement, or replace it with
more specific entitlements such as:

- `fileACL.write.storage`
- `fileACL.write.pluginPath`
- `fileACL.write.local`
- `fileACL.write.networkShares`

## Recommended Policy

For this SMB2 PR, do not add protocol-local checks inside SMB2 unless the
product decision is that JavaScript should never write to network shares. SMB2
does not know whether its caller is the UI, a trusted core path, or a plugin.
The correct enforcement point is the JavaScript filesystem ACL layer.

Before marking SMB2 write support as safe for plugins:

1. Fix `copyfile(from, to)` read ACL behavior.
2. Replace raw prefix path scoping with boundary-aware scope checks.
3. Decide whether `bypassFileACLWrite` is intentionally all-protocol.
4. Add JavaScript regression tests or a smoke plugin covering:
   - default plugin cannot open `smb2://host/share/file` for write;
   - default plugin cannot mkdir/unlink/rename an SMB2 URL;
   - plugin with write bypass can write SMB2 only when that entitlement is
     intentionally granted;
   - `copyfile()` cannot import disallowed local or SMB2 URLs unless read ACL
     allows them.
