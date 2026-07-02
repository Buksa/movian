/*
 *  Copyright (C) 2026 Movian contributors
 *
 *  This program is free software: you can redistribute it and/or modify
 *  it under the terms of the GNU General Public License as published by
 *  the Free Software Foundation, either version 3 of the License, or
 *  (at your option) any later version.
 */

/*
 * SMB2 fileaccess VFS backend.
 *
 * Implements the fa_protocol callbacks (scan/open/read/write/.../stat/...) on
 * top of a pooled libsmb2 session provided by fa_libsmb2_pool.c. Each VFS op
 * acquires a session, takes session->lock around its libsmb2 call(s) (libsmb2 is
 * not thread safe and a context is bound to one socket), and releases when done.
 */

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <poll.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include <smb2/smb2.h>
#include <smb2/libsmb2.h>
#include <smb2/libsmb2-raw.h>
#include <smb2/libsmb2-dcerpc-srvsvc.h>

#include "main.h"
#include "fileaccess/fa_proto.h"
#include "misc/rstr.h"

#include "fa_libsmb2_pool.h"

typedef struct {
  fa_handle_t h;
  movian_smb2_session_t *session;
  struct smb2fh *fh;
  int64_t size;
} movian_smb2_file_t;

typedef struct {
  int done;
  int status;
  struct srvsvc_NetrShareEnum_rep *reply;
} movian_smb2_enum_state_t;

/* Error-code translation kept local to the VFS layer. */

static fa_err_code_t
movian_smb2_errno_to_fap(int status)
{
  int err = -status;
  if(status < -4096)
    err = nterror_to_errno((uint32_t)status);

  switch(err) {
  case 0:
    return FAP_OK;
  case ENOENT:
    return FAP_NOENT;
  case EEXIST:
    return FAP_EXIST;
  case EACCES:
  case EPERM:
  case EROFS:
    return FAP_PERMISSION_DENIED;
  default:
    return FAP_ERROR;
  }
}


static char *
movian_smb2_child_url(const char *parent, const char *name)
{
  size_t parent_len = strlen(parent);
  while(parent_len > 0 && parent[parent_len - 1] == '/')
    parent_len--;

  size_t name_len = strlen(name);
  if(parent_len > SIZE_MAX - name_len - 2)
    return NULL;

  size_t len = parent_len + name_len + 2;
  char *url = malloc(len);
  if(url != NULL) {
    memcpy(url, parent, parent_len);
    url[parent_len] = '/';
    memcpy(url + parent_len + 1, name, name_len + 1);
  }
  return url;
}


static rstr_t *
movian_smb2_redirect(fa_protocol_t *fap, const char *url)
{
  const char *path = url + strlen("smb2://");
  if(*path == '\0' || strchr(path, '/') != NULL)
    return NULL;

  char redirected[URL_MAX];
  int len = snprintf(redirected, sizeof(redirected), "%s/", url);
  if(len < 0 || (size_t)len >= sizeof(redirected))
    return NULL;

  SMB2TRACE("Redirecting %s -> %s", url, redirected);
  return rstr_alloc(redirected);
}


static int
movian_smb2_normalize(fa_protocol_t *fap, const char *url,
                      char *dst, size_t dstlen)
{
  movian_smb2_target_t target = {};
  char errbuf[128];

  if(movian_smb2_target_parse(url, &target, errbuf, sizeof(errbuf)))
    return -1;

  int host_root = target.share == NULL || target.share[0] == '\0';
  movian_smb2_target_fini(&target);

  if(!host_root)
    return -1;

  size_t len = strlen(url);
  if(dstlen == 0 || len >= dstlen)
    return -1;

  memcpy(dst, url, len + 1);
  return 0;
}


static void
movian_smb2_enum_callback(struct smb2_context *smb2, int status,
                          void *command_data, void *opaque)
{
  movian_smb2_enum_state_t *state = opaque;
  state->status = status;
  state->reply = command_data;
  state->done = 1;
}


