#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "fa_zip_path.h"


static void
expect_unchanged(const char *path)
{
  char *allocated = (char *)1;
  const char *resolved = fa_zip_resolve_member_path(path, &allocated);

  assert(resolved == path);
  assert(allocated == NULL);
}


static void
expect_resolved(const char *path, const char *expected)
{
  char *allocated = NULL;
  const char *resolved = fa_zip_resolve_member_path(path, &allocated);

  assert(resolved != NULL);
  assert(resolved == allocated);
  assert(!strcmp(resolved, expected));
  free(allocated);
}


static void
expect_rejected(const char *path)
{
  char *allocated = (char *)1;
  const char *resolved = fa_zip_resolve_member_path(path, &allocated);

  assert(resolved == NULL);
  assert(allocated == NULL);
}


int
main(void)
{
  /* The issue's parent-relative and ordinary dot-segment cases. */
  expect_resolved("views/sub/../common.view", "views/common.view");
  expect_resolved("views/prototypes/../release_components.view",
                  "views/release_components.view");
  expect_resolved("a/b/c/../../d", "a/d");
  expect_resolved("views/./x.view", "views/x.view");
  expect_resolved("a\\b\\..\\c", "a/c");

  /* A terminal dot segment keeps zip_archive_find_file()'s directory check. */
  expect_resolved("a/.", "a/");
  expect_resolved("a/b/..", "a/");
  expect_resolved("views/sub/../", "views/");
  expect_resolved(".", "");
  expect_resolved("a/..", "");

  /* Ordinary members take the allocation-free, byte-preserving fast path. */
  expect_unchanged("");
  expect_unchanged("plain/member");
  expect_unchanged("views/");
  expect_unchanged("a//b");
  expect_unchanged("a\\b/c");
  expect_unchanged("root/%2e%2e/common.view");
  expect_unchanged("root/.hidden/common.view");

  /* Traversal cannot climb above the selected archive. */
  expect_rejected("../outside.view");
  expect_rejected("a/../../outside.view");
  expect_rejected("views/../../../other.zip/x.view");
  expect_rejected("..");

  puts("zip member path tests passed");
  return 0;
}
