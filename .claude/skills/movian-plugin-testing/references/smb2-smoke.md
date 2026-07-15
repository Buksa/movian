# SMB and SMB2 Runtime Smokes

Read this reference only for SMB/SMB2 client, browser, authentication, or
embedded server work.

## Navigation and Props

- Open SMB/SMB2 browser targets as `search:<url>` through `mdev open`.
- Assert the final `currentpage/url`; title/loading alone can be satisfied
  by the previous page.
- HTTP `*0` paths are unnamed-child display aliases. STPP uses dot paths and
  child proprefs returned by subscriptions.
- Change popup fields and live server settings through real STPP child
  proprefs. Use X11 keypresses
  (`support/devtools/mdevlib/x11_keypress.py`) only when visible
  popup/focus behavior is the proof.

## Authentication

Choose one path according to the test:

1. Seed an isolated keyring for protocol behavior.
2. Fill the real popup through STPP child proprefs for popup behavior.
3. Use X11 keypresses as the visual fallback.

Clean temporary keyring entries after tests that did not use a disposable
profile (`mdev`'s `/tmp/mdev/<name>/persistent/` is already disposable —
prefer a fresh `--name` over reusing a profile with stale credentials).

## Profile Contamination

Use an isolated profile with a local bittorrent cache path. Before
attributing early logs to the embedded server, inspect:

- `settings/bittorrent` for an SMB2 torrent cache;
- saved SMB/SMB2 bookmarks;
- service probes created at startup.

A remote cache can trigger `fa_fsinfo()` and `fa_scandir()` before the
embedded server starts. Treat unexpected `SMB2-CLIENT Connecting to ...`
lines as profile contamination unless the scenario intentionally includes a
remote client.

Record a short profile summary with every server smoke.

## Embedded Server Acceptance

For VFS list/browse behavior, compare the server implementation with the
FTP server's direct `fa_protocol_vfs.fap_scan(..., FA_NON_INTERACTIVE)`
pattern before changing enumeration.

Require:

- separate `SMB2-SERVER` and `SMB2-CLIENT` log assertions
  (`mdev log --tail N | grep 'SMB2-SERVER\|SMB2-CLIENT'`);
- real `IPC$/srvsvc` host-root enumeration;
- deep browse such as `smb2://127.0.0.1:<port>/share/zona/`;
- evidence for `Create OK -> QueryInfo: FILE/ALL -> QueryDir`;
- final Movian node/URL assertions, not only protocol logs.

For live server port changes, update the real settings prop and verify the
server-loop restart lines plus the listening socket.

## Windows Client Boundary

Native Windows Explorer and `net use` expect TCP 445. Non-445 SMB2 ports can
work for Linux/macOS libsmb2 clients, but Windows UNC forms containing
`@port` or `:port` are not a reliable acceptance path.

Treat TCP 445 bind failures in Flatpak/Linux as a packaging or
privileged-port constraint, not proof that SMB2 request handlers are broken.

## Server Lifecycle And Port-Change Lessons

- The Movian SMB2 server is a live settings surface. When changing its TCP
  port in a smoke, do it through STPP against the actual settings node
  propref, not through an HTTP `*N` alias. Open `settings:network`,
  subscribe to `navigators.current.currentpage.model.nodes`, discover the
  row whose `metadata.title` is `Server TCP port`, then `SET` that row's
  `value`.
- A safe port-change smoke should prove lifecycle, not just settings
  storage: capture the old port refusing connections, the new port
  accepting connections, and log lines showing the old server loop exited
  followed by a new `SMB2-SERVER Listening on port ...` line.
- If a running build aborts in thread `SMB2-server` while editing the port,
  suspect server lifecycle first. Do not retry destructive STPP writes
  against the same build until the stop/restart path is fixed; preserve
  `mdev log` output from both before and after.
- For Windows manual-client acceptance, test plain UNC on TCP 445:
  `net use \\host\share /user:<user> <password> /persistent:no`. Windows
  `\\host@port\share` and `\\host:port\share` are not dependable
  substitutes for ordinary users.
