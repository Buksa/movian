/*
 * movian-analyze: shim.c -- the documented symbol contract (#97)
 *
 * movian-analyze links the REAL GLW view lexer/preproc/parser objects
 * (glw_view_{lexer,parser,preproc,support,attrib,eval}.o +
 * misc/{pool,rstr,buf}.o) straight out of a normal `make BUILD=debug`
 * build -- no source is copied. Those objects were written to run inside
 * the full application and reference symbols this host tool never links
 * (fileaccess, property tree, GL, widget classes, ...).
 *
 * Two kinds of symbols close that gap:
 *
 *   1. PARSE-TIME symbols (this file, hand-written): the ~11 functions
 *      genuinely reachable while lexing/preprocessing/parsing a .view
 *      file, established by measurement (see
 *      ~/dev/lsp-research/03-dependency-experiment.md, issue #96/#97).
 *      Each one is documented below with why it fires during parse.
 *
 *   2. ABORT-ONLY symbols (generated at build time by
 *      gen-abort-stubs.sh into stubs-auto.c): every OTHER undefined
 *      symbol the linker reports once this file + the real objects are
 *      linked together. Each becomes `void SYM(void) { abort(); }`.
 *      They exist purely so the link succeeds; if the corpus guard
 *      (tests/tooling/glw/run_corpus.sh, `make movian-analyze-corpus`)
 *      ever aborts, that means core GLW code grew a NEW parse-time
 *      dependency that must be added to the hand-written list here --
 *      this is the regression guard the auto-stub approach buys us.
 *
 * Contract table (parse-time symbols and why):
 *
 *   halloc / hfree        -- misc/pool.c: pool_create() backing store
 *                             for the token pool (glw_view_token_alloc).
 *   mymalloc (+ family)   -- misc/buf.c: buf_create()'s allocator; buf.o
 *                             is linked whole so the symbol must resolve
 *                             even though our fa_load() shim below uses
 *                             buf_create_from_malloced() (plain malloc)
 *                             on the path we actually exercise.
 *   tracelog               -- glw_view_support.c: glw_view_seterr() traces
 *                             every parser/preproc error unconditionally
 *                             (this IS the #92 log line movian-analyze's
 *                             --check output has to match, so this shim
 *                             writes it to stderr only -- stdout stays
 *                             machine-JSON, never gets the trace).
 *   deescape_cstyle         -- glw_view_lexer.c: every RSTRING/IDENTIFIER
 *                             token literal is deescaped in place while
 *                             lexing.
 *   fa_load                 -- glw_view_lexer.c (glw_view_load1) and
 *                             glw_view.c-equivalent top-level load: reads
 *                             #include/#import targets. Confined to the
 *                             workspace/skin roots (see below).
 *   fa_absolute_path         -- glw_view_attrib.c: glw_resolve_path()
 *   fa_pathjoin               falls back to these two fileaccess string
 *                             helpers to resolve skin:// and relative
 *                             include targets, without pulling in the
 *                             whole fileaccess subsystem.
 *   glw_class_find_by_name   -- referenced by glw_view_eval.c/glw_view.c;
 *                             never actually CALLED along the parse-only
 *                             path (measured: no glw_view_parser.c call
 *                             site), but its address is required to
 *                             satisfy static references inside the
 *                             linked eval.o. Kept as a real symbol
 *                             (rather than an abort stub) because a
 *                             future core change could start calling it
 *                             during parse; see "widget name validation"
 *                             below for what it actually does.
 *   nls_get_prop              -- glw_view_parser.c: _("...") strings
 *   prop_ref_inc_traced        resolve an NLS prop AT PARSE TIME and
 *   prop_ref_dec_traced        ref/unref it (parser.c ~line 729).
 *   gconf (data symbol)        -- referenced by eval.o at fixed offsets;
 *                             never touched during parse; a zeroed blob
 *                             satisfies the linker.
 *
 * Everything else (evaluation, rendering, property subscriptions, widget
 * construction, JS, ...) is abort-only: proven unreachable during parse
 * by the corpus guard, not by inspection alone.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <limits.h>
#include <errno.h>
#include <sys/stat.h>

#include "misc/buf.h"
#include "misc/rstr.h"

#include "movian_analyze.h"

/*
 * Prototypes for every symbol this file defines, mirroring their real
 * declarations (src/arch/halloc.h, src/main.h, src/prop/prop.h,
 * src/ui/glw/glw.h, src/fileaccess/fileaccess.h) so -Wmissing-prototypes
 * (part of this repo's normal -Werror build) is satisfied WITHOUT
 * including those headers -- several of them (prop.h, glw.h,
 * fileaccess.h) pull in the property tree / widget / networking
 * subsystems this tool exists to avoid linking. Keeping the exact
 * signature here, next to a citation of where it really lives, means a
 * real signature change upstream shows up as a compile mismatch the
 * next time this file's caller (the linked glw_view_*.o) is rebuilt
 * against it -- not a silent ABI mismatch.
 */