static int
movian_smb2_wait_for_enum(struct smb2_context *smb2,
                          movian_smb2_enum_state_t *state)
{
  int64_t deadline =
    arch_get_ts() + (SMB2_DEFAULT_TIMEOUT + 5) * 1000000LL;

  while(!state->done) {
    int64_t remaining = deadline - arch_get_ts();
    if(remaining <= 0)
      return -1;

    struct pollfd pfd = {
      .fd = smb2_get_fd(smb2),
      .events = smb2_which_events(smb2),
    };

    int timeout = (remaining + 999) / 1000;
    if(timeout > 1000)
      timeout = 1000;

    int status = poll(&pfd, 1, timeout);
    if(status < 0) {
      if(errno == EINTR)
        continue;
      return -1;
    }
    if(smb2_service(smb2, status == 0 ? 0 : pfd.revents) < 0)
      return -1;
  }
  return 0;
}


static int
movian_smb2_scan_host(fa_dir_t *fd, const char *url,
                      const movian_smb2_target_t *target, int flags,
                      char *errbuf, size_t errlen)
{
  SMB2TRACE("Enumerating shares on %s", target->server);
  movian_smb2_session_t *session =
    movian_smb2_session_acquire(target, "IPC$", flags, SMB2_DEFAULT_TIMEOUT,
                                NULL, errbuf, errlen);
  if(session == NULL)
    return -1;

  struct smb2_context *smb2 = session->smb2;
  movian_smb2_enum_state_t state = {};
  int rc = -1;

  hts_mutex_lock(&session->lock);
  if(smb2_share_enum_async(smb2, SHARE_INFO_1,
                           movian_smb2_enum_callback, &state) < 0 ||
     movian_smb2_wait_for_enum(smb2, &state) < 0 ||
     state.status != 0 || state.reply == NULL) {
    snprintf(errbuf, errlen, "Unable to enumerate SMB2 shares: %s",
             smb2_get_error(smb2));
    SMB2TRACE("Share enumeration failed status=%d reason=%s",
              state.status, errbuf);
    if(state.reply != NULL)
      smb2_free_data(smb2, state.reply);
    if(state.status != 0)
      movian_smb2_session_invalidate(session);
    hts_mutex_unlock(&session->lock);
    goto out;
  }

  struct srvsvc_SHARE_INFO_1_CONTAINER *shares =
    &state.reply->ses.ShareEnum.Level1;
  if(shares->share_info_1 != NULL) {
    for(uint32_t i = 0; i < shares->EntriesRead; i++) {
      struct srvsvc_SHARE_INFO_1 *share =
        &shares->share_info_1[i];
      const char *name = share->netname;
      uint32_t share_type = share->type;
      int add = name != NULL &&
        (share_type & 3) == SHARE_TYPE_DISKTREE &&
        !(share_type & SHARE_TYPE_HIDDEN);

      SMB2TRACE("Enumerated share %s type=0x%x -> %s",
                name != NULL ? name : "<null>", share_type,
                add ? "add" : "filtered");

      if(!add)
        continue;

      char *child = movian_smb2_child_url(url, name);
      if(child == NULL)
        continue;

      fa_dir_entry_t *fde = fa_dir_add(fd, child, name, CONTENT_SHARE);
      if(fde != NULL) {
        fde->fde_stat.fs_type = CONTENT_SHARE;
        fde->fde_statdone = 1;
      }
      free(child);
    }
  }

  smb2_free_data(smb2, state.reply);
  hts_mutex_unlock(&session->lock);
  rc = 0;

out:
  movian_smb2_session_release(session);
  return rc;
}


