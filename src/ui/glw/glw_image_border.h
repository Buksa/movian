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
#pragma once

/**
 * The four coordinates of one axis of a nine-slice split: the two edges, and
 * the two rows inset from them by their borders.
 *
 * The axis runs from `start` to `end`, and the CALLER states which way round
 * that is. That is the whole point. The two axes do not run the same way: a
 * GLW widget's +1 is its top and a texture's t=0 is the image's top
 * (`glw_image_layout_repeated` pairs `+1` with `t=0`; the pixmap's first
 * scanline is the image's first, libjpeg.c:102-107, uploaded to t=0 by
 * glw_texture_opengl.c:89-95). So x runs -1 -> +1 and y runs +1 -> -1, and
 * writing that at the call site is what keeps the texture the right way up.
 *
 * It was implicit before, in two copies of the arithmetic, and it got lost:
 * a 2016 margin refactor (1d84a9a2c) replaced the y EDGES with -1 and +1 --
 * the x convention -- and left the two inset rows computing from the old
 * descending one. The rows then arrived out of order, every horizontal quad
 * was degenerate or inverted, and the top and bottom bands stopped drawing
 * while the verticals carried on (movian#117).
 *
 * `border_start` belongs to the `start` edge and `border_end` to the `end`
 * edge. A border wider than half the box clamps to the middle rather than
 * crossing over, so the result is always monotone from `start` to `end`.
 *
 * Deliberately dependency-free so it can be tested without the renderer:
 * see tests/tooling/glw/test_image_border.c.
 */
static inline void
glw_image_border_axis(float out[4], float start, int border_start,
                      int border_end, float end, float extent)
{
  const float span = end - start;
  const float inset_start = start + span * border_start / extent;
  const float inset_end   = end   - span * border_end   / extent;

  out[0] = start;
  out[3] = end;

  /* Neither inset may pass the middle, whichever way the axis runs. */
  if(span > 0.0f) {
    out[1] = inset_start < 0.0f ? inset_start : 0.0f;
    out[2] = inset_end   > 0.0f ? inset_end   : 0.0f;
  } else {
    out[1] = inset_start > 0.0f ? inset_start : 0.0f;
    out[2] = inset_end   < 0.0f ? inset_end   : 0.0f;
  }
}