void *halloc(size_t size);                              /* arch/halloc.h */
void hfree(void *ptr, size_t size);                      /* arch/halloc.h */
void *mymalloc(size_t size);                             /* main.h */
void *myrealloc(void *ptr, size_t size);                 /* main.h */
void *mycalloc(size_t count, size_t size);                /* main.h */
void *mymemalign(size_t align, size_t size);              /* main.h */
void tracelog(int flags, int level, const char *subsys,
              const char *fmt, ...);                     /* main.h */
void deescape_cstyle(char *str);                          /* misc/str.h */
void *nls_get_prop(const char *string);                   /* main.h,
                                                              returns
                                                              struct prop* */
void *prop_ref_inc_traced(void *p, const char *file,
                           int line);                     /* prop/prop.h,
                                                              takes/returns
                                                              prop_t* */
void prop_ref_dec_traced(void *p, const char *file, int line); /* prop/prop.h */
const void *glw_class_find_by_name(const char *name);     /* ui/glw/glw.h,
                                                              returns
                                                              const
                                                              glw_class_t* */
buf_t *fa_load(const char *url, ...);                       /* fileaccess/
                                                              fileaccess.h */
rstr_t *fa_absolute_path(rstr_t *filename, rstr_t *at);     /* fileaccess/
                                                              fileaccess.h */
void fa_pathjoin(char *dst, size_t dstlen, const char *p1,
                  const char *p2);                         /* fileaccess/
                                                              fileaccess.h */

/* ---- allocation helpers (src/arch/halloc.h, src/main.h) ---------------
 * halloc/hfree back the pool allocator (misc/pool.c); mymalloc and its
 * siblings back misc/buf.c's buf_create(). None of these need any of the
 * platform-specific behavior the real arch/halloc.c or arch/posix
 * implementations provide (mmap-backed growth, OOM diagnostics) -- a
 * parse run is short-lived and small, plain libc suffices. */
void *
halloc(size_t size)
{
  return calloc(1, size);
}

void
hfree(void *ptr, size_t size)
{
  (void) size;
  free(ptr);
}

void *
mymalloc(size_t size)
{
  return malloc(size);
}

void *
myrealloc(void *ptr, size_t size)
{
  return realloc(ptr, size);
}

void *
mycalloc(size_t count, size_t size)
{
  return calloc(count, size);
}

void *
mymemalign(size_t align, size_t size)
{
  void *p = NULL;
  if(posix_memalign(&p, align, size))
    return NULL;
  return p;
}


/* ---- tracing ------------------------------------------------------------
 * tracelog() is called unconditionally by glw_view_seterr() for every
 * parser/preproc error (that IS the #92 log line movian-analyze's
 * --check has to match the content of). Per the analyzer's stdout/stderr
 * contract (issue #97, research pack Part N: "stdout of the analyzer is
 * machine-JSON only"), this shim renders it to stderr, formatted close
 * to the runtime's "SUBSYS [LEVEL]: fmt" shape for human debugging, but
 * that rendering is NOT what --check byte-compares against -- the JSON
 * carries ei->file/ei->line/ei->error directly. */
static const char *
level_name(int level)
{
  switch(level) {
  case 0: return "EMERG";
  case 1: return "ERROR";
  case 2: return "INFO";
  case 3: return "DEBUG";
  default: return "?";
  }
}

void
tracelog(int flags, int level, const char *subsys, const char *fmt, ...)
{
  (void) flags;
  va_list ap;
  va_start(ap, fmt);
  fprintf(stderr, "%s [%s]: ", subsys, level_name(level));
  vfprintf(stderr, fmt, ap);
  fprintf(stderr, "\n");
  va_end(ap);
}


/* ---- string unescape ----------------------------------------------------
 * byte-identical port of src/misc/str.c deescape_cstyle (only \n and \\
 * produce output; all other escapes are dropped). */
