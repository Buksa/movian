# Public Work Patterns

Use these patterns when preparing small public branches for the `movian6`
line. They are based on changes that have already built, run, and reviewed
cleanly in this checkout.

## Branch Shape

- Start each change from current `movian6`.
- Keep one behavior or one documentation/tooling improvement per branch.
- Split commits by purpose: runtime fix, packaging change, smoke helper, docs.
- Do not rename old public concepts just because the implementation changed.
  Compatibility names such as `libav` options and `showtime` binary paths are
  part of the existing surface.
- Do not push until push is explicitly requested.
- After a merge, fast-forward local `movian6`, delete merged `public/*`
  branches, and update the local session handoff before starting the next task.

## Session Handoff

- Keep a short ignored local session note with the current branch, last merged
  PR, active issue, commands already run, and the next safe step.
- Update the note after every merge and before risky branch switches.
- Treat the note as operator state, not project documentation. Do not commit it
  unless it has been deliberately rewritten as a public guide.
- When a session resumes after a tool or editor restart, check the note first,
  then confirm with `git status --short --branch` before editing.

## Post-Merge Hygiene

- After merging a PR, fast-forward local `movian6` before starting new work.
- Remove merged local and remote topic branches once the merge is visible.
- Run the smallest relevant post-merge smoke:
  - debug build and `--help` for Linux build changes;
  - Flatpak local build and metadata validation for packaging changes;
  - targeted HTTP/API smoke for runtime behavior changes.
- Record the result in the session note or issue before opening the next branch.

## Public-Safe Implementation

- Prefer clean-room patches on top of upstream `movian6`.
- Use reference material only to understand desired behavior, then implement the
  smallest independent public patch.
- Avoid local machine paths, personal source tree names, and internal project
  names in public docs, commits, issues, and pull requests.
- For compatibility fixes, keep existing APIs working and add aliases or
  wrappers instead of replacing old spellings in the same branch.
- When a long-standing typo is script-visible, keep the old spelling and add the
  corrected spelling as an alias in a separate compatibility patch.

## Configure And Build Profiles

- Keep the default Linux debug path boring:

```sh
./support/configure-linux-debug.sh
make BUILD=debug -j$(nproc)
./build.debug/movian --help
```

- Use separate build names for risky checks:

```sh
./support/configure-linux-debug.sh --build=debug-feature-smoke --disable-librtmp
make BUILD=debug-feature-smoke -j$(nproc)
```

- Do not overload `build.debug` when a branch needs an alternate feature matrix.
  A named build directory makes logs and comparisons much easier.
- Keep WSL behavior runtime-detected when possible. Avoid adding configure
  switches for environment quirks unless the build genuinely needs them.
- Let Flatpak be a release-oriented profile. Debug-only developer tools should
  stay enabled in Linux debug builds and disabled in release/Flatpak unless the
  branch intentionally changes that policy.

## Feature Gating

- Preserve the old implementation when it is enabled and add new fallback code
  only behind the inverse feature gate. For example, the FFmpeg RTMP fallback is
  compiled only when `ENABLE_LIBRTMP` is false.
- Disabled runtime actions should be safe no-ops, not crashes. The GLW recorder
  hotkey is the model: debug builds can keep the tool, while release/Flatpak
  builds consume the hotkey without recording.
- Keep fallback protocols explicit. Register exactly the schemes the fallback
  owns and let the old backend win when it is compiled in.

## Backend And Fileaccess

- Check resolver semantics before reusing helpers. Fileaccess protocol matching
  treats `0` as a match, while backend `canhandle()` returns a positive score.
- If a protocol is playable media, a fileaccess implementation may not be
  enough. Add a small backend wrapper when the generic file backend would make
  the wrong assumptions.
- Live streams should normally be treated as non-seekable:
  - keep size unknown;
  - return unsupported for seek;
  - skip filesystem and subtitle scans;
  - avoid probing size with APIs that may consume stream data.
