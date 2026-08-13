# SMB2 client smoke tests

These are opt-in tests for the Movian SMB2 **client**. They require a known
remote writable SMB2/SMB3 share. No credentials are stored in the repository.
The scripts never start or configure a Movian SMB server.

## Configuration

```sh
export SMB_SMOKE_HOST=192.0.2.10
export SMB_SMOKE_SHARE=Users
export SMB_SMOKE_PATH='public/test'
export SMB_SMOKE_USER=example
export SMB_SMOKE_PASSWORD='secret'
export SMB_SMOKE_DOMAIN=WORKGROUP
```

`SMB_SMOKE_PATH` must point to a disposable writable directory. The GDB smoke
creates and removes a unique subdirectory below it. Optional variables:

- `SMB_SMOKE_ROOT`: Movian checkout; defaults to the current directory;
- `SMB_SMOKE_MOVIAN`: binary; defaults to `$SMB_SMOKE_ROOT/build.debug/movian`;
- `SMB_SMOKE_ART`: artifact directory; defaults to `/tmp/fa_libsmb2-real-gdb-smoke`;
- `SMB_SMOKE_TIMEOUT`: per-case timeout in seconds; defaults to `45`.

The test writes credentials only to an isolated temporary Movian profile,
sanitizes logs and scripts, removes the profile on exit, and checks that the
password is absent from artifacts.

## Movian client smoke

```sh
support/smb-smoke/run-gdb-smoke.sh
```

The script runs under GDB and checks:

- host-root navigation with and without a trailing slash;
- configured share/path navigation;
- create/write/truncate/append/read;
- rename/unlink/rmdir;
- absence of SIGSEGV, SIGABRT, SIGBUS, stack-smashing, and obvious buffer
  overflow diagnostics.

The smoke intentionally uses a remote server. It does not exercise the
embedded Movian server, server registration, server lifecycle, or server VFS.

## Direct pinned-libsmb2 smoke

`smb2-open-preserve.c` is a small dependency-level client check. It connects to
a share, opens a writable test path, closes it, and disconnects.

After `support/configure-linux-debug.sh` and the debug dependency build:

```sh
cc -Iext/libsmb2/include \
  support/smb-smoke/smb2-open-preserve.c \
  build.debug/inst/lib/libsmb2.a -lpthread \
  -o /tmp/smb2-open-preserve

/tmp/smb2-open-preserve \
  "$SMB_SMOKE_HOST" "$SMB_SMOKE_SHARE" "$SMB_SMOKE_PATH/file.bin" \
  "$SMB_SMOKE_USER" "$SMB_SMOKE_PASSWORD" "$SMB_SMOKE_DOMAIN"
```

This checks the pinned c443 dependency directly; it is separate from the
Movian fileaccess/pool smoke.

## Scope boundary

The following are deliberately not copied into this client feature:

- `run-embedded-server-smoke.sh`;
- server-specific `smb_server*.c` coverage;
- server patches and server lifecycle tests;
- any test requiring `codex/smb2-server-mvp` or the future `feature/smb-server`.
