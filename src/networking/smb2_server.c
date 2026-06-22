/*
 *  Copyright (C) 2026 Movian contributors
 *
 *  This program is free software: you can redistribute it and/or modify
 *  it under the terms of the GNU General Public License as published by
 *  the Free Software Foundation, either version 3 of the License, or
 *  (at your option) any later version.
 */

#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#include <smb2/smb2.h>
#include <smb2/libsmb2.h>
#include <smb2/libsmb2-raw.h>

#include "main.h"
#include "arch/threads.h"
#include "asyncio.h"
#include "settings.h"
#include "fileaccess/fileaccess.h"
#include "fileaccess/fa_proto.h"
#include "smb2_server.h"

#define SMB2SRV_DEFAULT_PORT 1445
#define SMB2SRV_MAX_CONNECTIONS 8

#define SMB2SRV_TRACE(level, fmt, ...) do {                         \
  if(gconf.enable_smb_debug)                                        \
    TRACE((level), "SMB2-SERVER", fmt, ##__VA_ARGS__);              \
} while(0)

typedef struct smb2srv_handle {
  LIST_ENTRY(smb2srv_handle) link;
  smb2_file_id file_id;
  struct smb2_context *smb2;
  char *vfs_url;
  fa_handle_t *fh;
  fa_dir_t *dir;
  uint8_t *dir_info;
  char *dir_pattern;
  int dir_sent;
  int is_dir;
  int64_t size;
  time_t mtime;
  struct smb2_file_basic_info basic_info;
  struct smb2_file_standard_info standard_info;
  struct smb2_file_all_info all_info;
  struct smb2_file_network_open_info network_open_info;
  struct smb2_file_fs_size_info fs_size_info;
} smb2srv_handle_t;

typedef struct smb2srv_state {
  hts_mutex_t mutex;
  struct smb2_server server;
  struct smb2_ioctl_validate_negotiate_info validate_info;
  setting_t *port_setting;
  int enabled;
  int thread_running;
  int restart_requested;
  int reverting_port_setting;
  int port;
  uint64_t next_file_id;
  char *username;
  char *password;
  char *share_name;
  char *share_root;
  LIST_HEAD(, smb2srv_handle) handles;
} smb2srv_state_t;

static smb2srv_state_t smb2srv;


static int smb2srv_config_ready_locked(void);


static int
smb2srv_queue_status(struct smb2_context *smb2, uint16_t command,
                     uint32_t status)
{
  struct smb2_error_reply err;
  struct smb2_pdu *pdu;

  memset(&err, 0, sizeof(err));
  pdu = smb2_cmd_error_reply_async(smb2, &err, command, status, NULL, NULL);
  if(pdu == NULL)
    return -1;
  smb2_queue_pdu(smb2, pdu);
  return 1;
}


static uint32_t
smb2srv_attrs_from_type(int type)
{
  if(type == CONTENT_DIR || type == CONTENT_SHARE)
    return SMB2_FILE_ATTRIBUTE_DIRECTORY;
  return SMB2_FILE_ATTRIBUTE_NORMAL;
}


static void
smb2srv_fill_time(struct smb2_timeval *tv, time_t t)
{
  tv->tv_sec = t > 0 ? t : 1;
  tv->tv_usec = 0;
}


static uint32_t
smb2srv_name_utf16_len(const char *name)
{
  struct smb2_utf16 *utf16;
  uint32_t len;

  if(name == NULL || name[0] == '\0')
    return 0;

  utf16 = smb2_utf8_to_utf16(name);
  if(utf16 == NULL)
    return 0;

  len = 2 * utf16->len;
  free(utf16);
  return len;
}


static void
smb2srv_handle_close(smb2srv_handle_t *h)
{
  if(h->fh != NULL)
    fa_close(h->fh);
  if(h->dir != NULL)
    fa_dir_free(h->dir);
  free(h->dir_info);
  free(h->dir_pattern);
  free(h->vfs_url);
  free(h);
}


static void
smb2srv_handles_close_for_context_locked(struct smb2_context *smb2)
{
  smb2srv_handle_t *h, *next;

  for(h = LIST_FIRST(&smb2srv.handles); h != NULL; h = next) {
    next = LIST_NEXT(h, link);
    if(h->smb2 != smb2)
      continue;
    LIST_REMOVE(h, link);
    smb2srv_handle_close(h);
  }
}


static smb2srv_handle_t *
smb2srv_handle_find_locked(struct smb2_context *smb2, const smb2_file_id file_id)
{
  smb2srv_handle_t *h;
  int compound = 1;

  for(size_t i = 0; i < SMB2_FD_SIZE; i++) {
    if(file_id[i] != 0xff) {
      compound = 0;
      break;
    }
  }

  LIST_FOREACH(h, &smb2srv.handles, link) {
    if(compound && h->smb2 == smb2)
      return h;
    if(h->smb2 == smb2 && !memcmp(h->file_id, file_id, SMB2_FD_SIZE))
      return h;
  }
  return NULL;
}


static void
smb2srv_handle_make_id_locked(smb2srv_handle_t *h)
{
  uint64_t id = ++smb2srv.next_file_id;

  memset(h->file_id, 0, sizeof(h->file_id));
  memcpy(h->file_id, &id, sizeof(id));
}