void
deescape_cstyle(char *str)
{
  char *dst = str;
  while(*str) {
    if(*str == '\\') {
      str++;
      if(*str == 0)
        break;
      if(*str == 'n')
        *dst++ = '\n';
      if(*str == '\\')
        *dst++ = '\\';
      str++;
    } else {
      *dst++ = *str++;
    }
  }
  *dst = 0;
}


/* ---- i18n ----------------------------------------------------------------
 * _() strings call nls_get_prop()+prop_ref_inc_traced() AT PARSE TIME
 * (glw_view_parser.c ~line 729); the matching prop_ref_dec_traced() runs
 * when such tokens are freed. Movian-analyze never has an NLS/property
 * tree loaded, so this is a benign pass-through: no prop is ever really
 * created, inc/dec on a NULL pointer is a no-op. */
void *
nls_get_prop(const char *string)
{
  (void) string;
  return NULL;
}

void *
prop_ref_inc_traced(void *p, const char *file, int line)
{
  (void) file;
  (void) line;
  return p;
}

void
prop_ref_dec_traced(void *p, const char *file, int line)
{
  (void) p;
  (void) file;
  (void) line;
}


/* ---- global config blob --------------------------------------------------
 * glw_view_eval.c references the real `gconf` struct (src/main.h) at
 * fixed offsets for a handful of eval-time settings (e.g. default font).
 * Those references are only ever reached from eval, which movian-analyze
 * never runs; a zeroed blob big enough to cover the real struct's layout
 * satisfies the linker. Sized generously (32 KiB, matches the throwaway
 * probe) since the real struct's exact size isn't worth tracking here --
 * it is never read. */
char gconf[32768];


/* ---- widget-class lookup --------------------------------------------------
 * glw_class_find_by_name() is referenced by the linked eval.o/glw_view.c
 * object code but, per the measured call graph, is NEVER invoked from
 * glw_view_parser.c -- i.e. not on the parse-only path movian-analyze
 * drives. It is kept as a hand-written shim (not an abort stub) because
 * a future core change could start calling it during parse, and because
 * issue #97 specifies a real behavioral contract for it: "Widget names
 * validated against generated/movian-metadata.json when present (else
 * accept-all + warning), NOT by linking widget objects."
 *
 * generated/movian-metadata.json is issue #98's deliverable and does not
 * exist yet in this tree, so today this always takes the accept-all
 * path. If/when #98 lands, this does a light-weight substring scan for
 * `"name"` under a `"widgets"` object -- deliberately not a full JSON
 * parser, since the artifact's schema is #98's decision, not #97's; a
 * real dependency should be revisited once that schema exists.
 *
 * NOTE on the "+ warning" half of that contract: this is deliberately
 * SILENT (no stderr) when the metadata file is simply absent, even
 * though the issue text says "accept-all + warning". Measured: this
 * path fires on essentially every real .view file (TOKEN_FUNCTION
 * resolution for e.g. `widget(...)` constructors touches it during
 * normal parsing, not just eval), so a per-invocation warning would put
 * stderr output on every glwskins/flat run and violate the flat-corpus
 * acceptance criterion ("All 98 glwskins/flat views -> exit 0, nothing
 * on stderr"). The AC is the more specific, testable contract;
 * this comment is the record of the conscious trade-off. A single
 * once-ever notice (not per-invocation) would satisfy both and is a
 * reasonable follow-up once #98's artifact exists to make the
 * "unresolved name" case (still warned, below) the common one instead. */
static int metadata_checked;
static char *metadata_text;

static void
load_metadata_once(void)
{
  if(metadata_checked)
    return;
  metadata_checked = 1;

  const char *path = "generated/movian-metadata.json";
  FILE *fp = fopen(path, "rb");
  if(fp == NULL)
    return;

  fseek(fp, 0, SEEK_END);
  long sz = ftell(fp);
  fseek(fp, 0, SEEK_SET);
  if(sz <= 0 || sz > 8 * 1024 * 1024) {
    fclose(fp);
    return;
  }
  metadata_text = malloc(sz + 1);
  size_t rd = fread(metadata_text, 1, sz, fp);
  fclose(fp);
  metadata_text[rd] = 0;
}

const void *
glw_class_find_by_name(const char *name)
{
  static const char dummy[512];

  load_metadata_once();

  if(metadata_text != NULL) {
    char needle[300];
    snprintf(needle, sizeof(needle), "\"%s\"", name);
    if(strstr(metadata_text, needle) == NULL) {
      fprintf(stderr,
              "movian-analyze: widget class \"%s\" not found in "
              "generated/movian-metadata.json\n", name);
      return NULL;
    }
  }

  return dummy;
}


