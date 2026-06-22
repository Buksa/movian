# Linux SMB2 and SMB3 Backend

Linux builds include a bundled SMB2/SMB3 backend based on a pinned
`ext/libsmb2` submodule commit. The current Movian branch tracks the
`Buksa/libsmb2` `codex/movian-server-isolation` branch, rebased on current
upstream `sahlberg/libsmb2` master, with a small Movian embedding hardening
patch. It is available through the temporary `smb2://` scheme while
the existing `smb://` backend remains unchanged for compatibility and A/B
testing.

## URLs

Use the same server, share, and path layout as an SMB URL:

```text
smb2://server/share/path
smb2://server:port/share/path
smb2://user@server/share/path
smb2://domain;user@server/share/path
```

Opening `smb2://server/` enumerates disk shares exposed by the server.
Passwords are requested through Movian's authentication dialog and keyring;
they are not part of the URL.

Linux builds discover `_smb._tcp` services through Avahi. Discovered servers
appear under Local network and preserve the advertised port in their
`smb2://server:port/` URL. Flatpak builds use the host Avahi daemon through
its system D-Bus service.

## Supported Operations

The backend supports:

- host share enumeration;
- directory browsing;
- path metadata;
- file open, read, seek, size, and close;
- write, append, truncate, rename, unlink, rmdir, mkdir, and filesystem
  free-space queries where the remote share permits them;
- guest and authenticated sessions.

Extended-attribute operations are not implemented.

Each open file or directory operation owns its own libsmb2 context. This keeps
parallel resources independent and avoids sharing mutable connection state
between file handles.

## Build

The standard Linux debug helper enables both backends:

```sh
./support/configure-linux-debug.sh
make BUILD=debug -j$(nproc)
./build.debug/movian --help
```

The resulting binary contains the pinned static libsmb2 library and does not
require a system `libsmb2.so`. The local Flatpak profile uses the same bundled
backend.

`CONFIG_LIBSMB2` is enabled by default on Linux. Other platform defaults are
unchanged, so their existing `smb://` implementation remains in use.

## Migration

The separate `smb2://` scheme is a transition tool, not a replacement public
standard. It allows the old and new implementations to be tested side by side.
After the new backend has equivalent platform coverage, a later change can
move standard `smb://` URLs to libsmb2, retain `smb2://` briefly as a
compatibility alias, and then remove the alias.
