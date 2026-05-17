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
#include <stdio.h>

#include "main.h"
#include "networking/http.h"
#include "networking/http_server.h"
#include "task.h"
#include "event.h"
#include "screenshot.h"
#include "image/pixmap.h"
#include "htsmsg/htsmsg_json.h"
#include "fileaccess/http_client.h"
#include "fileaccess/fileaccess.h"

#include <libavcodec/avcodec.h>
#include <libswscale/swscale.h>

static hts_mutex_t screenshot_mutex;
static hts_cond_t screenshot_cond;
static http_connection_t *screenshot_connection;
static int screenshot_raw_waiting;
static int screenshot_raw_done;
static buf_t *screenshot_raw_image;
static char *screenshot_raw_error;


/**
 *
 */
static void
screenshot_raw_complete(buf_t *image, const char *errmsg)
{
  hts_mutex_lock(&screenshot_mutex);
  if(screenshot_raw_waiting) {
    if(screenshot_raw_image != NULL)
      buf_release(screenshot_raw_image);
    free(screenshot_raw_error);
    screenshot_raw_image = image;
    screenshot_raw_error = errmsg ? strdup(errmsg) : NULL;
    screenshot_raw_done = 1;
    hts_cond_signal(&screenshot_cond);
  } else if(image != NULL) {
    buf_release(image);
  }
  hts_mutex_unlock(&screenshot_mutex);
}


/**
 *
 */
static int
hc_screenshot_raw(http_connection_t *hc)
{
  hts_mutex_lock(&screenshot_mutex);
  if(screenshot_raw_waiting || screenshot_connection != NULL) {
    hts_mutex_unlock(&screenshot_mutex);
    return 502;
  }
  screenshot_raw_waiting = 1;
  screenshot_raw_done = 0;
  if(screenshot_raw_image != NULL) {
    buf_release(screenshot_raw_image);
    screenshot_raw_image = NULL;
  }
  free(screenshot_raw_error);
  screenshot_raw_error = NULL;
  hts_mutex_unlock(&screenshot_mutex);

  event_to_ui(event_create(EVENT_MAKE_SCREENSHOT, sizeof(event_t)));

  hts_mutex_lock(&screenshot_mutex);
  int timedout = 0;
  while(!screenshot_raw_done) {
    if(hts_cond_wait_timeout(&screenshot_cond, &screenshot_mutex, 5000)) {
      timedout = 1;
      break;
    }
  }

  buf_t *image = screenshot_raw_image;
  screenshot_raw_image = NULL;
  char *error = screenshot_raw_error;
  screenshot_raw_error = NULL;
  screenshot_raw_waiting = 0;
  screenshot_raw_done = 0;
  hts_mutex_unlock(&screenshot_mutex);

  if(timedout) {
    if(image != NULL)
      buf_release(image);
    free(error);
    return http_error(hc, 504, "Screenshot timed out");
  }

  if(image == NULL) {
    int r = http_error(hc, 500, "%s",
                       error != NULL ? error : "Screenshot capture failed");
    free(error);
    return r;
  }

  htsbuf_queue_t out;
  htsbuf_queue_init(&out, 0);
  htsbuf_append(&out, buf_data(image), buf_len(image));
  struct http_header_list headers;
  LIST_INIT(&headers);
  http_header_add(&headers, "Content-Type", "image/png", 0);
  http_header_add_int(&headers, "Content-Length", buf_len(image));
  http_header_add(&headers, "Connection", "Close", 0);
  int r = http_send_raw(hc, 200, "OK", &headers, &out);
  buf_release(image);
  free(error);
  return r;
}


/**
 *
 */
static int
hc_screenshot(http_connection_t *hc, const char *remain,
              void *opaque, http_cmd_t method)
{
  const char *raw = http_arg_get_req(hc, "raw");
  int want_raw = (remain != NULL && !strcmp(remain, "raw")) ||
                 (raw != NULL && (!strcmp(raw, "1") || !strcmp(raw, "true")));

  if(want_raw)
    return hc_screenshot_raw(hc);

  hts_mutex_lock(&screenshot_mutex);
  if(screenshot_raw_waiting || screenshot_connection != NULL) {
    hts_mutex_unlock(&screenshot_mutex);
    return 502;
  }
  screenshot_connection = hc;
  hts_mutex_unlock(&screenshot_mutex);

  event_to_ui(event_create(EVENT_MAKE_SCREENSHOT, sizeof(event_t)));
  return 0;
}


