/*
 *  Copyright (C) 2007-2015 Lonelycoder AB
 *
 *  This program is free software: you can redistribute it and/or modify
 *  it under the terms of the GNU General Public License as published by
 *  the Free Software Foundation, either version 3 of the License, or
 *  (at your option) any later version.
 *
 *  This program is distributed in the hope that it will be useful,
 *  but WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 *  GNU General Public License for more details.
 *
 *  You should have received a copy of the GNU General Public License
 *  along with this program.  If not, see <http://www.gnu.org/licenses/>.
 *
 *  This program is also available under a commercial proprietary license.
 *  For more information, contact andreas@lonelycoder.com
 */

#include <stdlib.h>
#include <string.h>

#include "fa_zip_path.h"


static int
zip_path_separator(char c)
{
  return c == '/' || c == '\\';
}


static int
zip_path_dot_segment(const char *segment, size_t len)
{
  return (len == 1 && segment[0] == '.') ||
    (len == 2 && segment[0] == '.' && segment[1] == '.');
}


static int
zip_path_needs_normalization(const char *path)
{
  while(*path) {
    const char *segment = path;
    while(*path && !zip_path_separator(*path))
      path++;
    if(zip_path_dot_segment(segment, path - segment))
      return 1;
    if(*path)
      path++;
  }
  return 0;
}


const char *
fa_zip_resolve_member_path(const char *path, char **allocated)
{
  *allocated = NULL;

  /* Preserve ordinary ZIP names byte-for-byte and avoid allocating on the
   * common lookup path. */
  if(!zip_path_needs_normalization(path))
    return path;

  const size_t len = strlen(path);
  char *out = malloc(len + 1);
  if(out == NULL)
    return NULL;

  int directory_required = len > 0 && zip_path_separator(path[len - 1]);
  const char *src = path;
  char *dst = out;

  while(*src) {
    const char *segment = src;
    while(*src && !zip_path_separator(*src))
      src++;
    const size_t segment_len = src - segment;
    if(*src)
      src++;
    const int terminal = *src == 0;

    if(segment_len == 0)
      continue;

    if(segment_len == 1 && segment[0] == '.') {
      if(terminal)
        directory_required = 1;
      continue;
    }

    if(segment_len == 2 && segment[0] == '.' && segment[1] == '.') {
      if(dst == out) {
        free(out);
        return NULL;
      }
      dst--;
      while(dst > out && dst[-1] != '/')
        dst--;
      if(terminal)
        directory_required = 1;
      continue;
    }

    memcpy(dst, segment, segment_len);
    dst += segment_len;
    *dst++ = '/';
  }

  if(dst > out && !directory_required)
    dst--;
  *dst = 0;
  *allocated = out;
  return out;
}
