# SMB2 Windows Visibility Research

This note captures the current research for making Movian's SMB2 server usable
from ordinary Windows clients.

## Current State

The Movian SMB2 server MVP currently serves a read-only share on a
non-privileged development port. On the Steam Deck Flatpak test host:

- `192.168.1.56:1445` is open and accepts libsmb2 session setup.
- `192.168.1.56:445` is closed.
- Windows `\\192.168.1.56\Media` does not connect while port `445` is closed.
- Non-standard Windows paths such as `\\192.168.1.56@1445\Media` are not a
  reliable user-facing path.

The MVP request handlers are therefore usable on high ports, but Windows
Explorer and `net use` still need a way to reach the server on TCP `445`.

Authentication is a separate current blocker from TCP visibility. Runtime tests
against the AG build on Steam Deck show anonymous high-port browsing succeeds
with Samba `smbclient`, while password-protected NTLM sessions fail before the
first directory create/query request. Do not treat anonymous high-port success
as proof that ordinary Windows password-authenticated UNC access is complete.

This is not only a Flatpak problem. It is a general host-platform exposure
problem: Windows SMB clients expect TCP `445`, while many platforms reserve or
heavily restrict that port for privileged/system services.

## Windows Requirements

Modern Windows SMB clients use direct-hosted SMB over TCP `445` for normal UNC
paths such as:

```text
\\192.168.1.56\Media
```

This is separate from network-neighborhood visibility. Even after TCP `445`
works, auto-appearance under Explorer's Network view may require a Windows
discovery layer such as WS-Discovery. The first acceptance target should be
manual UNC access by IP address.

## Platform Scope

The SMB2 request handlers can be portable, but Windows-visible service exposure
is platform-specific:

- Linux and SteamOS reserve TCP ports below `1024` for privileged processes by
  default. A regular Movian process cannot bind TCP `445` without root,
  `CAP_NET_BIND_SERVICE`, a host redirect/proxy, socket activation, or a global
  privileged-port policy change.
- Flatpak adds sandbox constraints on top of the normal Linux rule. The current
  local Flatpak does not receive Linux capabilities, so the host must expose or
  forward TCP `445`.
- Android uses a Linux kernel but runs apps inside an application sandbox.
  Binding TCP `445` should be treated as a platform integration problem that
  would require Android-specific privileges, service lifecycle, and foreground
  networking policy. A normal Android app should not be assumed able to expose
  a Windows SMB server on TCP `445`.
- macOS and other Unix-like systems also treat low ports as privileged or
  otherwise system-managed. A native package would need a helper, launchd/system
  service, pf redirect, or equivalent host integration.
- Windows as a server platform is different again: TCP `445` may already be
  owned by the OS SMB Server service, and firewall/service ownership must be
  handled through Windows packaging and service policy.

Therefore, keep the core SMB2 server on an unprivileged configurable port, and
solve TCP `445` exposure with a platform-specific host helper.

## Flatpak Capability Result

Adding a Docker/Podman-style capability argument is not a viable Flatpak
solution:

```text
flatpak build-finish --cap-add=NET_BIND_SERVICE ...
```

Local testing with Flatpak 1.14 reports:

```text
Unknown option --cap-add=NET_BIND_SERVICE
```

`flatpak run` and `flatpak override` expose similar sandbox permission flags but
not a Linux capability grant. Inside the running Flatpak sandbox, capability
sets were empty (`CapEff`, `CapBnd`, and `CapAmb` all zero). Treat TCP `445`
exposure as host packaging or forwarding work, not as a manifest finish-arg.
Native non-Flatpak Linux builds may use normal host mechanisms such as
`setcap cap_net_bind_service=+ep` on the binary, but that is still a host
installation decision rather than SMB2 handler logic.

## Option A: Host Port Redirect

A root-owned host rule can redirect incoming TCP `445` to the Movian high port:

```text
host:445 -> 127.0.0.1:1445
```

Possible implementations:

- `nftables` redirect rule;
- `iptables` redirect rule on older systems.

Pros:

- Movian and Flatpak remain unprivileged.
- No changes are required in libsmb2's server loop.
- The SMB2 request-handler smoke remains identical on `1445`.

