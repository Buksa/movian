# SMB2 Windows Visibility Research

This note captures the research for making Movian's built-in SMB2 server usable
from ordinary Windows clients. It is the background for
[#46](https://github.com/Buksa/movian/issues/46).

**Provenance.** The runtime measurements below were taken in June 2026 against
the read-only server MVP, on a Steam Deck Flatpak test host. That MVP was
superseded by the current `src/networking/smb_server*.c`, which is read-write
and structurally different. The *measurements* are unaffected — they are about
the host and the Windows client, not about our request handlers — and every
claim this note makes about the current tree was re-verified before it landed;
each such claim cites file and line. Host addresses are written as
`192.0.2.56` (RFC 5737 documentation range), not the real test host.

## Current State

The server is **off unless the user turns it on**: `smbserver.enable` defaults
to `0` (`src/networking/smb_server.c:416-423`) and the listener thread is only
created while `smb_enable` is true (`:266-291`). When enabled it listens on a
non-privileged port — `smb_port` defaults to `1445`
(`src/networking/smb_server.c:55`). On the Steam Deck Flatpak test host, with
the server enabled:

- `192.0.2.56:1445` is open and accepts libsmb2 session setup.
- `192.0.2.56:445` is closed.
- Windows `\\192.0.2.56\Media` does not connect while port `445` is closed.
- Non-standard Windows paths such as `\\192.0.2.56@1445\Media` are not a
  reliable user-facing path.

The request handlers are therefore usable on high ports, but Windows Explorer
and `net use` still need a way to reach the server on TCP `445`.

Authentication is **not** a blocker, and the June note claiming it was has been
removed rather than carried over. Against the MVP, password-protected NTLM
sessions failed before the first directory request. Against the current server
that is a hard gate in CI-adjacent tooling:
`support/smb-smoke/run-embedded-server-smoke.sh` performs
password-authenticated listings over SMB2 and over each of `SMB3_00`,
`SMB3_02` and `SMB3_11` with client min and max pinned to the same dialect, and
`fail`s the run if any of them errors or if a *wrong* password is accepted.

What that gate does not cover is a **Windows** client, because no Windows
client can reach the server at all while 445 is closed. So: password auth is
proven against Samba `smbclient`; Windows-side auth is simply unmeasured, and
becomes measurable only once this document's subject is solved. Do not read the
smoke's green as evidence about Windows, and do not read the June note as
evidence that auth is broken.

This is not only a Flatpak problem. It is a general host-platform exposure
problem: Windows SMB clients expect TCP `445`, while many platforms reserve or
heavily restrict that port for privileged/system services.

## Windows Requirements

Modern Windows SMB clients use direct-hosted SMB over TCP `445` for normal UNC
paths such as:

```text
\\192.0.2.56\Media
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
- Connections arriving while Movian is not running are refused by `socat`, not
  queued. This is the correct behaviour for a media player and is *why* the
  proxy works: systemd starts `socat`, never Movian. See Option C for what
  happens when you ask systemd to start Movian instead.

This is the recommended next experiment because it is reversible and does not
require changing Movian's SMB2 server internals.

The same pattern applies to native Linux packages and Flatpak installations:
the host service manager owns privileged TCP `445`; Movian continues to run as
an unprivileged process on a high port.

## Option C: Direct systemd Socket Activation — ruled out

In direct socket activation, systemd binds TCP `445`, **starts Movian**, and
passes the listening socket through file descriptor `3` using the `LISTEN_FDS`
protocol. The daemon must call `sd_listen_fds()` or implement the same protocol
and then accept clients from the inherited descriptor.

The June note called this the clean design and listed only mechanical
obstacles. It missed the disqualifying one, which is a project decision, not an
implementation cost: **Movian has no headless daemon mode and one will not be
added** (`AGENTS.md:60-61`). Its lifetime is tied to the UI event loop —
`main()` blocks in `glw_x11_main()` — and a launch with no display
self-terminates **~2.5–3 s after startup** (`AGENTS.md:63-65`).

Socket activation is precisely a request to start Movian on demand, from
systemd, outside any UI session. The accepted connection would be handed to a
process that exits before a Windows client finishes browsing, and an idle
session would be impossible — the same blind spot that let the #76 signing
guard's rejection of Samba's unsigned `SMB2_ECHO` keepalive pass every one-shot
smoke (`AGENTS.md:66-69`).

Any revival of this option must first answer how systemd learns about the
user's graphical session and refrains from starting anything outside it — at
which point it is no longer socket activation but Option B with extra steps.
Prefer Option B.

The mechanical obstacles below are recorded because they remain true and would
still have to be solved even under a UI-session-aware design. Each was
re-checked against the current tree:

- `src/networking/smb_server.c:244` calls `smb2_serve_port()`.
- `smb2_serve_port()` in bundled libsmb2 calls `smb2_bind_and_listen()` itself,
  so the server cannot be handed a socket someone else bound. The file header
  says as much: *"do NOT pre-bind"* (`src/networking/smb_server.c:11`).
- libsmb2 exposes `smb2_serve_port_async(fd, ...)`
  (`ext/libsmb2/include/smb2/libsmb2.h:1399`), so the pieces exist, but
  Movian/libsmb2 would need a `serve_fd` path or a local copy of the serve loop.
- Flatpak fd inheritance through the launcher must be verified separately.
- Stop/restart lifecycle must avoid shutting down a systemd-owned listening fd.
  Today stopping the server requires closing the listen fd out from under a
  blocked `smb2_serve_port()` (`src/networking/smb_server.c:295`), which is
  exactly the fd systemd would own.

Note the last one is not merely a lifecycle wrinkle: the fd systemd would own
is the fd we close to stop the server, so the two designs are in direct
conflict over the same descriptor.

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
Test-NetConnection 192.0.2.56 -Port 445
cmd /c "net use \\192.0.2.56\Media /user:movian <password> /persistent:no"
```

## Recommended Next Step

Validate a reversible systemd socket proxy on Linux/SteamOS, starting with the
Steam Deck test host:

1. Enable the SMB server in Settings → Network (it is off by default) and keep
   Movian, UI and all, serving on `127.0.0.1:1445` / `0.0.0.0:1445`.
2. Install temporary root-owned `movian-smb2-forward.socket` and
   `movian-smb2-forward@.service`.
3. Start the socket and verify host TCP `445` is listening.
4. From Windows, run:

```powershell
Test-NetConnection 192.0.2.56 -Port 445
cmd /c "net use \\192.0.2.56\Media /user:movian <password> /persistent:no"
```

If this passes, document the unit files as the first Linux/SteamOS
Windows-access helper. If it fails, capture the Windows error, Movian log tail,
and the proxy service journal before trying nftables.