static int
smb2srv_pattern_match(const char *pattern, const char *name)
{
  if(name == NULL)
    name = "";

  if(pattern == NULL || pattern[0] == '\0' ||
     (pattern[0] == '*' && pattern[1] == '\0') ||
     !strcmp(pattern, "*.*"))
    return 1;

  while(*pattern != '\0') {
    if(*pattern == '*') {
      while(*pattern == '*')
        pattern++;
      if(*pattern == '\0')
        return 1;
      while(*name != '\0') {
        if(smb2srv_pattern_match(pattern, name))
          return 1;
        name++;
      }
      return 0;
    }

    if(*name == '\0')
      return 0;

    if(*pattern != '?' &&
       tolower((unsigned char)*pattern) != tolower((unsigned char)*name))
      return 0;

    pattern++;
    name++;
  }
  return *name == '\0';
}


static int
smb2srv_bad_segment(const char *start, size_t len)
{
  return len == 0 ||
         (len == 1 && start[0] == '.') ||
         (len == 2 && start[0] == '.' && start[1] == '.');
}


static char *
smb2srv_map_path(const char *root, const char *smb_path)
{
  const char *p = smb_path != NULL ? smb_path : "";
  char *clean, *out;
  size_t root_len, clean_len, i, j = 0;
  size_t seg_start = 0;
  int in_segment = 0;

  while(*p == '\\' || *p == '/')
    p++;

  clean = malloc(strlen(p) + 1);
  if(clean == NULL)
    return NULL;

  for(i = 0; p[i] != '\0'; i++) {
    char c = p[i] == '\\' ? '/' : p[i];

    if(c == '/') {
      if(in_segment && smb2srv_bad_segment(clean + seg_start,
                                           j - seg_start)) {
        free(clean);
        return NULL;
      }
      in_segment = 0;
      if(j > 0 && clean[j - 1] != '/')
        clean[j++] = '/';
      continue;
    }

    if(!in_segment) {
      seg_start = j;
      in_segment = 1;
    }
    clean[j++] = c;
  }

  if(in_segment && smb2srv_bad_segment(clean + seg_start, j - seg_start)) {
    free(clean);
    return NULL;
  }
  if(j > 0 && clean[j - 1] == '/')
    j--;
  clean[j] = '\0';

  clean_len = strlen(clean);
  if(clean_len == 0) {
    out = strdup(root);
    free(clean);
    return out;
  }

  root_len = strlen(root);
  out = malloc(root_len + clean_len + 2);
  if(out != NULL)
    snprintf(out, root_len + clean_len + 2, "%s%s%s", root,
             root_len > 0 && root[root_len - 1] == '/' ? "" : "/",
             clean);
  free(clean);
  return out;
}


static const char *
smb2srv_request_share_name(const char *path)
{
  const char *name = path;
  const char *slash;

  if(name == NULL)
    return "";
  slash = strrchr(name, '\\');
  if(slash != NULL)
    name = slash + 1;
  slash = strrchr(name, '/');
  if(slash != NULL)
    name = slash + 1;
  return name;
}


static int
smb2srv_destruction_event(struct smb2_server *srvr, struct smb2_context *smb2)
{
  SMB2SRV_TRACE(TRACE_DEBUG, "session destroyed");
  hts_mutex_lock(&smb2srv.mutex);
  smb2srv_handles_close_for_context_locked(smb2);
  hts_mutex_unlock(&smb2srv.mutex);
  return 0;
}


static int
smb2srv_authorize(struct smb2_server *srvr, struct smb2_context *smb2,
                  const char *user, const char *domain, const char *workstation)
{
  int ok;

  hts_mutex_lock(&smb2srv.mutex);
  ok = smb2srv.username != NULL && smb2srv.password != NULL &&
       smb2srv.username[0] != '\0' && smb2srv.password[0] != '\0' &&
       user != NULL && !strcmp(user, smb2srv.username);
  if(ok)
    smb2_set_password(smb2, smb2srv.password);
  hts_mutex_unlock(&smb2srv.mutex);

  SMB2SRV_TRACE(ok ? TRACE_DEBUG : TRACE_ERROR,
                "auth user=%s domain=%s workstation=%s status=%s",
                user != NULL ? user : "<unset>",
                domain != NULL ? domain : "<unset>",
                workstation != NULL ? workstation : "<unset>",
                ok ? "ok" : "denied");
  return ok ? 0 : -1;
}


static int
smb2srv_session_established(struct smb2_server *srvr, struct smb2_context *smb2)
{
  SMB2SRV_TRACE(TRACE_DEBUG, "session established dialect=%04x",
                smb2_get_dialect(smb2));
  return 0;
}


static int
smb2srv_logoff(struct smb2_server *srvr, struct smb2_context *smb2)
{
  SMB2SRV_TRACE(TRACE_DEBUG, "session logoff");
  return 0;
}


static int
smb2srv_tree_connect(struct smb2_server *srvr, struct smb2_context *smb2,
                     struct smb2_tree_connect_request *req,
                     struct smb2_tree_connect_reply *rep)
{
  char *path = req != NULL && req->path != NULL && req->path_length > 0 ?
    (char *)smb2_utf16_to_utf8(req->path, req->path_length / 2) : NULL;
  const char *name = smb2srv_request_share_name(path);
  int ok;

  hts_mutex_lock(&smb2srv.mutex);
  ok = smb2srv.share_name != NULL && !strcmp(name, smb2srv.share_name);
  hts_mutex_unlock(&smb2srv.mutex);

  SMB2SRV_TRACE(TRACE_DEBUG, "tree-connect path=%s share=%s status=%s",
                path != NULL ? path : "<unset>", name,
                ok ? "ok" : "denied");

  if(!ok) {
    SMB2SRV_TRACE(TRACE_ERROR, "unknown share '%s'", name);
    free(path);
    return smb2srv_queue_status(smb2, SMB2_TREE_CONNECT,
                                SMB2_STATUS_OBJECT_NAME_NOT_FOUND);
  }
  free(path);