- Verify that the playback path reaches media decode, not just network connect.
  Useful log anchors are `Probed as`, `Starting playback`, stream summaries,
  and `global/media/current/url`.

## FFmpeg And Static Linking

- Prefer upstream release tags for bundled FFmpeg updates. The current baseline
  uses FFmpeg `n4.4.7`.
- Keep existing user-facing option names where renaming would create churn.
- Put API compatibility shims in a small compatibility header instead of
  scattering version checks through unrelated code.
- When enabling optional FFmpeg libraries in a static build, import FFmpeg's
  generated `EXTRALIBS-*` values from `ffbuild/config.mak`. Otherwise optional
  dependencies may configure successfully and fail only at final link.
- For Flatpak RTMP-family protocol support, the successful SDK flags are:

```text
--enable-version3
--enable-gmp
--enable-gnutls
```

`gmp` enables FFmpeg's RTMP encrypted helper, and `gnutls` enables TLS-backed
protocols such as `rtmps`.

## Flatpak Packaging

- Keep the local Flatpak manifest focused on sideload testing, not Flathub
  publishing.
- Prefer SDK-provided libraries over bundling more source when the SDK already
  carries the dependency.
- Generate AppStream release version from `git describe` so Discover,
  `flatpak info`, logs, and About agree.
- Install the app icon from a repository image asset and remove old conflicting
  desktop/icon names from the image.
- Validate both metadata and runtime linkage:

```sh
support/flatpak/build-local.sh
flatpak build build.flatpak-builder /app/bin/showtime --help
desktop-file-validate support/flatpak/dev.uzver.Movian.desktop \
  support/flatpak/dev.uzver.Movian.GameMode.desktop
appstreamcli validate --no-net \
  build.flatpak-builder/files/share/metainfo/dev.uzver.Movian.metainfo.xml
flatpak build build.flatpak-builder ldd /app/bin/showtime |
  rg -i 'gnutls|gmp|librtmp|gtk|gdk|webkit|dvd|vaapi|vdpau|libva|nvidia|cuda' || true
```

For the current Flatpak RTMP profile, `libgmp` and `libgnutls` are expected;
external `librtmp`, GTK/WebKit, DVD, VAAPI, VDPAU, and GPU-vendor libraries are
not expected.

## Smoke Tests

- A good smoke test proves the smallest behavior that can regress.
- Prefer synthetic local inputs over public network resources:
  - generated PNG/WebP files for image paths;
  - generated HLS or RTMP streams for media paths;
  - isolated plugin fixtures for route and prop behavior.
- Start Movian with isolated state:

```sh
--disable-upgrades \
--persistent /tmp/movian-feature-smoke/persistent \
--cache /tmp/movian-feature-smoke/cache
```

- Use the HTTP API for repeatable runtime checks:
  - `/api/open` for navigation and media open;
  - `/api/prop/...` for state assertions;
  - `/api/screenshot/raw` for capture;
  - `/api/image` for image payload checks.
- Assert state, not just logs. Logs tell the story, props prove the current
  model changed.
- Store artifacts under `/tmp/movian-*-smoke` and keep:
  - command or summary;
  - Movian log;
  - generated input/server log;
  - prop state or screenshot when relevant.
- If a UI or runtime wait exceeds two minutes, stop the run and keep artifacts
  instead of letting it drift.

## Pull Request Evidence

Every public pull request should include:

- branch name and commit list;
- concise behavior summary;
- exact configure/build commands;
- runtime smoke command and artifact path;
- Flatpak validation when packaging or release behavior changes;
- dependency grep output when library surface changes;
- issue link when the branch closes or advances an issue.

Keep PR descriptions factual. Avoid local-only source paths and internal names.

## Issues As Backlog

- Use issues for work that should survive session loss.
- Close issues only when the branch fully resolves them.
- If a branch solves part of an issue, reference it in the PR and leave the
  issue open with the remaining smoke or design work.
- Split follow-ups when the next step has a different risk profile. Example:
  release recorder policy and compressed recorder profiles are different tasks.