static int
movian_smb2_scan_directory(fa_dir_t *fd, const char *url,
                           const movian_smb2_target_t *target, int flags,
                           char *errbuf, size_t errlen)
{
  SMB2TRACE("scan directory url=%s share=%s path='%s' flags=0x%x",
            url, target->share, target->path, flags);

  movian_smb2_session_t *session =
    movian_smb2_session_acquire(target, target->share, flags,
                                SMB2_DEFAULT_TIMEOUT, NULL, errbuf, errlen);
  if(session == NULL) {
    SMB2TRACE("scan directory connect failed url=%s reason=%s", url, errbuf);
    return -1;
  }

  struct smb2_context *smb2 = session->smb2;
  int rc = -1;

  hts_mutex_lock(&session->lock);
  struct smb2dir *dir = smb2_opendir(smb2, target->path);
  if(dir == NULL) {
    snprintf(errbuf, errlen, "Unable to open SMB2 directory: %s",
             smb2_get_error(smb2));
    SMB2TRACE("scan directory opendir failed url=%s path='%s' reason=%s",
              url, target->path, errbuf);
    hts_mutex_unlock(&session->lock);
    goto out;
  }

  int entries = 0;
  struct smb2dirent *entry;
  while((entry = smb2_readdir(smb2, dir)) != NULL) {
    if(!strcmp(entry->name, ".") || !strcmp(entry->name, ".."))
      continue;

    int type = entry->st.smb2_type == SMB2_TYPE_DIRECTORY ?
      CONTENT_DIR : CONTENT_FILE;
    char *child = movian_smb2_child_url(url, entry->name);
    if(child == NULL)
      continue;

    SMB2TRACE("scan directory entry url=%s name=%s child=%s smb2_type=%d type=%d",
              url, entry->name, child, entry->st.smb2_type, type);
    fa_dir_entry_t *fde = fa_dir_add(fd, child, entry->name, type);
    if(fde != NULL) {
      fde->fde_stat.fs_type = type;
      fde->fde_stat.fs_size = entry->st.smb2_size;
      fde->fde_stat.fs_mtime = entry->st.smb2_mtime;
      fde->fde_statdone = 1;
      entries++;
    }
    free(child);
  }

  SMB2TRACE("scan directory done url=%s path='%s' entries=%d",
            url, target->path, entries);
  smb2_closedir(smb2, dir);
  hts_mutex_unlock(&session->lock);
  rc = 0;

out:
  movian_smb2_session_release(session);
  return rc;
}


static int
movian_smb2_scan(fa_protocol_t *fap, fa_dir_t *fd, const char *url,
                 char *errbuf, size_t errlen, int flags)
{
  movian_smb2_target_t target = {};
  if(movian_smb2_target_parse(url, &target, errbuf, errlen)) {
    SMB2TRACE("scan parse failed url=%s reason=%s", url, errbuf);
    return -1;
  }

  SMB2TRACE("scan url=%s server=%s share=%s path='%s' flags=0x%x",
            url, target.server,
            target.share != NULL ? target.share : "<host>",
            target.path, flags);
  int status = target.share == NULL || target.share[0] == '\0' ?
    movian_smb2_scan_host(fd, url, &target, flags, errbuf, errlen) :
    movian_smb2_scan_directory(fd, url, &target, flags, errbuf, errlen);
  SMB2TRACE("scan done url=%s status=%d%s%s", url, status,
            status ? " reason=" : "", status ? errbuf : "");

  movian_smb2_target_fini(&target);
  return status;
}