  memset(rep, 0, sizeof(*rep));
  rep->share_type = SMB2_SHARE_TYPE_DISK;
  rep->share_flags = SMB2_SHAREFLAG_ACCESS_BASED_DIRECTORY_ENUM;
  rep->capabilities = 0;
  rep->maximal_access = SMB2_GENERIC_READ | SMB2_FILE_READ_DATA |
    SMB2_FILE_LIST_DIRECTORY | SMB2_FILE_READ_EA | SMB2_FILE_READ_ATTRIBUTES |
    SMB2_READ_CONTROL | SMB2_SYNCHRONIZE;
  return 0;
}


static int
smb2srv_tree_disconnect(struct smb2_server *srvr, struct smb2_context *smb2,
                        const uint32_t tree_id)
{
  SMB2SRV_TRACE(TRACE_DEBUG, "tree disconnect 0x%x", tree_id);
  hts_mutex_lock(&smb2srv.mutex);
  smb2srv_handles_close_for_context_locked(smb2);
  hts_mutex_unlock(&smb2srv.mutex);
  return 0;
}


static int
smb2srv_create(struct smb2_server *srvr, struct smb2_context *smb2,
               struct smb2_create_request *req, struct smb2_create_reply *rep)
{
  smb2srv_handle_t *h;
  struct fa_stat fs;
  char errbuf[512];
  char *root;
  char *url;
  int want_dir;

  SMB2SRV_TRACE(TRACE_DEBUG,
                "create name=%s access=0x%x disposition=0x%x options=0x%x",
                req->name != NULL ? req->name : "",
                req->desired_access, req->create_disposition,
                req->create_options);

  if(req->desired_access & (SMB2_FILE_WRITE_DATA | SMB2_FILE_APPEND_DATA |
                            SMB2_FILE_WRITE_EA | SMB2_FILE_WRITE_ATTRIBUTES |
                            SMB2_DELETE | SMB2_GENERIC_WRITE |
                            SMB2_GENERIC_ALL)) {
    return smb2srv_queue_status(smb2, SMB2_CREATE, SMB2_STATUS_ACCESS_DENIED);
  }

  if(req->create_disposition != SMB2_FILE_OPEN &&
     req->create_disposition != SMB2_FILE_OPEN_IF) {
    return smb2srv_queue_status(smb2, SMB2_CREATE, SMB2_STATUS_ACCESS_DENIED);
  }

  hts_mutex_lock(&smb2srv.mutex);
  root = smb2srv.share_root != NULL ? strdup(smb2srv.share_root) : NULL;
  hts_mutex_unlock(&smb2srv.mutex);
  if(root == NULL || root[0] == '\0') {
    free(root);
    return smb2srv_queue_status(smb2, SMB2_CREATE, SMB2_STATUS_ACCESS_DENIED);
  }

  url = smb2srv_map_path(root, req->name);
  free(root);
  if(url == NULL)
    return smb2srv_queue_status(smb2, SMB2_CREATE,
                                SMB2_STATUS_OBJECT_PATH_SYNTAX_BAD);

  memset(&fs, 0, sizeof(fs));
  if(fa_stat_ex(url, &fs, errbuf, sizeof(errbuf), FA_NON_INTERACTIVE)) {
    SMB2SRV_TRACE(TRACE_ERROR, "stat failed for %s: %s", url, errbuf);
    free(url);
    return smb2srv_queue_status(smb2, SMB2_CREATE,
                                SMB2_STATUS_OBJECT_NAME_NOT_FOUND);
  }

  want_dir = (req->create_options & SMB2_FILE_DIRECTORY_FILE) != 0;
  if(want_dir && fs.fs_type != CONTENT_DIR && fs.fs_type != CONTENT_SHARE) {
    free(url);
    return smb2srv_queue_status(smb2, SMB2_CREATE,
                                SMB2_STATUS_NOT_A_DIRECTORY);
  }
  if((req->create_options & SMB2_FILE_NON_DIRECTORY_FILE) &&
     (fs.fs_type == CONTENT_DIR || fs.fs_type == CONTENT_SHARE)) {
    free(url);
    return smb2srv_queue_status(smb2, SMB2_CREATE,
                                SMB2_STATUS_FILE_IS_A_DIRECTORY);
  }

  h = calloc(1, sizeof(*h));
  if(h == NULL) {
    free(url);
    return smb2srv_queue_status(smb2, SMB2_CREATE, SMB2_STATUS_NO_MEMORY);
  }
  h->vfs_url = url;
  h->smb2 = smb2;
  h->is_dir = fs.fs_type == CONTENT_DIR || fs.fs_type == CONTENT_SHARE;
  h->size = fs.fs_size > 0 ? fs.fs_size : 0;
  h->mtime = fs.fs_mtime;

  if(!h->is_dir) {
    h->fh = fa_open_ex(url, errbuf, sizeof(errbuf), FA_NON_INTERACTIVE, NULL);
    if(h->fh == NULL) {
      SMB2SRV_TRACE(TRACE_ERROR, "open failed for %s: %s", url, errbuf);
      smb2srv_handle_close(h);
      return smb2srv_queue_status(smb2, SMB2_CREATE,
                                  SMB2_STATUS_OBJECT_NAME_NOT_FOUND);
    }
  }

  hts_mutex_lock(&smb2srv.mutex);
  smb2srv_handle_make_id_locked(h);
  LIST_INSERT_HEAD(&smb2srv.handles, h, link);
  hts_mutex_unlock(&smb2srv.mutex);

