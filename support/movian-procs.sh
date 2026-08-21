# Shared: find genuinely running Movian processes.
#
# Sourced, never executed. Defines movian_running_procs(), which prints one
# "<pid> <cmdline>" line per live Movian.
#
# Why this exists at all. Three smoke runners each carried
#
#     pgrep -a -f '/movian( |$)|build\.(debug|release|...)/movian'
#
# and `pgrep -f` matches the whole command line of every process, INCLUDING
# the one doing the matching. Any caller whose command line names the binary
# -- every invocation that exports MOVIAN_BIN, every wrapper, every `ssh
# host "... build.debug/movian ..."` -- matched itself and was told a Movian
# was already running (movian#214). The obvious workaround, setting
# ALLOW_EXISTING_MOVIAN=1, disables the check entirely, which teaches exactly
# the wrong reflex.
#
# `/proc/<pid>/comm` is the kernel's name for the executable. Unlike argv it
# cannot be set by mentioning a path, so a shell that merely talks about
# movian is not a movian. support/devtools/mdevlib/harness.py already
# resolved this the same way for the Python side, including the
# repository-name false positive in #119; this is that correction reaching
# its shell siblings.

# Every pid from here up to init. A wrapper script that launches Movian later
# is a legitimate hit for other purposes, but it is never the "someone else is
# already running one" the callers are asking about.
_movian_proc_self_chain() {
  local pid=$$ chain=" $$ " parent
  while [ -n "$pid" ] && [ "$pid" != "0" ] && [ "$pid" != "1" ]; do
    parent=$(awk '{print $4}' "/proc/$pid/stat" 2>/dev/null) || break
    [ -n "$parent" ] || break
    chain="$chain$parent "
    pid=$parent
  done
  printf '%s' "$chain"
}

movian_running_procs() {
  local chain pid comm cmdline
  if [ ! -d /proc/1 ]; then
    # No procfs. `pgrep -x` matches the executable NAME rather than the
    # command line, so it does not have the self-match defect even though it
    # is weaker than the /proc check below.
    pgrep -ax movian 2>/dev/null || true
    return 0
  fi
  chain=$(_movian_proc_self_chain)
  for dir in /proc/[0-9]*; do
    pid=${dir#/proc/}
    case "$chain" in *" $pid "*) continue ;; esac
    comm=$(cat "$dir/comm" 2>/dev/null) || continue
    [ "$comm" = "movian" ] || continue
    cmdline=$(tr '\0' ' ' < "$dir/cmdline" 2>/dev/null)
    printf '%s %s\n' "$pid" "${cmdline:-$comm}"
  done
}
