# Bundled Module Update Plan

Use this plan when deciding whether to update a bundled third-party module.
The goal is to keep module work small, reviewable, and tied to a concrete
Linux, Flatpak, plugin, or platform smoke test.

## Policy

- Do not bulk-update submodules.
- Keep each module update in its own branch and pull request.
- Prefer exact commits or upstream release tags over floating branch updates.
- Keep compatibility-only source changes separate from submodule pointer
  updates unless the module update cannot build without them.
- Record the audit output, build commands, and runtime smoke in the pull
  request body.
- For Linux and Flatpak work, use
  `docs/Guides/LINUX_FLATPAK_SMOKE_CHECKLIST.md`.

## Audit Command

Run the read-only helper from the repository root:

```sh
support/check-submodules.sh
```

The helper compares gitlinks in `HEAD` with the branch configured in
`.gitmodules`. It does not initialize, fetch, or update submodule checkouts.

For automation or CI-style checks:

```sh
support/check-submodules.sh --fail-on-outdated
```

That mode exits non-zero when any configured branch has moved. A non-zero
result is not automatically a bug; it is a prompt to decide whether the moved
module is in scope.

## Snapshot

Audit snapshot from May 27, 2026:

| Module | Configured branch | Pinned | Upstream | Status |
| --- | --- | --- | --- | --- |
| `ext/libav` | `release/4.4` | `c52a5c913acc` | `c52a5c913acc` | current |
| `ext/rtmpdump` | `master` | `75416eefebbd` | `75416eefebbd` | current |
| `ext/libntfs_ext` | `master` | `6bb9475c9fc4` | `6bb9475c9fc4` | current |
| `ext/gumbo-parser` | `master` | `6fd66040a8ee` | `6fd66040a8ee` | current |
| `ios/freetype2-ios` | `master` | `a41c5be05c8e` | `a41c5be05c8e` | current |
| `ext/vmir` | `master` | `e81176b539a3` | `e81176b539a3` | current |
| `ext/libyuv` | `main` | `9b9cfeff9720` | `de63bd90f439` | outdated, parked |

The only configured branch that has moved is `ext/libyuv`. Leave it pinned
until Android or another libyuv user becomes active work.

## Current Decisions

| Module | Current role | Decision |
| --- | --- | --- |
| `ext/libav` | Bundled FFmpeg media backend for Linux and Flatpak. | Hold at FFmpeg `n4.4.7` until the modern system FFmpeg migration is a dedicated compatibility project. |
| `ext/gumbo-parser` | HTML parsing used by ECMAScript/plugin-facing code. | No pointer update needed. Keep existing script-visible API behavior stable; compatibility aliases belong in a separate PR. |
| `ext/rtmpdump` | RTMP backend in Linux debug builds; disabled in the Flatpak profile. | No pointer update needed. If RTMP is revisited, decide first whether to modernize, disable by default, or remove. |
| `ext/vmir` | Native plugin bitcode support. | No pointer update needed. Treat changes as plugin runtime work. |
| `ext/libntfs_ext` | PS3 NTFS support. | Out of current Linux/Flatpak/WSL scope. Update only with PS3-focused smoke. |
| `ios/freetype2-ios` | iOS support asset. | Out of current Linux/Flatpak/WSL scope. Update only with iOS-focused smoke. |
| `ext/libyuv` | Android/video conversion path. | Intentionally out of scope for now; track separately. |

Vendored directories that are not git submodules, such as `ext/polarssl-1.3`,
`ext/sqlite`, `ext/duktape`, `ext/nanosvg`, and `ext/minilibs`, are not covered
by `support/check-submodules.sh`. Treat them as separate source-import updates
only when there is a specific build, security, or platform reason.

## Update Workflow

For a real module update:

1. Create a dedicated branch, for example `public/update-gumbo-parser`.
2. Run `support/check-submodules.sh` and record the starting point.
3. Inspect the upstream commit range before changing the gitlink.
4. Choose one exact upstream commit or tag.
5. Update only that module pointer and the minimal compatibility fixes needed
   to build it.
6. Run the module-specific smoke below.
7. Put the before/after commits and smoke output in the pull request body.

Avoid updating multiple module pointers in one PR. If two modules must move
together, explain the build dependency in the PR body.

## Module-Specific Smoke

Use these checks in addition to `git diff --check`.

| Module | Minimum checks |
| --- | --- |
| `ext/libav` | `./support/configure-linux-debug.sh`, `make BUILD=debug -j$(nproc)`, `./build.debug/movian --help`, local media playback, HLS playback when touched, Flatpak build, Flatpak `/app/bin/showtime --help`. If muxing or recorder paths changed, verify the output with `ffprobe`. |
| `ext/gumbo-parser` | Linux debug build, `./build.debug/movian --help`, plugin route smoke for an HTML-parsing plugin or fixture, and no script-visible API rename in the same PR. |
| `ext/rtmpdump` | Linux debug build with the intended TLS backend, `./build.debug/movian --help`, and an RTMP playback smoke if a stable test URL is available. Flatpak checks are needed only if the Flatpak profile enables RTMP. |
| `ext/vmir` | Linux debug build, `./build.debug/movian --help`, and a native bitcode/plugin smoke when a fixture is available. |
| `ext/libntfs_ext` | PS3-focused build and NTFS mount/file smoke. Not covered by the Linux/Flatpak baseline. |
| `ios/freetype2-ios` | iOS-focused build and font rendering smoke. Not covered by the Linux/Flatpak baseline. |
| `ext/libyuv` | Android or other active libyuv platform build plus video conversion/camera path smoke. Not covered by the current Linux/Flatpak baseline. |

## Near-Term Order

1. Keep `ext/libav` at FFmpeg `n4.4.7` while media smoke remains stable.
2. Use `support/check-submodules.sh` in future module PRs to show what moved.
3. Leave `ext/libyuv` for its own decision issue.
4. Do not start `rtmpdump`, `libntfs_ext`, iOS, or libyuv work until the target
   platform and manual smoke path are available.
5. Revisit modern system FFmpeg as a separate compatibility project, not as a
   simple submodule bump.