  memset(rep, 0, sizeof(*rep));
  rep->oplock_level = SMB2_OPLOCK_LEVEL_NONE;
  rep->creation_time = smb2_timeval_to_win(&(struct smb2_timeval){h->mtime, 0});
  rep->last_access_time = rep->creation_time;
  rep->last_write_time = rep->creation_time;
  rep->change_time = rep->creation_time;
  rep->end_of_file = h->size;
  rep->allocation_size = h->size;
  rep->file_attributes = smb2srv_attrs_from_type(fs.fs_type);
  memcpy(rep->file_id, h->file_id, SMB2_FD_SIZE);
  return 0;
}


static int
smb2srv_close(struct smb2_server *srvr, struct smb2_context *smb2,
              struct smb2_close_request *req, struct smb2_close_reply *rep)
{
  smb2srv_handle_t *h;

  hts_mutex_lock(&smb2srv.mutex);
  h = smb2srv_handle_find_locked(smb2, req->file_id);
  if(h != NULL)
    LIST_REMOVE(h, link);
  hts_mutex_unlock(&smb2srv.mutex);

  if(h == NULL)
    return smb2srv_queue_status(smb2, SMB2_CLOSE, SMB2_STATUS_INVALID_HANDLE);

  memset(rep, 0, sizeof(*rep));
  rep->end_of_file = h->size;
  rep->allocation_size = h->size;
  rep->creation_time = smb2_timeval_to_win(&(struct smb2_timeval){h->mtime, 0});
  rep->last_access_time = rep->creation_time;
  rep->last_write_time = rep->creation_time;
  rep->change_time = rep->creation_time;
  rep->file_attributes = h->is_dir ? SMB2_FILE_ATTRIBUTE_DIRECTORY :
    SMB2_FILE_ATTRIBUTE_NORMAL;
  smb2srv_handle_close(h);
  return 0;
}


static int
smb2srv_flush(struct smb2_server *srvr, struct smb2_context *smb2,
              struct smb2_flush_request *req)
{
  return 0;
}


static int
smb2srv_read(struct smb2_server *srvr, struct smb2_context *smb2,
             struct smb2_read_request *req, struct smb2_read_reply *rep)
{
  smb2srv_handle_t *h;
  uint32_t count = req->length;
  uint8_t *buf;
  int64_t pos;
  int r;

  hts_mutex_lock(&smb2srv.mutex);
  h = smb2srv_handle_find_locked(smb2, req->file_id);
  hts_mutex_unlock(&smb2srv.mutex);

  if(h == NULL || h->fh == NULL)
    return smb2srv_queue_status(smb2, SMB2_READ, SMB2_STATUS_INVALID_HANDLE);

  if(count == 0) {
    memset(rep, 0, sizeof(*rep));
    return 0;
  }

  buf = malloc(count);
  if(buf == NULL)
    return smb2srv_queue_status(smb2, SMB2_READ, SMB2_STATUS_NO_MEMORY);

  pos = fa_seek(h->fh, req->offset, SEEK_SET);
  if(pos < 0) {
    free(buf);
    return smb2srv_queue_status(smb2, SMB2_READ, SMB2_STATUS_INVALID_PARAMETER);
  }

  r = fa_read(h->fh, buf, count);
  if(r < 0) {
    free(buf);
    return smb2srv_queue_status(smb2, SMB2_READ, SMB2_STATUS_END_OF_FILE);
  }

  memset(rep, 0, sizeof(*rep));
  rep->data_length = r;
  rep->data_remaining = 0;
  rep->data = buf;
  return 0;
}


static int
smb2srv_write(struct smb2_server *srvr, struct smb2_context *smb2,
              struct smb2_write_request *req, struct smb2_write_reply *rep)
{
  return smb2srv_queue_status(smb2, SMB2_WRITE, SMB2_STATUS_ACCESS_DENIED);
}


static int
smb2srv_query_directory(struct smb2_server *srvr, struct smb2_context *smb2,
                        struct smb2_query_directory_request *req,
                        struct smb2_query_directory_reply *rep)
{
  smb2srv_handle_t *h;
  fa_dir_entry_t *fde, *selected = NULL;
  struct smb2_fileidbothdirectoryinformation *out;
  size_t seen = 0, stride;
  char errbuf[512];

  hts_mutex_lock(&smb2srv.mutex);
  h = smb2srv_handle_find_locked(smb2, req->file_id);
  hts_mutex_unlock(&smb2srv.mutex);

  if(h == NULL || !h->is_dir)
    return smb2srv_queue_status(smb2, SMB2_QUERY_DIRECTORY,
                                SMB2_STATUS_INVALID_HANDLE);

  if(req->file_information_class != SMB2_FILE_ID_FULL_DIRECTORY_INFORMATION &&
     req->file_information_class != SMB2_FILE_ID_BOTH_DIRECTORY_INFORMATION) {
    SMB2SRV_TRACE(TRACE_DEBUG, "unsupported query-directory class=%u",
                  req->file_information_class);
    return smb2srv_queue_status(smb2, SMB2_QUERY_DIRECTORY,
                                SMB2_STATUS_INVALID_INFO_CLASS);
  }

  if(req->flags & SMB2_RESTART_SCANS)
    h->dir_sent = 0;

  if(req->name != NULL && req->name[0] != '\0') {
    if(h->dir_pattern == NULL || strcmp(h->dir_pattern, req->name)) {
      char *pattern = strdup(req->name);
      if(pattern == NULL)
        return smb2srv_queue_status(smb2, SMB2_QUERY_DIRECTORY,
                                    SMB2_STATUS_NO_MEMORY);
      free(h->dir_pattern);
      h->dir_pattern = pattern;
      h->dir_sent = 0;
    }
  }

  SMB2SRV_TRACE(TRACE_DEBUG,
                "query-directory class=%u flags=0x%x sent=%d pattern=%s path=%s",
                req->file_information_class, req->flags, h->dir_sent,
                h->dir_pattern != NULL ? h->dir_pattern : "*", h->vfs_url);

