/* -*-  mode:c; tab-width:8; c-basic-offset:8; indent-tabs-mode:nil;  -*- */
/*
 * Movian SMB2 server — VFS path mapping + CREATE/READ/WRITE/CLOSE/
 * QUERY_DIRECTORY/QUERY_INFO/SET_INFO handlers.
 * Split out of smb_server.c; see smb_server_private.h for shared state.
 */

#include <assert.h>
#include <stdio.h>
#include <sys/types.h>
#include <sys/statvfs.h>
#include <unistd.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <stddef.h>

#include "main.h"
#include "asyncio.h"
#include "arch/threads.h"
#include "settings.h"
#include "fileaccess/fileaccess.h"
#include "fileaccess/fa_proto.h"
#include "htsmsg/htsmsg_store.h"
#include "usage.h"

#include "smb_server.h"
#include "misc/str.h"

#include <smb2/smb2.h>
#include <smb2/libsmb2.h>
#include <smb2/libsmb2-dcerpc.h>
#include <smb2/libsmb2-raw.h>

#include "smb_server_private.h"

/* ------------------------------------------------------------------ */
/* VFS helpers                                                          */
/* ------------------------------------------------------------------ */

static size_t
utf8_to_utf16_bytes(const char *utf8)
{
    if (utf8 == NULL)
        return 0;
    size_t len = 0;
    while (*utf8) {
        unsigned char c = (unsigned char)*utf8;
        if (c < 0x80) {
            len++;
            utf8++;
        } else if ((c & 0xe0) == 0xc0) {
            len++;
            utf8 += 2;
        } else if ((c & 0xf0) == 0xe0) {
            len++;
            utf8 += 3;
        } else if ((c & 0xf8) == 0xf0) {
            len += 2; /* Surrogate pair: 2 UTF-16 code units */
            utf8 += 4;
        } else {
            utf8++; /* Invalid UTF-8 byte, skip */
        }
    }
    return len * 2;
}

static uint64_t
smb_time_to_win(time_t t)
{
    struct smb2_timeval tv = { .tv_sec = (uint32_t)t, .tv_usec = 0 };
    return smb2_timeval_to_win(&tv);
}

static void
smb_fill_timevals(struct smb2_timeval *tv, time_t mtime)
{
    for(int i = 0; i < 4; i++) {
        tv[i].tv_sec  = (uint32_t)mtime;
        tv[i].tv_usec = 0;
    }
}

static int
vfs_stat(const char *url, struct fa_stat *fs, char *errbuf, size_t errlen)
{
    return fa_stat_ex(url, fs, errbuf, errlen, FA_NON_INTERACTIVE);
}

static fa_handle_t *
vfs_open(const char *url, char *errbuf, size_t errlen, int flags)
{
    return fa_open_ex(url, errbuf, errlen, flags, NULL);
}

static int
vfs_makedir(const char *url)
{
    return fa_makedir(url);
}

int
vfs_unlink(const char *url, char *errbuf, size_t errlen)
{
    return fa_unlink(url, errbuf, errlen);
}

int
vfs_rmdir(const char *url, char *errbuf, size_t errlen)
{
    return fa_rmdir(url, errbuf, errlen);
}

static int
smb_get_statvfs(smb_connection_t *sc, smb_file_entry_t *fe, struct statvfs *sv)
{
    const char *p = fe ? fe->path : sc->sc_share_root;
    if(p && strncmp(p, "file://", 7) == 0)
        p += 7;
    return (p && statvfs(p, sv) == 0) ? 0 : -1;
}

static int
vfs_rename(const char *old, const char *newp, char *errbuf, size_t errlen)
{
    return fa_rename(old, newp, errbuf, errlen);
}

static fa_dir_t *
vfs_scandir(const char *url, char *errbuf, size_t errlen)
{
    return fa_scandir(url, errbuf, errlen);
}

/* ------------------------------------------------------------------ */
/* Path resolution                                                      */
/* ------------------------------------------------------------------ */

/*
 * Build an absolute vfs path from share-relative UTF-8 name.
 * req->name from libsmb2 server API is already UTF-8; backslashes are
 * converted to forward slashes by us.
 */
static int
smb_build_path(smb_connection_t *sc, const char *name, char **pathp)
{
    const char *root = sc->sc_share_root;

    *pathp = NULL;

    if(name == NULL || *name == '\0') {
        *pathp = strdup(root);
        return *pathp != NULL ? 0 : SMB2_STATUS_INSUFFICIENT_RESOURCES;
    }

    const char *src = name;
    while(*src == '/' || *src == '\\')
        src++;

    if(*src == '\0') {
        *pathp = strdup(root);
        return *pathp != NULL ? 0 : SMB2_STATUS_INSUFFICIENT_RESOURCES;
    }

    size_t rlen = strlen(root);
    size_t nlen = strlen(src);
    int need_slash = (rlen > 0 && root[rlen - 1] != '/');
    size_t plen = rlen + need_slash + nlen + 1;

    char *path = malloc(plen);
    if(path == NULL)
        return SMB2_STATUS_INSUFFICIENT_RESOURCES;

    memcpy(path, root, rlen);
    char *dest = path + rlen;
    if(need_slash) {
        *dest++ = '/';
    }

    char *rel_start = dest;
    int last_was_slash = 0;
    while(*src) {
        char c = (*src == '\\') ? '/' : *src;
        if(c == '/') {
            if(!last_was_slash) {
                *dest++ = '/';
                last_was_slash = 1;
            }
        } else {
            *dest++ = c;
            last_was_slash = 0;
        }
        src++;
    }
    /* Strip trailing slash if any */
    if(dest > rel_start && *(dest - 1) == '/') {
        dest--;
    }
    *dest = '\0';

    /* Reject path traversal */
    const char *p = rel_start;
    while(*p) {
        if(p[0] == '.' && p[1] == '.' && (p[2] == '/' || p[2] == '\0')) {
            free(path);
            return SMB2_STATUS_OBJECT_PATH_SYNTAX_BAD;
        }
        const char *next = strchr(p, '/');
        p = next ? next + 1 : p + strlen(p);
    }

    *pathp = path;
    return 0;
}

