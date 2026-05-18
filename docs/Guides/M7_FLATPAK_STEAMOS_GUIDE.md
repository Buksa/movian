# M7 Flatpak / SteamOS Guide

Дата: 2026-05-13

Цель: собрать локальный GLW-only Flatpak для SteamOS / Steam Deck и проверить
его как sideload package. Это не Flathub recipe.

## Что уже подготовлено

- Manifest: `support/flatpak/dev.uzver.MovianM7.yml`
- Desktop file: `support/flatpak/dev.uzver.MovianM7.desktop`
- AppStream metadata template: `support/flatpak/dev.uzver.MovianM7.metainfo.xml.in`
- Build wrapper: `support/flatpak/build-local.sh`

Flatpak build использует:

```sh
--disable-gu --disable-webkit --disable-dvd --disable-librtmp
```

Поэтому пакет должен быть GLW/X11/OpenGL-only, без GTK2/GU. DVD отключён для
первого SteamOS MVP, потому что bundled `ext/dvd` падает на более строгом
компиляторе Freedesktop SDK 25.08; для plugin/stream/local-file сценариев DVD
не нужен. TLS берётся из OpenSSL внутри Freedesktop SDK, не из bundled
PolarSSL. RTMP отключён, потому что bundled `rtmpdump` зависит от OpenSSL 1.x
internals (`HMAC_CTX`, `DH->p`, `DH->g`), которые закрыты в OpenSSL 3.
Bundled libav дополнительно получает `--disable-inline-asm` и
`--disable-hwaccels`, чтобы старый bundled libav оставался переносимым внутри
Flatpak SDK.
Для Freedesktop SDK compiler manifest добавляет
`LIBAV_CFLAGS=-Wno-error=incompatible-pointer-types`, потому что старый libav
snapshot иначе падает на pointer-type diagnostics.
Public WSL GLX compatibility определяется во время запуска по WSL
environment/osrelease и не требует отдельного SteamOS configure flag.
Flatpak устанавливает `$PWD/build.flatpak/movian.bundle` как
`/app/bin/showtime`, поэтому отдельный upstream `make install` target не нужен.
Manifest сначала удаляет скопированный `build.flatpak`, чтобы local `type: dir`
source не принёс stale absolute build paths из host checkout внутрь sandbox.
AppStream metadata генерируется во время build из template и получает ту же
`git describe` версию, которую показывает Movian в About/log.

## Установка инструментов в WSL Ubuntu

```sh
sudo apt update
sudo apt install -y \
  flatpak \
  flatpak-builder \
  desktop-file-utils \
  appstream \
  dbus-user-session
```

Добавить Flathub и runtime:

```sh
flatpak remote-add --user --if-not-exists flathub \
  https://dl.flathub.org/repo/flathub.flatpakrepo

flatpak install --user -y flathub \
  org.freedesktop.Platform//25.08 \
  org.freedesktop.Sdk//25.08
```

Если `25.08` ещё недоступен в конкретном окружении, посмотреть доступные
версии:

```sh
flatpak remote-ls flathub --runtime | grep 'org.freedesktop.Sdk'
```

и заменить `runtime-version` в manifest.

## Сборка в WSL Ubuntu

```sh
cd /home/uzver/movian-public-clean
support/flatpak/build-local.sh
```

Ожидаемый файл:

```text
/home/uzver/movian-public-clean/build.flatpak/dev.uzver.MovianM7.flatpak
```

Проверено в WSL Ubuntu 2026-05-13:

- bundle собрался;
- `/app/bin/showtime --help` работает внутри Flatpak build sandbox;
- `ldd /app/bin/showtime` не показывает GTK/GDK/WebKit/RTMP/DVD/VAAPI/VDPAU
  libs.
- manifest сохраняет legacy state directories через `--persist=.hts` и
  `--persist=.cache/movian`, поэтому установленные плагины должны переживать
  перезапуск.
- Gaming Mode без window manager не должен зациклиться на пересоздании
  fullscreen window.

