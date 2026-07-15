/*
 * movian-analyze -- host CLI driving the REAL GLW lexer/preproc/parser
 * outside the application (issue #97, part of the language-tooling
 * umbrella #96). See support/devtools/analyze/shim.c for the symbol
 * contract that makes this link, and tests/tooling/glw/ for the corpus
 * guard and fixtures this tool is validated against.
 *
 * Usage:
 *   movian-analyze --check  [options] <file.view>
 *   movian-analyze --tokens [options] <file.view>
 *
 * Options:
 *   --skin DIR       resolve skin:// against DIR
 *                     (default: <root>/glwskins/<SHOWTIME_GLW_DEFAULT_SKIN>,
 *                     the same default the runtime uses -- see glw.c)
 *   --root DIR        workspace root: dataroot:// maps to this directory,
 *                     and #include/#import targets must resolve inside
 *                     it or --skin (default: CWD)
 *   --max-size BYTES  reject files bigger than this before parsing
 *                     (default: 2 MiB)
 *   --max-depth N      --tokens only: max #import/#include recursion
 *                     depth (default: 32)
 *
 * stdout carries machine-readable JSON only; all diagnostics/trace go to
 * stderr (issue #97 / research pack Part N).
 *
 * --check JSON:
 *   success (exit 0):  {"ok":true,"file":"<path>"}
 *   failure (exit 1):  {"file":"<path>","line":<n>,"error":"<msg>"}
 *   (the file/line/error triple is byte-identical to what the runtime's
 *   #92 TRACE_ERROR line carries after its "GLW [ERROR]: " prefix)
 *
 * --tokens JSON:
 *   success (exit 0):  {"tokens":[{"type":"...","file":"...","line":n,
 *                                  "value":...}, ...]}
 *   top-level file open/lex failure (exit 1): same shape as --check's
 *   failure object. Per-file failures among #include/#import targets
 *   are logged to stderr and skipped (the token stream is the TOLERANT
 *   layer -- see research pack Part L -- so one bad include must not
 *   blank the whole symbol/outline view of the file being edited).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <limits.h>
#include <errno.h>

#include "config.h"
#include "glw.h"
#include "glw_view.h"
#include "misc/pool.h"
#include "misc/buf.h"

#include "movian_analyze.h"

/* fa_load() is declared for the real fileaccess subsystem in
 * fileaccess/fileaccess.h, which we deliberately do not include (it
 * drags in the whole subsystem); shim.c provides a conforming
 * definition, declared here to call it directly from the --tokens
 * import walker. */
buf_t *fa_load(const char *url, ...);


/* ------------------------------------------------------------------- */
/* small JSON writer (stdout only; no library dependency)              */
/* ------------------------------------------------------------------- */

static void
json_string(FILE *out, const char *s)
{
  fputc('"', out);
  for(const unsigned char *p = (const unsigned char *) s; *p; p++) {
    switch(*p) {
    case '"':  fputs("\\\"", out); break;
    case '\\': fputs("\\\\", out); break;
    case '\n': fputs("\\n", out); break;
    case '\r': fputs("\\r", out); break;
    case '\t': fputs("\\t", out); break;
    default:
      if(*p < 0x20)
        fprintf(out, "\\u%04x", *p);
      else
        fputc(*p, out);
    }
  }
  fputc('"', out);
}


/* ------------------------------------------------------------------- */
/* file loading                                                        */
/* ------------------------------------------------------------------- */

/* Reads the top-level, user-supplied file directly -- NOT through
 * fa_load()'s workspace confinement (the caller named this file on the
 * command line; there is nothing to confine it against). Still applies
 * the same size cap as included files, and the same real-runtime
 * open-failure-message parity as shim.c's fa_load() (see
 * analyze_is_absolute_or_scheme() in movian_analyze.h): the real
 * gcv_load() open of the top-level view URL goes through the identical
 * fa_resolve_proto()/fs_open() path #include/#import targets do.
 * Returns NULL and sets *errmsg on failure; *errmsg points at a static
 * buffer valid until the next read_top_file() call. */