/* ------------------------------------------------------------------ */
/* NT status helper                                                     */
/* ------------------------------------------------------------------ */

static int
smb_errno_to_ntstatus(int err)
{
    switch(err > 0 ? err : -err) {
    case ENOENT:    return SMB2_STATUS_OBJECT_NAME_NOT_FOUND;
    case EACCES:    return SMB2_STATUS_ACCESS_DENIED;
    case EEXIST:    return SMB2_STATUS_OBJECT_NAME_COLLISION;
    case ENOTDIR:   return SMB2_STATUS_NOT_A_DIRECTORY;
    case EISDIR:    return SMB2_STATUS_FILE_IS_A_DIRECTORY;
    case ENOSPC:    return SMB2_STATUS_INSUFFICIENT_RESOURCES;
    case ENOMEM:    return SMB2_STATUS_INSUFFICIENT_RESOURCES;
    case EROFS:     return SMB2_STATUS_MEDIA_WRITE_PROTECTED;
    case ENOTEMPTY: return SMB2_STATUS_DIRECTORY_NOT_EMPTY;
    case EINVAL:    return SMB2_STATUS_INVALID_PARAMETER;
    case ENOTSUP:
#if ENOTSUP != EOPNOTSUPP
    case EOPNOTSUPP:
#endif
                    return SMB2_STATUS_NOT_SUPPORTED;
    default:        return SMB2_STATUS_INTERNAL_ERROR;
    }
}


/* ------------------------------------------------------------------ */
/* Handler: CREATE                                                      */
/* ------------------------------------------------------------------ */

int
smb_create(struct smb2_server *srvr, struct smb2_context *smb2,
           struct smb2_create_request *req,
           struct smb2_create_reply *rep)
{
    if(req == NULL || rep == NULL)
        return SMB2_STATUS_INVALID_PARAMETER;
    smb_connection_t *sc = smb2_get_opaque(smb2);
    if(sc == NULL)
        return SMB2_STATUS_INTERNAL_ERROR;
    int auth_status = smb_reject_stale_session(srvr, smb2);
    if(auth_status)
        return auth_status;

    /*
     * req->name is UTF-8, already decoded by libsmb2.
     * An empty/NULL name means the share root itself.
     */
    smb_tree_type_t tree_type = smb_tree_lookup(sc, req->tree_id);
    if(tree_type == SMB_TREE_UNKNOWN) {
        SMBTRACE("Create: rejecting unknown tree_id=0x%08x name='%s'",
                 req->tree_id, req->name ? req->name : "");
        return SMB2_STATUS_NETWORK_NAME_DELETED;
    }

    if(tree_type == SMB_TREE_IPC && smb_is_srvsvc_pipe(req->name)) {
        char *pipe = strdup("srvsvc");
        if(pipe == NULL)
            return SMB2_STATUS_INSUFFICIENT_RESOURCES;

        smb_file_entry_t *fe = smb_alloc_file(sc, pipe);
        if(fe == NULL) {
            free(pipe);
            return SMB2_STATUS_INSUFFICIENT_RESOURCES;
        }

        fe->is_pipe = 1;
        fe->tree_id = req->tree_id;
        fe->mtime = time(NULL);
        memcpy(sc->sc_related_file_id, fe->file_id, SMB2_FD_SIZE);
        sc->sc_related_file_valid = 1;
        memcpy(rep->file_id, fe->file_id, SMB2_FD_SIZE);
        rep->file_attributes = SMB2_FILE_ATTRIBUTE_NORMAL;
        rep->create_action = SMB2_FILE_OPENED;
        rep->end_of_file = 0;
        rep->allocation_size = 0;
        uint64_t win_time = smb_time_to_win(fe->mtime);
        rep->creation_time = rep->last_access_time =
            rep->last_write_time = rep->change_time = win_time;
        SMBTRACE("Create OK: pipe '%s'", pipe);
        return 0;
    }
    if(tree_type == SMB_TREE_IPC) {
        SMBTRACE("Create: rejecting non-pipe IPC$ open '%s'",
                 req->name ? req->name : "");
        return SMB2_STATUS_OBJECT_NAME_NOT_FOUND;
    }

    char *path = NULL;
    int path_status = smb_build_path(sc, req->name, &path);
    if(path_status != 0)
        return path_status;

    SMBTRACE("Create: '%s' disp=%s opts=0x%08x acc=0x%08x",
             path,
             req->create_disposition == SMB2_FILE_OPEN         ? "OPEN" :
             req->create_disposition == SMB2_FILE_CREATE       ? "CREATE" :
             req->create_disposition == SMB2_FILE_OPEN_IF      ? "OPEN_IF" :
             req->create_disposition == SMB2_FILE_OVERWRITE    ? "OVERWRITE" :
             req->create_disposition == SMB2_FILE_OVERWRITE_IF ? "OVERWRITE_IF" :
             req->create_disposition == SMB2_FILE_SUPERSEDE    ? "SUPERSEDE" : "?",
             req->create_options, req->desired_access);

    smb_file_entry_t *fe = smb_alloc_file(sc, path);
    if(fe == NULL) {
        free(path);
        return SMB2_STATUS_INSUFFICIENT_RESOURCES;
    }
    fe->tree_id = req->tree_id;

    struct fa_stat fs = {};
    char errbuf[256];
    int exists = !vfs_stat(path, &fs, errbuf, sizeof(errbuf));
    int existed = exists;
    int is_dir = exists && content_dirish(fs.fs_type);

    /* --- Honour create_options directory/non-directory constraints --- */

    if((req->create_options & SMB2_FILE_DIRECTORY_FILE) && exists && !is_dir) {
        SMBTRACE("Create: '%s' expected DIR but found FILE", path);
        smb_free_file(sc, fe);
        return SMB2_STATUS_NOT_A_DIRECTORY;
    }
    if((req->create_options & SMB2_FILE_NON_DIRECTORY_FILE) && is_dir) {
        SMBTRACE("Create: '%s' expected FILE but found DIR", path);
        smb_free_file(sc, fe);
        return SMB2_STATUS_FILE_IS_A_DIRECTORY;
    }