Если wrapper пишет:

```text
flatpak-builder: not found
```

значит сначала нужно поставить пакеты из раздела выше.

Проверить локально:

```sh
flatpak install --user --reinstall --bundle build.flatpak/dev.uzver.MovianM7.flatpak
flatpak run dev.uzver.MovianM7
```

Проверить зависимости внутри sandbox:

```sh
flatpak run --command=sh dev.uzver.MovianM7 -c \
  'ldd /app/bin/showtime | grep -Ei "gtk|gdk|webkit|rtmp|dvd|vaapi|vdpau|libva|nvidia" || true'
```

Ожидаемо: пустой вывод.

До установки bundle можно проверить build sandbox:

```sh
flatpak build build.flatpak-builder /app/bin/showtime --help
flatpak build build.flatpak-builder ldd /app/bin/showtime | \
  grep -Ei 'gtk|gdk|webkit|rtmp|dvd|vaapi|vdpau|libva|nvidia' || true
```

## Перенос на Steam Deck

Собрать лучше в WSL/Ubuntu или другой Linux VM, а на Steam Deck только
установить готовый bundle.

Пример через `scp`:

```sh
scp /home/uzver/movian-public-clean/build.flatpak/dev.uzver.MovianM7.flatpak \
  deck@steamdeck:~/Downloads/
```

На Steam Deck в Desktop Mode:

```sh
flatpak install --user --reinstall --bundle ~/Downloads/dev.uzver.MovianM7.flatpak
flatpak run dev.uzver.MovianM7
```

После обновления bundle плагины, которые пропали в старой сборке, нужно
установить один раз заново. Проверить host-side storage можно так:

```sh
find ~/.var/app/dev.uzver.MovianM7 -path '*installedplugins*' -print
```

Movian сам использует legacy путь `$HOME/.hts/showtime/installedplugins`; в
Flatpak он сохраняется через `--persist=.hts`.

Добавить в Gaming Mode, вариант 1:

1. Steam Desktop Mode.
2. Library -> Add a Non-Steam Game.
3. Выбрать `Movian M7 (GameMode)`, если он появился в списке.
4. Если виден только обычный `Movian M7`, можно выбрать его и в Launch Options
   добавить:

   ```text
   --fullscreen
   ```

Вариант 2, более диагностический: создать host-side launcher script на Deck,
добавить его как Non-Steam Game и получить лог запуска.

```sh
mkdir -p ~/bin
cat > ~/bin/movian-m7-gamemode.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
log_file="${HOME}/movian-m7-gamemode.log"
{
  echo "=== $(date -Iseconds) ==="
  echo "DISPLAY=${DISPLAY:-}"
  echo "WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-}"
  echo "XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-}"
  echo "SteamAppId=${SteamAppId:-}"
  echo "SteamGameId=${SteamGameId:-}"
  /usr/bin/flatpak info dev.uzver.MovianM7 || true
  exec /usr/bin/flatpak run dev.uzver.MovianM7 --fullscreen -d "$@"
} >>"${log_file}" 2>&1
EOF
chmod +x ~/bin/movian-m7-gamemode.sh
```

В Steam выбрать `~/bin/movian-m7-gamemode.sh` как Non-Steam Game. Если Gaming
Mode всё ещё не открывает окно, прислать `~/movian-m7-gamemode.log`.

В свойствах Non-Steam Game лучше выставить:

```text
Target: /home/deck/bin/movian-m7-gamemode.sh
Start In: /home/deck
Launch Options: пусто
```

## Steam Deck gamepad через Steam Input

Movian Linux/GLW получает обычные X11 keyboard/mouse events. Steam Deck
touchscreen работает как pointer input сразу, но дефолтный Steam Input layout
`Gamepad` отдаёт controller events, которые текущая Linux/X11 сборка Movian не
читает напрямую.

Для Gaming Mode выбери у Non-Steam Game controller icon -> Current Layout ->
Edit Layout и задай keyboard-style mapping:

