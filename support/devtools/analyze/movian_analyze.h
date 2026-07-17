/*
 * movian-analyze: shared driver/shim state (#97). Not part of core
 * Movian -- only included by movian_analyze.c and shim.c.
 */
#ifndef MOVIAN_ANALYZE_H
#define MOVIAN_ANALYZE_H

#include <stddef.h>
#include <string.h>
#include <limits.h>

#define ANALYZE_MAX_ROOTS 2

/* Runtime open-failure-message parity (see the block comment above
 * fa_load() in shim.c for the measured fa_resolve_proto()/fs_open()
 * behavior this mirrors): a relative, scheme-less path fails with the
 * fixed string "File not found" without ever reaching open(2); an
 * absolute path (or a "scheme://" URL) reaches open(2) and fails with
 * strerror(errno). Shared by shim.c's fa_load() (for #include/#import
 * targets) and movian_analyze.c's read_top_file() (for the CLI-supplied
 * top-level file), which hit the exact same real-runtime code path for
 * their respective callers (glw_view_load1() vs. gcv_load()). */
static inline int
analyze_is_absolute_or_scheme(const char *url)
{
  if(url[0] == '/')
    return 1;
  return strstr(url, "://") != NULL;
}

/*
 * fa_load()'s policy, set once by movian_analyze.c's argument parsing
 * before the lexer/preproc/parser run. Read-only from shim.c's point of
 * view after main() finishes setup.
 *
 * `roots` are realpath()-resolved workspace/skin directories that
 * #include/#import targets must resolve inside of (see
 * path_is_confined() in shim.c); `workspace_root` (unresolved, as given)
 * is what "dataroot://" is rewritten relative to, matching the debug
 * build's app_dataroot() == "./" == CWD-at-launch convention.
 */
typedef struct analyze_fa_policy {
  char roots[ANALYZE_MAX_ROOTS][PATH_MAX];
  int root_count;
  char workspace_root[PATH_MAX];
  size_t max_file_size;
} analyze_fa_policy_t;

extern analyze_fa_policy_t g_fa_policy;

#endif /* MOVIAN_ANALYZE_H */
