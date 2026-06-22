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

Prefer key auth over passwords. From Windows PowerShell:

```powershell
mkdir $HOME\.ssh -Force
ssh-keygen -t ed25519 -f $HOME\.ssh\movian_deck -C movian-deck
$pub = Get-Content $HOME\.ssh\movian_deck.pub
ssh deck@<deck-ip> "mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo '$pub' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

Windows usually does not provide `ssh-copy-id`. The PowerShell command above is
the portable replacement.

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
ssh -n -i ~/.ssh/movian_deck deck@<deck-ip> \
  'flatpak install --user -y /home/deck/Downloads/dev.uzver.Movian-<short-sha>.flatpak'
```

Use `ssh -n` in scripted workflows where more local commands follow the SSH
call. Without it, OpenSSH can consume the rest of a here-doc or pipeline as
remote stdin.

If `flatpak install` appears to hang, check whether it is still working in the
Flatpak repository:

```sh
ssh -i ~/.ssh/movian_deck deck@<deck-ip> \
  'pgrep -a -f "flatpak install|dev.uzver.Movian"; flatpak info --user dev.uzver.Movian | sed -n "1,80p"'
```

Verify the installed commit and version:

```sh
ssh -n -i ~/.ssh/movian_deck deck@<deck-ip> \
  'flatpak info --user dev.uzver.Movian | sed -n "1,80p"'
```

## Launch And Observe

Launching the GLW UI through a plain SSH command can exit when the SSH session
ends. In Desktop Mode, prefer a transient user systemd unit with the Plasma X11
display and the current random `xauth_*` file:

```sh
ssh -n -i ~/.ssh/movian_deck deck@<deck-ip> '
  flatpak kill dev.uzver.Movian 2>/dev/null || true
  systemctl --user stop movian-codex-test.service 2>/dev/null || true
  XAUTH=$(find /run/user/1000 -maxdepth 1 -name "xauth_*" | head -1)
  systemd-run --user --unit=movian-codex-test --collect \
    --setenv=DISPLAY=:0 --setenv=XAUTHORITY="$XAUTH" \
    flatpak run dev.uzver.Movian
'
```

`xauth_*` changes after reboot. Resolve it dynamically. If this still fails,
ask the Deck user to start Movian normally, then continue the remote smoke
through SSH and HTTP.

Useful probes:

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

Do not add Docker/Podman-style capability arguments to the Flatpak manifest.
Flatpak 1.14 `build-finish`, `run`, and `override` do not support
`--cap-add=NET_BIND_SERVICE`; a test run reports `Unknown option
--cap-add=NET_BIND_SERVICE`. Keep high-port handler smokes separate from the
product/packaging work needed to expose TCP `445` to ordinary Windows clients.
For the current Windows-access research and candidate host-side forwarding
options, see `docs/Guides/SMB2_WINDOWS_VISIBILITY_RESEARCH.md`.

If an unavailable port is rejected, the previous working listener should remain
alive. Verify the old high port still accepts TCP and SMB2 session setup before
continuing.

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
