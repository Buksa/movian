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