static fa_handle_t *
movian_smb2_open(fa_protocol_t *fap, const char *url,
                 char *errbuf, size_t errlen, int flags,
                 fa_open_extra_t *foe)
{
  movian_smb2_target_t target = {};
  if(movian_smb2_target_parse(url, &target, errbuf, errlen)) {
    SMB2TRACE("open parse failed url=%s reason=%s", url, errbuf);
    return NULL;
  }

  SMB2TRACE("open url=%s share=%s path='%s' flags=0x%x",
            url, target.share != NULL ? target.share : "<host>",
            target.path, flags);
  if(target.share == NULL || target.share[0] == '\0' ||
     target.path[0] == '\0') {
    snprintf(errbuf, errlen, "SMB2 URL does not identify a file");
    SMB2TRACE("open rejected url=%s reason=%s", url, errbuf);
    movian_smb2_target_fini(&target);
    return NULL;
  }

  int timeout = foe != NULL && foe->foe_open_timeout > 0 ?
    (foe->foe_open_timeout + 999) / 1000 : SMB2_DEFAULT_TIMEOUT;
  movian_smb2_session_t *session =
    movian_smb2_session_acquire(&target, target.share, flags, timeout,
                                NULL, errbuf, errlen);
  if(session == NULL) {
    movian_smb2_target_fini(&target);
    return NULL;
  }

  struct smb2_context *smb2 = session->smb2;

  int open_flags = O_RDONLY;
  if(flags & FA_WRITE) {
    open_flags = O_RDWR | O_CREAT;
    if(!(flags & FA_APPEND))
      open_flags |= O_TRUNC;
  }

  movian_smb2_file_t *file = NULL;

  hts_mutex_lock(&session->lock);

  struct smb2fh *fh = smb2_open(smb2, target.path, open_flags);
  if(fh == NULL) {
    snprintf(errbuf, errlen, "Unable to open SMB2 file: %s",
             smb2_get_error(smb2));
    SMB2TRACE("open failed url=%s path='%s' open_flags=0x%x reason=%s",
              url, target.path, open_flags, errbuf);
    goto fail_locked;
  }

  struct smb2_stat_64 statbuf;
  if(smb2_fstat(smb2, fh, &statbuf) < 0 ||
     statbuf.smb2_type == SMB2_TYPE_DIRECTORY) {
    snprintf(errbuf, errlen, "Unable to stat SMB2 file: %s",
             smb2_get_error(smb2));
    SMB2TRACE("open fstat rejected url=%s path='%s' smb2_type=%d reason=%s",
              url, target.path, statbuf.smb2_type, errbuf);
    smb2_close(smb2, fh);
    goto fail_locked;
  }
  SMB2TRACE("open ok url=%s path='%s' size=%"PRId64, url, target.path,
            statbuf.smb2_size);

  file = calloc(1, sizeof(*file));
  if(file == NULL) {
    snprintf(errbuf, errlen, "Out of memory while opening SMB2 file");
    smb2_close(smb2, fh);
    goto fail_locked;
  }

  file->h.fh_proto = fap;
  file->fh = fh;
  file->size = statbuf.smb2_size;

  if(flags & FA_APPEND) {
    if(smb2_lseek(smb2, file->fh, 0, SEEK_END, NULL) < 0) {
      snprintf(errbuf, errlen, "Unable to seek SMB2 file: %s",
               smb2_get_error(smb2));
      smb2_close(smb2, file->fh);
      free(file);
      file = NULL;
      goto fail_locked;
    }
  }

  hts_mutex_unlock(&session->lock);

  /* The handle now owns the session reference. */
  file->session = session;
  movian_smb2_target_fini(&target);
  return &file->h;

fail_locked:
  hts_mutex_unlock(&session->lock);
  movian_smb2_session_release(session);
  movian_smb2_target_fini(&target);
  return NULL;
}


static void
movian_smb2_close(fa_handle_t *fh)
{
  movian_smb2_file_t *file = (movian_smb2_file_t *)fh;

  hts_mutex_lock(&file->session->lock);
  smb2_close(file->session->smb2, file->fh);
  hts_mutex_unlock(&file->session->lock);

  movian_smb2_session_t *session = file->session;
  free(file);
  movian_smb2_session_release(session);
}


/*
 * Maximum number of READ PDUs we keep in flight for a single movian_smb2_read()
 * call. Pipelining overlaps the network round trip of one chunk with the
 * server-side disk read of the next, which is what makes the cifs native
 * backend fast for large files; SMB2 gains the same by issuing several
 * smb2_pread_async() PDUs before blocking on the first reply.
 */
#define SMB2_READ_PIPELINE_DEPTH 4

typedef struct {
  int done;
  int status;
} movian_smb2_read_req_t;


static void
movian_smb2_read_cb(struct smb2_context *smb2, int status,
                    void *command_data, void *opaque)
{
  movian_smb2_read_req_t *req = opaque;
  req->status = status;
  req->done = 1;
}


