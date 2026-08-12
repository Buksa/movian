/*
 *  Copyright (C) 2026 Movian contributors
 *
 *  This program is free software: you can redistribute it and/or modify
 *  it under the terms of the GNU General Public License as published by
 *  the Free Software Foundation, either version 3 of the License, or
 *  (at your option) any later version.
 */

#include <errno.h>
#include <fcntl.h>
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
#include "keyring.h"
#include "fileaccess/fa_proto.h"

#define SMB2_DEFAULT_TIMEOUT 15

typedef struct {
  char *server;
  char *share;
  char *path;
  char *user;
  char *domain;
} movian_smb2_target_t;

typedef struct {
  char *user;
  char *password;
  char *domain;
} movian_smb2_credentials_t;

typedef struct {
  fa_handle_t h;
  struct smb2_context *smb2;
  struct smb2fh *fh;
  int64_t size;
  hts_mutex_t mutex;
} movian_smb2_file_t;

typedef struct {
  int done;
  int status;
  struct srvsvc_NetrShareEnum_rep *reply;
} movian_smb2_enum_state_t;


static void
movian_smb2_target_fini(movian_smb2_target_t *target)
{
  free(target->server);
  free(target->share);
  free(target->path);
  free(target->user);
  free(target->domain);
}


static int
movian_smb2_target_parse(const char *url, movian_smb2_target_t *target,
                         char *errbuf, size_t errlen)
{
  if(strncmp(url, "smb2://", 7)) {
    snprintf(errbuf, errlen, "Invalid SMB2 URL");
    return -1;
  }

  struct smb2_context *smb2 = smb2_init_context();
  if(smb2 == NULL) {
    snprintf(errbuf, errlen, "Unable to initialize SMB2");
    return -1;
  }

  size_t parser_url_len = strlen(url);
  char *parser_url = malloc(parser_url_len);
  if(parser_url == NULL) {
    snprintf(errbuf, errlen, "Out of memory while parsing SMB2 URL");
    smb2_destroy_context(smb2);
    return -1;
  }
  snprintf(parser_url, parser_url_len, "smb://%s", url + 7);

  struct smb2_url *parsed = smb2_parse_url(smb2, parser_url);
  free(parser_url);
  if(parsed == NULL || parsed->server == NULL || parsed->server[0] == '\0') {
    snprintf(errbuf, errlen, "Invalid SMB2 URL: %s", smb2_get_error(smb2));
    if(parsed != NULL)
      smb2_destroy_url(parsed);
    smb2_destroy_context(smb2);
    return -1;
  }

  target->server = strdup(parsed->server);
  target->share = parsed->share != NULL ? strdup(parsed->share) : NULL;
  target->path = parsed->path != NULL ? strdup(parsed->path) : strdup("");
  target->user = parsed->user != NULL ? strdup(parsed->user) : NULL;
  target->domain = parsed->domain != NULL ? strdup(parsed->domain) : NULL;

  smb2_destroy_url(parsed);
  smb2_destroy_context(smb2);

  if(target->server == NULL || target->path == NULL) {
    snprintf(errbuf, errlen, "Out of memory while parsing SMB2 URL");
    movian_smb2_target_fini(target);
    memset(target, 0, sizeof(*target));
    return -1;
  }
  return 0;
}


static void
movian_smb2_credentials_fini(movian_smb2_credentials_t *credentials)
{
  free(credentials->user);
  free(credentials->password);
  free(credentials->domain);
}


static char *
movian_smb2_keyring_id(const movian_smb2_target_t *target)
{
  size_t len = strlen(target->server) + 32;

  if(target->user != NULL)
    len += strlen(target->user);
  if(target->domain != NULL)
    len += strlen(target->domain);

  char *id = malloc(len);
  if(id == NULL)
    return NULL;

  if(target->user != NULL) {
    snprintf(id, len, "smb2:connection:%s%s%s@%s",
             target->domain != NULL ? target->domain : "",
             target->domain != NULL ? ";" : "",
             target->user, target->server);
  } else {
    snprintf(id, len, "smb2:connection:%s", target->server);
  }
  return id;
}