Cons:

- Requires privileged host setup.
- Needs careful persistence and removal scripts.
- Port ownership and firewall behavior are outside the Flatpak manifest.

This is a strong candidate for a Linux/SteamOS helper once the exact rule is
validated.

## Option B: systemd Socket Proxy

systemd can bind privileged TCP `445` and launch a small proxy to the Movian
high port:

```ini
# movian-smb2-forward.socket
[Socket]
ListenStream=0.0.0.0:445
Accept=yes

[Install]
WantedBy=sockets.target
```

```ini
# movian-smb2-forward@.service
[Service]
User=deck
StandardInput=socket
StandardOutput=socket
ExecStart=/usr/bin/socat - TCP:127.0.0.1:1445
```

Pros:

- Uses systemd to own the privileged port.
- Keeps Movian unprivileged and unchanged.
- Easier to disable than a raw firewall rule.
- `socat` is present on the tested Steam Deck.

Cons:

- Still requires privileged host unit installation.
- Adds one proxy process per connection with `Accept=yes`.
- Needs runtime validation with Windows `net use`.

This is the recommended next experiment because it is reversible and does not
require changing Movian's SMB2 server internals.

The same pattern applies to native Linux packages and Flatpak installations:
the host service manager owns privileged TCP `445`; Movian continues to run as
an unprivileged process on a high port.

## Option C: Direct systemd Socket Activation

In direct socket activation, systemd binds TCP `445`, starts Movian, and passes
the listening socket through file descriptor `3` using the `LISTEN_FDS`
protocol. The daemon must call `sd_listen_fds()` or implement the same protocol
and then accept clients from the inherited descriptor.

This is architecturally clean, but it is not a small packaging-only change for
the current MVP:

- `src/networking/smb2_server.c` currently calls `smb2_serve_port()`.
- `smb2_serve_port()` in bundled libsmb2 calls `smb2_bind_and_listen()` itself.
- libsmb2 exposes `smb2_serve_port_async(fd, ...)`, so the pieces exist, but
  Movian/libsmb2 would need a `serve_fd` path or a local copy of the serve loop.
- Flatpak fd inheritance through the launcher must be verified separately.
- Stop/restart lifecycle must avoid shutting down a systemd-owned listening fd.

This is a plausible v2/v3 design if we want to remove the proxy layer. It
applies to native Linux too, but it is more invasive than a host redirect or
systemd socket proxy.

## Option D: Lower `ip_unprivileged_port_start`

Linux can allow unprivileged processes to bind low ports by changing:

```text
/proc/sys/net/ipv4/ip_unprivileged_port_start
```

The tested Steam Deck uses the normal value `1024`. Lowering it to `0` would let
ordinary user processes bind TCP `445`, but this is a global host security
change. It affects all unprivileged processes, not only Movian. It is not
recommended as the default product path.

## Discovery Layer

TCP `445` is required for manual UNC access, but it does not guarantee the
device appears under Explorer's Network view. For modern Windows network
discovery, plan a separate WS-Discovery/wsdd-style responder after manual UNC
access works.

Do not block the MVP on discovery. The first Windows acceptance should be:

```powershell
Test-NetConnection 192.168.1.56 -Port 445
cmd /c "net use \\192.168.1.56\Media /user:movian 123 /persistent:no"
```

## Recommended Next Step

Validate a reversible systemd socket proxy on Linux/SteamOS, starting with the
Steam Deck test host:

1. Keep Movian SMB2 serving on `127.0.0.1:1445` / `0.0.0.0:1445`.
2. Install temporary root-owned `movian-smb2-forward.socket` and
   `movian-smb2-forward@.service`.
3. Start the socket and verify host TCP `445` is listening.
4. From Windows, run:

```powershell
Test-NetConnection 192.168.1.56 -Port 445
cmd /c "net use \\192.168.1.56\Media /user:movian 123 /persistent:no"
```

If this passes, document the unit files as the first Linux/SteamOS
Windows-access helper. If it fails, capture the Windows error, Movian log tail,
and the proxy service journal before trying nftables.