static int
movian_smb2_read(fa_handle_t *fh, void *buf, size_t size)
{
  movian_smb2_file_t *file = (movian_smb2_file_t *)fh;
  uint32_t count = size > INT_MAX ? INT_MAX : size;

  hts_mutex_lock(&file->session->lock);

  /*
   * Keep the simple synchronous path for small reads: the bookkeeping for a
   * pipelined run is not worth it below one chunk, and short reads (tags,
   * directory metadata) dominate browsing.
   */
  uint32_t max_read = smb2_get_max_read_size(file->session->smb2);
  if(max_read == 0 || count <= max_read) {
    int status = smb2_read(file->session->smb2, file->fh, buf, count);
    hts_mutex_unlock(&file->session->lock);
    return status;
  }

  struct smb2_context *smb2 = file->session->smb2;
  struct smb2fh *sfh = file->fh;

  int64_t base = smb2_lseek(smb2, sfh, 0, SEEK_CUR, NULL);
  if(base < 0) {
    hts_mutex_unlock(&file->session->lock);
    return -1;
  }

  movian_smb2_read_req_t reqs[SMB2_READ_PIPELINE_DEPTH];
  memset(reqs, 0, sizeof(reqs));

  uint32_t chunk = max_read;
  uint32_t issued = 0;
  int nreqs = 0;
  while(issued < count && nreqs < SMB2_READ_PIPELINE_DEPTH) {
    uint32_t this_count = count - issued;
    if(this_count > chunk)
      this_count = chunk;

    if(smb2_pread_async(smb2, sfh, (uint8_t *)buf + issued, this_count,
                        base + issued, movian_smb2_read_cb,
                        &reqs[nreqs]) < 0) {
      if(nreqs == 0) {
        hts_mutex_unlock(&file->session->lock);
        return -1;
      }
      break;
    }
    issued += this_count;
    nreqs++;
  }

  if(nreqs == 0) {
    hts_mutex_unlock(&file->session->lock);
    return -1;
  }

  int64_t deadline = arch_get_ts() +
    (SMB2_DEFAULT_TIMEOUT + 5) * 1000000LL;
  while(1) {
    int all_done = 1;
    for(int i = 0; i < nreqs; i++)
      if(!reqs[i].done) {
        all_done = 0;
        break;
      }
    if(all_done)
      break;

    int64_t remaining = deadline - arch_get_ts();
    if(remaining <= 0)
      break;

    struct pollfd pfd = {
      .fd = smb2_get_fd(smb2),
      .events = smb2_which_events(smb2),
    };
    int timeout = (remaining + 999) / 1000;
    if(timeout > 1000)
      timeout = 1000;

    int rc = poll(&pfd, 1, timeout);
    if(rc < 0) {
      if(errno == EINTR)
        continue;
      break;
    }
    if(smb2_service(smb2, rc == 0 ? 0 : pfd.revents) < 0)
      break;
  }

  /*
   * Reassemble. Each pread wrote straight into its slice of buf, so we only
   * need to total up the bytes and stop at the first short/error read the way
   * a normal read() would.
   */
  int total = 0;
  int failed = 0;
  for(int i = 0; i < nreqs; i++) {
    if(!reqs[i].done || reqs[i].status < 0) {
      failed = 1;
      break;
    }
    total += reqs[i].status;
    if(reqs[i].status < (int)chunk)
      break;
  }

  if(!failed && total > 0)
    smb2_lseek(smb2, sfh, base + total, SEEK_SET, NULL);

  hts_mutex_unlock(&file->session->lock);
  return failed ? -1 : total;
}


static int
movian_smb2_write(fa_handle_t *fh, const void *buf, size_t size)
{
  movian_smb2_file_t *file = (movian_smb2_file_t *)fh;
  uint32_t count = size > INT_MAX ? INT_MAX : size;

  hts_mutex_lock(&file->session->lock);
  int status = smb2_write(file->session->smb2, file->fh, buf, count);
  if(status > 0) {
    int64_t pos = smb2_lseek(file->session->smb2, file->fh, 0, SEEK_CUR, NULL);
    if(pos > file->size)
      file->size = pos;
  }
  hts_mutex_unlock(&file->session->lock);
  return status;
}


static int64_t
movian_smb2_seek(fa_handle_t *fh, int64_t pos, int whence, int lazy)
{
  movian_smb2_file_t *file = (movian_smb2_file_t *)fh;

  hts_mutex_lock(&file->session->lock);
  int64_t status =
    smb2_lseek(file->session->smb2, file->fh, pos, whence, NULL);
  hts_mutex_unlock(&file->session->lock);
  return status;
}


