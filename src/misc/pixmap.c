/*
 *  Pixmaps - Helpers for transfer of images between modules
 *  Copyright (C) 2009 Andreas Öman
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
 */


#include <arch/atomic.h>

#include "pixmap.h"

/**
 *
 */
pixmap_t *
pixmap_alloc_coded(const void *data, size_t size, enum CodecID codec)
{
  pixmap_t *pm = calloc(1, sizeof(pixmap_t));
  pm->pm_refcount = 1;
  pm->pm_size = size;
  pm->pm_data = malloc(size + FF_INPUT_BUFFER_PADDING_SIZE);
  if(data != NULL)
    memcpy(pm->pm_data, data, size);

  memset(pm->pm_data + size, 0, FF_INPUT_BUFFER_PADDING_SIZE);

  pm->pm_codec = codec;
  return pm;
}


/**
 *
 */
void
pixmap_release(pixmap_t *pm)
{
  if(atomic_add(&pm->pm_refcount, -1) > 1)
    return;
  
  if(pm->pm_codec == CODEC_ID_NONE) {
    avpicture_free(&pm->pm_pict);
  } else {
    free(pm->pm_data);
  }
  free(pm);
}

/**
 *
 */
pixmap_t *
pixmap_dup(pixmap_t *pm)
{
  atomic_add(&pm->pm_refcount, 1);
  return pm;
}


/**
 *
 */
pixmap_t *
pixmap_create_rgb24(int width, int height, const void *pixels, int pitch)
{
  pixmap_t *pm = calloc(1, sizeof(pixmap_t));

  pm->pm_refcount = 1;
  pm->pm_codec = CODEC_ID_NONE;

  pm->pm_width = width;
  pm->pm_height = height;
  pm->pm_pixfmt = PIX_FMT_RGB24;

  pm->pm_pict.data[0] = malloc(height * pitch);
  pm->pm_pict.linesize[0] = pitch;

  memcpy(pm->pm_pict.data[0], pixels, height * pitch);
  return pm;
}