static void
movian_smb2_default_credentials(const movian_smb2_target_t *target,
                                movian_smb2_credentials_t *credentials)
{
  credentials->user = strdup(target->user != NULL ? target->user : "guest");
  credentials->password = NULL;
  credentials->domain =
    target->domain != NULL ? strdup(target->domain) : NULL;
}


static int
movian_smb2_apply_target_identity(const movian_smb2_target_t *target,
                                  movian_smb2_credentials_t *credentials,
                                  char *errbuf, size_t errlen)
{
  if(target->user != NULL) {
    char *user = strdup(target->user);
    if(user == NULL) {
      snprintf(errbuf, errlen, "Out of memory while preparing SMB2 login");
      return -1;
    }
    free(credentials->user);
    credentials->user = user;
  }

  if(target->domain != NULL) {
    char *domain = strdup(target->domain);
    if(domain == NULL) {
      snprintf(errbuf, errlen, "Out of memory while preparing SMB2 login");
      return -1;
    }
    free(credentials->domain);
    credentials->domain = domain;
  }
  return 0;
}


static int
movian_smb2_is_auth_error(int status, const char *reason)
{
  if(status == -EACCES || status == -EPERM)
    return 1;
  if(reason == NULL)
    return 0;
  return strstr(reason, "STATUS_LOGON_FAILURE") != NULL ||
    strstr(reason, "STATUS_ACCESS_DENIED") != NULL ||
    strstr(reason, "STATUS_NETWORK_ACCESS_DENIED") != NULL ||
    strstr(reason, "STATUS_INVALID_PARAMETER") != NULL ||
    strstr(reason, "STATUS_INVALID_ACCOUNT_NAME") != NULL ||
    strstr(reason, "STATUS_WRONG_PASSWORD") != NULL ||
    strstr(reason, "STATUS_ACCOUNT_RESTRICTION") != NULL ||
    strstr(reason, "STATUS_INVALID_LOGON_HOURS") != NULL ||
    strstr(reason, "STATUS_PASSWORD_EXPIRED") != NULL ||
    strstr(reason, "STATUS_PASSWORD_MUST_CHANGE") != NULL ||
    strstr(reason, "STATUS_ACCOUNT_DISABLED") != NULL ||
    strstr(reason, "STATUS_ACCOUNT_EXPIRED") != NULL ||
    strstr(reason, "STATUS_ACCOUNT_LOCKED_OUT") != NULL ||
    strstr(reason, "STATUS_LOGON_NOT_GRANTED") != NULL ||
    strstr(reason, "STATUS_LOGON_TYPE_NOT_GRANTED") != NULL;
}


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


static struct smb2_context *
movian_smb2_connect_once(const movian_smb2_target_t *target,
                         const char *share,
                         const movian_smb2_credentials_t *credentials,
                         int timeout, int *status,
                         char *errbuf, size_t errlen)
{
  const char *user =
    credentials->user != NULL && credentials->user[0] != '\0' ?
    credentials->user : "guest";
  struct smb2_context *smb2 = smb2_init_context();
  if(smb2 == NULL) {
    *status = -ENOMEM;
    snprintf(errbuf, errlen, "Unable to initialize SMB2");
    return NULL;
  }

  smb2_set_timeout(smb2, timeout);
  smb2_set_security_mode(smb2, SMB2_NEGOTIATE_SIGNING_ENABLED);
  smb2_set_user(smb2, user);
  smb2_set_password(smb2, credentials->password);
  if(credentials->domain != NULL)
    smb2_set_domain(smb2, credentials->domain);

  *status = smb2_connect_share(smb2, target->server, share, user);
  if(*status == 0)
    return smb2;

  snprintf(errbuf, errlen, "%s", smb2_get_error(smb2));
  smb2_destroy_context(smb2);
  return NULL;
}