static int64_t
movian_smb2_fsize(fa_handle_t *fh)
{
  movian_smb2_file_t *file = (movian_smb2_file_t *)fh;
  return file->size;
}


static fa_err_code_t
movian_smb2_ftruncate(fa_handle_t *fh, uint64_t newsize)
{
  movian_smb2_file_t *file = (movian_smb2_file_t *)fh;

  hts_mutex_lock(&file->session->lock);
  int status = smb2_ftruncate(file->session->smb2, file->fh, newsize);
  if(status == 0)
    file->size = newsize > INT64_MAX ? INT64_MAX : (int64_t)newsize;
  hts_mutex_unlock(&file->session->lock);

  return status < 0 ? movian_smb2_errno_to_fap(status) : FAP_OK;
}


static void
movian_smb2_set_read_timeout(fa_handle_t *fh, int ms)
{
  movian_smb2_file_t *file = (movian_smb2_file_t *)fh;
  int seconds = ms > 0 ? (ms + 999) / 1000 : 0;

  hts_mutex_lock(&file->session->lock);
  smb2_set_timeout(file->session->smb2, seconds);
  hts_mutex_unlock(&file->session->lock);
}


static int
movian_smb2_stat(fa_protocol_t *fap, const char *url, struct fa_stat *fs,
                 int flags, char *errbuf, size_t errlen)
{
  movian_smb2_target_t target = {};
  if(movian_smb2_target_parse(url, &target, errbuf, errlen)) {
    SMB2TRACE("stat parse failed url=%s reason=%s", url, errbuf);
    return FAP_ERROR;
  }

  SMB2TRACE("stat url=%s share=%s path='%s' flags=0x%x", url,
            target.share != NULL ? target.share : "<host>", target.path,
            flags);
  memset(fs, 0, sizeof(*fs));
  if(target.share == NULL || target.share[0] == '\0') {
    fs->fs_type = CONTENT_SHARE;
    SMB2TRACE("stat host url=%s type=%d", url, fs->fs_type);
    movian_smb2_target_fini(&target);
    return FAP_OK;
  }

  int auth_needed = 0;
  movian_smb2_session_t *session =
    movian_smb2_session_acquire(&target, target.share, flags,
                                SMB2_DEFAULT_TIMEOUT, &auth_needed,
                                errbuf, errlen);
  if(session == NULL) {
    SMB2TRACE("stat connect failed url=%s auth_needed=%d reason=%s",
              url, auth_needed, errbuf);
    movian_smb2_target_fini(&target);
    return auth_needed ? FAP_NEED_AUTH : FAP_ERROR;
  }

  struct smb2_context *smb2 = session->smb2;
  int status = FAP_OK;

  hts_mutex_lock(&session->lock);
  if(target.path[0] == '\0') {
    fs->fs_type = CONTENT_DIR;
  } else {
    struct smb2_stat_64 statbuf;
    if(smb2_stat(smb2, target.path, &statbuf) < 0) {
      snprintf(errbuf, errlen, "Unable to stat SMB2 path: %s",
               smb2_get_error(smb2));
      SMB2TRACE("stat failed url=%s path='%s' reason=%s", url,
                target.path, errbuf);
      status = FAP_ERROR;
    } else {
      fs->fs_type = statbuf.smb2_type == SMB2_TYPE_DIRECTORY ?
        CONTENT_DIR : CONTENT_FILE;
      fs->fs_size = statbuf.smb2_size;
      fs->fs_mtime = statbuf.smb2_mtime;
      SMB2TRACE("stat ok url=%s path='%s' smb2_type=%d type=%d size=%"PRId64,
                url, target.path, statbuf.smb2_type, fs->fs_type,
                fs->fs_size);
    }
  }
  hts_mutex_unlock(&session->lock);

  SMB2TRACE("stat done url=%s status=%d type=%d", url, status,
            fs->fs_type);
  movian_smb2_session_release(session);
  movian_smb2_target_fini(&target);
  return status;
}


