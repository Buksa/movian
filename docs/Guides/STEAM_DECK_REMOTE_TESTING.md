# Steam Deck Remote Testing

Use this workflow when a Movian Flatpak needs to be installed and smoke-tested
on a Steam Deck from a Windows/WSL development machine.

The goal is to automate copy/install/log collection while still allowing the
Deck user to launch the UI manually when the desktop session does not accept
GUI launches from SSH.

## One-Time Deck Setup

On the Steam Deck, in Desktop Mode:

```sh
passwd
sudo systemctl enable --now sshd
ip -4 addr show wlan0
```

Prefer key auth over passwords. From WSL/Linux, create a key and install it
with `ssh-copy-id`:

```sh
ssh-keygen -t ed25519 -f ~/.ssh/movian_deck -C movian-deck
ssh-copy-id -i ~/.ssh/movian_deck.pub deck@<deck-ip>
ssh -i ~/.ssh/movian_deck deck@<deck-ip> 'whoami; uname -srm; cat /etc/hostname'
```

If setting up the key from Windows PowerShell, use the portable fallback below
because Windows usually does not provide `ssh-copy-id`:

```powershell
mkdir $HOME\.ssh -Force
ssh-keygen -t ed25519 -f $HOME\.ssh\movian_deck -C movian-deck
$pub = Get-Content $HOME\.ssh\movian_deck.pub
ssh deck@<deck-ip> "mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo '$pub' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

## WSL Key Copy

Do not use the private key directly from `/mnt/c/...` with WSL OpenSSH. Windows
mounted files can appear as mode `0777`, and OpenSSH will reject the key as too
open.

Copy the key into WSL once:

```sh
mkdir -p ~/.ssh
cp /mnt/c/Users/<windows-user>/.ssh/movian_deck ~/.ssh/movian_deck
cp /mnt/c/Users/<windows-user>/.ssh/movian_deck.pub ~/.ssh/movian_deck.pub
chmod 700 ~/.ssh
chmod 600 ~/.ssh/movian_deck
chmod 644 ~/.ssh/movian_deck.pub
ssh -i ~/.ssh/movian_deck deck@<deck-ip> 'whoami; uname -srm; cat /etc/hostname'
```

Use `cat /etc/hostname` rather than assuming `hostname` exists in the Deck SSH
environment.

## Copy And Install A Bundle

Build locally:

```sh
support/flatpak/build-local.sh
sha256sum build.flatpak/dev.uzver.Movian.flatpak
```

Copy to the Deck with a unique filename so an older download is not overwritten:

```sh
scp -i ~/.ssh/movian_deck \
  build.flatpak/dev.uzver.Movian.flatpak \
  deck@<deck-ip>:/home/deck/Downloads/dev.uzver.Movian-<short-sha>.flatpak
```

Install remotely:

```sh
ssh -i ~/.ssh/movian_deck deck@<deck-ip> \
  'flatpak install --user -y /home/deck/Downloads/dev.uzver.Movian-<short-sha>.flatpak'
```

If `flatpak install` appears to hang, check whether it is still working in the
Flatpak repository:

```sh
ssh -i ~/.ssh/movian_deck deck@<deck-ip> \
  'pgrep -a -f "flatpak install|dev.uzver.Movian"; flatpak info --user dev.uzver.Movian | sed -n "1,80p"'
```

Verify the installed commit and version:

```sh
ssh -i ~/.ssh/movian_deck deck@<deck-ip> \
  'flatpak info --user dev.uzver.Movian | sed -n "1,80p"'
```

## Launch And Observe

Launching the GLW UI through SSH can exit immediately in some Deck desktop
sessions. Before giving up and asking the Deck user to start Movian normally,
try launching with the real desktop session environment. On a KWin/Wayland
session `DISPLAY=:0` alone fails with "Authorization required"; read the live
env off the running compositor and reuse it:

```sh
ssh -i ~/.ssh/movian_deck deck@<deck-ip> '
  pid=$(pgrep -x plasmashell | head -1)
  tr "\0" "\n" < /proc/$pid/environ | \
    grep -E "^(DISPLAY|WAYLAND_DISPLAY|XAUTHORITY|DBUS_SESSION_BUS_ADDRESS)="
'
```

Export those before `flatpak run`. If the flatpak `command` binary is missing,
override it explicitly (a known packaging bug once installed the binary at
`/app/showtime` instead of `/app/bin/showtime`):
`flatpak run --command=/app/showtime dev.uzver.Movian`.

Two undocumented HTTP endpoints help during a remote smoke:
`GET /api/open?url=<url>` opens a URL in the running instance, and
`GET /api/prop/<path>` browses the live prop tree (e.g.
`/api/prop/global/services/all` to inspect discovered services).

Do not put credentials in the `smb2://user:pass@host` URL form: navigation logs
the full URL and `/api/logfile/0` is unauthenticated. Pre-seed the keyring
instead (`support/smb-smoke/common.sh`). The Deck may be shared — watch for SSH
logins and keyring dialogs you did not initiate.

```sh
curl -fsS http://<deck-ip>:42000/api/diag
curl -fsS http://<deck-ip>:42000/api/logfile/0 -o /tmp/steamdeck-log0.txt

ssh -i ~/.ssh/movian_deck deck@<deck-ip> \
  'ps -eo pid,comm,args | grep -iE "/app/bin/showtime|movian|flatpak run" | grep -v grep || true'

ssh -i ~/.ssh/movian_deck deck@<deck-ip> \
  'ss -ltnp 2>/dev/null | grep -E ":(42000|1445|445)" || true'
```

When the Deck sleeps, the whole host can disappear from the network. Distinguish
that from an app crash by checking ping, SSH, and HTTP together.

## SMB2 Server Remote Smoke

For the SMB2 server MVP, test these as separate claims:

- Movian process is alive and HTTP responds.
- The SMB2 server logs `SMB2-SERVER Listening on port <port>`.
- TCP connects to that port.
- A libsmb2 client can perform session setup and browse/read.
- Read-only mutation attempts fail.
- Runtime port changes through settings restart the server without crashing.
- Windows native acceptance uses TCP `445`; non-445 ports are development-only
  for Windows Explorer users.

Use STPP for live settings changes. HTTP prop aliases such as `*0` are useful
for inspection but are not STPP path segments. Open `settings:network`,
subscribe to `navigators.current.currentpage.model.nodes`, find the row whose
`metadata.title` is `Server TCP port`, and set that row's `value`.

A successful port-change smoke includes:

```text
old TCP port refuses connections
new TCP port accepts connections
SMB2-SERVER Server loop exited ...
SMB2-SERVER Listening on port <new-port>
no CRASH / Signal lines in /api/logfile/0 or /api/logfile/1
```

If setting port `445` is accepted but no listener remains on `445`, classify the
result as privileged-port packaging/forwarding work unless request-handler
smokes also fail on a high port.

Before leaving the Deck, restore the last known working development port and
confirm it listens.

## Windows-Side Check

Native Windows SMB clients expect TCP `445`:

```powershell
Test-NetConnection <deck-ip> -Port 445
cmd /c "net use \\<deck-ip>\<share> /user:<user> <password> /persistent:no"
```

Do not treat `\\host@port\share` or `\\host:port\share` as a reliable
user-facing acceptance path.
