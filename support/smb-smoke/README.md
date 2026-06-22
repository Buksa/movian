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

For browser parity checks, keep using `search:smb2://...` through `/api/open`.
This matches the user navigation path and catches root URL normalization, for
example `smb2://host` becoming `smb2://host/`.

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

## SMB2 Server MVP Notes

The SMB2 server lives under Movian network settings and should be tested as a
runtime setting, not only as a process-start option.

For the full SSH-assisted Steam Deck copy/install/log workflow, see
`docs/Guides/STEAM_DECK_REMOTE_TESTING.md`.
For Windows visibility, TCP `445`, and systemd/nft forwarding research, see
`docs/Guides/SMB2_WINDOWS_VISIBILITY_RESEARCH.md`.

Successful findings to preserve in future smokes:

- Use STPP dot paths and real child proprefs for settings writes. HTTP paths
  such as `/api/prop/.../*0` are display aliases and are useful for inspection,
  but `*0` is not a valid STPP path segment.
- To change the server TCP port, open `settings:network`, subscribe to
  `navigators.current.currentpage.model.nodes`, find the `Server TCP port`
  row by `metadata.title`, and set that row's `value`.
- A port-change smoke passes only when logs show the old server loop exiting
  and a new `SMB2-SERVER Listening on port ...` line, the old TCP port refuses
  connections, and the new port accepts a browse/read probe.
- If the app aborts in thread `SMB2-server` during a port edit, preserve
  `/api/logfile/0` and `/api/logfile/1` and treat it as a server lifecycle
  bug before repeating the write against the same build.
- For Linux/macOS/libsmb2 clients, a non-privileged development port such as
  `1445` is acceptable. For ordinary Windows Explorer or `net use` acceptance,
  the service must be reachable on TCP `445`; Windows `\\host@port\share` and
  `\\host:port\share` are not reliable user-facing connection forms.
- If TCP `445` cannot be bound on the target platform while higher ports work,
  keep handler correctness and privileged-port packaging/forwarding as separate
  test results. This applies beyond Flatpak: native Linux, SteamOS, Android,
  macOS, and other targets need their own host integration story for Windows
  SMB clients.
- A failed switch to an unavailable port must leave the previous working SMB2
  server listener alive. Verify this by attempting a privileged or occupied
  port, then checking that the previous high port still accepts TCP and SMB2
  session setup.
- If a persisted startup port such as `445` is unavailable, the server should
  fall back to the default high port and the Network settings row should also
  show the effective fallback port. A stale UI value is a failed smoke even when
  the listener itself survived.
