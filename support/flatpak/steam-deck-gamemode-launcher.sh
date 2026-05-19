#!/usr/bin/env bash
set -euo pipefail

log_file="${HOME}/movian-gamemode.log"

{
  echo "=== $(date -Iseconds) ==="
  echo "DISPLAY=${DISPLAY:-}"
  echo "WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-}"
  echo "XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-}"
  echo "SteamAppId=${SteamAppId:-}"
  echo "SteamGameId=${SteamGameId:-}"
  echo "STEAM_COMPAT_CLIENT_INSTALL_PATH=${STEAM_COMPAT_CLIENT_INSTALL_PATH:-}"
  /usr/bin/flatpak info dev.uzver.Movian || true
  exec /usr/bin/flatpak run dev.uzver.Movian --fullscreen -d "$@"
} >>"${log_file}" 2>&1