- If Linux/Flatpak cannot bind TCP 445 but higher ports work with libsmb2 or
  `smbclient`, classify the failure as privileged-port packaging or
  forwarding work. Keep request-handler smokes separate from the product
  question of how to expose TCP 445.
- A failed switch to an unavailable SMB2 server port should leave the
  previous listener alive. Smokes should try an occupied or privileged
  port, then verify the old high port still accepts TCP and SMB2 session
  setup.
- Flatpak 1.14 does not support Docker/Podman-style
  `--cap-add=NET_BIND_SERVICE` in `build-finish`, `run`, or `override`; the
  observed error is `Unknown option --cap-add=NET_BIND_SERVICE`. Do not put
  capability args in the manifest. Treat TCP 445 exposure as host packaging
  or forwarding work.
- When a persisted startup port is unavailable, verify both the effective
  listener and the Network settings value. The setting should revert to the
  fallback port so the UI does not keep showing an unreachable `445`.
- Embedded SMB2 server root navigation is not a synthetic Movian root. A
  good smoke must exercise `IPC$`, open pipe `srvsvc`, and observe
  `SMB2_FSCTL_PIPE_TRANSCEIVE`/`NetrShareEnum` returning the configured
  share. Grep for `SMB2-SERVER`, `srvsvc`, `PIPE_TRANSCEIVE`, and the share
  name.
- Split logs by role: `SMB2-SERVER` belongs to embedded request handlers;
  `SMB2-CLIENT` belongs to Movian's outbound SMB2 fileaccess client. Avoid
  old broad `SMB2` greps when diagnosing client-vs-server behavior.
- For VFS parity, create an isolated `persistent/settings/bookmarks2` JSON
  array with a media directory named `zona`, set `smbserver.root` to `/`,
  then verify both `smbclient //127.0.0.1/share -c "ls; cd zona; ls"` and
  Movian HTTP navigation to `smb2://127.0.0.1:<port>/share/zona/`.
- If a directory URL reaches server `Create OK` but Movian does not scan it,
  look for missing `QueryInfo: FILE/ALL`. `libsmb2` client `stat()` uses a
  related compound `CREATE + QUERY_INFO + CLOSE` chain where `QUERY_INFO`
  and `CLOSE` carry an all-`FF` File ID placeholder. The server must
  resolve that placeholder to the just-created handle before `QueryDir` can
  happen.
- Keep SMB3 password signing diagnostics separate from navigation parity.
  Current green baseline is password SMB2 write/read plus anonymous
  SMB2/SMB3 root/VFS navigation. Make password SMB3 listing fatal only when
  the task is specifically about the signing/auth path.

## SMB2 Client Pool / Keepalive Assertions

A single-URL navigation or GDB write smoke does not cover the SMB2 client's
session pool, pipelined reads, or 30 s echo keepalive. Verify those
separately by:

1. Seeding `smbdebug=1` into the isolated profile
   (`mdev run --dev-flags smbdebug=1`; see `debug-flags.md`) so
   `SMB2-CLIENT` lines are emitted under `-d`.
2. Opening `search:smb2://$HOST/$SHARE/` via `mdev open`, waiting for
   ready, then re-opening the host root and share a second time.
3. Asserting against `SMB2-CLIENT` log lines:
   - **Pool reuse** — roughly one `Pool create ... refcount=1` per
     `(host, share)` pair and many `Pool reuse ... refcount=1` per VFS call.
     A regression shows `Connecting to` / `Session setup` counts close to
     the operation count.
   - **Echo keepalive** — holding an idle pooled session ~30 s yields
     `Keepalive ... echo rc=0 missed=0`, recurring every 30 s, with the
     session never flipping to `broken`. `rc=-12` means the bundled
     libsmb2 is missing the `smb2_echo` socket-validity fix (see
     `debug-flags.md` — currently present in this tree's pinned commit).

`mdev` already wraps the binary with `stdbuf -oL -eL`
(`support/devtools/mdevlib/harness.py`), so idle-period log lines are not
lost to stdio buffering the way they would be with a bare backgrounded
launch — no extra wrapping needed when using `mdev run`.