```text
D-pad / Left Stick: Arrow Up / Down / Left / Right
A: Enter
B: Escape
X: Backspace
Y или Menu: Menu key
L1/R1: Page Up / Page Down
Steam Input media play/pause: XF86 Audio Play/Pause, если нужен playback toggle
```

Минимальная проверка: назначить хотя бы D-pad Up на `Arrow Up` и A на `Enter`.
Если фокус в Movian начал двигаться, проблема именно в layout, а не в Flatpak
или GLW window.

Нативный raw controller path через `/dev/input/event*` в старом коде есть
(`src/ipc/devevent.c`), но он не подключён в Linux makefile и потребовал бы
широких Flatpak permissions к input devices. Для Steam Deck MVP лучше держать
управление через Steam Input.

## Сборка прямо на Steam Deck

Не рекомендованный путь: SteamOS immutable, pacman-пакеты могут пропасть после
обновления. Лучше собирать вне Deck.

Если всё-таки нужно:

```sh
sudo steamos-readonly disable
sudo pacman -Syu --needed flatpak-builder base-devel git
```

После этого шаги такие же:

```sh
flatpak remote-add --user --if-not-exists flathub \
  https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install --user -y flathub \
  org.freedesktop.Platform//25.08 \
  org.freedesktop.Sdk//25.08
cd ~/movian
support/flatpak/build-local.sh
```

После сборки read-only mode можно вернуть:

```sh
sudo steamos-readonly enable
```

## Установка готового bundle на Steam Deck без сборки

Это основной рекомендуемый путь: собрать `.flatpak` в WSL/Ubuntu/VM, перенести
на Deck и установить:

```sh
flatpak install --user --reinstall ~/Downloads/dev.uzver.MovianM7.flatpak
flatpak run dev.uzver.MovianM7
```

## Smoke checks

1. `flatpak run dev.uzver.MovianM7 --help`
2. UI opens in Desktop Mode.
3. Log shows OpenGL renderer, not GTK/GU.
4. Plugin repo loads from `https://repo.movian.eu/plugins-v1.json`.
5. Install a plugin, restart Movian, plugin is still present.
6. MP4 playback works.
7. HLS playback works.
8. `/api/screenshot/raw` returns an image.
9. Gaming Mode opens via `Movian M7 (GameMode)` or the diagnostic launcher.

## Known risks

- WSL может быть неудобен для running GUI Flatpak; build может пройти, а запуск
  лучше проверять на Steam Deck.
- Manifest uses `type: dir`, поэтому это local/sideload recipe. Для Flathub
  нужны pinned git/archive sources and hashes.
- `SKIP_SUBMODULE_UPDATE=1` требует, чтобы `ext/libav`, `ext/gumbo-parser` и
  `ext/vmir` уже были populated в исходниках.
- DVD backend отключён в Flatpak MVP. Если он понадобится позже, надо отдельно
  поправить bundled `ext/dvd` под новый compiler.
- Bundled PolarSSL не используется в Flatpak MVP: старый код падает на
  Freedesktop SDK 25.08 из-за более строгой проверки implicit declarations.
- RTMP backend отключён в Flatpak MVP. Если понадобится RTMP, нужен отдельный
  порт bundled `rtmpdump` под OpenSSL 3 или замена зависимости.
- Hardware acceleration в bundled libav отключён явно. Для Steam Deck это
  можно вернуть позже отдельной задачей, но потребуется проверить VAAPI deps и
  sandbox permissions.
- Если Gaming Mode зависает на Steam logo, но Desktop Mode и Big Picture
  работают, сначала использовать host-side diagnostic launcher и смотреть
  `~/movian-m7-gamemode.log`. Это отделяет проблему Flatpak/package от
  gamescope/Steam launch environment.
- Если в логе тысячи повторов `OpenGL Renderer` после `No window manager
  found`, это fullscreen state loop: GLW пересоздает окно каждый кадр. В M7
  Flatpak это исправлено выставлением `is_fullscreen = want_fullscreen` после
  no-WM reopen.
