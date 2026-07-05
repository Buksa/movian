# Ubuntu SMB2 Test Stand

A dedicated Linux box on the real LAN for testing the Movian **SMB2 client**
against real servers (Windows, Samba, NAS). It exists because WSL2 cannot do
the job: its NAT blocks the UDP broadcast that NetBIOS name resolution and LAN
discovery need, and in mirrored-network mode WSL shares the Windows host IP, so
the host's own SMB server appears unreachable from inside WSL.

A native Ubuntu/Debian machine on the LAN has none of those limits: broadcast
works, every LAN host is directly reachable, and there is no display/mirror
quirk.

## 1. Provision

On the target machine (fresh Ubuntu 22.04+/Debian 12+):

```sh
git clone https://github.com/Buksa/movian.git ~/GitHub/movian
cd ~/GitHub/movian
support/smb-smoke/setup-ubuntu-stand.sh --branch <branch>
```

The script installs the Movian build dependencies **and** the test-stand tools
(`smbclient`, `nmblookup` via `samba-common-bin`, `gdb`, `xvfb`, `cmake`),
fetches submodules, builds `build.debug/movian`, and prints tool versions. It is
idempotent and never touches credentials. Flags: `--repo-dir DIR`,
`--branch BRANCH`, `--no-apt`, `--no-build`.

Package list, for reference (README.markdown + `support/configure-linux-debug.sh`):

```
build-essential pkg-config git curl yasm python-is-python3 cmake
libsqlite3-dev libfreetype6-dev libfontconfig1-dev libx11-dev libxext-dev
libgl1-mesa-dev libpulse-dev libssl-dev libavahi-client-dev libxss-dev
libxxf86vm-dev libgmp-dev libgnutls28-dev
smbclient samba-common-bin gdb xvfb
```

## 2. Remote access

Drive the stand over SSH from the dev machine with a **passphrase-free** key so
non-interactive tooling works (a passphrase key fails under `BatchMode`):

```sh
ssh-keygen -t ed25519 -f ~/.ssh/movian_stand -N ''      # no passphrase
ssh-copy-id -i ~/.ssh/movian_stand.pub <user>@<stand-ip>
ssh -i ~/.ssh/movian_stand <user>@<stand-ip> 'uname -srm; cat /etc/hostname'
```

If `ssh-copy-id` cannot prompt for a password (no TTY, e.g. from an automated
shell), append the public key to `~/.ssh/authorized_keys` on the stand directly.

## 3. Baseline with smbclient

Before testing Movian, confirm the server and credentials with the reference
client. Keep credentials in a mode-600 auth file, never on the command line:

```sh
umask 077; cat > /tmp/authfile <<EOF
username = <user>
password = <password>
domain   = <WORKGROUP-or-domain>
EOF
smbclient -L //<server> -A /tmp/authfile          # list shares
smbclient //<server>/<share> -A /tmp/authfile -c 'dir'
```

For NetBIOS name resolution (the WSL-impossible path):

```sh
nmblookup <NETBIOS-NAME>            # forward name -> address(es)
nmblookup -A <server-ip>            # node status (names on that host)
```

A multi-homed Windows host (VirtualBox/VMware/Hyper-V/WSL adapters) answers
with **several** addresses; only the real LAN one is reachable.

## 4. Movian SMB2 client smoke

The GUI runners (`support/smb-smoke/run-http-nav-smoke.sh`) drive the full GLW
UI. On a headless stand, wrap them with a virtual display:

```sh
xvfb-run -a support/smb-smoke/run-http-nav-smoke.sh
```

For **direct fileaccess** tests that do not need the UI at all, use the
ECMAScript entry point with a small script against `require('native/fs')`:

```sh
cat > /tmp/t.js <<'JS'
var fs = require('native/fs');
// NBT resolution binds its socket in INIT_GROUP_ASYNCIO, which starts AFTER a
// -j script's synchronous top-level runs, so defer NBT tests off the boot path:
setTimeout(function () {
  try { console.log('RESULT ' + fs.readdir('smb2://<host>/<share>').join(',')); }
  catch (e) { console.log('RESULT FAIL ' + e); }
}, 20000);
JS

# Pre-seed the keyring so the headless run needs no auth prompt.
# keyring id is "smb2:connection:<host>" (host = exactly what appears in the URL,
# IP or NetBIOS name); one entry per host you target.
prof=$(mktemp -d); mkdir -p "$prof/persistent/settings"
python3 - <<'PY' > "$prof/persistent/settings/keyring"
import json
c = {"username": "<user>", "password": "<password>", "domain": "<domain>"}
print(json.dumps({"smb2:connection:<host>": c}, separators=(",", ":")))
PY
printf '{"smbdebug":1}' > "$prof/persistent/settings/dev"

cd ~/GitHub/movian
DISPLAY=:0 build.debug/movian -d --disable-upgrades --bypass-ecmascript-acl \
  -j /tmp/t.js --persistent "$prof/persistent" --cache "$prof/cache" >/tmp/mv.log 2>&1 &
sleep 25
grep -E 'SMB2-CLIENT|Pre-resolv|RESULT' /tmp/mv.log
```

`smbdebug` (the `SMB2-CLIENT` trace) is set by writing `{"smbdebug":1}` into
`<profile>/persistent/settings/dev` before launch; it is not settable over HTTP.

### What each URL exercises

| URL | Exercises |
| --- | --- |
| `smb2://<host>/` | share enumeration (srvsvc), admin-share filtering |
| `smb2://<host>/<share>` | password auth, directory scan, hidden/system filtering |
| `smb2://<NETBIOS-NAME>/<share>` | NBT name resolution (dot-less pre-resolve) |

## 5. Hygiene

- Credentials live only in mode-600 keyring/auth files; delete them after the
  run. The `smb2://user:pass@host` URL form is logged in cleartext and the HTTP
  API's `/api/logfile` is unauthenticated -- pre-seed the keyring instead.
- Kill the launched `movian` and confirm it is gone (`pgrep -a -f movian`);
  headless instances have leaked when a kill was misread as successful.
- The stand has no CodeGraph index -- code intelligence stays on the dev
  workspace; the stand is a build/run target only.
