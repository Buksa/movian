# SMB/SMB2 Smoke Tests

This directory contains opt-in runtime smoke tests for the SMB2 fileaccess
backend. The tests are intended for developer machines with a known writable
test share.

No credentials are stored in the repository. Pass them through environment
variables when running the scripts.

## Required Environment

```sh
export SMB_SMOKE_HOST=192.0.2.10
export SMB_SMOKE_SHARE=Users
export SMB_SMOKE_PATH='public/test'
export SMB_SMOKE_USER=example
export SMB_SMOKE_PASSWORD='secret'
export SMB_SMOKE_DOMAIN=WORKGROUP
```

`SMB_SMOKE_PATH` must point to a disposable/writable test directory. The GDB
write smoke creates and removes a unique `codex-gdb-*` subdirectory below it.

Optional variables:

- `SMB_SMOKE_ART`: artifact directory. Defaults under `/tmp`.
- `SMB_SMOKE_ROOT`: Movian checkout. Defaults to current working directory.
- `SMB_SMOKE_MOVIAN`: Movian binary. Defaults to
  `$SMB_SMOKE_ROOT/build.debug/movian`.
- `SMB_SMOKE_TIMEOUT`: per-case timeout in seconds.

## Fast Navigation Smoke

```sh
support/smb-smoke/run-http-nav-smoke.sh
```

Checks:

- `smb2://$SMB_SMOKE_HOST` redirects/normalizes to host root with `/`;
- `smb2://$SMB_SMOKE_HOST/` reaches `loading=0`;
- the configured share/path reaches `loading=0`;
- first page nodes are captured for comparison.

## Embedded SMB2 Server Smoke

```sh
support/smb-smoke/run-embedded-server-smoke.sh
```

Checks a local Movian SMB2 server with an isolated profile:

- profile hygiene: the smoke writes a local bittorrent cache path and records
  `profile-summary.txt` so saved torrent/bookmark/keyring state cannot quietly
  trigger unrelated remote SMB2 client scans;
- password SMB2 `smbclient` listing and writable operations;
- password SMB3 listing as a diagnostic check; set
  `SMB_SERVER_SMOKE_REQUIRE_PASSWORD_SMB3=1` to make it mandatory;
- wrong password rejection;
- `get`, `put`, `mkdir`, `rename`, `del`, `rmdir` using
  `SMB_SERVER_SMOKE_FILE_DIALECT` (default `SMB2`);
- traversal upload attempts stay scoped below the exported root;
- password SMB2 read/write protocol operations are observed in server logs;
- Movian can navigate its own anonymous `smb2://127.0.0.1:<port>/share/` and
  `.../share/zona/` URLs;
- host-root enumeration through `IPC$/srvsvc` exposes the configured share;
- the default `/` share root exports `vfs:///`, not the raw filesystem root;
- nested VFS browsing works through `smb2://127.0.0.1:<port>/share/zona/`;
- server logs prove `srvsvc` `PIPE_TRANSCEIVE` and `QueryInfo: FILE/ALL`, so
  host-root enumeration and compound directory `stat` are both exercised.
- remote SMB2 client guard: by default embedded smokes fail if Movian logs
  `SMB2-CLIENT Connecting to ...` for anything other than the local self-client
  (`127.0.0.1`). Set `SMB_SMOKE_ALLOW_REMOTE_CLIENTS=1` only when intentionally
  testing a non-isolated profile.

The Movian media browser may filter non-media files such as `.txt` from the UI
node list even when `smbclient ls` shows them. Use media extensions for
Movian self-navigation assertions and `smbclient` for protocol-level listings.

When debugging embedded server/client interactions, grep for split components:
`SMB2-SERVER` for the embedded server lifecycle and request handlers, and
`SMB2-CLIENT` for Movian's outbound SMB2 fileaccess client. Root enumeration
should show `IPC$`, `srvsvc`, `PIPE_TRANSCEIVE`, and the returned share name in
server logs. Deep VFS child navigation should show `QueryInfo: FILE/ALL`
between `Create OK` for the directory and the following `QueryDir`.

If `SMB2-CLIENT` appears before `SMB2-SERVER` starts, first inspect the profile,
not the server lifecycle. A saved bittorrent cache path such as
`smb2://host/share` makes bittorrent disk I/O call `fa_fsinfo()` and
`fa_scandir()` during startup; saved bookmarks can also create service probes.
Use isolated profiles or the generated `profile-summary.txt` before concluding
that the embedded server triggered the client.

## GDB Smoke

```sh
support/smb-smoke/run-gdb-smoke.sh
```

Checks under GDB:

- host root with and without trailing slash;
- configured share/path navigation;
- write, truncate, append, read, rename, unlink, and rmdir in a unique
  temporary subdirectory below `SMB_SMOKE_PATH`;
- absence of `SIGSEGV`, `SIGABRT`, `SIGBUS`, stack-smashing, and obvious
  buffer-overflow diagnostics.

The scripts seed credentials into an isolated Movian profile and remove the
seeded keyring before exit. Logs are also sanitized for the password string.