    /* --- Handle create disposition --- */

    switch(req->create_disposition) {
    case SMB2_FILE_CREATE:
        if(exists) {
            SMBTRACE("Create: '%s' collision (FILE_CREATE but already exists)", path);
            smb_free_file(sc, fe);
            return SMB2_STATUS_OBJECT_NAME_COLLISION;
        }
        if(req->create_options & SMB2_FILE_DIRECTORY_FILE) {
            if(vfs_makedir(path)) {
                smb_free_file(sc, fe);
                return SMB2_STATUS_ACCESS_DENIED;
            }
            is_dir = 1; exists = 1;
            if(vfs_stat(path, &fs, errbuf, sizeof(errbuf))) {
                fs.fs_mtime = time(NULL);
            }
        }
        break;

    case SMB2_FILE_OPEN:
        if(!exists) {
            SMBTRACE("Create: '%s' not found (FILE_OPEN)", path);
            smb_free_file(sc, fe);
            return SMB2_STATUS_OBJECT_NAME_NOT_FOUND;
        }
        break;

    case SMB2_FILE_OPEN_IF:
        if(!exists) {
            if(req->create_options & SMB2_FILE_DIRECTORY_FILE) {
                if(vfs_makedir(path)) {
                    smb_free_file(sc, fe);
                    return SMB2_STATUS_ACCESS_DENIED;
                }
                is_dir = 1; exists = 1;
                if(vfs_stat(path, &fs, errbuf, sizeof(errbuf))) {
                    fs.fs_mtime = time(NULL);
                }
            }
        }
        break;

    case SMB2_FILE_OVERWRITE:
        if(!exists) {
            SMBTRACE("Create: '%s' not found (FILE_OVERWRITE)", path);
            smb_free_file(sc, fe);
            return SMB2_STATUS_OBJECT_NAME_NOT_FOUND;
        }
        break;

    case SMB2_FILE_SUPERSEDE:
    case SMB2_FILE_OVERWRITE_IF:
        if(!exists && (req->create_options & SMB2_FILE_DIRECTORY_FILE)) {
            if(vfs_makedir(path)) {
                smb_free_file(sc, fe);
                return SMB2_STATUS_ACCESS_DENIED;
            }
            is_dir = 1; exists = 1;
            if(vfs_stat(path, &fs, errbuf, sizeof(errbuf))) {
                fs.fs_mtime = time(NULL);
            }
        }
        break;

    default:
        smb_free_file(sc, fe);
        return SMB2_STATUS_INVALID_PARAMETER;
    }

    fe->is_dir = is_dir;
    fe->delete_on_close = !!(req->create_options & SMB2_FILE_DELETE_ON_CLOSE);
    fe->mtime = exists ? fs.fs_mtime : time(NULL);

    if(is_dir) {
        fe->size    = 0;
        fe->fa_dir  = NULL;  /* lazy scan in query_directory */
    } else {
        int flags = 0;
        int truncate_on_open =
            exists && (req->create_disposition == SMB2_FILE_OVERWRITE ||
                       req->create_disposition == SMB2_FILE_OVERWRITE_IF ||
                       req->create_disposition == SMB2_FILE_SUPERSEDE);
        int want_write = !!(req->desired_access & (SMB2_FILE_WRITE_DATA |
                                                   SMB2_FILE_APPEND_DATA |
                                                   SMB2_FILE_WRITE_ATTRIBUTES));

        if(!exists || want_write) {
            flags |= FA_WRITE;
            if(exists && !truncate_on_open) {
                flags |= FA_APPEND;
            }
        }

        fe->fa_fh = vfs_open(path, errbuf, sizeof(errbuf), flags);
        if(fe->fa_fh == NULL) {
            SMBTRACE("Create: open failed for '%s': %s", path, errbuf);
            smb_free_file(sc, fe);
            return SMB2_STATUS_ACCESS_DENIED;
        }

        if((flags & FA_APPEND) && fa_seek(fe->fa_fh, 0, SEEK_SET) < 0) {
            smb_free_file(sc, fe);
            return SMB2_STATUS_ACCESS_DENIED;
        }

        // Handle overwrite/supersede truncation via fa_ftruncate
        if(truncate_on_open) {
            if(fa_ftruncate(fe->fa_fh, 0) < 0) {
                smb_free_file(sc, fe);
                return SMB2_STATUS_ACCESS_DENIED;
            }
            fe->size = 0;
        } else {
            fe->size = exists ? fs.fs_size : 0;
        }
    }

    memcpy(sc->sc_related_file_id, fe->file_id, SMB2_FD_SIZE);
    sc->sc_related_file_valid = 1;
    memcpy(rep->file_id, fe->file_id, SMB2_FD_SIZE);
    rep->file_attributes = is_dir ? SMB2_FILE_ATTRIBUTE_DIRECTORY
                                  : SMB2_FILE_ATTRIBUTE_NORMAL;

    if(existed) {
        if(req->create_disposition == SMB2_FILE_OVERWRITE ||
           req->create_disposition == SMB2_FILE_OVERWRITE_IF) {
            rep->create_action = SMB2_FILE_OVERWRITTEN;
        } else if(req->create_disposition == SMB2_FILE_SUPERSEDE) {
            rep->create_action = SMB2_FILE_SUPERSEDED;
        } else {
            rep->create_action = SMB2_FILE_OPENED;
        }
    } else {
        rep->create_action = SMB2_FILE_CREATED;
    }

    rep->end_of_file     = fe->size;
    rep->allocation_size = fe->size;

    uint64_t win_time = smb_time_to_win(fe->mtime);
    rep->creation_time = rep->last_access_time = rep->last_write_time = rep->change_time = win_time;

    SMBTRACE("Create OK: '%s' %s size=%lld delete_on_close=%d",
             path, is_dir ? "DIR" : "FILE", (long long)fe->size, fe->delete_on_close);
    return 0;
}

