# Linux SSH GUI Launch

How to launch Movian's GLW/X11/OpenGL UI over SSH on a regular Linux machine
(non-Flatpak, non-SteamOS build).

## Prerequisites

- Movian built locally (e.g. `build.debug/movian`)

> **Always launch from the repository root** (`cd <repo> && ./build.debug/movian`).
> The debug build's `app_dataroot()` is `"./"`, so skins/fonts/translations
> resolve against the current directory — launched from anywhere else, GLW
> fails to load its shaders and the window comes up broken or blank. Every
> `DISPLAY=... /path/to/movian/build.debug/movian` example below assumes the
> working directory is the repo root.
- SSH server running with `X11Forwarding yes` in `/etc/ssh/sshd_config`
- `xauth` installed on both client and server (`xorg-xauth` or equivalent)

### Server-side check

```sh
systemctl is-active sshd          # should print "active"
grep X11Forwarding /etc/ssh/sshd_config  # should say "yes"
which xauth                       # should find /usr/bin/xauth
```

If X11Forwarding is not enabled, edit `/etc/ssh/sshd_config`:

```
X11Forwarding yes
X11DisplayOffset 10
X11UseLocalhost yes
```

Then restart sshd: `sudo systemctl restart sshd`.

## Scenario 1: SSH with X11 Forwarding

You SSH **into** the build machine and want the GUI window to appear on your
local display.

### Linux / macOS client

```sh
ssh -Y user@<build-machine-ip>
# or, for stricter X11 security:
ssh -X user@<build-machine-ip>

echo $DISPLAY   # should show e.g. localhost:10.0
cd /path/to/movian
./build.debug/movian
```

`-Y` (trusted forwarding) is recommended for OpenGL applications because it
bypasses the X11 SECURITY extension, which can interfere with GLX contexts.

### Windows client

Install an X server (VcXsrv, X410, or the built-in WSLg X11 in Windows 11).

With OpenSSH (PowerShell / WSL):

```sh
ssh -Y user@<build-machine-ip>
./build.debug/movian
```

With PuTTY: Connection > SSH > X11 > check "Enable X11 forwarding".

### OpenGL caveat

X11 forwarding uses **indirect rendering** by default — OpenGL calls travel
over the network. This is functional but slow. For full GPU-accelerated
rendering, use VNC or run locally with a display (see Scenario 2/3).

## Scenario 2: Display Server Already Running on the Machine

If the machine already has a graphical session (GNOME, KDE, i3, etc.):

```sh
# Find the running X display
echo $DISPLAY          # e.g. :0
ps aux | grep Xorg     # find the X server process

# Launch Movian in the existing X session
DISPLAY=:0 /path/to/movian/build.debug/movian
```

If no X session is running, start one:

```sh
# startx will start a full X session
startx
# Then in the new X terminal:
/path/to/movian/build.debug/movian
```

## Scenario 3: Headless (No Monitor) via Xvfb

On a server with no physical display:

```sh
apt install xvfb   # Debian/Ubuntu
# or: pacman -S xorg-server-xvfb  (Arch)

# Start a virtual framebuffer
Xvfb :99 -screen 0 1920x1080x24 &

# Launch Movian
DISPLAY=:99 /path/to/movian/build.debug/movian &

# Movian listens on HTTP port 42000
curl -fsS http://localhost:42000/api/diag
curl -fsS http://localhost:42000/api/logfile/0
```

## Scenario 4: VNC for Full OpenGL Performance

VNC provides direct rendering (GPU-accelerated OpenGL) through a remote
desktop session.

```sh
# Install VNC server
apt install tigervnc-standalone-server  # or tigervnc-server

# Set a VNC password
vncpasswd

# Start VNC on display :1 at 1920x1080
vncserver :1 -geometry 1920x1080

# Launch Movian in the VNC session
DISPLAY=:1 /path/to/movian/build.debug/movian &

# Connect from client
vncviewer <server-ip>:5901
```

## Scenario 5: Wayland Forwarding via Waypipe

If the remote machine runs a Wayland compositor, `waypipe` provides
transparent Wayland forwarding over SSH:

```sh
# Install waypipe
apt install waypipe   # Debian/Ubuntu
# or: pacman -S waypipe  (Arch)

# Connect and launch
waypipe ssh user@<host> ./build.debug/movian
```

## Capturing Desktop Environment Variables

When launching through an existing desktop session (KDE, GNOME), you may need
to capture the compositor's environment variables. This is the same technique
used in the Steam Deck remote testing workflow.

### KDE Plasma

```sh
pid=$(pgrep -x plasmashell | head -1)
tr "\0" "\n" < /proc/$pid/environ | \
  grep -E "^(DISPLAY|WAYLAND_DISPLAY|XAUTHORITY|DBUS_SESSION_BUS_ADDRESS)="
```

### GNOME

```sh
pid=$(pgrep -x gnome-shell | head -1)
tr "\0" "\n" < /proc/$pid/environ | \
  grep -E "^(DISPLAY|WAYLAND_DISPLAY|XAUTHORITY|DBUS_SESSION_BUS_ADDRESS)="
```

Export those variables before launching Movian.

## HTTP API for Remote Monitoring

Movian exposes HTTP endpoints on port 42000:

```sh
# Diagnostics
curl -fsS http://<ip>:42000/api/diag

# Log output
curl -fsS http://<ip>:42000/api/logfile/0 -o /tmp/movian-log0.txt

# Open a URL in the running instance
curl -fsS "http://<ip>:42000/api/open?url=<url>"

# Inspect prop tree
curl -fsS http://<ip>:42000/api/prop/global/services/all
```

## Troubleshooting

- `Can't open display` — `DISPLAY` variable is not set. Run `echo $DISPLAY`.
  If empty, X11 forwarding was not negotiated; check sshd_config and reconnect
  with `ssh -Y`.
- `Authorization required` on Wayland — Wayland sessions do not allow X11
  connections by default. Use the env-var capture technique or switch to VNC.
- OpenGL is slow — Indirect rendering over X11 forwarding. Use VNC or run
  locally for GPU-accelerated rendering.
- `Error: could not open runtime directory` — Movian needs a writable home
  directory. Ensure `HOME` is set correctly in the SSH session.