  if(h->dir == NULL) {
    h->dir = fa_scandir(h->vfs_url, errbuf, sizeof(errbuf));
    if(h->dir == NULL) {
      SMB2SRV_TRACE(TRACE_ERROR, "scan failed for %s: %s", h->vfs_url, errbuf);
      return smb2srv_queue_status(smb2, SMB2_QUERY_DIRECTORY,
                                  SMB2_STATUS_OBJECT_NAME_NOT_FOUND);
    }
  }

  RB_FOREACH(fde, &h->dir->fd_entries, fde_link) {
    if(!smb2srv_pattern_match(h->dir_pattern, rstr_get(fde->fde_filename)))
      continue;
    if(seen++ < (size_t)h->dir_sent)
      continue;
    selected = fde;
    break;
  }
  if(selected == NULL) {
    return smb2srv_queue_status(smb2, SMB2_QUERY_DIRECTORY,
                                SMB2_STATUS_NO_MORE_FILES);
  }

  stride = (sizeof(*out) + 7) & ~7;
  free(h->dir_info);
  h->dir_info = calloc(1, stride);
  if(h->dir_info == NULL)
    return smb2srv_queue_status(smb2, SMB2_QUERY_DIRECTORY,
                                SMB2_STATUS_NO_MEMORY);
  out = (void *)h->dir_info;

  {
    struct smb2_fileidbothdirectoryinformation *di = out;
    struct fa_stat *st = &selected->fde_stat;
    const char *name = rstr_get(selected->fde_filename);
    uint32_t name_len = smb2srv_name_utf16_len(name);

    if(!selected->fde_statdone && fa_dir_entry_stat(selected))
      memset(st, 0, sizeof(*st));

    di->file_index = h->dir_sent;
    smb2srv_fill_time(&di->creation_time, st->fs_mtime);
    smb2srv_fill_time(&di->last_access_time, st->fs_mtime);
    smb2srv_fill_time(&di->last_write_time, st->fs_mtime);
    smb2srv_fill_time(&di->change_time, st->fs_mtime);
    di->end_of_file = st->fs_size > 0 ? st->fs_size : 0;
    di->allocation_size = di->end_of_file;
    di->file_attributes = smb2srv_attrs_from_type(st->fs_type);
    di->file_name_length = name_len;
    di->short_name_length = name_len < sizeof(di->short_name) ?
      name_len : sizeof(di->short_name);
    di->name = name;
    SMB2SRV_TRACE(TRACE_DEBUG,
                  "dir-entry name=%s type=%d size=%"PRId64" name_len=%u",
                  name, st->fs_type, di->end_of_file, name_len);
  }

  h->dir_sent++;
  rep->output_buffer = (uint8_t *)out;
  rep->output_buffer_length = stride;
  SMB2SRV_TRACE(TRACE_DEBUG,
                "query-directory reply entries=1 stride=%zu",
                stride);
  return 0;
}


static int
smb2srv_query_info(struct smb2_server *srvr, struct smb2_context *smb2,
                   struct smb2_query_info_request *req,
                   struct smb2_query_info_reply *rep)
{
  smb2srv_handle_t *h;

  hts_mutex_lock(&smb2srv.mutex);
  h = smb2srv_handle_find_locked(smb2, req->file_id);
  hts_mutex_unlock(&smb2srv.mutex);

  if(h == NULL)
    return smb2srv_queue_status(smb2, SMB2_QUERY_INFO,
                                SMB2_STATUS_INVALID_HANDLE);

  SMB2SRV_TRACE(TRACE_DEBUG, "query-info type=%u class=%u for %s",
                req->info_type, req->file_info_class, h->vfs_url);

  if(req->info_type == SMB2_0_INFO_FILE &&
     req->file_info_class == SMB2_FILE_ALL_INFORMATION) {
    struct smb2_file_all_info *info = &h->all_info;
    memset(info, 0, sizeof(*info));
    smb2srv_fill_time(&info->basic.creation_time, h->mtime);
    smb2srv_fill_time(&info->basic.last_access_time, h->mtime);
    smb2srv_fill_time(&info->basic.last_write_time, h->mtime);
    smb2srv_fill_time(&info->basic.change_time, h->mtime);
    info->basic.file_attributes = h->is_dir ? SMB2_FILE_ATTRIBUTE_DIRECTORY :
      SMB2_FILE_ATTRIBUTE_NORMAL;
    info->standard.allocation_size = h->size;
    info->standard.end_of_file = h->size;
    info->standard.number_of_links = 1;
    info->standard.directory = h->is_dir ? 1 : 0;
    info->access_flags = SMB2_FILE_READ_DATA | SMB2_FILE_READ_ATTRIBUTES;
    if(h->is_dir)
      info->access_flags |= SMB2_FILE_LIST_DIRECTORY;
    rep->output_buffer = (uint8_t *)info;
    rep->output_buffer_length = sizeof(*info);
    return 0;
  }

  if(req->info_type == SMB2_0_INFO_FILE &&
     req->file_info_class == SMB2_FILE_BASIC_INFORMATION) {
    struct smb2_file_basic_info *info = &h->basic_info;
    memset(info, 0, sizeof(*info));
    smb2srv_fill_time(&info->creation_time, h->mtime);
    smb2srv_fill_time(&info->last_access_time, h->mtime);
    smb2srv_fill_time(&info->last_write_time, h->mtime);
    smb2srv_fill_time(&info->change_time, h->mtime);
    info->file_attributes = h->is_dir ? SMB2_FILE_ATTRIBUTE_DIRECTORY :
      SMB2_FILE_ATTRIBUTE_NORMAL;
    rep->output_buffer = (uint8_t *)info;
    rep->output_buffer_length = sizeof(*info);
    return 0;
  }