/* ------------------------------------------------------------------ */
/* Handler: CLOSE                                                       */
/* ------------------------------------------------------------------ */

int
smb_close(struct smb2_server *srvr, struct smb2_context *smb2,
          struct smb2_close_request *req,
          struct smb2_close_reply *rep)
{
    if(req == NULL || rep == NULL)
        return SMB2_STATUS_INVALID_PARAMETER;
    smb_connection_t *sc = smb2_get_opaque(smb2);
    if(sc == NULL)
        return SMB2_STATUS_INTERNAL_ERROR;
    int auth_status = smb_reject_stale_session(srvr, smb2);
    if(auth_status)
        return auth_status;

    smb_file_entry_t *fe = smb_find_file(sc, req->file_id);
    if(fe == NULL)
        return SMB2_STATUS_FILE_CLOSED;

    memset(rep, 0, sizeof(*rep));

    if(req->flags & SMB2_CLOSE_FLAG_POSTQUERY_ATTRIB) {
        time_t mt = fe->mtime;
        int64_t sz = fe->size;
        struct fa_stat fs;
        if(fe->path && vfs_stat(fe->path, &fs, NULL, 0) == 0) {
            mt = fs.fs_mtime;
            sz = fs.fs_size;
        }
        uint64_t win_time = smb_time_to_win(mt);
        rep->creation_time = rep->last_access_time = rep->last_write_time = rep->change_time = win_time;
        rep->end_of_file = rep->allocation_size = sz;
        rep->file_attributes = fe->is_dir ? SMB2_FILE_ATTRIBUTE_DIRECTORY
                                          : SMB2_FILE_ATTRIBUTE_NORMAL;
    }

    return smb_close_file_entry(sc, fe);
}

/* ------------------------------------------------------------------ */
/* Handler: FLUSH                                                       */
/* ------------------------------------------------------------------ */

int
smb_flush(struct smb2_server *srvr, struct smb2_context *smb2,
          struct smb2_flush_request *req)
{
    return 0;
}

/* ------------------------------------------------------------------ */
/* Handler: READ                                                        */
/* ------------------------------------------------------------------ */

int
smb_read(struct smb2_server *srvr, struct smb2_context *smb2,
         struct smb2_read_request *req,
         struct smb2_read_reply *rep)
{
    if(req == NULL || rep == NULL)
        return SMB2_STATUS_INVALID_PARAMETER;
    smb_connection_t *sc = smb2_get_opaque(smb2);
    if(sc == NULL)
        return SMB2_STATUS_INTERNAL_ERROR;
    int auth_status = smb_reject_stale_session(srvr, smb2);
    if(auth_status)
        return auth_status;

    smb_file_entry_t *fe = smb_find_file(sc, req->file_id);
    if(fe == NULL || fe->is_dir || fe->fa_fh == NULL)
        return SMB2_STATUS_INVALID_HANDLE;

    SMBTRACE("Read: '%s' offset=%llu len=%u",
             fe->path, (unsigned long long)req->offset, req->length);

    /* SMB2 READ uses absolute file offsets — must seek before read */
    if(req->offset != (uint64_t)fe->pos) {
        if(fa_seek(fe->fa_fh, (int64_t)req->offset, SEEK_SET) < 0)
            return SMB2_STATUS_INTERNAL_ERROR;
        fe->pos = (int64_t)req->offset;
    }

    uint32_t count = req->length;
    if(count > 1024 * 1024)
        count = 1024 * 1024;   /* 1 MB cap */

    rep->data = malloc(count);
    if(rep->data == NULL)
        return SMB2_STATUS_INSUFFICIENT_RESOURCES;

    int r = fa_read(fe->fa_fh, rep->data, count);
    if(r < 0) {
        free(rep->data);
        rep->data        = NULL;
        rep->data_length = 0;
        return SMB2_STATUS_INTERNAL_ERROR;
    }
    if(r == 0) {
        /* EOF */
        SMBTRACE("Read EOF: '%s' at offset %llu", fe->path, (unsigned long long)req->offset);
        free(rep->data);
        rep->data        = NULL;
        rep->data_length = 0;
        rep->data_remaining = 0;
        return SMB2_STATUS_END_OF_FILE;
    }

    fe->pos += r;

    rep->data_length    = (uint32_t)r;
    rep->data_remaining = 0;
    SMBTRACE("Read OK: %u bytes from offset %llu", (uint32_t)r, (unsigned long long)req->offset);
    return 0;
}

/* ------------------------------------------------------------------ */
/* Handler: WRITE                                                       */
/* ------------------------------------------------------------------ */

int
smb_write(struct smb2_server *srvr, struct smb2_context *smb2,
          struct smb2_write_request *req,
          struct smb2_write_reply *rep)
{
    if(req == NULL || rep == NULL)
        return SMB2_STATUS_INVALID_PARAMETER;
    smb_connection_t *sc = smb2_get_opaque(smb2);
    if(sc == NULL)
        return SMB2_STATUS_INTERNAL_ERROR;
    int auth_status = smb_reject_stale_session(srvr, smb2);
    if(auth_status)
        return auth_status;

    smb_file_entry_t *fe = smb_find_file(sc, req->file_id);
    if(fe == NULL || fe->is_dir || fe->fa_fh == NULL)
        return SMB2_STATUS_INVALID_HANDLE;

    SMBTRACE("Write: '%s' offset=%llu len=%u",
             fe->path, (unsigned long long)req->offset, req->length);

    if(req->offset != (uint64_t)fe->pos) {
        if(fa_seek(fe->fa_fh, (int64_t)req->offset, SEEK_SET) < 0)
            return SMB2_STATUS_INTERNAL_ERROR;
        fe->pos = (int64_t)req->offset;
    }

    int r = fa_write(fe->fa_fh, req->buf, req->length);
    if(r < 0)
        return smb_errno_to_ntstatus(errno);

    fe->pos += r;

    /* Update cached size */
    int64_t end = (int64_t)req->offset + r;
    if(end > fe->size)
        fe->size = end;

