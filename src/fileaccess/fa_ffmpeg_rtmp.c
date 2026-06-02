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

#include "config.h"

#if !ENABLE_LIBRTMP

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <libavformat/avio.h>
#include <libavformat/avformat.h>

#include "main.h"
#include "backend/backend.h"
#include "fileaccess.h"
#include "fa_audio.h"
#include "fa_proto.h"
#include "fa_video.h"

typedef struct ffmpeg_rtmp_handle {
  fa_handle_t h;
  AVIOContext *avio;
  int64_t size;
} ffmpeg_rtmp_handle_t;


static const char *ffmpeg_rtmp_protocols[] = {
  "rtmp",
  "rtmpt",
  "rtmpe",
  "rtmps",
  "rtmpte",
  "rtmpts",
  NULL,
};


static int
ffmpeg_rtmp_match_proto(const char *prefix)
{
  for(int i = 0; ffmpeg_rtmp_protocols[i] != NULL; i++)
    if(!strcmp(prefix, ffmpeg_rtmp_protocols[i]))
      return 0;

  return 1;
}


static int
ffmpeg_rtmp_canhandle(const char *url)
{
  for(int i = 0; ffmpeg_rtmp_protocols[i] != NULL; i++) {
    const char *proto = ffmpeg_rtmp_protocols[i];
    size_t len = strlen(proto);
    if(!strncmp(url, proto, len) && !strncmp(url + len, "://", 3))
      return 10;
  }

  return 0;
}


static void
ffmpeg_rtmp_error(char *errbuf, size_t errlen, const char *prefix, int err)
{
  if(errbuf == NULL || errlen == 0)
    return;

  char msg[256];
  if(av_strerror(err, msg, sizeof(msg)))
    snprintf(msg, sizeof(msg), "libav error %d", err);

  snprintf(errbuf, errlen, "%s: %s", prefix, msg);
}


static void
ffmpeg_rtmp_init(void)
{
  avformat_network_init();
}


static fa_handle_t *
ffmpeg_rtmp_open(fa_protocol_t *fap, const char *url, char *errbuf,
                 size_t errlen, int flags, fa_open_extra_t *foe)
{
  (void)flags;
  (void)foe;

  AVIOContext *avio = NULL;
  int err = avio_open2(&avio, url, AVIO_FLAG_READ, NULL, NULL);
  if(err < 0) {
    ffmpeg_rtmp_error(errbuf, errlen, "Unable to open RTMP URL", err);
    return NULL;
  }

  ffmpeg_rtmp_handle_t *fh = calloc(1, sizeof(ffmpeg_rtmp_handle_t));
  fh->h.fh_proto = fap;
  fh->avio = avio;
  fh->size = -1;

  return &fh->h;
}


static void
ffmpeg_rtmp_close(fa_handle_t *handle)
{
  ffmpeg_rtmp_handle_t *fh = (ffmpeg_rtmp_handle_t *)handle;
  avio_closep(&fh->avio);
  free(fh);
}


static int
ffmpeg_rtmp_read(fa_handle_t *handle, void *buf, size_t size)
{
  ffmpeg_rtmp_handle_t *fh = (ffmpeg_rtmp_handle_t *)handle;
  int r = avio_read(fh->avio, buf, size);
  return r == AVERROR_EOF ? 0 : r;
}


static int64_t
ffmpeg_rtmp_seek(fa_handle_t *handle, int64_t pos, int whence, int lazy)
{
  (void)handle;
  (void)pos;
  (void)whence;
  (void)lazy;

  return -1;
}


static int64_t
ffmpeg_rtmp_fsize(fa_handle_t *handle)
{
  ffmpeg_rtmp_handle_t *fh = (ffmpeg_rtmp_handle_t *)handle;
  return fh->size;
}


static int
ffmpeg_rtmp_stat(fa_protocol_t *fap, const char *url, struct fa_stat *fs,
                 int flags, char *errbuf, size_t errlen)
{
  (void)fap;
  (void)url;
  (void)flags;
  (void)errbuf;
  (void)errlen;

  memset(fs, 0, sizeof(struct fa_stat));
  fs->fs_size = -1;
  fs->fs_type = CONTENT_FILE;
  return 0;
}


static void
ffmpeg_rtmp_get_last_component(fa_protocol_t *fap, const char *url,
                               char *dst, size_t dstlen)
{
  (void)fap;

  int e, b;

  if(dstlen == 0)
    return;

  for(e = 0; url[e] != 0 && url[e] != '?'; e++);
  if(e > 0 && url[e - 1] == '/')
    e--;

  if(e == 0) {
    *dst = 0;
    return;
  }

  for(b = e; b > 0; b--)
    if(url[b - 1] == '/')
      break;

  if(dstlen > e - b + 1)
    dstlen = e - b + 1;

  memcpy(dst, url + b, dstlen);
  dst[dstlen - 1] = 0;
}


static int
ffmpeg_rtmp_backend_open(prop_t *page, const char *url, int sync)
{
  return backend_open_video(page, url, sync);
}


static event_t *
ffmpeg_rtmp_backend_playvideo(const char *url, media_pipe_t *mp,
                              char *errbuf, size_t errlen,
                              video_queue_t *vq, struct vsource_list *vsl,
                              const video_args_t *va0)
{
  video_args_t va = *va0;

  (void)vsl;
  va.flags |= BACKEND_VIDEO_NO_FS_SCAN | BACKEND_VIDEO_NO_SUBTITLE_SCAN;
  return be_file_playvideo_ffmpeg_url(url, mp, errbuf, errlen, vq, &va);
}


static fa_protocol_t fa_protocol_ffmpeg_rtmp = {
  .fap_init  = ffmpeg_rtmp_init,
  .fap_flags = FAP_INCLUDE_PROTO_IN_URL,
  .fap_name  = "ffmpeg-rtmp",
  .fap_match_proto = ffmpeg_rtmp_match_proto,
  .fap_open  = ffmpeg_rtmp_open,
  .fap_close = ffmpeg_rtmp_close,
  .fap_read  = ffmpeg_rtmp_read,
  .fap_seek  = ffmpeg_rtmp_seek,
  .fap_fsize = ffmpeg_rtmp_fsize,
  .fap_stat  = ffmpeg_rtmp_stat,
  .fap_get_last_component = ffmpeg_rtmp_get_last_component,
};

FAP_REGISTER(ffmpeg_rtmp);


static backend_t be_ffmpeg_rtmp = {
  .be_canhandle = ffmpeg_rtmp_canhandle,
  .be_open = ffmpeg_rtmp_backend_open,
  .be_play_audio = be_file_playaudio,
  .be_play_video = ffmpeg_rtmp_backend_playvideo,
};

BE_REGISTER(ffmpeg_rtmp);

#endif // !ENABLE_LIBRTMP