/*
 * Run a single-path libsmb2 call against a pooled session.
 *
 * Parses the URL, requires a share + path, acquires the session, takes the
 * session lock, invokes op(smb2, target->path), and tears everything down on
 * every path. Returns the libsmb2 status from op on success/failure of the op
 * itself (0 on success, <0 = -errno on failure), or -1 for URL/connect/acquire
 * failures before op runs. Callers map that to their own return type.
 *
 * This collapses the near-identical parse/validate/acquire/lock/release
 * scaffolding that unlink/rmdir/makedir would otherwise each duplicate.
 */
typedef int (*movian_smb2_path_op_t)(struct smb2_context *smb2,
                                     const char *path);

static int
movian_smb2_run_path_op(const char *url, const char *what,
                        movian_smb2_path_op_t op,
                        char *errbuf, size_t errlen)
{
  movian_smb2_target_t target = {};
  if(movian_smb2_target_parse(url, &target, errbuf, errlen))
    return -1;

  if(target.share == NULL || target.share[0] == '\0' ||
     target.path[0] == '\0') {
    snprintf(errbuf, errlen, "SMB2 URL does not identify a path");
    movian_smb2_target_fini(&target);
    return -1;
  }

  movian_smb2_session_t *session =
    movian_smb2_session_acquire(&target, target.share, 0,
                                SMB2_DEFAULT_TIMEOUT, NULL, errbuf, errlen);
  if(session == NULL) {
    movian_smb2_target_fini(&target);
    return -1;
  }

  hts_mutex_lock(&session->lock);
  int status = op(session->smb2, target.path);
  if(status < 0)
    snprintf(errbuf, errlen, "Unable to %s: %s", what,
             smb2_get_error(session->smb2));
  hts_mutex_unlock(&session->lock);

  movian_smb2_session_release(session);
  movian_smb2_target_fini(&target);
  return status;
}


static int
unlink_op(struct smb2_context *smb2, const char *path)
{
  return smb2_unlink(smb2, path);
}


static int
movian_smb2_unlink(const fa_protocol_t *fap, const char *url,
                   char *errbuf, size_t errlen)
{
  int status = movian_smb2_run_path_op(url, "delete SMB2 file", unlink_op,
                                       errbuf, errlen);
  return status < 0 ? -1 : 0;
}


static int
rmdir_op(struct smb2_context *smb2, const char *path)
{
  return smb2_rmdir(smb2, path);
}


static int
movian_smb2_rmdir(const fa_protocol_t *fap, const char *url,
                  char *errbuf, size_t errlen)
{
  int status = movian_smb2_run_path_op(url, "remove SMB2 directory", rmdir_op,
                                       errbuf, errlen);
  return status < 0 ? -1 : 0;
}


static int
movian_smb2_rename(const fa_protocol_t *fap, const char *old_url,
                   const char *new_url, char *errbuf, size_t errlen)
{
  movian_smb2_target_t old_target = {};
  movian_smb2_target_t new_target = {};

  if(movian_smb2_target_parse(old_url, &old_target, errbuf, errlen))
    return -1;

  if(movian_smb2_target_parse(new_url, &new_target, errbuf, errlen)) {
    movian_smb2_target_fini(&old_target);
    return -1;
  }

  if(old_target.share == NULL || old_target.share[0] == '\0' ||
     new_target.share == NULL || new_target.share[0] == '\0' ||
     old_target.path[0] == '\0' || new_target.path[0] == '\0') {
    snprintf(errbuf, errlen, "SMB2 URL does not identify a path");
    movian_smb2_target_fini(&old_target);
    movian_smb2_target_fini(&new_target);
    return -1;
  }

  if(strcmp(old_target.server, new_target.server) != 0 ||
     strcmp(old_target.share, new_target.share) != 0) {
    snprintf(errbuf, errlen, "Cross-share SMB2 rename not supported");
    movian_smb2_target_fini(&old_target);
    movian_smb2_target_fini(&new_target);
    return -2;
  }

  movian_smb2_session_t *session =
    movian_smb2_session_acquire(&old_target, old_target.share, 0,
                                SMB2_DEFAULT_TIMEOUT, NULL, errbuf, errlen);
  if(session == NULL) {
    movian_smb2_target_fini(&old_target);
    movian_smb2_target_fini(&new_target);
    return -1;
  }

  hts_mutex_lock(&session->lock);
  int status = smb2_rename(session->smb2, old_target.path, new_target.path);
  if(status < 0)
    snprintf(errbuf, errlen, "Unable to rename SMB2 path: %s",
             smb2_get_error(session->smb2));
  hts_mutex_unlock(&session->lock);

  movian_smb2_session_release(session);
  movian_smb2_target_fini(&old_target);
  movian_smb2_target_fini(&new_target);
  return status == -EXDEV ? -2 : status < 0 ? -1 : 0;
}