static char *
read_top_file(const char *path, size_t max_size, const char **errmsg)
{
  static char errbuf[256];

  FILE *fp = fopen(path, "rb");
  if(fp == NULL) {
    if(analyze_is_absolute_or_scheme(path))
      snprintf(errbuf, sizeof(errbuf), "%s", strerror(errno));
    else
      snprintf(errbuf, sizeof(errbuf), "File not found");
    *errmsg = errbuf;
    return NULL;
  }
  fseek(fp, 0, SEEK_END);
  long sz = ftell(fp);
  fseek(fp, 0, SEEK_SET);
  if(sz < 0) {
    fclose(fp);
    *errmsg = "Unable to determine file size";
    return NULL;
  }
  if((size_t) sz > max_size) {
    fclose(fp);
    snprintf(errbuf, sizeof(errbuf), "File too large (%ld bytes)", sz);
    *errmsg = errbuf;
    return NULL;
  }
  char *data = malloc((size_t) sz + 1);
  size_t rd = fread(data, 1, (size_t) sz, fp);
  fclose(fp);
  if(rd != (size_t) sz) {
    free(data);
    *errmsg = "Short read";
    return NULL;
  }
  data[sz] = 0;
  return data;
}


/* ------------------------------------------------------------------- */
/* --check                                                              */
/* ------------------------------------------------------------------- */

static int
cmd_check(glw_root_t *gr, const char *path)
{
  const char *open_err;
  char *src = read_top_file(path, g_fa_policy.max_file_size, &open_err);
  if(src == NULL) {
    printf("{\"file\":");
    json_string(stdout, path);
    printf(",\"line\":0,\"error\":");
    char msg[512];
    snprintf(msg, sizeof(msg), "Unable to open \"%s\" -- %s", path,
             open_err);
    json_string(stdout, msg);
    printf("}\n");
    return 1;
  }

  rstr_t *file = rstr_alloc(path);
  errorinfo_t ei;
  memset(&ei, 0, sizeof(ei));

  token_t *sof = glw_view_token_alloc(gr);
  sof->type = TOKEN_START;
  sof->file = rstr_dup(file);

  token_t *l = glw_view_lexer(gr, src, &ei, file, sof);
  free(src);
  if(l == NULL)
    goto bad;

  token_t *eof = glw_view_token_alloc(gr);
  eof->type = TOKEN_END;
  eof->file = rstr_dup(file);
  l->next = eof;

  if(glw_view_preproc(gr, sof, &ei, 0) || glw_view_parse(sof, &ei, gr))
    goto bad;

  /* Deliberately not glw_view_free_chain(gr, sof): measured (corpus
   * guard) to walk into TOKEN_FUNCTION dtor callbacks for some
   * successfully-parsed function tokens (e.g. glw_tex_flush_all() via a
   * texture-holding function's dtor in glw_view_token_free()) -- an
   * eval-adjacent cleanup path outside the proven parse-only closure,
   * so it hits an abort-only stub. This is a short-lived CLI process;
   * the OS reclaims the pool on exit. */
  printf("{\"ok\":true,\"file\":");
  json_string(stdout, path);
  printf("}\n");
  return 0;

 bad:
  printf("{\"file\":");
  json_string(stdout, ei.file);
  printf(",\"line\":%d,\"error\":", ei.line);
  json_string(stdout, ei.error);
  printf("}\n");
  return 1;
}


/* ------------------------------------------------------------------- */
/* --tokens                                                             */
/* ------------------------------------------------------------------- */

/* Machine-stable names for every token_type_t the LEXER can produce
 * (see src/ui/glw/glw_view_lexer.c). Synthetic post-parse types
 * (TOKEN_EXPR, TOKEN_RPN, ...) never appear in --tokens output since we
 * stop at lexing/import-splicing and never call glw_view_parse(); they
 * are listed as "?" defensively rather than omitted, so an unexpected
 * type never crashes the JSON writer. */