static struct smb2_context *
movian_smb2_connect_impl(const movian_smb2_target_t *target, const char *share,
                         int flags, int timeout, int *auth_needed,
                         char *errbuf, size_t errlen)
{
  movian_smb2_credentials_t credentials = {};
  char *keyring_id = movian_smb2_keyring_id(target);
  int status;

  if(auth_needed != NULL)
    *auth_needed = 0;

  if(keyring_id == NULL) {
    snprintf(errbuf, errlen, "Out of memory while preparing SMB2 login");
    return NULL;
  }

  int keyring_status =
    keyring_lookup(keyring_id, &credentials.user, &credentials.password,
                   &credentials.domain, NULL, NULL, NULL, 0);
  if(keyring_status != KEYRING_OK) {
    movian_smb2_default_credentials(target, &credentials);
  } else if(movian_smb2_apply_target_identity(target, &credentials,
                                               errbuf, errlen)) {
    movian_smb2_credentials_fini(&credentials);
    free(keyring_id);
    return NULL;
  }

  char reason[256] = {};
  struct smb2_context *smb2 =
    movian_smb2_connect_once(target, share, &credentials, timeout, &status,
                            reason, sizeof(reason));
  if(smb2 != NULL) {
    movian_smb2_credentials_fini(&credentials);
    free(keyring_id);
    return smb2;
  }

  int auth_error = movian_smb2_is_auth_error(status, reason);
  if(auth_error && auth_needed != NULL)
    *auth_needed = 1;

  if(flags & (FA_NON_INTERACTIVE | FA_DISABLE_AUTH) ||
     !auth_error) {
    snprintf(errbuf, errlen, "Unable to connect to SMB2 share: %s", reason);
    movian_smb2_credentials_fini(&credentials);
    free(keyring_id);
    return NULL;
  }

  movian_smb2_credentials_fini(&credentials);
  memset(&credentials, 0, sizeof(credentials));
  if(movian_smb2_apply_target_identity(target, &credentials,
                                       errbuf, errlen)) {
    free(keyring_id);
    return NULL;
  }

  size_t source_len = strlen(target->server) + 16;
  char *source = malloc(source_len);
  if(source == NULL) {
    snprintf(errbuf, errlen, "Out of memory while preparing SMB2 login");
    free(keyring_id);
    return NULL;
  }
  snprintf(source, source_len, "SMB2 server '%s'", target->server);

  keyring_status =
    keyring_lookup(keyring_id,
                   target->user != NULL ? NULL : &credentials.user,
                   &credentials.password,
                   target->domain != NULL ? NULL : &credentials.domain,
                   NULL, source, reason,
                   KEYRING_QUERY_USER | KEYRING_SHOW_REMEMBER_ME |
                   KEYRING_REMEMBER_ME_SET);
  free(source);

  if(keyring_status != KEYRING_OK) {
    snprintf(errbuf, errlen, "Authentication rejected by user");
    movian_smb2_credentials_fini(&credentials);
    free(keyring_id);
    return NULL;
  }

  smb2 = movian_smb2_connect_once(target, share, &credentials, timeout,
                                  &status, reason, sizeof(reason));
  if(smb2 == NULL)
    snprintf(errbuf, errlen, "Unable to connect to SMB2 share: %s", reason);

  movian_smb2_credentials_fini(&credentials);
  free(keyring_id);
  return smb2;
}


static struct smb2_context *
movian_smb2_connect(const movian_smb2_target_t *target, const char *share,
                    int flags, int timeout, char *errbuf, size_t errlen)
{
  return movian_smb2_connect_impl(target, share, flags, timeout, NULL,
                                  errbuf, errlen);
}


static void
movian_smb2_disconnect(struct smb2_context *smb2)
{
  if(smb2 == NULL)
    return;
  smb2_disconnect_share(smb2);
  smb2_destroy_context(smb2);
}


