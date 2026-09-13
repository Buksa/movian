/*
 * The nine-slice vertex split, in isolation (movian#117).
 *
 * A `backdrop` with `border: N` drew only its left and right bands. The
 * horizontals never appeared, because the two axes computed the same thing
 * with two copies of the arithmetic and the copies had diverged: x produced
 * a monotonic row of vertex coordinates and y produced
 * `-1, 1-2b/h, -1+2b/h, 1`, whose middle two rows are in the wrong order.
 * Every horizontal quad built from them is degenerate or inverted, so the
 * bands collapse. The vertical ones were fine, which is why only they showed.
 *
 * `glw_image_border_axis` is that arithmetic, once, called for both axes, so
 * the two cannot drift apart again. What this file pins is the property the
 * geometry needs and the old code lost: the four coordinates are
 * non-decreasing. A tesselated quad between rows i and i+1 has a
 * non-negative height exactly when that holds.
 *
 * Header-only and dependency-free on purpose: the real function is under
 * test, not a copy of it, and pulling glw.h would drag the renderer in.
 *
 * Build and run:
 *   cc -I src -Werror -o /tmp/t tests/tooling/glw/test_image_border.c
 *   /tmp/t
 */
#include <stdio.h>
#include <string.h>

#include "ui/glw/glw_image_border.h"

static int failures;

static void
check(const char *what, int ok, const char *detail)
{
  if(!ok) {
    failures++;
    printf("  FAIL %-34s %s\n", what, detail);
  } else {
    printf("  PASS %-34s %s\n", what, detail);
  }
}

/* Monotone in the direction the axis was asked to run. A quad between rows
   i and i+1 has a non-negative extent exactly when this holds, whichever way
   round the axis is. */
static int
monotone(const float v[4])
{
  const float span = v[3] - v[0];
  return (v[1] - v[0]) * span >= 0.0f && (v[2] - v[1]) * span >= 0.0f
      && (v[3] - v[2]) * span >= 0.0f;
}

int
main(void)
{
  char detail[160];
  float v[4];

  /* The borders and box sizes from the report: border 3/4/8 on boxes from
     1.5em to 4.6em, which at this skin's em is roughly 24 to 74 pixels. */
  static const int borders[] = {0, 1, 3, 4, 8, 12, 24};
  static const float extents[] = {8.0f, 24.0f, 37.0f, 74.0f, 300.0f};

  printf("every border/extent pair yields a non-decreasing row:\n");
  for(unsigned int b = 0; b < sizeof(borders) / sizeof(borders[0]); b++) {
    for(unsigned int e = 0; e < sizeof(extents) / sizeof(extents[0]); e++) {
      glw_image_border_axis(v, -1.0f, borders[b], borders[b], 1.0f,
                            extents[e]);
      snprintf(detail, sizeof(detail),
               "border=%d extent=%.0f -> %.3f %.3f %.3f %.3f",
               borders[b], extents[e], v[0], v[1], v[2], v[3]);
      check("monotone, x direction", monotone(v), detail);
    }
  }

  printf("asymmetric borders keep their order too:\n");
  glw_image_border_axis(v, -1.0f, 2, 10, 1.0f, 40.0f);
  snprintf(detail, sizeof(detail), "lo=2 hi=10 -> %.3f %.3f %.3f %.3f",
           v[0], v[1], v[2], v[3]);
  check("monotone, lo < hi", monotone(v), detail);
  glw_image_border_axis(v, -1.0f, 10, 2, 1.0f, 40.0f);
  snprintf(detail, sizeof(detail), "lo=10 hi=2 -> %.3f %.3f %.3f %.3f",
           v[0], v[1], v[2], v[3]);
  check("monotone, lo > hi", monotone(v), detail);

  printf("the band nearest each edge is the one that edge asked for:\n");
  glw_image_border_axis(v, -1.0f, 4, 0, 1.0f, 40.0f);
  snprintf(detail, sizeof(detail), "lo=4 hi=0 -> row1=%.3f row2=%.3f",
           v[1], v[2]);
  /* A border on the low edge only: row 1 moves off -1, row 2 stays at +1. */
  check("low border moves row 1", v[1] > -1.0f && v[2] == 1.0f, detail);
  glw_image_border_axis(v, -1.0f, 0, 4, 1.0f, 40.0f);
  snprintf(detail, sizeof(detail), "lo=0 hi=4 -> row1=%.3f row2=%.3f",
           v[1], v[2]);
  check("high border moves row 2", v[1] == -1.0f && v[2] < 1.0f, detail);

  printf("a border wider than the box collapses rather than inverting:\n");
  glw_image_border_axis(v, -1.0f, 40, 40, 1.0f, 10.0f);
  snprintf(detail, sizeof(detail), "border=40 extent=10 -> %.3f %.3f %.3f %.3f",
           v[0], v[1], v[2], v[3]);
  check("clamped to the centre", monotone(v) && v[1] == 0.0f
        && v[2] == 0.0f, detail);

  /* The y axis runs the other way, and this is the case a symmetric frame
     hides. `glw_image_layout_repeated` pairs vertex +1 with texture t=0, and
     t=0 holds the image's first scanline, so the TOP border must land at the
     +1 end. An earlier version of this fix ran y ascending like x, which put
     the top border's texels on the bottom band -- invisible with an 8x8
     frame, obvious with an asymmetric one. */
  printf("the y axis runs +1 -> -1, so the top border stays at the top:\n");
  glw_image_border_axis(v, 1.0f, 8, 2, -1.0f, 40.0f);
  snprintf(detail, sizeof(detail), "top=8 bottom=2 -> %.3f %.3f %.3f %.3f",
           v[0], v[1], v[2], v[3]);
  check("monotone, y direction", monotone(v), detail);
  check("row 0 is the top edge", v[0] == 1.0f, detail);
  check("row 3 is the bottom edge", v[3] == -1.0f, detail);
  /* 8/40 of the half-height down from +1; 2/40 up from -1. Asymmetric on
     purpose: equal borders would pass even with the two swapped. */
  check("top inset is the bigger one",
        (1.0f - v[1]) > (v[2] - (-1.0f)), detail);

  printf("and a y border wider than the box still collapses inward:\n");
  glw_image_border_axis(v, 1.0f, 40, 40, -1.0f, 10.0f);
  snprintf(detail, sizeof(detail), "border=40 extent=10 -> %.3f %.3f %.3f %.3f",
           v[0], v[1], v[2], v[3]);
  check("clamped, y direction", monotone(v) && v[1] == 0.0f && v[2] == 0.0f,
        detail);

  printf("\n%s: %d failure(s)\n", failures ? "FAIL" : "OK", failures);
  return failures != 0;
}