static const char *
token_type_name(token_type_t t)
{
  switch(t) {
  case TOKEN_HASH:               return "HASH";
  case TOKEN_ASSIGNMENT:         return "ASSIGNMENT";
  case TOKEN_COND_ASSIGNMENT:    return "COND_ASSIGNMENT";
  case TOKEN_REF_ASSIGNMENT:     return "REF_ASSIGNMENT";
  case TOKEN_DEBUG_ASSIGNMENT:   return "DEBUG_ASSIGNMENT";
  case TOKEN_LINK_ASSIGNMENT:    return "LINK_ASSIGNMENT";
  case TOKEN_END_OF_EXPR:        return "END_OF_EXPR";
  case TOKEN_SEPARATOR:          return "SEPARATOR";
  case TOKEN_BLOCK_OPEN:         return "BLOCK_OPEN";
  case TOKEN_BLOCK_CLOSE:        return "BLOCK_CLOSE";
  case TOKEN_LEFT_PARENTHESIS:   return "LEFT_PARENTHESIS";
  case TOKEN_RIGHT_PARENTHESIS:  return "RIGHT_PARENTHESIS";
  case TOKEN_LEFT_BRACKET:       return "LEFT_BRACKET";
  case TOKEN_RIGHT_BRACKET:      return "RIGHT_BRACKET";
  case TOKEN_DOT:                return "DOT";
  case TOKEN_ADD:                return "ADD";
  case TOKEN_SUB:                return "SUB";
  case TOKEN_MULTIPLY:           return "MULTIPLY";
  case TOKEN_DIVIDE:             return "DIVIDE";
  case TOKEN_MODULO:             return "MODULO";
  case TOKEN_DOLLAR:             return "DOLLAR";
  case TOKEN_AMPERSAND:          return "AMPERSAND";
  case TOKEN_BOOLEAN_AND:        return "BOOLEAN_AND";
  case TOKEN_BOOLEAN_OR:         return "BOOLEAN_OR";
  case TOKEN_BOOLEAN_XOR:        return "BOOLEAN_XOR";
  case TOKEN_EQ:                 return "EQ";
  case TOKEN_NEQ:                return "NEQ";
  case TOKEN_BOOLEAN_NOT:        return "BOOLEAN_NOT";
  case TOKEN_NULL_COALESCE:      return "NULL_COALESCE";
  case TOKEN_LT:                 return "LT";
  case TOKEN_GT:                 return "GT";
  case TOKEN_QUESTIONMARK:       return "QUESTIONMARK";
  case TOKEN_COLON:               return "COLON";
  case TOKEN_RSTRING:            return "RSTRING";
  case TOKEN_CSTRING:            return "CSTRING";
  case TOKEN_FLOAT:              return "FLOAT";
  case TOKEN_EM:                 return "EM";
  case TOKEN_INT:                return "INT";
  case TOKEN_IDENTIFIER:         return "IDENTIFIER";
  case TOKEN_VOID:               return "VOID";
  case TOKEN_START:              return "START";
  case TOKEN_END:                return "END";
  default:                       return "?";
  }
}

typedef struct token_sink {
  FILE *out;
  int count;
} token_sink_t;

static void
emit_token(token_sink_t *sink, token_t *t)
{
  if(t->type == TOKEN_START || t->type == TOKEN_END)
    return; /* sentinels, not real source tokens */

  if(sink->count++ > 0)
    fputc(',', sink->out);

  fprintf(sink->out, "{\"type\":");
  json_string(sink->out, token_type_name(t->type));
  fprintf(sink->out, ",\"file\":");
  json_string(sink->out, rstr_get(t->file));
  fprintf(sink->out, ",\"line\":%d", t->line);

  switch(t->type) {
  case TOKEN_RSTRING:
  case TOKEN_IDENTIFIER:
    fprintf(sink->out, ",\"value\":");
    json_string(sink->out, rstr_get(t->t_rstring));
    break;
  case TOKEN_FLOAT:
    fprintf(sink->out, ",\"value\":%g", t->t_float);
    break;
  case TOKEN_INT:
    fprintf(sink->out, ",\"value\":%d", t->t_int);
    break;
  default:
    break;
  }
  fputc('}', sink->out);
}

#define MAX_IMPORT_DEPTH_DEFAULT 32
#define MAX_VISITED 256

typedef struct import_walk {
  glw_root_t *gr;
  token_sink_t *sink;
  int max_depth;
  int visited_count;
  char visited[MAX_VISITED][PATH_MAX];
} import_walk_t;

static int
already_visited(import_walk_t *w, const char *path)
{
  char real[PATH_MAX];
  const char *key = realpath(path, real) != NULL ? real : path;
  for(int i = 0; i < w->visited_count; i++)
    if(!strcmp(w->visited[i], key))
      return 1;
  if(w->visited_count < MAX_VISITED)
    snprintf(w->visited[w->visited_count++], PATH_MAX, "%s", key);
  return 0;
}

static void lex_and_walk(import_walk_t *w, rstr_t *file, int depth);