    rep->count     = (uint32_t)r;
    rep->remaining = 0;
    SMBTRACE("Write OK: %u bytes at offset %llu (file size now %lld)",
             (uint32_t)r, (unsigned long long)req->offset, (long long)fe->size);
    return 0;
}

/* ------------------------------------------------------------------ */
/* Handler: QUERY_DIRECTORY                                             */
/* ------------------------------------------------------------------ */

#define PAD_TO_64BIT(len) (((len) + 7) & ~7u)

int
smb_query_directory(struct smb2_server *srvr, struct smb2_context *smb2,
                    struct smb2_query_directory_request *req,
                    struct smb2_query_directory_reply *rep)
{
    if(req == NULL || rep == NULL)
        return SMB2_STATUS_INVALID_PARAMETER;
    smb_connection_t *sc = smb2_get_opaque(smb2);
    if(sc == NULL)
        return SMB2_STATUS_INTERNAL_ERROR;
    int auth_status = smb_reject_stale_session(srvr, smb2);
    if(auth_status)
        return auth_status;

    if(req->file_information_class != SMB2_FILE_ID_BOTH_DIRECTORY_INFORMATION &&
       req->file_information_class != SMB2_FILE_ID_FULL_DIRECTORY_INFORMATION)
        return SMB2_STATUS_INVALID_INFO_CLASS;

    smb_file_entry_t *fe = smb_find_file(sc, req->file_id);
    if(fe == NULL || !fe->is_dir)
        return SMB2_STATUS_INVALID_HANDLE;

    /*
     * SMB2 QUERY_DIRECTORY flags:
     *   SMB2_RESTART_SCANS (0x01) — rewind to start
     *   SMB2_REOPEN        (0x10) — re-scan the directory
     *
     * We rescan on RESTART or REOPEN (simplest correct behaviour).
     * On subsequent calls with the same handle we return 0 (→ NO_MORE_FILES)
     * until the client reopens.
     */
    if(req->flags & (SMB2_RESTART_SCANS | SMB2_REOPEN)) {
        SMBTRACE("QueryDir: %s '%s'",
                 (req->flags & SMB2_REOPEN) ? "REOPEN" : "RESTART_SCANS", fe->path);
        if(fe->fa_dir) {
            fa_dir_free(fe->fa_dir);
            fe->fa_dir  = NULL;
        }
        fe->dir_done = 0;
        fe->next_fde = NULL;
        if(req->flags & SMB2_REOPEN) {
            free(fe->dir_pattern);
            fe->dir_pattern = NULL;
        }
    }

    if(fe->dir_done) {
        SMBTRACE("QueryDir: NO_MORE_FILES for '%s'", fe->path);
        rep->output_buffer        = NULL;
        rep->output_buffer_length = 0;
        return 0;
    }

    SMBTRACE("QueryDir: '%s' flags=0x%02x idx=%u class=%u", fe->path, req->flags, req->file_index, req->file_information_class);

    if(req->name && req->name[0] != '\0') {
        char *pattern = strdup(req->name);
        if(pattern == NULL)
            return SMB2_STATUS_INSUFFICIENT_RESOURCES;
        free(fe->dir_pattern);
        fe->dir_pattern = pattern;
    }
    const char *pattern = fe->dir_pattern;

    /* Lazy scan */
    if(fe->fa_dir == NULL) {
        char errbuf[256];
        fe->fa_dir = vfs_scandir(fe->path, errbuf, sizeof(errbuf));
        if(fe->fa_dir == NULL) {
            /* Empty or inaccessible directories match the historical baseline. */
            fe->dir_done = 1;
            fe->next_fde = NULL;
            rep->output_buffer        = NULL;
            rep->output_buffer_length = 0;
            return 0;
        }
        fe->next_fde = RB_FIRST(&fe->fa_dir->fd_entries);
    }

    /*
     * Build the response buffer with all remaining entries.
     * libsmb2 sends STATUS_NO_MORE_FILES automatically when
     * output_buffer_length == 0.
     *
     * SMB2_RETURN_SINGLE_ENTRY: return only one entry per call.
     */
    int single_entry = !!(req->flags & SMB2_RETURN_SINGLE_ENTRY);

    int full_info =
        req->file_information_class == SMB2_FILE_ID_FULL_DIRECTORY_INFORMATION;
    size_t entry_size = full_info ?
        PAD_TO_64BIT(sizeof(struct smb2_fileidfulldirectoryinformation)) :
        PAD_TO_64BIT(sizeof(struct smb2_fileidbothdirectoryinformation));
    int capacity = single_entry ? 1 : 16;

    uint8_t *info = malloc(capacity * entry_size);
    if(info == NULL)
        return SMB2_STATUS_INSUFFICIENT_RESOURCES;

    uint32_t info_len = 0;
    int      n_added  = 0;
    uint32_t serialized_wire_len = 0;

    fa_dir_entry_t *fde = fe->next_fde;
    if(fde == NULL && !fe->dir_done) {
        fde = RB_FIRST(&fe->fa_dir->fd_entries);
    }