/**
 *
 */
typedef struct response {
  char *errmsg;
  char *url;
} response_t;


/**
 *
 */
static void
screenshot_response_task(void *task)
{
  response_t *r = task;
  hts_mutex_lock(&screenshot_mutex);
  if(screenshot_connection == NULL) {
    hts_mutex_unlock(&screenshot_mutex);
  } else {
    http_connection_t *hc = screenshot_connection;
    screenshot_connection = NULL;
    hts_mutex_unlock(&screenshot_mutex);

    if(r->url != NULL) {
      http_redirect(hc, r->url);
    } else {
      const char *msg = r->errmsg;
      if(msg == NULL)
        msg = "Error not specified";
      htsbuf_queue_t out;
      htsbuf_queue_init(&out, 0);
      htsbuf_append(&out, msg, strlen(msg));
      htsbuf_append_byte(&out, '\n');
      http_send_reply(hc, 500, "text/plain", NULL, NULL, 0, &out);
    }
  }
  free(r->url);
  free(r->errmsg);
  free(r);
}



/**
 *
 */
static void
screenshot_response(const char *url, const char *errmsg)
{
  response_t *r = calloc(1, sizeof(response_t));
  r->url    = url    ? strdup(url)    : NULL;
  r->errmsg = errmsg ? strdup(errmsg) : NULL;
  asyncio_run_task(screenshot_response_task, r);
}


/**
 *
 */
static buf_t *
screenshot_compress(pixmap_t *pm, int codecid)
{
  AVCodec *codec = avcodec_find_encoder(codecid);
  if(codec == NULL)
    return NULL;

  const int width = pm->pm_width;
  const int height = pm->pm_height;

  AVCodecContext *ctx = avcodec_alloc_context3(codec);
  ctx->pix_fmt = codec->pix_fmts[0];
  ctx->time_base.den = 1;
  ctx->time_base.num = 1;
  ctx->sample_aspect_ratio.num = 1;
  ctx->sample_aspect_ratio.den = 1;
  ctx->width  = width;
  ctx->height = height;

  if(avcodec_open2(ctx, codec, NULL) < 0) {
    TRACE(TRACE_ERROR, "ScreenShot", "Unable to open image encoder");
    return NULL;
  }

  AVFrame *oframe = av_frame_alloc();
  oframe->format = ctx->pix_fmt;
  oframe->width  = width;
  oframe->height = height;

  avpicture_alloc((AVPicture *)oframe, ctx->pix_fmt, width, height);

  const uint8_t *ptr[4] = {};
  int strides[4] = {0};

  if(pm->pm_flags & PIXMAP_VFLIP) {
    ptr[0] = pm->pm_data + pm->pm_linesize * (height - 1);
    strides[0] = -pm->pm_linesize;
  } else {
    ptr[0] = pm->pm_data;
    strides[0] = pm->pm_linesize;
  }
  struct SwsContext *sws;
  sws = sws_getContext(width, height, AV_PIX_FMT_RGB32,
                       width, height, ctx->pix_fmt, SWS_BILINEAR,
                       NULL, NULL, NULL);

  sws_scale(sws, ptr, strides,
            0, height, &oframe->data[0], &oframe->linesize[0]);
  sws_freeContext(sws);

  oframe->pts = AV_NOPTS_VALUE;
  AVPacket out;
  memset(&out, 0, sizeof(AVPacket));
  int got_packet;
  int r = avcodec_encode_video2(ctx, &out, oframe, &got_packet);
  buf_t *b;
  if(r >= 0 && got_packet) {
    b = buf_create_and_adopt(out.size, out.data, &av_free);
  } else {
    assert(out.data == NULL);
    b = NULL;
  }
  av_frame_free(&oframe);
  avcodec_close(ctx);
  av_free(ctx);
  return b;
}


/**
 *
 */