/* Scans one already-lexed, flat token chain for '#import "X"' / '#include
 * "X"' directive shapes -- the same syntactic pattern
 * glw_view_preproc.c's real preprocessor recognizes -- WITHOUT running
 * the real (macro-capable, single-error, destructive) preprocessor.
 * This is deliberate: --tokens is the tolerant layer (research pack Part
 * L), so a macro error elsewhere in the file must not prevent us from
 * following an #import that is itself fine. Each followed target is
 * lexed with the REAL glw_view_lexer() and recursively scanned the same
 * way, giving every token honest per-file provenance (t->file) without
 * ever invoking glw_view_preproc(). */
static void
scan_for_imports(import_walk_t *w, token_t *chain, int depth)
{
  if(chain == NULL)
    return;

  if(depth >= w->max_depth) {
    fprintf(stderr, "movian-analyze: max import depth (%d) reached at %s, "
            "not following further\n", w->max_depth,
            rstr_get(chain->file));
    return;
  }

  for(token_t *t = chain; t != NULL; t = t->next) {
    if(t->type != TOKEN_HASH || t->next == NULL)
      continue;
    token_t *kw = t->next;
    if(kw->type != TOKEN_IDENTIFIER)
      continue;
    const char *kwname = rstr_get(kw->t_rstring);
    if(strcmp(kwname, "import") && strcmp(kwname, "include"))
      continue;
    token_t *target = kw->next;
    if(target == NULL || target->type != TOKEN_RSTRING)
      continue;

    rstr_t *resolved = glw_resolve_path(target->t_rstring, t->file, w->gr,
                                        NULL);
    if(resolved == NULL)
      continue;
    lex_and_walk(w, resolved, depth + 1);
    rstr_release(resolved);
  }
}

static void
lex_and_walk(import_walk_t *w, rstr_t *file, int depth)
{
  if(already_visited(w, rstr_get(file)))
    return;

  /* shim.c's fa_load() ignores all varargs (no FA_LOAD_* tag parsing --
   * see its header comment); pass a bare NULL sentinel for readability
   * only, matching the real fa_load()'s calling convention. */
  buf_t *buf = fa_load(rstr_get(file), NULL);
  if(buf == NULL) {
    fprintf(stderr, "movian-analyze: --tokens: unable to open imported "
            "file \"%s\", skipping\n", rstr_get(file));
    return;
  }

  errorinfo_t ei;
  memset(&ei, 0, sizeof(ei));
  token_t *sof = glw_view_token_alloc(w->gr);
  sof->type = TOKEN_START;
  sof->file = rstr_dup(file);

  token_t *l = glw_view_lexer(w->gr, buf_cstr(buf), &ei, file, sof);
  buf_release(buf);
  if(l == NULL) {
    fprintf(stderr, "movian-analyze: --tokens: lexer error in imported "
            "file %s:%d: %s, skipping its tokens\n", ei.file, ei.line,
            ei.error);
    glw_view_free_chain(w->gr, sof);
    return;
  }

  for(token_t *t = sof->next; t != NULL; t = t->next)
    emit_token(w->sink, t);

  scan_for_imports(w, sof->next, depth);
  glw_view_free_chain(w->gr, sof);
}

static int
cmd_tokens(glw_root_t *gr, const char *path, int max_depth)
{
  const char *open_err;
  char *src = read_top_file(path, g_fa_policy.max_file_size, &open_err);
  if(src == NULL) {
    printf("{\"file\":");
    json_string(stdout, path);
    printf(",\"line\":0,\"error\":");
    char msg[512];
    snprintf(msg, sizeof(msg), "Unable to open \"%s\" -- %s", path,
             open_err);
    json_string(stdout, msg);
    printf("}\n");
    return 1;
  }

  rstr_t *file = rstr_alloc(path);
  errorinfo_t ei;
  memset(&ei, 0, sizeof(ei));

  token_t *sof = glw_view_token_alloc(gr);
  sof->type = TOKEN_START;
  sof->file = rstr_dup(file);

  token_t *l = glw_view_lexer(gr, src, &ei, file, sof);
  free(src);
  if(l == NULL) {
    glw_view_free_chain(gr, sof);
    rstr_release(file);
    printf("{\"file\":");
    json_string(stdout, ei.file);
    printf(",\"line\":%d,\"error\":", ei.line);
    json_string(stdout, ei.error);
    printf("}\n");
    return 1;
  }

  token_sink_t sink = { .out = stdout, .count = 0 };
  printf("{\"tokens\":[");
  for(token_t *t = sof->next; t != NULL; t = t->next)
    emit_token(&sink, t);

  import_walk_t w;
  memset(&w, 0, sizeof(w));
  w.gr = gr;
  w.sink = &sink;
  w.max_depth = max_depth;
  already_visited(&w, path); /* seed with the top file so a cyclic
                                #import back to it is a no-op */
  scan_for_imports(&w, sof->next, 0);

  printf("]}\n");

  glw_view_free_chain(gr, sof);
  rstr_release(file);
  return 0;
}