static char *
movian_smb2_child_url(const char *parent, const char *name)
{
  size_t parent_len = strlen(parent);
  while(parent_len > 0 && parent[parent_len - 1] == '/')
    parent_len--;

  size_t len = parent_len + strlen(name) + 2;
  char *url = malloc(len);
  if(url != NULL)
    snprintf(url, len, "%.*s/%s", (int)parent_len, parent, name);
  return url;
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
  struct smb2_context *smb2 =
    movian_smb2_connect(target, "IPC$", flags, SMB2_DEFAULT_TIMEOUT,
                        errbuf, errlen);
  if(smb2 == NULL)
    return -1;

  movian_smb2_enum_state_t state = {};
  if(smb2_share_enum_async(smb2, SHARE_INFO_1,
                           movian_smb2_enum_callback, &state) < 0 ||
     movian_smb2_wait_for_enum(smb2, &state) < 0 ||
     state.status != 0 || state.reply == NULL) {
    snprintf(errbuf, errlen, "Unable to enumerate SMB2 shares: %s",
             smb2_get_error(smb2));
    if(state.reply != NULL)
      smb2_free_data(smb2, state.reply);
    movian_smb2_disconnect(smb2);
    return -1;
  }

  struct srvsvc_SHARE_INFO_1_CONTAINER *shares =
    &state.reply->ses.ShareInfo.Level1;
  if(shares->Buffer != NULL) {
    for(uint32_t i = 0; i < shares->EntriesRead; i++) {
      struct srvsvc_SHARE_INFO_1 *share =
        &shares->Buffer->share_info_1[i];
      const char *name = share->netname.utf8;

      if((share->type & 3) != SHARE_TYPE_DISKTREE || name == NULL)
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
  movian_smb2_disconnect(smb2);
  return 0;
}


static int
movian_smb2_scan_directory(fa_dir_t *fd, const char *url,
                           const movian_smb2_target_t *target, int flags,
                           char *errbuf, size_t errlen)
{
  struct smb2_context *smb2 =
    movian_smb2_connect(target, target->share, flags, SMB2_DEFAULT_TIMEOUT,
                        errbuf, errlen);
  if(smb2 == NULL)
    return -1;

  struct smb2dir *dir = smb2_opendir(smb2, target->path);
  if(dir == NULL) {
    snprintf(errbuf, errlen, "Unable to open SMB2 directory: %s",
             smb2_get_error(smb2));
    movian_smb2_disconnect(smb2);
    return -1;
  }

  struct smb2dirent *entry;
  while((entry = smb2_readdir(smb2, dir)) != NULL) {
    if(!strcmp(entry->name, ".") || !strcmp(entry->name, ".."))
      continue;

    int type = entry->st.smb2_type == SMB2_TYPE_DIRECTORY ?
      CONTENT_DIR : CONTENT_FILE;
    char *child = movian_smb2_child_url(url, entry->name);
    if(child == NULL)
      continue;

    fa_dir_entry_t *fde = fa_dir_add(fd, child, entry->name, type);
    if(fde != NULL) {
      fde->fde_stat.fs_type = type;
      fde->fde_stat.fs_size = entry->st.smb2_size;
      fde->fde_stat.fs_mtime = entry->st.smb2_mtime;
      fde->fde_statdone = 1;
    }
    free(child);
  }

  smb2_closedir(smb2, dir);
  movian_smb2_disconnect(smb2);
  return 0;
}


static int
movian_smb2_scan(fa_protocol_t *fap, fa_dir_t *fd, const char *url,
                 char *errbuf, size_t errlen, int flags)
{
  movian_smb2_target_t target = {};
  if(movian_smb2_target_parse(url, &target, errbuf, errlen))
    return -1;

  int status = target.share == NULL || target.share[0] == '\0' ?
    movian_smb2_scan_host(fd, url, &target, flags, errbuf, errlen) :
    movian_smb2_scan_directory(fd, url, &target, flags, errbuf, errlen);

  movian_smb2_target_fini(&target);
  return status;
}


static fa_handle_t *
movian_smb2_open(fa_protocol_t *fap, const char *url,
                 char *errbuf, size_t errlen, int flags,
                 fa_open_extra_t *foe)
{
  movian_smb2_target_t target = {};
  if(movian_smb2_target_parse(url, &target, errbuf, errlen))
    return NULL;

  if(target.share == NULL || target.share[0] == '\0' ||
     target.path[0] == '\0') {
    snprintf(errbuf, errlen, "SMB2 URL does not identify a file");
    movian_smb2_target_fini(&target);
    return NULL;
  }

  int timeout = foe != NULL && foe->foe_open_timeout > 0 ?
    (foe->foe_open_timeout + 999) / 1000 : SMB2_DEFAULT_TIMEOUT;
  struct smb2_context *smb2 =
    movian_smb2_connect(&target, target.share, flags, timeout,
                        errbuf, errlen);
  if(smb2 == NULL) {
    movian_smb2_target_fini(&target);
    return NULL;
  }

  int open_flags = O_RDONLY;
  if(flags & FA_WRITE) {
    open_flags = O_RDWR | O_CREAT;
    if(!(flags & FA_APPEND))
      open_flags |= O_TRUNC;
  }

  struct smb2fh *fh = smb2_open(smb2, target.path, open_flags);
  if(fh == NULL) {
    snprintf(errbuf, errlen, "Unable to open SMB2 file: %s",
             smb2_get_error(smb2));
    movian_smb2_disconnect(smb2);
    movian_smb2_target_fini(&target);
    return NULL;
  }

  struct smb2_stat_64 statbuf;
  if(smb2_fstat(smb2, fh, &statbuf) < 0 ||
     statbuf.smb2_type == SMB2_TYPE_DIRECTORY) {
    snprintf(errbuf, errlen, "Unable to stat SMB2 file: %s",
             smb2_get_error(smb2));
    smb2_close(smb2, fh);
    movian_smb2_disconnect(smb2);
    movian_smb2_target_fini(&target);
    return NULL;
  }

  movian_smb2_file_t *file = calloc(1, sizeof(*file));
  if(file == NULL) {
    snprintf(errbuf, errlen, "Out of memory while opening SMB2 file");
    smb2_close(smb2, fh);
    movian_smb2_disconnect(smb2);
    movian_smb2_target_fini(&target);
    return NULL;
  }

  file->h.fh_proto = fap;
  file->smb2 = smb2;
  file->fh = fh;
  file->size = statbuf.smb2_size;
  hts_mutex_init(&file->mutex);

  if(flags & FA_APPEND) {
    if(smb2_lseek(file->smb2, file->fh, 0, SEEK_END, NULL) < 0) {
      snprintf(errbuf, errlen, "Unable to seek SMB2 file: %s",
               smb2_get_error(smb2));
      hts_mutex_destroy(&file->mutex);
      free(file);
      smb2_close(smb2, fh);
      movian_smb2_disconnect(smb2);
      movian_smb2_target_fini(&target);
      return NULL;
    }
  }

  movian_smb2_target_fini(&target);
  return &file->h;
}


static void
movian_smb2_close(fa_handle_t *fh)
{
  movian_smb2_file_t *file = (movian_smb2_file_t *)fh;

  hts_mutex_lock(&file->mutex);
  smb2_close(file->smb2, file->fh);
  movian_smb2_disconnect(file->smb2);
  hts_mutex_unlock(&file->mutex);
  hts_mutex_destroy(&file->mutex);
  free(file);
}


static int
movian_smb2_read(fa_handle_t *fh, void *buf, size_t size)
{
  movian_smb2_file_t *file = (movian_smb2_file_t *)fh;
  uint32_t count = size > INT_MAX ? INT_MAX : size;

  hts_mutex_lock(&file->mutex);
  int status = smb2_read(file->smb2, file->fh, buf, count);
  hts_mutex_unlock(&file->mutex);
  return status;
}


static int
movian_smb2_write(fa_handle_t *fh, const void *buf, size_t size)
{
  movian_smb2_file_t *file = (movian_smb2_file_t *)fh;
  uint32_t count = size > INT_MAX ? INT_MAX : size;

  hts_mutex_lock(&file->mutex);
  int status = smb2_write(file->smb2, file->fh, buf, count);
  if(status > 0) {
    int64_t pos = smb2_lseek(file->smb2, file->fh, 0, SEEK_CUR, NULL);
    if(pos > file->size)
      file->size = pos;
  }
  hts_mutex_unlock(&file->mutex);
  return status;
}


static int64_t
movian_smb2_seek(fa_handle_t *fh, int64_t pos, int whence, int lazy)
{
  movian_smb2_file_t *file = (movian_smb2_file_t *)fh;

  hts_mutex_lock(&file->mutex);
  int64_t status = smb2_lseek(file->smb2, file->fh, pos, whence, NULL);
  hts_mutex_unlock(&file->mutex);
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

  hts_mutex_lock(&file->mutex);
  int status = smb2_ftruncate(file->smb2, file->fh, newsize);
  if(status == 0)
    file->size = newsize;
  hts_mutex_unlock(&file->mutex);

  return status < 0 ? movian_smb2_errno_to_fap(status) : FAP_OK;
}


static void
movian_smb2_set_read_timeout(fa_handle_t *fh, int ms)
{
  movian_smb2_file_t *file = (movian_smb2_file_t *)fh;
  int seconds = ms > 0 ? (ms + 999) / 1000 : 0;

  hts_mutex_lock(&file->mutex);
  smb2_set_timeout(file->smb2, seconds);
  hts_mutex_unlock(&file->mutex);
}


static int
movian_smb2_stat(fa_protocol_t *fap, const char *url, struct fa_stat *fs,
                 int flags, char *errbuf, size_t errlen)
{
  movian_smb2_target_t target = {};
  if(movian_smb2_target_parse(url, &target, errbuf, errlen))
    return FAP_ERROR;

  memset(fs, 0, sizeof(*fs));
  if(target.share == NULL || target.share[0] == '\0') {
    fs->fs_type = CONTENT_DIR;
    movian_smb2_target_fini(&target);
    return FAP_OK;
  }

  int auth_needed = 0;
  struct smb2_context *smb2 =
    movian_smb2_connect_impl(&target, target.share, flags,
                             SMB2_DEFAULT_TIMEOUT, &auth_needed,
                             errbuf, errlen);
  if(smb2 == NULL) {
    movian_smb2_target_fini(&target);
    return auth_needed ? FAP_NEED_AUTH : FAP_ERROR;
  }

  int status = FAP_OK;
  if(target.path[0] == '\0') {
    fs->fs_type = CONTENT_DIR;
  } else {
    struct smb2_stat_64 statbuf;
    if(smb2_stat(smb2, target.path, &statbuf) < 0) {
      snprintf(errbuf, errlen, "Unable to stat SMB2 path: %s",
               smb2_get_error(smb2));
      status = FAP_ERROR;
    } else {
      fs->fs_type = statbuf.smb2_type == SMB2_TYPE_DIRECTORY ?
        CONTENT_DIR : CONTENT_FILE;
      fs->fs_size = statbuf.smb2_size;
      fs->fs_mtime = statbuf.smb2_mtime;
    }
  }

  movian_smb2_disconnect(smb2);
  movian_smb2_target_fini(&target);
  return status;
}


static int
movian_smb2_unlink(const fa_protocol_t *fap, const char *url,
                   char *errbuf, size_t errlen)
{
  movian_smb2_target_t target = {};
  if(movian_smb2_target_parse(url, &target, errbuf, errlen))
    return -1;

  if(target.share == NULL || target.share[0] == '\0' ||
     target.path[0] == '\0') {
    snprintf(errbuf, errlen, "SMB2 URL does not identify a file");
    movian_smb2_target_fini(&target);
    return -1;
  }

  struct smb2_context *smb2 =
    movian_smb2_connect(&target, target.share, 0, SMB2_DEFAULT_TIMEOUT,
                        errbuf, errlen);
  if(smb2 == NULL) {
    movian_smb2_target_fini(&target);
    return -1;
  }

  int status = smb2_unlink(smb2, target.path);
  if(status < 0)
    snprintf(errbuf, errlen, "Unable to delete SMB2 file: %s",
             smb2_get_error(smb2));

  movian_smb2_disconnect(smb2);
  movian_smb2_target_fini(&target);
  return status < 0 ? -1 : 0;
}


static int
movian_smb2_rmdir(const fa_protocol_t *fap, const char *url,
                  char *errbuf, size_t errlen)
{
  movian_smb2_target_t target = {};
  if(movian_smb2_target_parse(url, &target, errbuf, errlen))
    return -1;

  if(target.share == NULL || target.share[0] == '\0' ||
     target.path[0] == '\0') {
    snprintf(errbuf, errlen, "SMB2 URL does not identify a directory");
    movian_smb2_target_fini(&target);
    return -1;
  }

  struct smb2_context *smb2 =
    movian_smb2_connect(&target, target.share, 0, SMB2_DEFAULT_TIMEOUT,
                        errbuf, errlen);
  if(smb2 == NULL) {
    movian_smb2_target_fini(&target);
    return -1;
  }

  int status = smb2_rmdir(smb2, target.path);
  if(status < 0)
    snprintf(errbuf, errlen, "Unable to remove SMB2 directory: %s",
             smb2_get_error(smb2));

  movian_smb2_disconnect(smb2);
  movian_smb2_target_fini(&target);
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

  struct smb2_context *smb2 =
    movian_smb2_connect(&old_target, old_target.share, 0,
                        SMB2_DEFAULT_TIMEOUT, errbuf, errlen);
  if(smb2 == NULL) {
    movian_smb2_target_fini(&old_target);
    movian_smb2_target_fini(&new_target);
    return -1;
  }

  int status = smb2_rename(smb2, old_target.path, new_target.path);
  if(status < 0)
    snprintf(errbuf, errlen, "Unable to rename SMB2 path: %s",
             smb2_get_error(smb2));

  movian_smb2_disconnect(smb2);
  movian_smb2_target_fini(&old_target);
  movian_smb2_target_fini(&new_target);
  return status == -EXDEV ? -2 : status < 0 ? -1 : 0;
}


static fa_err_code_t
movian_smb2_makedir(fa_protocol_t *fap, const char *url)
{
  char errbuf[256];
  movian_smb2_target_t target = {};
  if(movian_smb2_target_parse(url, &target, errbuf, sizeof(errbuf)))
    return FAP_ERROR;

  if(target.share == NULL || target.share[0] == '\0' ||
     target.path[0] == '\0') {
    movian_smb2_target_fini(&target);
    return FAP_ERROR;
  }

  struct smb2_context *smb2 =
    movian_smb2_connect(&target, target.share, 0, SMB2_DEFAULT_TIMEOUT,
                        errbuf, sizeof(errbuf));
  if(smb2 == NULL) {
    movian_smb2_target_fini(&target);
    return FAP_ERROR;
  }

  int status = smb2_mkdir(smb2, target.path);
  movian_smb2_disconnect(smb2);
  movian_smb2_target_fini(&target);
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

  struct smb2_context *smb2 =
    movian_smb2_connect(&target, target.share, 0, SMB2_DEFAULT_TIMEOUT,
                        errbuf, sizeof(errbuf));
  if(smb2 == NULL) {
    movian_smb2_target_fini(&target);
    return FAP_ERROR;
  }

  struct smb2_statvfs vfs;
  int status = smb2_statvfs(smb2, target.path[0] ? target.path : "", &vfs);
  if(status == 0) {
    ffi->ffi_size = (uint64_t)vfs.f_blocks * vfs.f_frsize;
    ffi->ffi_avail = (uint64_t)vfs.f_bavail * vfs.f_frsize;
  }

  movian_smb2_disconnect(smb2);
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
};

FAP_REGISTER(smb2);