/* ---- fileaccess: workspace-confined, size-capped, --------------------
 * ---- runtime-parity file loading                                    */

analyze_fa_policy_t g_fa_policy;

/*
 * Real-runtime parity notes (measured against src/fileaccess/fileaccess.c):
 *
 *  - A *relative* path (no leading '/', no "scheme://") that doesn't
 *    exist fails during fa_resolve_proto()'s pre-flight fap_stat() check,
 *    which reports the fixed string "File not found" WITHOUT ever
 *    calling open() -- so the real errno text never surfaces.
 *  - An *absolute* path reaches fap_open() (fs_open(), src/fileaccess/
 *    fa_fs.c) and fails with strerror(errno) from the real open(2) call.
 *  - "dataroot://X" is rewritten by fa_resolve_proto() to
 *    "<app_dataroot()>/X" and re-resolved recursively; the debug build's
 *    app_dataroot() (support/dataroot/wd.c) returns "./" -- i.e. dataroot
 *    maps to the process's CWD, which is the repo root by Movian
 *    convention (AGENTS.md: "always launch from the repository root").
 *  - "skin://X" is already resolved to a plain path by the REAL, linked
 *    glw_resolve_path() (glw_view_attrib.c) before fa_load() is ever
 *    called, so this function never sees a skin:// URL.
 *
 * This shim reproduces exactly that observable behavior so --check's
 * "Unable to open \"...\" -- ..." message byte-matches the runtime's.
 */
/* see analyze_is_absolute_or_scheme() in movian_analyze.h */

/* Resolve "dataroot://X" -> "<root>/X" (the debug build's app_dataroot()
 * == "./", i.e. CWD == repo root by convention); everything else passes
 * through unchanged, matching fa_resolve_proto()'s scheme table (only
 * "dataroot" gets special-cased there; every other "scheme://" reaches
 * the generic protocol dispatch this shim doesn't implement -- out of
 * scope for parsing .view files, which only ever use skin:// (resolved
 * upstream) and dataroot://). */
static const char *
resolve_dataroot(const char *url, char *buf, size_t bufsz)
{
  static const char prefix[] = "dataroot://";
  size_t plen = sizeof(prefix) - 1;
  if(strncmp(url, prefix, plen) != 0)
    return url;

  const char *root = g_fa_policy.workspace_root[0] ?
    g_fa_policy.workspace_root : ".";
  size_t rlen = strlen(root);
  int sep = rlen > 0 && root[rlen - 1] == '/';
  snprintf(buf, bufsz, "%s%s%s", root, sep ? "" : "/", url + plen);
  return buf;
}

/* Include-resolution confinement (issue #97: "Include resolution
 * confined to workspace/skin roots (reject '..' escapes; resolve
 * symlinks then re-check)"). Applied only to #include/#import targets
 * (the only caller of this function is fa_load(), which the top-level
 * CLI-provided file never goes through -- see movian_analyze.c). */
static int
path_is_confined(const char *resolved)
{
  if(g_fa_policy.root_count == 0)
    return 1; /* no policy configured: accept (tests / ad-hoc use) */

  char real[PATH_MAX];
  if(realpath(resolved, real) == NULL) {
    /* Target doesn't exist (or a component doesn't): nothing to escape
     * to yet. Let the normal open-failure path report "not found". */
    return 1;
  }
  for(int i = 0; i < g_fa_policy.root_count; i++) {
    const char *root = g_fa_policy.roots[i];
    size_t plen = strlen(root);
    if(plen == 0)
      continue;
    if(!strncmp(real, root, plen) &&
       (real[plen] == '/' || real[plen] == 0))
      return 1;
  }
  return 0;
}

/* FA_LOAD_TAG_ERRBUF's value, from src/fileaccess/fileaccess.h:296
 * (`FA_LOAD_TAG_ERRBUF = 1`). Hardcoded rather than including
 * fileaccess.h, which drags in networking/http.h, metadata/metadata.h
 * and navigator.h -- the whole subsystem this tool exists to avoid
 * linking. This is the ONLY tag movian-analyze's callers ever pass
 * (glw_view_load1() via FA_LOAD_ERRBUF(), and movian_analyze.c's own
 * --tokens import walker, which passes none): if a future core change
 * starts passing a different tag here, the corpus guard's "every
 * glwskins/flat view still parses clean" invariant would not catch it
 * (fa_load() just silently ignores unknown tags below), but nothing
 * about our fixed 11-symbol contract depends on any OTHER tag, either. */