static void
screenshot_process(void *task)
{
  pixmap_t *pm = task;

  if(pm == NULL) {
    hts_mutex_lock(&screenshot_mutex);
    int raw_waiting = screenshot_raw_waiting;
    hts_mutex_unlock(&screenshot_mutex);
    if(raw_waiting) {
      screenshot_raw_complete(NULL,
                              "Screenshot not supported on this platform");
      return;
    }
    screenshot_response(NULL, "Screenshot not supported on this platform");
    return;
  }

  TRACE(TRACE_DEBUG, "Screenshot", "Processing image %d x %d",
        pm->pm_width, pm->pm_height);

  hts_mutex_lock(&screenshot_mutex);
  int has_connection = screenshot_connection != NULL;
  int raw_waiting = screenshot_raw_waiting;
  hts_mutex_unlock(&screenshot_mutex);

  int codecid = AV_CODEC_ID_PNG;
  if(has_connection && !raw_waiting)
    codecid = AV_CODEC_ID_MJPEG;

  buf_t *b = screenshot_compress(pm, codecid);
  pixmap_release(pm);
  if(b == NULL) {
    if(raw_waiting) {
      screenshot_raw_complete(NULL, "Unable to compress image");
      return;
    }
    screenshot_response(NULL, "Unable to compress image");
    return;
  }

  if(raw_waiting) {
    screenshot_raw_complete(b, NULL);
    return;
  }

  if(!has_connection) {
    char path[512];
    char errbuf[512];
    snprintf(path, sizeof(path), "%s/screenshot.png",
             gconf.cache_path);
    fa_handle_t *fa = fa_open_ex(path, errbuf, sizeof(errbuf),
                                 FA_WRITE, NULL);
    if(fa == NULL) {
      TRACE(TRACE_ERROR, "SCREENSHOT", "Unable to open %s -- %s",
            path, errbuf);
      buf_release(b);
      return;
    }
    fa_write(fa, buf_data(b), buf_len(b));
    fa_close(fa);
    TRACE(TRACE_INFO, "SCREENSHOT", "Written to %s", path);
    buf_release(b);
    return;
  }

  buf_t *result = NULL;
  htsbuf_queue_t hq;
  htsbuf_queue_init(&hq, 0);

  htsbuf_append(&hq, "image=", 6);
  htsbuf_append_and_escape_url_len(&hq, buf_cstr(b), buf_len(b));

  char errbuf[256];

  int ret = http_req("https://api.imgur.com/3/upload",
                     HTTP_FLAGS(FA_CONTENT_ON_ERROR),
                     HTTP_REQUEST_HEADER("Authorization",
                                         "Client-ID 7c79b311d4797ed"),
                     HTTP_RESULT_PTR(&result),
                     HTTP_POSTDATA(&hq, "application/x-www-form-urlencoded"),
                     HTTP_ERRBUF(errbuf, sizeof(errbuf)),
                     NULL);


  if(ret) {
    screenshot_response(NULL, errbuf);
  } else {

    htsmsg_t *response = htsmsg_json_deserialize(buf_cstr(result));
    if(response == NULL) {
      screenshot_response(NULL, "Unable to parse imgur response");
    } else {

      if(htsmsg_get_u32_or_default(response, "success", 0)) {
        const char *url = htsmsg_get_str_multi(response, "data", "link", NULL);
        screenshot_response(url, "No link in imgur response");
      } else {
        const char *msg = htsmsg_get_str_multi(response, "data", "error", NULL);
        if(msg == NULL) {
          screenshot_response(NULL, "Unkown imgur error");
        } else {
          snprintf(errbuf, sizeof(errbuf), "Imgur error: %s", msg);
          screenshot_response(NULL, errbuf);
        }
      }
      htsmsg_release(response);
    }
    buf_release(result);
  }
  buf_release(b);
}


/**
 *
 */
void
screenshot_deliver(pixmap_t *pm)
{
  task_run(screenshot_process, pm ? pixmap_dup(pm) : NULL);
}


/**
 *
 */
static void
screenshot_init(void)
{
  hts_mutex_init(&screenshot_mutex);
  hts_cond_init(&screenshot_cond, &screenshot_mutex);
  http_path_add("/api/screenshot", NULL, hc_screenshot, 0);
}

INITME(INIT_GROUP_API, screenshot_init, NULL, 0);