    for(; fde != NULL; fde = RB_NEXT(fde, fde_link)) {
        fe->next_fde = fde;
        const char *name = rstr_get(fde->fde_filename);
        if(name == NULL)
            continue;

        if(pattern != NULL) {
            if(!pattern_match(name, pattern))
                continue;
        }

        /*
         * Check client buffer limit on wire.
         * Note: req->output_buffer_length is the client's limit for the serialized
         * network payload. We track serialized_wire_len which matches the wire format
         * size that libsmb2 will compute and send.
         */
        size_t name_bytes = utf8_to_utf16_bytes(name);
        size_t wire_hdr = (req->file_information_class == SMB2_FILE_ID_BOTH_DIRECTORY_INFORMATION)
            ? SMB2_FILEID_BOTH_DIRECTORY_INFORMATION_SIZE
            : SMB2_FILEID_FULL_DIRECTORY_INFORMATION_SIZE;
        size_t wire_size = PAD_TO_64BIT(wire_hdr + name_bytes);

        if(n_added > 0 && serialized_wire_len + wire_size > req->output_buffer_length) {
            break;
        }

        if(n_added >= capacity) {
            int new_capacity = capacity * 2;
            void *new_info = realloc(info, new_capacity * entry_size);
            if(new_info == NULL) {
                free(info);
                return SMB2_STATUS_INSUFFICIENT_RESOURCES;
            }
            info = new_info;
            capacity = new_capacity;
        }

        int entry_is_dir = content_dirish(fde->fde_type);

        SMBTRACE("QueryDir: entry name='%s' url='%s' is_dir=%d", name, rstr_get(fde->fde_url), entry_is_dir);

        /* Stat entries that haven't been statted yet (files only, skip dirs to avoid loopback deadlocks) */
        if(!fde->fde_statdone && !entry_is_dir) {
            SMBTRACE("QueryDir: statting '%s'", rstr_get(fde->fde_url));
            fa_stat_ex(rstr_get(fde->fde_url), &fde->fde_stat, NULL, 0,
                       FA_NON_INTERACTIVE);
            SMBTRACE("QueryDir: statted '%s'", rstr_get(fde->fde_url));
        }

        /*
         * Directory information structs store name as a const char * (UTF-8).
         * libsmb2 re-encodes to UTF-16 when building the wire PDU.
         * The string pointer name remains valid as long as fe->fa_dir is alive.
         */
        time_t mtime = fde->fde_statdone ? fde->fde_stat.fs_mtime : fe->mtime;
        if(full_info) {
            struct smb2_fileidfulldirectoryinformation *fs =
                (struct smb2_fileidfulldirectoryinformation *)(info + info_len);
            memset(fs, 0, entry_size);
            fs->file_index = n_added;
            fs->file_attributes = entry_is_dir ? SMB2_FILE_ATTRIBUTE_DIRECTORY
                                               : SMB2_FILE_ATTRIBUTE_NORMAL;
            if(!entry_is_dir) {
                fs->end_of_file = fde->fde_stat.fs_size;
                fs->allocation_size = fde->fde_stat.fs_size;
            }
            smb_fill_timevals(&fs->creation_time, mtime);
            fs->file_name_length = name_bytes;
            fs->name = name;
        } else {
            struct smb2_fileidbothdirectoryinformation *fs =
                (struct smb2_fileidbothdirectoryinformation *)(info + info_len);
            memset(fs, 0, entry_size);
            fs->file_index = n_added;
            fs->file_attributes = entry_is_dir ? SMB2_FILE_ATTRIBUTE_DIRECTORY
                                               : SMB2_FILE_ATTRIBUTE_NORMAL;
            if(!entry_is_dir) {
                fs->end_of_file = fde->fde_stat.fs_size;
                fs->allocation_size = fde->fde_stat.fs_size;
            }
            smb_fill_timevals(&fs->creation_time, mtime);
            fs->file_name_length = name_bytes;
            fs->short_name_length = 0;
            fs->name = name;
        }

        info_len += entry_size;
        serialized_wire_len += wire_size;
        n_added++;

        fe->next_fde = RB_NEXT(fde, fde_link);

        if(single_entry)
            break;
    }

    if(n_added == 0) {
        /* No entries at all or first call on empty dir */
        free(info);
        fe->dir_done = 1;
        fe->next_fde = NULL;
        rep->output_buffer        = NULL;
        rep->output_buffer_length = 0;
        return 0;
    }

    /* Shrink buffer to fit actual serialized entries if necessary */
    if(n_added < capacity) {
        void *new_info = realloc(info, info_len);
        if(new_info)
            info = new_info;
    }

    /* Mark as done only when we ran out of matching files */
    fe->dir_done = (fe->next_fde == NULL);

    rep->output_buffer        = info;
    rep->output_buffer_length = info_len;
    SMBTRACE("QueryDir: returning %d entries (%u bytes, wire=%u)", n_added, info_len, serialized_wire_len);
    return 0;
}

/* ------------------------------------------------------------------ */
/* Handler: QUERY_INFO                                                  */
/* ------------------------------------------------------------------ */

int
smb_query_info(struct smb2_server *srvr, struct smb2_context *smb2,
               struct smb2_query_info_request *req,
               struct smb2_query_info_reply *rep)
{
    if(req == NULL || rep == NULL)
        return SMB2_STATUS_INVALID_PARAMETER;
    smb_connection_t *sc = smb2_get_opaque(smb2);
    if(sc == NULL)
        return SMB2_STATUS_INTERNAL_ERROR;
    int auth_status = smb_reject_stale_session(srvr, smb2);
    if(auth_status)
        return auth_status;

    smb_file_entry_t *fe = smb_find_file(sc, req->file_id);

    void    *info = NULL;
    int      len  = 0;