static int
mkdir_op(struct smb2_context *smb2, const char *path)
{
  return smb2_mkdir(smb2, path);
}


static fa_err_code_t
movian_smb2_makedir(fa_protocol_t *fap, const char *url)
{
  char errbuf[256];
  int status = movian_smb2_run_path_op(url, "create SMB2 directory",
                                       mkdir_op, errbuf, sizeof(errbuf));
  return status < 0 ? movian_smb2_errno_to_fap(status) : FAP_OK;
}


static fa_err_code_t
movian_smb2_fsinfo(fa_protocol_t *fap, const char *url, fa_fsinfo_t *ffi)
{
  char errbuf[256];
  movian_smb2_target_t target = {};

  memset(ffi, 0, sizeof(*ffi));

  if(movian_smb2_target_parse(url, &target, errbuf, sizeof(errbuf)))
    return FAP_ERROR;

  if(target.share == NULL || target.share[0] == '\0') {
    movian_smb2_target_fini(&target);
    return FAP_ERROR;
  }

  movian_smb2_session_t *session =
    movian_smb2_session_acquire(&target, target.share, 0,
                                SMB2_DEFAULT_TIMEOUT, NULL,
                                errbuf, sizeof(errbuf));
  if(session == NULL) {
    movian_smb2_target_fini(&target);
    return FAP_ERROR;
  }

  struct smb2_statvfs vfs;
  hts_mutex_lock(&session->lock);
  int status = smb2_statvfs(session->smb2,
                            target.path[0] ? target.path : "", &vfs);
  hts_mutex_unlock(&session->lock);

  if(status == 0) {
    uint64_t frsize = vfs.f_frsize;
    ffi->ffi_size = frsize != 0 && vfs.f_blocks > UINT64_MAX / frsize ?
      UINT64_MAX : (uint64_t)vfs.f_blocks * frsize;
    ffi->ffi_avail = frsize != 0 && vfs.f_bavail > UINT64_MAX / frsize ?
      UINT64_MAX : (uint64_t)vfs.f_bavail * frsize;
  }

  movian_smb2_session_release(session);
  movian_smb2_target_fini(&target);
  return status < 0 ? movian_smb2_errno_to_fap(status) : FAP_OK;
}


static int
movian_smb2_no_parking(fa_handle_t *fh)
{
  return 1;
}


static fa_protocol_t fa_protocol_smb2 = {
  .fap_flags = FAP_INCLUDE_PROTO_IN_URL | FAP_ALLOW_CACHE,
  .fap_name = "smb2",
  .fap_scan = movian_smb2_scan,
  .fap_open = movian_smb2_open,
  .fap_close = movian_smb2_close,
  .fap_read = movian_smb2_read,
  .fap_write = movian_smb2_write,
  .fap_seek = movian_smb2_seek,
  .fap_fsize = movian_smb2_fsize,
  .fap_ftruncate = movian_smb2_ftruncate,
  .fap_stat = movian_smb2_stat,
  .fap_unlink = movian_smb2_unlink,
  .fap_rmdir = movian_smb2_rmdir,
  .fap_rename = movian_smb2_rename,
  .fap_makedir = movian_smb2_makedir,
  .fap_fsinfo = movian_smb2_fsinfo,
  .fap_set_read_timeout = movian_smb2_set_read_timeout,
  .fap_no_parking = movian_smb2_no_parking,
  .fap_redirect = movian_smb2_redirect,
  .fap_normalize = movian_smb2_normalize,
};

FAP_REGISTER(smb2);
