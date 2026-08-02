// Guard hook — blocks repeatedly regressed mistakes (see evolve /evolve audit):
//
// Pattern 1 — Orca agent dispatch flags. Regressed at least twice (incident
// journal). Launching mimo in an Orca worktree/automation WITHOUT
// --dangerously-skip-permissions, or codex WITHOUT -a never -s workspace-write,
// stalls on permission prompts in a headless context. The check fires on any
// bash command that invokes these binaries in an orchestration context.
//
// Pattern 4 — bare ./configure.linux --build=debug. Breaks polarssl/rtmp
// unless --enable-polarssl --disable-librtmp (plus LIBAV flags) are passed,
// which is exactly what support/configure-linux-debug.sh does for us. Block
// the bare form and point to the helper. The explicit comparison-build form
// (with --enable-librtmp) is allowed through.
//
// Pattern 6 — gh api without --paginate. Silently truncates to 30 items per
// page. Three #137 status reports were wrong because 30-per-page truncation
// hid 94 of 124 review findings. Always pass --paginate.

function isOrcaDispatchContext(cmd: string): boolean {
  // Only treat as orchestration dispatch if it looks like a background/spawn/
  // worktree handoff — not an interactive `mimo --help`. We look for signals
  // that this is non-interactive: the binary is being launched (not just
  // queried), and it's not obviously a help/version check.
  if (/\b(?:mimo|codex)\b.*--help\b/.test(cmd)) return false
  if (/\b(?:mimo|codex)\b.*--version\b/.test(cmd)) return false
  if (/\bmimo\b.*config\b/.test(cmd) && !/\bmimo\b.*run\b/.test(cmd)) return false
  // Heuristic: orca worktree, background, run, exec, or dispatch tokens.
  return (
    /\borca\b/i.test(cmd) ||
    /worktree/i.test(cmd) ||
    /\bmimo\s+run\b/.test(cmd) ||
    /\bcodex\s+exec\b/.test(cmd) ||
    /\bnohup\b/.test(cmd) ||
    /--bg\b/.test(cmd) ||
    /&\s*$/.test(cmd)
  )
}

function checkMimoFlags(cmd: string): string | null {
  // Find mimo invocations that are launches (run / spawn / exec / bare launch),
  // not help/config. If launching and missing the flag, flag it.
  const mimoLaunch = /\bmimo\b(?!\s*(?:--help|--version|config))/
  if (!mimoLaunch.test(cmd)) return null
  if (!isOrcaDispatchContext(cmd)) return null
  if (/--dangerously-skip-permissions/.test(cmd)) return null
  return (
    "Orca/mimo dispatch is missing `--dangerously-skip-permissions`. Headless Orca " +
    "worktrees have no human to approve prompts — the process will stall. Re-run with " +
    "`mimo run --dangerously-skip-permissions ...`. (incident-class: dispatch-stall; " +
    "verifier-checks Rule 5)"
  )
}

function checkCodexFlags(cmd: string): string | null {
  if (!/\bcodex\b/.test(cmd)) return null
  if (/\bcodex\b.*--help\b/.test(cmd)) return null
  if (!isOrcaDispatchContext(cmd)) return null
  // Require both: -a never (or --ask=never / approval never) AND workspace-write sandbox.
  const hasNever = /(?:^|\s)-a\s+never\b/.test(cmd) || /--ask[=\s]never\b/.test(cmd)
  const hasWorkspaceWrite =
    /(?:^|\s)-s\s+workspace-write\b/.test(cmd) || /--sandbox[=\s]workspace-write\b/.test(cmd)
  if (hasNever && hasWorkspaceWrite) return null
  const missing: string[] = []
  if (!hasNever) missing.push("-a never")
  if (!hasWorkspaceWrite) missing.push("-s workspace-write")
  return (
    "Orca/codex dispatch is missing required flag(s): " +
    missing.join(", ") +
    ". Headless dispatch needs both: `-a never -s workspace-write` or the process " +
    "blocks on approval prompts. (incident-class: dispatch-stall)"
  )
}

function checkGhApiPagination(cmd: string): string | null {
  // `gh api` without --paginate silently truncates to 30 items per page.
  // Three consecutive #137 status reports were wrong because 30-per-page
  // truncation hid 94 of 124 review findings — the same partial-answer
  // failure the review was finding in the checker. Always pass --paginate
  // unless you are certain the endpoint returns ≤30 items.
  if (!/\bgh\s+api\b/.test(cmd)) return null
  if (/--paginate/.test(cmd)) return null
  // Non-GET requests must be exempt: `gh` itself rejects the flag with
  // "the `--paginate` option is not supported for non-GET requests", so
  // demanding it here would deadlock every write call (posting an issue
  // comment, replying to a review) with no command that satisfies both.
  if (/(?:-X|--method)[=\s]+(?:POST|PATCH|PUT|DELETE)\b/i.test(cmd)) return null
  // GraphQL paginates by cursor in the query, not by the Link header, so
  // --paginate is not the control that prevents truncation there.
  if (/\bgh\s+api\s+graphql\b/.test(cmd)) return null
  return (
    "`gh api` without `--paginate` silently truncates to 30 items per page. " +
    "Three #137 status reports were wrong from this. Add `--paginate` to the " +
    "command. (MEMORY.md rule 2026-08-01; review-loop-scaffolding incident)"
  )
}

function checkBareConfigureLinux(cmd: string): string | null {
  // Match a bare `./configure.linux` invocation that configures a debug build
  // WITHOUT the polarssl/rtmp flags. The approved helper is
  // support/configure-linux-debug.sh. We allow:
  //   - references inside support/configure-linux-debug.sh itself (the helper
  //     execs ./configure.linux with the right flags)
  //   - explicit comparison builds that deliberately pass --enable-librtmp
  //   - non-debug builds (out of scope for the regression)
  if (!/configure\.linux/.test(cmd)) return null
  // Is this the helper script being invoked? Allow.
  if (/support\/configure-linux-debug\.sh/.test(cmd)) return null
  // Explicit comparison build with --enable-librtmp → intentional, allow.
  if (/--enable-librtmp/.test(cmd)) return null
  // Only guard debug builds — the regression is about debug builds silently
  // losing polarssl/rtmp parity.
  if (!/--build=debug\b/.test(cmd)) return null
  // If they're passing both required flags manually, allow.
  if (/--enable-polarssl/.test(cmd) && /--disable-librtmp/.test(cmd)) return null
  return (
    "Bare `./configure.linux --build=debug` breaks polarssl/rtmp parity. Use the " +
    "helper instead: `./support/configure-linux-debug.sh` (it passes " +
    "--enable-polarssl --disable-librtmp plus the LIBAV flags). For a deliberate " +
    "librtmp comparison build, pass `--enable-librtmp` explicitly. " +
    "(incident-class: build-parity-loss; AGENTS.md Build And Validation)"
  )
}

export default {
  "tool.execute.before": async (input, output) => {
    if (input.tool !== "bash") return
    const cmd: string = output.args?.command ?? ""
    if (!cmd) return

    const checks = [checkMimoFlags, checkCodexFlags, checkGhApiPagination, checkBareConfigureLinux]
    for (const check of checks) {
      const reason = check(cmd)
      if (reason) {
        output.cancel = true
        output.cancelReason = reason
        return
      }
    }
  },
}