    if(req->info_type == SMB2_0_INFO_FILESYSTEM) {

        switch(req->file_info_class) {
        case SMB2_FILE_FS_SIZE_INFORMATION: {
            struct smb2_file_fs_size_info *fs = calloc(1, sizeof(*fs));
            if(!fs) return SMB2_STATUS_INSUFFICIENT_RESOURCES;
            struct statvfs sv;
            if(smb_get_statvfs(sc, fe, &sv) == 0) {
                fs->total_allocation_units     = sv.f_blocks;
                fs->available_allocation_units = sv.f_bavail;
                fs->sectors_per_allocation_unit = 1;
                fs->bytes_per_sector           = (uint32_t)sv.f_frsize;
            } else {
                fs->total_allocation_units     = 0x100000;
                fs->available_allocation_units = 0x10000;
                fs->sectors_per_allocation_unit = 1;
                fs->bytes_per_sector           = 512;
            }
            info = fs; len = sizeof(*fs);
            break;
        }
        case SMB2_FILE_FS_FULL_SIZE_INFORMATION: {
            struct smb2_file_fs_full_size_info *fs = calloc(1, sizeof(*fs));
            if(!fs) return SMB2_STATUS_INSUFFICIENT_RESOURCES;
            struct statvfs sv;
            if(smb_get_statvfs(sc, fe, &sv) == 0) {
                fs->total_allocation_units              = sv.f_blocks;
                fs->caller_available_allocation_units   = sv.f_bavail;
                fs->actual_available_allocation_units   = sv.f_bfree;
                fs->sectors_per_allocation_unit         = 1;
                fs->bytes_per_sector                    = (uint32_t)sv.f_frsize;
            } else {
                fs->total_allocation_units            = 0x100000;
                fs->caller_available_allocation_units = 0x10000;
                fs->actual_available_allocation_units = 0x10000;
                fs->sectors_per_allocation_unit       = 1;
                fs->bytes_per_sector                  = 512;
            }
            info = fs; len = sizeof(*fs);
            break;
        }
        case SMB2_FILE_FS_ATTRIBUTE_INFORMATION: {
            struct smb2_file_fs_attribute_info *fs = calloc(1, sizeof(*fs));
            if(!fs) return SMB2_STATUS_INSUFFICIENT_RESOURCES;
            /* FILE_CASE_PRESERVED_NAMES | FILE_UNICODE_ON_DISK */
            fs->filesystem_attributes         = 0x00000002 | 0x00000004;
            fs->maximum_component_name_length = 255;
            /* libsmb2 re-encodes this as UTF-16 on the wire — ASCII is fine */
            fs->filesystem_name        = (const uint8_t *)"Movian";
            fs->filesystem_name_length = 6;
            info = fs; len = sizeof(*fs);
            break;
        }
        case SMB2_FILE_FS_DEVICE_INFORMATION: {
            struct smb2_file_fs_device_info *fs = calloc(1, sizeof(*fs));
            if(!fs) return SMB2_STATUS_INSUFFICIENT_RESOURCES;
            fs->device_type    = FILE_DEVICE_DISK;
            fs->characteristics = 0;
            info = fs; len = sizeof(*fs);
            break;
        }
        default:
            return SMB2_STATUS_INVALID_INFO_CLASS;
        }

    } else if(req->info_type == SMB2_0_INFO_FILE) {

        if(fe == NULL)
            return SMB2_STATUS_INVALID_HANDLE;

        switch(req->file_info_class) {
        case SMB2_FILE_BASIC_INFORMATION: {
            struct smb2_file_basic_info *fs = calloc(1, sizeof(*fs));
            if(!fs) return SMB2_STATUS_INSUFFICIENT_RESOURCES;
            fs->file_attributes = fe->is_dir ? SMB2_FILE_ATTRIBUTE_DIRECTORY
                                             : SMB2_FILE_ATTRIBUTE_NORMAL;
            smb_fill_timevals(&fs->creation_time, fe->mtime);
            info = fs; len = sizeof(*fs);
            break;
        }
        case SMB2_FILE_STANDARD_INFORMATION: {
            struct smb2_file_standard_info *fs = calloc(1, sizeof(*fs));
            if(!fs) return SMB2_STATUS_INSUFFICIENT_RESOURCES;
            fs->end_of_file     = fe->size;
            fs->allocation_size = fe->size;
            fs->number_of_links = 1;
            fs->delete_pending  = fe->delete_on_close;
            fs->directory       = fe->is_dir;
            info = fs; len = sizeof(*fs);
            break;
        }
        case SMB2_FILE_ALL_INFORMATION: {
            struct smb2_file_all_info *fs = calloc(1, sizeof(*fs));
            if(!fs) return SMB2_STATUS_INSUFFICIENT_RESOURCES;
            fs->basic.file_attributes      = fe->is_dir
                                             ? SMB2_FILE_ATTRIBUTE_DIRECTORY
                                             : SMB2_FILE_ATTRIBUTE_NORMAL;
            smb_fill_timevals(&fs->basic.creation_time, fe->mtime);
            fs->standard.end_of_file       = fe->size;
            fs->standard.allocation_size   = fe->size;
            fs->standard.number_of_links   = 1;
            fs->standard.delete_pending    = fe->delete_on_close;
            fs->standard.directory         = fe->is_dir;
            fs->index_number               = 1;
            fs->access_flags               = 0x001f01ff;
            fs->name                       = (const uint8_t *)"";
            info = fs; len = sizeof(*fs);
            break;
        }
        case SMB2_FILE_NETWORK_OPEN_INFORMATION: {
            struct smb2_file_network_open_info *fs = calloc(1, sizeof(*fs));
            if(!fs) return SMB2_STATUS_INSUFFICIENT_RESOURCES;
            fs->file_attributes  = fe->is_dir ? SMB2_FILE_ATTRIBUTE_DIRECTORY
                                              : SMB2_FILE_ATTRIBUTE_NORMAL;
            smb_fill_timevals(&fs->creation_time, fe->mtime);
            fs->end_of_file      = fe->size;
            fs->allocation_size  = fe->size;
            info = fs; len = sizeof(*fs);
            break;
        }
        default:
            return SMB2_STATUS_INVALID_INFO_CLASS;
        }
    } else {
        return SMB2_STATUS_INVALID_INFO_CLASS;
    }

    rep->output_buffer        = info;
    rep->output_buffer_length = (uint32_t)len;
    SMBTRACE("QueryInfo: %s/%s → %d bytes",
             req->info_type == SMB2_0_INFO_FILESYSTEM ? "FS" :
             req->info_type == SMB2_0_INFO_FILE       ? "FILE" :
             req->info_type == SMB2_0_INFO_SECURITY   ? "SEC" : "?",
             req->file_info_class == SMB2_FILE_BASIC_INFORMATION          ? "BASIC" :
             req->file_info_class == SMB2_FILE_STANDARD_INFORMATION       ? "STANDARD" :
             req->file_info_class == SMB2_FILE_ALL_INFORMATION            ? "ALL" :
             req->file_info_class == SMB2_FILE_NETWORK_OPEN_INFORMATION   ? "NETWORK_OPEN" :
             req->file_info_class == SMB2_FILE_FS_SIZE_INFORMATION        ? "FS_SIZE" :
             req->file_info_class == SMB2_FILE_FS_FULL_SIZE_INFORMATION   ? "FS_FULL_SIZE" :
             req->file_info_class == SMB2_FILE_FS_ATTRIBUTE_INFORMATION   ? "FS_ATTR" :
             req->file_info_class == SMB2_FILE_FS_DEVICE_INFORMATION      ? "FS_DEVICE" : "?",
             len);
    return 0;
}