/* ------------------------------------------------------------------- */
/* setup + argv                                                        */
/* ------------------------------------------------------------------- */

static void
add_root(const char *dir)
{
  if(dir == NULL || dir[0] == 0 || g_fa_policy.root_count >= ANALYZE_MAX_ROOTS)
    return;
  char real[PATH_MAX];
  if(realpath(dir, real) == NULL)
    return; /* doesn't exist (yet); nothing to confine to */
  snprintf(g_fa_policy.roots[g_fa_policy.root_count++], PATH_MAX, "%s",
           real);
}

static void
usage(const char *prog)
{
  fprintf(stderr,
          "usage: %s --check|--tokens [--skin DIR] [--root DIR] "
          "[--max-size BYTES] [--max-depth N] <file.view>\n", prog);
}

int
main(int argc, char **argv)
{
  const char *mode = NULL;
  const char *file = NULL;
  const char *skin_opt = NULL;
  const char *root_opt = NULL;
  size_t max_size = 2 * 1024 * 1024;
  int max_depth = MAX_IMPORT_DEPTH_DEFAULT;

  for(int i = 1; i < argc; i++) {
    if(!strcmp(argv[i], "--check") || !strcmp(argv[i], "--tokens")) {
      mode = argv[i];
    } else if(!strcmp(argv[i], "--skin") && i + 1 < argc) {
      skin_opt = argv[++i];
    } else if(!strcmp(argv[i], "--root") && i + 1 < argc) {
      root_opt = argv[++i];
    } else if(!strcmp(argv[i], "--max-size") && i + 1 < argc) {
      max_size = (size_t) strtoull(argv[++i], NULL, 10);
    } else if(!strcmp(argv[i], "--max-depth") && i + 1 < argc) {
      max_depth = atoi(argv[++i]);
    } else if(!strcmp(argv[i], "-h") || !strcmp(argv[i], "--help")) {
      usage(argv[0]);
      return 0;
    } else if(argv[i][0] != '-') {
      file = argv[i];
    } else {
      fprintf(stderr, "movian-analyze: unrecognized option '%s'\n",
              argv[i]);
      usage(argv[0]);
      return 2;
    }
  }

  if(mode == NULL || file == NULL) {
    usage(argv[0]);
    return 2;
  }

  char cwd[PATH_MAX];
  if(getcwd(cwd, sizeof(cwd)) == NULL)
    strcpy(cwd, ".");
  const char *root = root_opt ? root_opt : cwd;

  char skin_default[PATH_MAX];
  snprintf(skin_default, sizeof(skin_default), "%s/glwskins/%s", root,
           SHOWTIME_GLW_DEFAULT_SKIN);
  const char *skin = skin_opt ? skin_opt : skin_default;

  memset(&g_fa_policy, 0, sizeof(g_fa_policy));
  g_fa_policy.max_file_size = max_size;
  /* dataroot:// rewrites relative to workspace_root; when --root wasn't
   * given, use "." (not the resolved absolute cwd) so a missing
   * dataroot:// target fails with the SAME message class ("File not
   * found", a relative-path failure -- see shim.c's fa_load() parity
   * notes) the real app_dataroot() == "./" produces. An explicit --root
   * is used verbatim: it's already an analyzer-only convenience with no
   * real-runtime equivalent to match. */
  snprintf(g_fa_policy.workspace_root, PATH_MAX, "%s", root_opt ? root_opt : ".");
  add_root(root);
  add_root(skin);

  glw_root_t gr;
  memset(&gr, 0, sizeof(gr));
  gr.gr_token_pool = pool_create("movian-analyze-tokens", sizeof(token_t),
                                  POOL_ZERO_MEM);
  gr.gr_skin = strdup(skin);

  int rc;
  if(!strcmp(mode, "--check"))
    rc = cmd_check(&gr, file);
  else
    rc = cmd_tokens(&gr, file, max_depth);

  return rc;
}