  if(req->info_type == SMB2_0_INFO_FILE &&
     req->file_info_class == SMB2_FILE_STANDARD_INFORMATION) {
    struct smb2_file_standard_info *info = &h->standard_info;
    memset(info, 0, sizeof(*info));
    info->allocation_size = h->size;
    info->end_of_file = h->size;
    info->number_of_links = 1;
    info->directory = h->is_dir ? 1 : 0;
    rep->output_buffer = (uint8_t *)info;
    rep->output_buffer_length = sizeof(*info);
    return 0;
  }

  if(req->info_type == SMB2_0_INFO_FILE &&
     req->file_info_class == SMB2_FILE_NETWORK_OPEN_INFORMATION) {
    struct smb2_file_network_open_info *info = &h->network_open_info;
    memset(info, 0, sizeof(*info));
    smb2srv_fill_time(&info->creation_time, h->mtime);
    smb2srv_fill_time(&info->last_access_time, h->mtime);
    smb2srv_fill_time(&info->last_write_time, h->mtime);
    smb2srv_fill_time(&info->change_time, h->mtime);
    info->allocation_size = h->size;
    info->end_of_file = h->size;
    info->file_attributes = h->is_dir ? SMB2_FILE_ATTRIBUTE_DIRECTORY :
      SMB2_FILE_ATTRIBUTE_NORMAL;
    rep->output_buffer = (uint8_t *)info;
    rep->output_buffer_length = sizeof(*info);
    return 0;
  }

  if(req->info_type == SMB2_0_INFO_FILESYSTEM &&
     req->file_info_class == SMB2_FILE_FS_SIZE_INFORMATION) {
    struct smb2_file_fs_size_info *info = &h->fs_size_info;
    fa_fsinfo_t ffi;

    memset(info, 0, sizeof(*info));
    if(fa_fsinfo(h->vfs_url, &ffi)) {
      ffi.ffi_size = 0;
      ffi.ffi_avail = 0;
    }
    info->total_allocation_units = ffi.ffi_size / 512;
    info->available_allocation_units = ffi.ffi_avail / 512;
    info->sectors_per_allocation_unit = 1;
    info->bytes_per_sector = 512;
    rep->output_buffer = (uint8_t *)info;
    rep->output_buffer_length = sizeof(*info);
    return 0;
  }

  TRACE(TRACE_ERROR, "SMB2-SERVER",
        "Unsupported query-info type=%u class=%u for %s",
        req->info_type, req->file_info_class, h->vfs_url);
  return smb2srv_queue_status(smb2, SMB2_QUERY_INFO,
                              SMB2_STATUS_INVALID_INFO_CLASS);
}


static int
smb2srv_set_info(struct smb2_server *srvr, struct smb2_context *smb2,
                 struct smb2_set_info_request *req)
{
  return smb2srv_queue_status(smb2, SMB2_SET_INFO, SMB2_STATUS_ACCESS_DENIED);
}


static int
smb2srv_ioctl(struct smb2_server *srvr, struct smb2_context *smb2,
              struct smb2_ioctl_request *req, struct smb2_ioctl_reply *rep)
{
  struct smb2_ioctl_validate_negotiate_info *out = &smb2srv.validate_info;
  struct smb2_ioctl_validate_negotiate_info *in = req->input;

  if(req->ctl_code != SMB2_FSCTL_VALIDATE_NEGOTIATE_INFO)
    return smb2srv_queue_status(smb2, SMB2_IOCTL, SMB2_STATUS_NOT_SUPPORTED);

  memset(out, 0, sizeof(*out));
  out->capabilities = srvr->capabilities;
  memcpy(out->guid, srvr->guid, sizeof(out->guid));
  out->security_mode = srvr->security_mode;
  out->dialect = req->input_count >= sizeof(*in) && in != NULL ?
    in->dialect : SMB2_VERSION_0311;

  memset(rep, 0, sizeof(*rep));
  rep->ctl_code = req->ctl_code;
  memcpy(rep->file_id, req->file_id, SMB2_FD_SIZE);
  rep->output = out;
  rep->output_count = sizeof(*out);
  return 0;
}


static int
smb2srv_cancel(struct smb2_server *srvr, struct smb2_context *smb2)
{
  return 0;
}


static int
smb2srv_echo(struct smb2_server *srvr, struct smb2_context *smb2)
{
  return 0;
}


static struct smb2_server_request_handlers smb2srv_handlers = {
  smb2srv_destruction_event,
  smb2srv_authorize,
  smb2srv_session_established,
  smb2srv_logoff,
  smb2srv_tree_connect,
  smb2srv_tree_disconnect,
  smb2srv_create,
  smb2srv_close,
  smb2srv_flush,
  smb2srv_read,
  smb2srv_write,
  NULL,
  NULL,
  NULL,
  smb2srv_ioctl,
  smb2srv_cancel,
  smb2srv_echo,
  smb2srv_query_directory,
  NULL,
  smb2srv_query_info,
  smb2srv_set_info
};


static void
smb2srv_client_connected(struct smb2_context *smb2, void *cb_data)
{
  SMB2SRV_TRACE(TRACE_DEBUG, "client connected");
  smb2_set_version(smb2, SMB2_VERSION_ANY);
}