/* ------------------------------------------------------------------ */
/* Handler: SET_INFO                                                    */
/* ------------------------------------------------------------------ */

/*
 * Decode raw RENAME_INFORMATION buffer (passthrough mode):
 *   offset 0:    uint8  replace_if_exist
 *   offset 1-7:  padding
 *   offset 8-15: uint64 RootDirectory (ignored, we use relative paths)
 *   offset 16-19: uint32 FileNameLength (in bytes, UTF-16LE)
 *   offset 20+:  FileNameLength bytes of UTF-16LE name
 */
static char *
smb_decode_rename_path(smb_connection_t *sc, const uint8_t *buf, uint32_t buflen)
{
    if(buf == NULL || buflen < 20)
        return NULL;

    uint32_t name_bytes = ((uint32_t)buf[16]) |
                          ((uint32_t)buf[17] << 8) |
                          ((uint32_t)buf[18] << 16) |
                          ((uint32_t)buf[19] << 24);

    if(name_bytes == 0 || 20 + name_bytes > buflen)
        return NULL;
    if(name_bytes % 2 != 0)
        return NULL;

    uint32_t nchars = name_bytes / 2;
    uint8_t *tmp = malloc(name_bytes);
    if(tmp == NULL)
        return NULL;
    memcpy(tmp, buf + 20, name_bytes);
    const char *utf8 = smb2_utf16_to_utf8((const uint16_t *)tmp, nchars);
    free(tmp);
    if(utf8 == NULL)
        return NULL;

    char *path = NULL;
    int path_status = smb_build_path(sc, utf8, &path);
    free((void *)utf8);
    return path_status == 0 ? path : NULL;
}

int
smb_set_info(struct smb2_server *srvr, struct smb2_context *smb2,
             struct smb2_set_info_request *req)
{
    if(req == NULL)
        return SMB2_STATUS_INVALID_PARAMETER;
    smb_connection_t *sc = smb2_get_opaque(smb2);
    if(sc == NULL)
        return SMB2_STATUS_INTERNAL_ERROR;
    int auth_status = smb_reject_stale_session(srvr, smb2);
    if(auth_status)
        return auth_status;

    smb_file_entry_t *fe = smb_find_file(sc, req->file_id);
    if(fe == NULL)
        return SMB2_STATUS_INVALID_HANDLE;

    if(req->info_type != SMB2_0_INFO_FILE)
        return SMB2_STATUS_NOT_SUPPORTED;

    char errbuf[256];

    switch(req->file_info_class) {
    case SMB2_FILE_RENAME_INFORMATION: {
        /*
         * input_data is raw bytes (passthrough mode enabled in session_established).
         * buf[0] = replace_if_exist
         * buf[16-19] = FileNameLength (LE)
         * buf[20+] = UTF-16LE name
         */
        char *new_path = smb_decode_rename_path(sc,
                             (const uint8_t *)req->input_data,
                             req->buffer_length);
        if(new_path == NULL)
            return SMB2_STATUS_INVALID_PARAMETER;

        int replace_if_exists = ((const uint8_t *)req->input_data)[0] != 0;
        if(!replace_if_exists) {
            struct fa_stat target_fs;
            if(vfs_stat(new_path, &target_fs, errbuf, sizeof(errbuf)) == 0) {
                SMBINFO("Rename collision: '%s' -> '%s' exists and replace is disabled",
                        fe->path, new_path);
                free(new_path);
                return SMB2_STATUS_OBJECT_NAME_COLLISION;
            }
        }

        SMBINFO("Rename: '%s' → '%s'", fe->path, new_path);
        SMBTRACE("Rename: executing vfs_rename");
        int r = vfs_rename(fe->path, new_path, errbuf, sizeof(errbuf));
        if(r) {
            SMBINFO("Rename FAILED: '%s' → '%s': %s", fe->path, new_path, errbuf);
            free(new_path);
            return smb_errno_to_ntstatus(r);
        }
        free(fe->path);
        fe->path = new_path;
        break;
    }
    case SMB2_FILE_DISPOSITION_INFORMATION: {
        /*
         * FileDispositionInformation: 1 byte — delete_pending flag.
         */
        if(req->input_data == NULL || req->buffer_length < 1)
            return SMB2_STATUS_INVALID_PARAMETER;
        fe->delete_on_close = !!((uint8_t *)req->input_data)[0];
        SMBTRACE("SetInfo disposition: '%s' delete_on_close=%d", fe->path, fe->delete_on_close);
        break;
    }
    case SMB2_FILE_END_OF_FILE_INFORMATION: {
        /* Truncate: 8-byte little-endian end-of-file offset */
        if(req->input_data == NULL || req->buffer_length < 8)
            return SMB2_STATUS_INVALID_PARAMETER;
        const uint8_t *b = (const uint8_t *)req->input_data;
        uint64_t eof = (uint64_t)b[0] | ((uint64_t)b[1] << 8) |
                       ((uint64_t)b[2] << 16) | ((uint64_t)b[3] << 24) |
                       ((uint64_t)b[4] << 32) | ((uint64_t)b[5] << 40) |
                       ((uint64_t)b[6] << 48) | ((uint64_t)b[7] << 56);
        SMBTRACE("SetInfo EndOfFile: '%s' truncate to %llu", fe->path, (unsigned long long)eof);
        if(fe->fa_fh) {
            if(fa_ftruncate(fe->fa_fh, (int64_t)eof) < 0)
                return SMB2_STATUS_ACCESS_DENIED;
            fe->size = (int64_t)eof;
            if(fe->pos > fe->size)
                fe->pos = fe->size;
        } else {
            return SMB2_STATUS_INVALID_HANDLE;
        }
        break;
    }
    default:
        return SMB2_STATUS_NOT_SUPPORTED;
    }

    return 0;
}
