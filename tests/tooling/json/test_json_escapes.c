/*
 * The core's own JSON string decoder, exercised through its public entry
 * point (movian#250).
 *
 * `src/misc/json.c` is the parser every htsmsg JSON reader ends at:
 * `htsmsg_json_deserialize2` calls `json_deserialize` (htsmsg_json.c:228-230)
 * and `plugins.c:628` reads every plugin manifest through it. A wrong
 * character here is a wrong plugin id, a wrong service name, a wrong field in
 * any API payload -- never a crash, which is why it lasted since 3fa120ff7
 * (2013-08-04).
 *
 * The file under test is the real one. `utf8_put`, `utf8_get` and
 * `my_str2double` are supplied here rather than linked from `str.o`, which
 * drags in the charset tables, libav and gconf. That substitution is safe for
 * exactly this test and not in general: every code point it asks about is
 * below 0x80, where UTF-8 is one byte and `utf8_put`'s only interesting
 * behaviour -- dropping U+FFFE, U+FFFF and D800..DFFF (str.c:687-688) --
 * cannot apply. A test that needed those would have to link the real one.
 *
 * NOT a gate, deliberately. It was written to establish the defect and to
 * show the fix, it did both, and that evidence is recorded on movian#250 and
 * movian#252. Nothing runs it on a push: the fix is a single expression, and
 * paying for it on every push forever is not what it is worth.
 *
 * Run it when you touch this decoder -- which is the case it was built for,
 * since the defect it pins survived a replacement of the whole decoder in
 * 2011 and a 166-line rewrite of this very function in 2013. It sweeps all
 * 22 hex digit characters rather than the six that were wrong, so it answers
 * a rewrite and not only an edit.
 *
 *   cc -I src -Werror -o /tmp/t tests/tooling/json/test_json_escapes.c \
 *      src/misc/json.c && /tmp/t
 *
 * Exit 0 and "OK: 0 failure(s)". Restoring `- 'F' + 10` in json.c:72 turns it
 * red, which is how you check the test still measures something.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "misc/json.h"

int utf8_put(char *out, int c)
{
  if(c == 0xfffe || c == 0xffff || (c >= 0xD800 && c < 0xE000))
    return 0;
  if(c < 0x80) {
    if(out) *out = c;
    return 1;
  }
  if(out) { *out++ = 0xc0 | (c >> 6); *out = 0x80 | (c & 0x3f); }
  return 2;
}

int utf8_get(const char **s)
{
  return *(*s)++;
}

double my_str2double(const char *str, const char **endptr)
{
  return strtod(str, (char **)endptr);
}

/* The smallest deserializer that can answer "what is the id?". */
struct map { char *id; };

static void *create_map(void *o) { (void)o; return calloc(1, sizeof(struct map)); }
static void *create_list(void *o) { (void)o; return calloc(1, sizeof(struct map)); }
static void destroy_obj(void *o, void *x) { (void)o; free(x); }
static void add_obj(void *o, void *p, const char *n, void *c)
{ (void)o; (void)p; (void)n; free(c); }
static void add_string(void *o, void *p, const char *n, char *s)
{
  (void)o;
  struct map *m = p;
  if(n != NULL && !strcmp(n, "id") && m->id == NULL) { m->id = s; return; }
  free(s);
}
static void add_long(void *o, void *p, const char *n, long v)
{ (void)o; (void)p; (void)n; (void)v; }
static void add_double(void *o, void *p, const char *n, double v)
{ (void)o; (void)p; (void)n; (void)v; }
static void add_bool(void *o, void *p, const char *n, int v)
{ (void)o; (void)p; (void)n; (void)v; }
static void add_null(void *o, void *p, const char *n)
{ (void)o; (void)p; (void)n; }

static const json_deserializer_t jd = {
  create_map, create_list, destroy_obj, add_obj, add_string,
  add_long, add_double, add_bool, add_null,
};

static int failures;

static void check(const char *json, const char *expect)
{
  struct map *m = json_deserialize(json, &jd, NULL, NULL, 0);
  const char *got = m != NULL ? m->id : NULL;
  int ok = got != NULL && !strcmp(got, expect);
  if(!ok)
    failures++;
  printf("  %-32s -> %-9s expect %-9s %s\n", json,
         got != NULL ? got : "(none)", expect, ok ? "PASS" : "FAIL");
  if(m != NULL) { free(m->id); free(m); }
}

int main(void)
{
  const char *digits = "0123456789abcdefABCDEF";
  char json[64], expect[8];

  printf("every hex digit character in \\u004X:\n");
  for(const char *d = digits; *d != 0; d++) {
    int v = *d <= '9' ? *d - '0' : (*d | 32) - 'a' + 10;
    snprintf(json, sizeof(json), "{\"id\":\"P\\u004%c\"}", *d);
    snprintf(expect, sizeof(expect), "P%c", 0x40 + v);
    check(json, expect);
  }

  printf("escapes and literals that must not change:\n");
  check("{\"id\":\"PJ\"}", "PJ");
  /* The four digits belong to the escape and the rest is literal text; an
     mdev guard once read this whole run as escape digits (movian#248). */
  check("{\"id\":\"P\\u0031FACE\"}", "P1FACE");
  check("{\"id\":\"a\\tb\"}", "a\tb");
  check("{\"id\":\"a\\\\b\"}", "a\\b");
  check("{\"id\":\"a\\\"b\"}", "a\"b");
  /* Two hex digits of a code point above 0x7f, to show the fix does not stop
     at the one-byte range. */
  check("{\"id\":\"\\u00E9\"}", "\xc3\xa9");
  check("{\"id\":\"\\u00e9\"}", "\xc3\xa9");

  printf("\n%s: %d failure(s)\n", failures != 0 ? "FAIL" : "OK", failures);
  return failures != 0;
}