static void *
smb2srv_thread(void *aux)
{
  int err;
  int restart = 0;

  hts_mutex_lock(&smb2srv.mutex);
  smb2srv.thread_running = 1;
  memset(&smb2srv.server, 0, sizeof(smb2srv.server));
  smb2srv.server.handlers = &smb2srv_handlers;
  smb2srv.server.signing_enabled = 1;
  smb2srv.server.allow_anonymous = 0;
  smb2srv.server.port = smb2srv.port;
  snprintf(smb2srv.server.hostname, sizeof(smb2srv.server.hostname),
           "%s", gconf.system_name[0] != '\0' ? gconf.system_name : "Movian");
  snprintf(smb2srv.server.domain, sizeof(smb2srv.server.domain), "WORKGROUP");
  hts_mutex_unlock(&smb2srv.mutex);

  TRACE(TRACE_INFO, "SMB2-SERVER", "Listening on port %d",
        smb2srv.server.port);
  err = smb2_serve_port(&smb2srv.server, SMB2SRV_MAX_CONNECTIONS,
                        smb2srv_client_connected, NULL);
  TRACE(err ? TRACE_ERROR : TRACE_INFO, "SMB2-SERVER",
        "Server loop exited with %d", err);

  hts_mutex_lock(&smb2srv.mutex);
  smb2srv.thread_running = 0;
  smb2srv.server.fd = -1;
  if(smb2srv.restart_requested) {
    smb2srv.restart_requested = 0;
    restart = smb2srv.enabled && smb2srv_config_ready_locked();
  }
  hts_mutex_unlock(&smb2srv.mutex);

  if(restart)
    hts_thread_create_detached("SMB2-server", smb2srv_thread, NULL,
                               THREAD_PRIO_BGTASK);
  return NULL;
}


static int
smb2srv_config_ready_locked(void)
{
  return smb2srv.port > 0 &&
    smb2srv.username != NULL && smb2srv.username[0] != '\0' &&
    smb2srv.password != NULL && smb2srv.password[0] != '\0' &&
    smb2srv.share_name != NULL && smb2srv.share_name[0] != '\0' &&
    smb2srv.share_root != NULL && smb2srv.share_root[0] != '\0';
}


static int
smb2srv_port_available(int port)
{
  int fd = -1;
  int err = smb2_bind_and_listen(port, SMB2SRV_MAX_CONNECTIONS, &fd);

  if(fd >= 0)
    close(fd);

  return err == 0;
}


static void
smb2srv_trace_port_unavailable(int port)
{
  if(port < 1024 && geteuid() != 0) {
    TRACE(TRACE_ERROR, "SMB2-SERVER",
          "Cannot listen on privileged port %d as uid %d",
          port, (int)geteuid());
  } else {
    TRACE(TRACE_ERROR, "SMB2-SERVER",
          "Cannot listen on port %d; it may be unavailable or already in use",
          port);
  }
}


static void
smb2srv_sync_port_setting(int port)
{
  char portbuf[16];

  hts_mutex_lock(&smb2srv.mutex);
  if(smb2srv.port_setting == NULL || smb2srv.reverting_port_setting) {
    hts_mutex_unlock(&smb2srv.mutex);
    return;
  }
  smb2srv.reverting_port_setting = 1;
  hts_mutex_unlock(&smb2srv.mutex);

  snprintf(portbuf, sizeof(portbuf), "%d", port);
  setting_set(smb2srv.port_setting, SETTING_STRING, portbuf);

  hts_mutex_lock(&smb2srv.mutex);
  smb2srv.reverting_port_setting = 0;
  hts_mutex_unlock(&smb2srv.mutex);
}


static void
smb2srv_stop_locked(int restart)
{
  if(smb2srv.server.fd >= 0) {
    int fd = smb2srv.server.fd;

    if(smb2srv.thread_running) {
      int wake_fd = open("/dev/null", O_RDONLY);

      if(restart)
        smb2srv.restart_requested = 1;

      /*
       * libsmb2 owns server.fd once smb2_serve_port() is running and closes
       * that descriptor on exit. Close the real listener here so the TCP port
       * stops accepting immediately, then hand libsmb2 a harmless readable fd
       * so its select()/accept() loop wakes and exits without double-closing a
       * descriptor that the process may have already reused.
       */
      if(wake_fd >= 0) {
        smb2srv.server.fd = wake_fd;
        shutdown(fd, SHUT_RDWR);
        close(fd);
      } else {
        shutdown(fd, SHUT_RDWR);
      }
    } else {
      close(fd);
      smb2srv.server.fd = -1;
    }
  }
}


static void
smb2srv_enable_disable(void)
{
  int start = 0;

  hts_mutex_lock(&smb2srv.mutex);
  if(!smb2srv.enabled) {
    smb2srv.restart_requested = 0;
    smb2srv_stop_locked(0);
  } else if(!smb2srv.thread_running && smb2srv_config_ready_locked()) {
    if(smb2srv_port_available(smb2srv.port)) {
      start = 1;
    } else if(smb2srv.port != SMB2SRV_DEFAULT_PORT &&
              smb2srv_port_available(SMB2SRV_DEFAULT_PORT)) {
      smb2srv_trace_port_unavailable(smb2srv.port);
      TRACE(TRACE_INFO, "SMB2-SERVER", "Falling back to port %d",
            SMB2SRV_DEFAULT_PORT);
      smb2srv.port = SMB2SRV_DEFAULT_PORT;
      hts_mutex_unlock(&smb2srv.mutex);
      smb2srv_sync_port_setting(SMB2SRV_DEFAULT_PORT);
      hts_mutex_lock(&smb2srv.mutex);
      start = 1;
    } else {
      smb2srv_trace_port_unavailable(smb2srv.port);
    }
  } else if(smb2srv.enabled && !smb2srv_config_ready_locked()) {
    smb2srv_stop_locked(0);
    SMB2SRV_TRACE(TRACE_DEBUG,
                  "not running: username, password, share and path are required");
  }
  hts_mutex_unlock(&smb2srv.mutex);

  if(start)
    hts_thread_create_detached("SMB2-server", smb2srv_thread, NULL,
                               THREAD_PRIO_MODEL);
}