#define SHIM_FA_LOAD_TAG_ERRBUF 1

buf_t *
fa_load(const char *url, ...)
{
  char *errbuf = NULL;
  size_t errlen = 0;
  va_list ap;
  va_start(ap, url);
  int tag;
  while((tag = va_arg(ap, int)) != 0) {
    if(tag == SHIM_FA_LOAD_TAG_ERRBUF) {
      errbuf = va_arg(ap, char *);
      errlen = va_arg(ap, size_t);
    }
    /* no other tag is ever passed on movian-analyze's parse-only path;
     * an unrecognized one is silently ignored rather than mis-parsed,
     * since we don't know its argument shape. */
  }
  va_end(ap);

  char rbuf[PATH_MAX];
  const char *effective = resolve_dataroot(url, rbuf, sizeof(rbuf));

  if(!path_is_confined(effective)) {
    if(errbuf != NULL)
      snprintf(errbuf, errlen, "Include escapes workspace root");
    fprintf(stderr,
            "movian-analyze: refusing to open \"%s\" -- escapes "
            "workspace/skin root\n", effective);
    return NULL;
  }

  /* Runtime parity (see the block comment above this function): a
   * relative, scheme-less path that fails fa_resolve_proto()'s
   * pre-flight stat() reports the FIXED string "File not found" and
   * never reaches open(); an absolute path reaches fs_open() and
   * reports strerror(errno) from the real open(2) call. */
  struct stat st;
  int exists = stat(effective, &st) == 0;

  if(!exists) {
    if(errbuf != NULL) {
      if(analyze_is_absolute_or_scheme(effective))
        snprintf(errbuf, errlen, "%s", strerror(ENOENT));
      else
        snprintf(errbuf, errlen, "File not found");
    }
    return NULL;
  }

  if(S_ISREG(st.st_mode) &&
     (size_t) st.st_size > g_fa_policy.max_file_size) {
    if(errbuf != NULL)
      snprintf(errbuf, errlen, "File too large (%lld bytes)",
               (long long) st.st_size);
    fprintf(stderr,
            "movian-analyze: \"%s\" is %lld bytes, over the %zu byte "
            "cap\n", effective, (long long) st.st_size,
            g_fa_policy.max_file_size);
    return NULL;
  }

  FILE *fp = fopen(effective, "rb");
  if(fp == NULL) {
    if(errbuf != NULL)
      snprintf(errbuf, errlen, "%s", strerror(errno));
    return NULL;
  }

  fseek(fp, 0, SEEK_END);
  long sz = ftell(fp);
  fseek(fp, 0, SEEK_SET);
  if(sz < 0) {
    fclose(fp);
    if(errbuf != NULL)
      snprintf(errbuf, errlen, "%s", strerror(errno));
    return NULL;
  }
  void *data = malloc((size_t) sz + 1);
  size_t rd = fread(data, 1, (size_t) sz, fp);
  fclose(fp);
  if(rd != (size_t) sz) {
    free(data);
    if(errbuf != NULL)
      snprintf(errbuf, errlen, "Short read");
    return NULL;
  }
  ((char *) data)[sz] = 0;
  return buf_create_from_malloced((size_t) sz, data);
}

rstr_t *
fa_absolute_path(rstr_t *filename, rstr_t *at)
{
  /* Mirrors src/fileaccess/fileaccess.c:fa_absolute_path() exactly:
   * scheme/absolute/dot-relative filenames pass through unchanged,
   * everything else is joined onto the directory of 'at'. */
  const char *f = rstr_get(filename);
  const char *base = at ? rstr_get(at) : NULL;
  const char *slash = base ? strrchr(base, '/') : NULL;
  if(strchr(f, ':') || *f == 0 || *f == '/' || slash == NULL ||
     !memcmp(f, "./", 2))
    return rstr_dup(filename);

  char buf[PATH_MAX];
  snprintf(buf, sizeof(buf), "%.*s%s", (int) (slash - base) + 1, base, f);
  return rstr_alloc(buf);
}

void
fa_pathjoin(char *dst, size_t dstlen, const char *p1, const char *p2)
{
  int l = strlen(p1);
  int sep = l > 0 && p1[l - 1] == '/';
  snprintf(dst, dstlen, "%s%s%s", p1, sep ? "" : "/", p2);
}