static void
smb2srv_set_enable(void *opaque, int v)
{
  hts_mutex_lock(&smb2srv.mutex);
  smb2srv.enabled = v;
  hts_mutex_unlock(&smb2srv.mutex);
  smb2srv_enable_disable();
}


static void
smb2srv_set_port(void *opaque, const char *str)
{
  int port = atoi(str);
  int revert_port = 0;

  hts_mutex_lock(&smb2srv.mutex);
  if(port < 1 || port > 65535)
    port = SMB2SRV_DEFAULT_PORT;

  if(smb2srv.reverting_port_setting) {
    smb2srv.port = port;
    hts_mutex_unlock(&smb2srv.mutex);
    return;
  }

  if(smb2srv.port != port && !smb2srv_port_available(port)) {
    smb2srv_trace_port_unavailable(port);
    revert_port = smb2srv.port;
    hts_mutex_unlock(&smb2srv.mutex);
    smb2srv_sync_port_setting(revert_port);
    return;
  }
  if(smb2srv.port != port && smb2srv.thread_running)
    smb2srv_stop_locked(1);
  smb2srv.port = port;
  hts_mutex_unlock(&smb2srv.mutex);
  smb2srv_enable_disable();
}


static void
smb2srv_set_username(void *opaque, const char *str)
{
  hts_mutex_lock(&smb2srv.mutex);
  mystrset(&smb2srv.username, str);
  hts_mutex_unlock(&smb2srv.mutex);
  smb2srv_enable_disable();
}


static void
smb2srv_set_password(void *opaque, const char *str)
{
  hts_mutex_lock(&smb2srv.mutex);
  mystrset(&smb2srv.password, str);
  hts_mutex_unlock(&smb2srv.mutex);
  smb2srv_enable_disable();
}


static void
smb2srv_set_share_name(void *opaque, const char *str)
{
  hts_mutex_lock(&smb2srv.mutex);
  mystrset(&smb2srv.share_name, str);
  hts_mutex_unlock(&smb2srv.mutex);
  smb2srv_enable_disable();
}


static void
smb2srv_set_share_root(void *opaque, const char *str)
{
  hts_mutex_lock(&smb2srv.mutex);
  mystrset(&smb2srv.share_root, str);
  hts_mutex_unlock(&smb2srv.mutex);
  smb2srv_enable_disable();
}


void
smb2_server_init(void)
{
  hts_mutex_init(&smb2srv.mutex);
  LIST_INIT(&smb2srv.handles);
  smb2srv.server.fd = -1;
  smb2srv.port = SMB2SRV_DEFAULT_PORT;
  TRACE(TRACE_INFO, "SMB2-SERVER", "Initializing SMB2 file server settings");

  settings_create_separator(gconf.settings_network, _p("SMB2 file server"));

  setting_create(SETTING_BOOL, gconf.settings_network, SETTINGS_INITIAL_UPDATE,
                 SETTING_TITLE(_p("Enable SMB2 file server")),
                 SETTING_VALUE(0),
                 SETTING_CALLBACK(smb2srv_set_enable, NULL),
                 SETTING_STORE("smb2server", "enable"),
                 SETTING_COURIER(asyncio_courier),
                 NULL);

  smb2srv.port_setting =
    setting_create(SETTING_STRING, gconf.settings_network,
                   SETTINGS_INITIAL_UPDATE,
                   SETTING_TITLE(_p("Server TCP port")),
                   SETTING_VALUE("1445"),
                   SETTING_CALLBACK(smb2srv_set_port, NULL),
                   SETTING_STORE("smb2server", "port"),
                   SETTING_COURIER(asyncio_courier),
                   NULL);
  smb2srv_sync_port_setting(smb2srv.port);

  setting_create(SETTING_STRING, gconf.settings_network,
                 SETTINGS_INITIAL_UPDATE,
                 SETTING_TITLE(_p("Username")),
                 SETTING_VALUE("movian"),
                 SETTING_CALLBACK(smb2srv_set_username, NULL),
                 SETTING_STORE("smb2server", "username"),
                 SETTING_COURIER(asyncio_courier),
                 NULL);

  setting_create(SETTING_STRING, gconf.settings_network,
                 SETTINGS_INITIAL_UPDATE | SETTINGS_PASSWORD,
                 SETTING_TITLE(_p("Password")),
                 SETTING_VALUE(""),
                 SETTING_CALLBACK(smb2srv_set_password, NULL),
                 SETTING_STORE("smb2server", "password"),
                 SETTING_COURIER(asyncio_courier),
                 NULL);

  setting_create(SETTING_STRING, gconf.settings_network,
                 SETTINGS_INITIAL_UPDATE,
                 SETTING_TITLE(_p("Share name")),
                 SETTING_VALUE("Media"),
                 SETTING_CALLBACK(smb2srv_set_share_name, NULL),
                 SETTING_STORE("smb2server", "share"),
                 SETTING_COURIER(asyncio_courier),
                 NULL);

  setting_create(SETTING_STRING, gconf.settings_network,
                 SETTINGS_INITIAL_UPDATE,
                 SETTING_TITLE(_p("Share path")),
                 SETTING_VALUE("vfs:///"),
                 SETTING_CALLBACK(smb2srv_set_share_root, NULL),
                 SETTING_STORE("smb2server", "path"),
                 SETTING_COURIER(asyncio_courier),
                 NULL);
}


INITME(INIT_GROUP_ASYNCIO, smb2_server_init, NULL, 0);
