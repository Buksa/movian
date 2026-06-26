/* -*-  mode:c; tab-width:8; c-basic-offset:8; indent-tabs-mode:nil;  -*- */
/*
 * Movian SMB2 server — backed by fa_protocol_vfs
 *
 * Architecture:
 *   smb_server_thread() blocks in smb2_serve_port() which owns the listen socket
 *   and its own select() loop (handles multiple clients internally).
 *   We restart the thread whenever port/enable settings change.
 *
 * Key libsmb2 server API facts (verified against source):
 *   - smb2_serve_port() calls smb2_bind_and_listen() itself; do NOT pre-bind.
 *   - req->name  in create/query_directory is UTF-8 (library decodes UTF-16LE).
 *   - smb2_fileidbothdirectoryinformation.name is const char* UTF-8.
 *   - query_directory returning output_buffer_length==0 signals STATUS_NO_MORE_FILES.
 *   - set_info variable data (rename, disposition) requires smb2_set_passthrough(smb2,1)
 *     to populate input_data; otherwise input_data is NULL.
 *   - Delete-on-close: set fe->delete_on_close from create_options, act in close.
 *   - SMB2_CLOSE_FLAG_DELETE does not exist in the protocol.
 */

#include <assert.h>
#include <stdio.h>
#include <sys/types.h>
#include <sys/statvfs.h>
#include <unistd.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>

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
#include <smb2/libsmb2-raw.h>

#ifndef SMB2_FILE_OPENED
#define SMB2_FILE_OPENED 1
#endif
#ifndef SMB2_FILE_CREATED
#define SMB2_FILE_CREATED 2
#endif

/* ------------------------------------------------------------------ */
/* Tracing                                                              */
/* ------------------------------------------------------------------ */

/*
 * Two log levels, matching Movian convention (see SSDP, STPP, etc.):
 *   SMBINFO  — always visible (TRACE_INFO):  server lifecycle, connections, auth results
 *   SMBTRACE — gated on enable_smb_debug (TRACE_DEBUG): per-request detail
 */
#define SMBINFO(x, ...)  tracelog(0, TRACE_INFO,  "SMB2", x, ##__VA_ARGS__)
#define SMBTRACE(x, ...) do { \
    if(gconf.enable_smb_debug) \
      tracelog(0, TRACE_DEBUG, "SMB2", x, ##__VA_ARGS__); \
} while(0)
/* ------------------------------------------------------------------ */
/* File handle table                                                    */
/* ------------------------------------------------------------------ */

#define SMB2_MAX_FILES 64

/*
 * We encode handle index (1-based) + generation counter into 8 bytes of
 * the 16-byte file_id so that smb_find_file() can validate handles quickly
 * without iterating the full table.
 *
 * Layout: file_id[0..3] = index (big-endian), file_id[4..7] = generation,
 *         file_id[8..15] = 0.
 * A zero file_id[0..7] means the slot is free.
 */

typedef struct {
    uint8_t     file_id[SMB2_FD_SIZE];
    fa_handle_t *fa_fh;            /* non-NULL if regular file open     */
    fa_dir_t    *fa_dir;           /* non-NULL if directory open        */
    char        *path;             /* absolute vfs path (malloc'd)      */
    int64_t     size;              /* cached file size (from stat)      */
    int64_t     pos;               /* cached file offset position       */
    time_t      mtime;             /* mtime timestamp                   */
    int         is_dir;
    int         delete_on_close;   /* set from SMB2_FILE_DELETE_ON_CLOSE */
    int         dir_done;          /* set after first full dir listing  */
    int         dir_idx;           /* count of returned dir entries     */
} smb_file_entry_t;

typedef struct smb_connection {
    struct smb2_server   sc_server;
    char                *sc_share_root;   /* root path for this share   */
    smb_file_entry_t     sc_files[SMB2_MAX_FILES];
    uint32_t             sc_gen;          /* generation counter         */
} smb_connection_t;

/* ------------------------------------------------------------------ */
/* Server state (protected by asyncio courier)                          */
/* ------------------------------------------------------------------ */

static int            smb_port   = 1445;
static int            smb_enable = 0;
static char          *smb_username   = NULL;   /* NULL = allow anonymous  */
static char          *smb_password   = NULL;   /* NULL = allow anonymous  */
static char          *smb_share_name  = NULL;
static char          *smb_share_root  = NULL;   /* configurable root  */
static int            smb_thread_running = 0;

/* ------------------------------------------------------------------ */
/* VFS helpers                                                          */
/* ------------------------------------------------------------------ */

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

static int
vfs_unlink(const char *url, char *errbuf, size_t errlen)
{
    return fa_unlink(url, errbuf, errlen);
}

static int
vfs_rmdir(const char *url, char *errbuf, size_t errlen)
{
    return fa_rmdir(url, errbuf, errlen);
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
static char *
smb_build_path(smb_connection_t *sc, const char *name)
{
    const char *root = sc->sc_share_root;

    if(name == NULL || *name == '\0')
        return strdup(root);

    size_t rlen = strlen(root);
    size_t nlen = strlen(name);
    int need_slash = (rlen > 0 && root[rlen-1] != '/');
    size_t plen = rlen + need_slash + nlen + 1;

    char *path = malloc(plen);
    if(path == NULL)
        return NULL;

    memcpy(path, root, rlen);
    char *dest = path + rlen;
    if(need_slash) {
        *dest++ = '/';
    }

    /* Copy name while converting backslash to slash and collapsing multiple slashes */
    const char *src = name;
    /* Strip leading slashes/backslashes */
    while(*src == '/' || *src == '\\') {
        src++;
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
            return NULL;
        }
        const char *next = strchr(p, '/');
        p = next ? next + 1 : p + strlen(p);
    }

    return path;
}

/* ------------------------------------------------------------------ */
/* File handle management                                               */
/* ------------------------------------------------------------------ */

static void
smb_encode_file_id(uint8_t *file_id, uint32_t idx, uint32_t gen)
{
    memset(file_id, 0, SMB2_FD_SIZE);
    /* big-endian index in bytes 0-3, generation in bytes 4-7 */
    file_id[0] = (idx >> 24) & 0xff;
    file_id[1] = (idx >> 16) & 0xff;
    file_id[2] = (idx >>  8) & 0xff;
    file_id[3] =  idx        & 0xff;
    file_id[4] = (gen >> 24) & 0xff;
    file_id[5] = (gen >> 16) & 0xff;
    file_id[6] = (gen >>  8) & 0xff;
    file_id[7] =  gen        & 0xff;
}

static uint32_t
smb_decode_file_idx(const uint8_t *file_id)
{
    return ((uint32_t)file_id[0] << 24) | ((uint32_t)file_id[1] << 16) |
           ((uint32_t)file_id[2] <<  8) |  (uint32_t)file_id[3];
}



/* Returns NULL if file_id is invalid or slot is free */
static smb_file_entry_t *
smb_find_file(smb_connection_t *sc, const uint8_t *file_id)
{
    uint32_t idx = smb_decode_file_idx(file_id);

    /* idx is 1-based */
    if(idx == 0 || idx > SMB2_MAX_FILES)
        return NULL;

    smb_file_entry_t *fe = &sc->sc_files[idx - 1];

    /* Slot must be active and file_id must match */
    if(fe->path == NULL || memcmp(fe->file_id, file_id, SMB2_FD_SIZE) != 0)
        return NULL;

    return fe;
}

static smb_file_entry_t *
smb_alloc_file(smb_connection_t *sc, char *path)
{
    for(int i = 0; i < SMB2_MAX_FILES; i++) {
        smb_file_entry_t *fe = &sc->sc_files[i];
        /* free slot: path is NULL */
        if(fe->path == NULL) {
            fe->path = path;
            sc->sc_gen++;
            smb_encode_file_id(fe->file_id, (uint32_t)(i + 1), sc->sc_gen);
            return fe;
        }
    }
    return NULL;
}

static void
smb_free_file(smb_connection_t *sc, smb_file_entry_t *fe)
{
    if(fe->is_dir) {
        if(fe->fa_dir)
            fa_dir_free(fe->fa_dir);
    } else {
        if(fe->fa_fh)
            fa_close(fe->fa_fh);
    }
    free(fe->path);
    memset(fe, 0, sizeof(*fe));
}

static void
smb_close_all_files(smb_connection_t *sc)
{
    int n_closed = 0;
    for(int i = 0; i < SMB2_MAX_FILES; i++) {
        smb_file_entry_t *fe = &sc->sc_files[i];
        if(fe->path != NULL) {
            SMBTRACE("Cleanup: closing leaked handle '%s'", fe->path);
            smb_free_file(sc, fe);
            n_closed++;
        }
    }
    if(n_closed)
        SMBTRACE("Cleanup: closed %d leaked file handle(s)", n_closed);
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
/* Handler: AUTHORIZE                                                   */
/* ------------------------------------------------------------------ */

static int
smb_authorize(struct smb2_server *srvr, struct smb2_context *smb2,
              const char *user, const char *domain, const char *workstation)
{
    /*
     * libsmb2 server NTLM auth flow (verified against ntlmssp.c):
     *
     * 1. This callback is called with the username from the AUTHENTICATION_MESSAGE.
     * 2. We must call smb2_set_password(smb2, password) to give libsmb2 the
     *    expected password — it will then verify NTLMv2 via HMAC-MD5 internally.
     * 3. Return 0 to proceed, -1 to reject immediately.
     *
     * Auth matrix:
     *   smb_username == NULL → anonymous mode: accept everyone, no password check.
     *   smb_username != NULL → require matching user + password.
     *     - If user doesn't match → return -1.
     *     - If user matches → call smb2_set_password() so NTLMv2 can be verified.
     *     - allow_anonymous on the server struct must be 0 to enforce this.
     */
    if(smb_username == NULL) {
        /* Anonymous mode: accept any connection */
        SMBINFO("Auth: anonymous access granted (user='%s', domain='%s', workstation='%s')",
                user ? user : "guest", domain ? domain : "", workstation ? workstation : "");
        if(user)
            smb2_set_user(smb2, user);
        return 0;
    }

    /* Password-protected mode */
    if(user == NULL || user[0] == '\0') {
        /* Client didn't send a username — reject */
        SMBINFO("Auth: rejected anonymous connection (server requires credentials)");
        return -1;
    }

    if(strcmp(user, smb_username) != 0) {
        SMBINFO("Auth: rejected unknown user '%s'", user);
        return -1;
    }

    /*
     * Username matches. Provide password so libsmb2 can verify NTLMv2.
     * smb2_set_password() makes a copy internally.
     */
    smb2_set_password(smb2, smb_password ? smb_password : "");
    SMBTRACE("Auth: user '%s' domain='%s' wks='%s' → NTLMv2 pending",
             user, domain ? domain : "", workstation ? workstation : "");
    SMBINFO("Auth: NTLMv2 challenge for user '%s'", user);
    return 0;
}

/* ------------------------------------------------------------------ */
/* Handler: SESSION_ESTABLISHED                                         */
/* ------------------------------------------------------------------ */

static int
smb_session_established(struct smb2_server *srvr, struct smb2_context *smb2)
{
    SMBINFO("Connection established: dialect=SMB%s",
             smb2_get_dialect(smb2) >= 0x0300 ? "3.x" :
             smb2_get_dialect(smb2) >= 0x0210 ? "2.1" : "2.0");
    SMBTRACE("Session: dialect=0x%04x passthrough=on", smb2_get_dialect(smb2));

    /*
     * Enable passthrough so that variable-length set_info buffers
     * (rename, disposition) are delivered raw in req->input_data.
     */
    smb2_set_passthrough(smb2, 1);

    /* Attach our per-connection state */
    smb_connection_t *sc = calloc(1, sizeof(smb_connection_t));
    if(sc == NULL)
        return -1;
    const char *root = smb_share_root && smb_share_root[0] ? smb_share_root : "/";
    char root_buf[512];
    snprintf(root_buf, sizeof(root_buf), "file://%s", root);
    sc->sc_share_root = strdup(root_buf);
    smb2_set_opaque(smb2, sc);
    return 0;
}

/* ------------------------------------------------------------------ */
/* Handler: LOGOFF / DESTRUCTION                                        */
/* ------------------------------------------------------------------ */

static int
smb_logoff(struct smb2_server *srvr, struct smb2_context *smb2)
{
    smb_connection_t *sc = smb2_get_opaque(smb2);
    if(sc) {
        smb_close_all_files(sc);
        free(sc->sc_share_root);
        free(sc);
        smb2_set_opaque(smb2, NULL);
    }
    {
        const char *_u = smb2_get_user(smb2);
        SMBINFO("Client (%s) logged off", _u ? _u : "anonymous");
    }
    SMBTRACE("Logoff: cleaned up all file handles");
    return 0;
}

static int
smb_destruction(struct smb2_server *srvr, struct smb2_context *smb2)
{
    /* Called on abrupt disconnect without LOGOFF */
    {
        const char *_u2 = smb2_get_user(smb2);
        SMBINFO("Client (%s) disconnected (abrupt)", _u2 ? _u2 : "anonymous");
    }
    return smb_logoff(srvr, smb2);
}

/* ------------------------------------------------------------------ */
/* Handler: TREE CONNECT / DISCONNECT                                   */
/* ------------------------------------------------------------------ */

static const char *
smb_get_share_name(const char *path)
{
    if(path == NULL)
        return NULL;
    /* Skip leading backslashes */
    while(*path == '\\')
        path++;
    /* Find next backslash */
    const char *slash = strchr(path, '\\');
    if(slash == NULL)
        return path; /* if there is no server name, just share name */
    return slash + 1;
}

static int
smb_tree_connect(struct smb2_server *srvr, struct smb2_context *smb2,
                 struct smb2_tree_connect_request *req,
                 struct smb2_tree_connect_reply *rep)
{
    if(req == NULL || rep == NULL)
        return -1;
    rep->share_type     = SMB2_SHARE_TYPE_DISK;
    rep->maximal_access = 0x101f01ff;
    rep->share_flags    = 0;
    rep->capabilities   = 0;
    const char *path_utf8 = req->path ? smb2_utf16_to_utf8(req->path, req->path_length / 2) : NULL;
    const char *share = smb_get_share_name(path_utf8);
    const char *expected = smb_share_name ? smb_share_name : "share";
    if(share == NULL || strcmp(share, expected) != 0) {
        SMBTRACE("Tree connect: bad network name '%s' (expected '%s')", share ? share : "?", expected);
        free((void *)path_utf8);
        return SMB2_STATUS_BAD_NETWORK_NAME;
    }
    SMBTRACE("Tree connect: share_type=DISK access=0x%08x path='%s'",
             rep->maximal_access, path_utf8 ? path_utf8 : "?");
    free((void *)path_utf8);
    return 0;
}

static int
smb_tree_disconnect(struct smb2_server *srvr, struct smb2_context *smb2,
                    const uint32_t tree_id)
{
    SMBTRACE("Tree disconnect: tree_id=%u", tree_id);
    return 0;
}

/* ------------------------------------------------------------------ */
/* Handler: CREATE                                                      */
/* ------------------------------------------------------------------ */

static int
smb_create(struct smb2_server *srvr, struct smb2_context *smb2,
           struct smb2_create_request *req,
           struct smb2_create_reply *rep)
{
    if(req == NULL || rep == NULL)
        return SMB2_STATUS_INVALID_PARAMETER;
    smb_connection_t *sc = smb2_get_opaque(smb2);
    if(sc == NULL)
        return SMB2_STATUS_INTERNAL_ERROR;

    /*
     * req->name is UTF-8, already decoded by libsmb2.
     * An empty/NULL name means the share root itself.
     */
    char *path = smb_build_path(sc, req->name);
    if(path == NULL)
        return SMB2_STATUS_INSUFFICIENT_RESOURCES;

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

    struct fa_stat fs;
    char errbuf[256];
    int exists = !vfs_stat(path, &fs, errbuf, sizeof(errbuf));
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
        int want_write = !!(req->desired_access & (SMB2_FILE_WRITE_DATA |
                                                   SMB2_FILE_APPEND_DATA |
                                                   SMB2_FILE_WRITE_ATTRIBUTES));

        if(!exists || want_write) {
            flags |= FA_WRITE;
        }

        fe->fa_fh = vfs_open(path, errbuf, sizeof(errbuf), flags);
        if(fe->fa_fh == NULL) {
            SMBTRACE("Create: open failed for '%s': %s", path, errbuf);
            smb_free_file(sc, fe);
            return SMB2_STATUS_ACCESS_DENIED;
        }

        // Handle overwrite/supersede truncation via fa_ftruncate
        if(exists && (req->create_disposition == SMB2_FILE_OVERWRITE ||
                      req->create_disposition == SMB2_FILE_OVERWRITE_IF ||
                      req->create_disposition == SMB2_FILE_SUPERSEDE)) {
            if(fa_ftruncate(fe->fa_fh, 0) < 0) {
                smb_free_file(sc, fe);
                return SMB2_STATUS_ACCESS_DENIED;
            }
            fe->size = 0;
        } else {
            fe->size = exists ? fs.fs_size : 0;
        }
    }

#ifndef SMB2_FILE_SUPERSEDED
#define SMB2_FILE_SUPERSEDED 0
#endif
#ifndef SMB2_FILE_OVERWRITTEN
#define SMB2_FILE_OVERWRITTEN 3
#endif

    memcpy(rep->file_id, fe->file_id, SMB2_FD_SIZE);
    rep->file_attributes = is_dir ? SMB2_FILE_ATTRIBUTE_DIRECTORY
                                  : SMB2_FILE_ATTRIBUTE_NORMAL;

    if(exists) {
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

    struct smb2_timeval tv;
    tv.tv_sec = exists ? fs.fs_mtime : time(NULL);
    tv.tv_usec = 0;
    uint64_t win_time = smb2_timeval_to_win(&tv);

    rep->creation_time    = win_time;
    rep->last_access_time = win_time;
    rep->last_write_time  = win_time;
    rep->change_time      = win_time;

    SMBTRACE("Create OK: '%s' %s size=%lld delete_on_close=%d",
             path, is_dir ? "DIR" : "FILE", (long long)fe->size, fe->delete_on_close);
    return 0;
}

/* ------------------------------------------------------------------ */
/* Handler: CLOSE                                                       */
/* ------------------------------------------------------------------ */

static int
smb_close(struct smb2_server *srvr, struct smb2_context *smb2,
          struct smb2_close_request *req,
          struct smb2_close_reply *rep)
{
    if(req == NULL || rep == NULL)
        return SMB2_STATUS_INVALID_PARAMETER;
    smb_connection_t *sc = smb2_get_opaque(smb2);
    if(sc == NULL)
        return SMB2_STATUS_INTERNAL_ERROR;

    smb_file_entry_t *fe = smb_find_file(sc, req->file_id);
    if(fe == NULL)
        return SMB2_STATUS_FILE_CLOSED;

    memset(rep, 0, sizeof(*rep));

    if(fe->delete_on_close && fe->path) {
        char errbuf[256];
        SMBINFO("Delete-on-close: '%s' (%s)", fe->path, fe->is_dir ? "dir" : "file");
        SMBTRACE("Close+delete executing unlink/rmdir");
        if(fe->is_dir)
            vfs_rmdir(fe->path, errbuf, sizeof(errbuf));
        else
            vfs_unlink(fe->path, errbuf, sizeof(errbuf));
    }

    SMBTRACE("Close: '%s' (%s)", fe->path, fe->is_dir ? "DIR" : "FILE");
    smb_free_file(sc, fe);
    return 0;
}

/* ------------------------------------------------------------------ */
/* Handler: FLUSH                                                       */
/* ------------------------------------------------------------------ */

static int
smb_flush(struct smb2_server *srvr, struct smb2_context *smb2,
          struct smb2_flush_request *req)
{
    return 0;
}

/* ------------------------------------------------------------------ */
/* Handler: READ                                                        */
/* ------------------------------------------------------------------ */

static int
smb_read(struct smb2_server *srvr, struct smb2_context *smb2,
         struct smb2_read_request *req,
         struct smb2_read_reply *rep)
{
    if(req == NULL || rep == NULL)
        return SMB2_STATUS_INVALID_PARAMETER;
    smb_connection_t *sc = smb2_get_opaque(smb2);
    if(sc == NULL)
        return SMB2_STATUS_INTERNAL_ERROR;

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

static int
smb_write(struct smb2_server *srvr, struct smb2_context *smb2,
          struct smb2_write_request *req,
          struct smb2_write_reply *rep)
{
    if(req == NULL || rep == NULL)
        return SMB2_STATUS_INVALID_PARAMETER;
    smb_connection_t *sc = smb2_get_opaque(smb2);
    if(sc == NULL)
        return SMB2_STATUS_INTERNAL_ERROR;

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

static int
smb_query_directory(struct smb2_server *srvr, struct smb2_context *smb2,
                    struct smb2_query_directory_request *req,
                    struct smb2_query_directory_reply *rep)
{
    if(req == NULL || rep == NULL)
        return SMB2_STATUS_INVALID_PARAMETER;
    smb_connection_t *sc = smb2_get_opaque(smb2);
    if(sc == NULL)
        return SMB2_STATUS_INTERNAL_ERROR;

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
        fe->dir_idx  = 0;
    }

    if(fe->dir_done) {
        SMBTRACE("QueryDir: NO_MORE_FILES for '%s'", fe->path);
        rep->output_buffer        = NULL;
        rep->output_buffer_length = 0;
        return 0;
    }

    SMBTRACE("QueryDir: '%s' flags=0x%02x idx=%u class=%u", fe->path, req->flags, req->file_index, req->file_information_class);

    /* Lazy scan */
    if(fe->fa_dir == NULL) {
        char errbuf[256];
        fe->fa_dir = vfs_scandir(fe->path, errbuf, sizeof(errbuf));
        if(fe->fa_dir == NULL) {
            /* Empty or inaccessible dir — return 0 to signal no more files */
            fe->dir_done = 1;
            rep->output_buffer        = NULL;
            rep->output_buffer_length = 0;
            return 0;
        }
    }

    /*
     * Build the response buffer with all remaining entries.
     * libsmb2 sends STATUS_NO_MORE_FILES automatically when
     * output_buffer_length == 0.
     *
     * SMB2_RETURN_SINGLE_ENTRY: return only one entry per call.
     */
    int single_entry = !!(req->flags & SMB2_RETURN_SINGLE_ENTRY);

    size_t entry_size = PAD_TO_64BIT(sizeof(struct smb2_fileidbothdirectoryinformation));
    int capacity = single_entry ? 1 : 16;

    uint8_t *info = malloc(capacity * entry_size);
    if(info == NULL)
        return SMB2_STATUS_INSUFFICIENT_RESOURCES;

    uint32_t info_len = 0;
    int      n_added  = 0;
    int      skip     = fe->dir_idx;
    int      cur_match_idx = 0;
    uint32_t serialized_wire_len = 0;

    fa_dir_entry_t *fde;
    RB_FOREACH(fde, &fe->fa_dir->fd_entries, fde_link) {
        const char *name = rstr_get(fde->fde_filename);
        if(name == NULL)
            continue;

        if(req->name && req->name[0] != '\0') {
            if(!pattern_match(name, req->name))
                continue;
        }

        if(cur_match_idx < skip) {
            cur_match_idx++;
            continue;
        }
        cur_match_idx++;

        /* Check client buffer limit on wire */
        size_t name_bytes = strlen(name);
        size_t wire_hdr = (req->file_information_class == SMB2_FILE_ID_BOTH_DIRECTORY_INFORMATION) ? 104 : 80;
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
         * smb2_fileidbothdirectoryinformation.name is const char* (UTF-8).
         * libsmb2 re-encodes to UTF-16 when building the wire PDU.
         * The string pointer name remains valid as long as fe->fa_dir is alive.
         */
        struct smb2_fileidbothdirectoryinformation *fsb =
            (struct smb2_fileidbothdirectoryinformation *)(info + info_len);
        memset(fsb, 0, entry_size);

        fsb->file_index       = n_added;
        fsb->file_attributes  = entry_is_dir ? SMB2_FILE_ATTRIBUTE_DIRECTORY
                                              : SMB2_FILE_ATTRIBUTE_NORMAL;
        if(!entry_is_dir) {
            fsb->end_of_file      = fde->fde_stat.fs_size;
            fsb->allocation_size  = fde->fde_stat.fs_size;
        }

        time_t mtime = fde->fde_statdone ? fde->fde_stat.fs_mtime : fe->mtime;
        fsb->creation_time.tv_sec     = mtime;
        fsb->creation_time.tv_usec    = 0;
        fsb->last_access_time.tv_sec  = mtime;
        fsb->last_access_time.tv_usec = 0;
        fsb->last_write_time.tv_sec   = mtime;
        fsb->last_write_time.tv_usec  = 0;
        fsb->change_time.tv_sec       = mtime;
        fsb->change_time.tv_usec      = 0;

        fsb->file_name_length = name_bytes;  /* UTF-8 byte count */

        /*
         * Short name: leave empty (modern clients ignore this legacy field).
         */
        fsb->short_name_length = 0;

        fsb->name = name;

        info_len += entry_size;
        serialized_wire_len += wire_size;
        n_added++;
        fe->dir_idx++;

        if(single_entry)
            break;
    }

    if(n_added == 0) {
        /* No entries at all or first call on empty dir */
        free(info);
        fe->dir_done = 1;
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

    /* Mark as done only when we ran out of matching files (n_added == 0 above) */
    fe->dir_done = 0;

    rep->output_buffer        = info;
    rep->output_buffer_length = info_len;
    SMBTRACE("QueryDir: returning %d entries (%u bytes, wire=%u)", n_added, info_len, serialized_wire_len);
    return 0;
}

/* ------------------------------------------------------------------ */
/* Handler: QUERY_INFO                                                  */
/* ------------------------------------------------------------------ */

static int
smb_query_info(struct smb2_server *srvr, struct smb2_context *smb2,
               struct smb2_query_info_request *req,
               struct smb2_query_info_reply *rep)
{
    if(req == NULL || rep == NULL)
        return SMB2_STATUS_INVALID_PARAMETER;
    smb_connection_t *sc = smb2_get_opaque(smb2);
    if(sc == NULL)
        return SMB2_STATUS_INTERNAL_ERROR;

    smb_file_entry_t *fe = smb_find_file(sc, req->file_id);

    void    *info = NULL;
    int      len  = 0;

    if(req->info_type == SMB2_0_INFO_FILESYSTEM) {

        switch(req->file_info_class) {
        case SMB2_FILE_FS_SIZE_INFORMATION: {
            struct smb2_file_fs_size_info *fs = calloc(1, sizeof(*fs));
            if(!fs) return SMB2_STATUS_INSUFFICIENT_RESOURCES;
            /* Try to get real values via statvfs */
            struct statvfs sv;
            const char *real_path = fe ? fe->path : NULL;
            if(real_path && strncmp(real_path, "file://", 7) == 0)
                real_path += 7;
            if(real_path && statvfs(real_path, &sv) == 0) {
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
            const char *real_path = fe ? fe->path : NULL;
            if(real_path && strncmp(real_path, "file://", 7) == 0)
                real_path += 7;
            if(real_path && statvfs(real_path, &sv) == 0) {
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
            fs->creation_time.tv_sec     = fe->mtime;
            fs->creation_time.tv_usec    = 0;
            fs->last_access_time.tv_sec  = fe->mtime;
            fs->last_access_time.tv_usec = 0;
            fs->last_write_time.tv_sec   = fe->mtime;
            fs->last_write_time.tv_usec  = 0;
            fs->change_time.tv_sec       = fe->mtime;
            fs->change_time.tv_usec      = 0;
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
            fs->basic.creation_time.tv_sec      = fe->mtime;
            fs->basic.creation_time.tv_usec     = 0;
            fs->basic.last_access_time.tv_sec   = fe->mtime;
            fs->basic.last_access_time.tv_usec  = 0;
            fs->basic.last_write_time.tv_sec    = fe->mtime;
            fs->basic.last_write_time.tv_usec   = 0;
            fs->basic.change_time.tv_sec        = fe->mtime;
            fs->basic.change_time.tv_usec       = 0;
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
            fs->creation_time.tv_sec     = fe->mtime;
            fs->creation_time.tv_usec    = 0;
            fs->last_access_time.tv_sec  = fe->mtime;
            fs->last_access_time.tv_usec = 0;
            fs->last_write_time.tv_sec   = fe->mtime;
            fs->last_write_time.tv_usec  = 0;
            fs->change_time.tv_sec       = fe->mtime;
            fs->change_time.tv_usec      = 0;
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
    const uint16_t *utf16 = (const uint16_t *)(buf + 20);
    const char *utf8 = smb2_utf16_to_utf8(utf16, nchars);
    if(utf8 == NULL)
        return NULL;

    char *path = smb_build_path(sc, utf8);
    free((void *)utf8);
    return path;
}

static int
smb_set_info(struct smb2_server *srvr, struct smb2_context *smb2,
             struct smb2_set_info_request *req)
{
    if(req == NULL)
        return SMB2_STATUS_INVALID_PARAMETER;
    smb_connection_t *sc = smb2_get_opaque(smb2);
    if(sc == NULL)
        return SMB2_STATUS_INTERNAL_ERROR;

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
        }
        fe->size = (int64_t)eof;
        if(fe->pos > fe->size)
            fe->pos = fe->size;
        break;
    }
    default:
        return SMB2_STATUS_NOT_SUPPORTED;
    }

    return 0;
}

/* ------------------------------------------------------------------ */
/* Handler: IOCTL                                                       */
/* ------------------------------------------------------------------ */

static int
smb_ioctl(struct smb2_server *srvr, struct smb2_context *smb2,
          struct smb2_ioctl_request *req,
          struct smb2_ioctl_reply *rep)
{
    if(req == NULL || rep == NULL)
        return SMB2_STATUS_INVALID_PARAMETER;
    memset(rep, 0, sizeof(*rep));
    rep->ctl_code = req->ctl_code;
    memcpy(rep->file_id, req->file_id, SMB2_FD_SIZE);

    switch(req->ctl_code) {
    case SMB2_FSCTL_VALIDATE_NEGOTIATE_INFO: {
        SMBTRACE("IOCTL: VALIDATE_NEGOTIATE_INFO");
        static struct smb2_ioctl_validate_negotiate_info vni;
        memset(&vni, 0, sizeof(vni));
        vni.capabilities = 0;
        vni.security_mode = 0;
        uint8_t server_guid[16] = {0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88,
                                   0x99, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF, 0x00};
        memcpy(vni.guid, server_guid, 16);
        vni.dialect = smb2_get_dialect(smb2);

        rep->output = &vni;
        rep->output_count = sizeof(vni);
        return 0;
    }
    case SMB2_FSCTL_DFS_GET_REFERRALS:
    case SMB2_FSCTL_DFS_GET_REFERRALS_EX:
        SMBTRACE("IOCTL: DFS_GET_REFERRALS (not a DFS server)");
        return SMB2_STATUS_BAD_NETWORK_NAME;
    default:
        SMBTRACE("IOCTL: unsupported ctl_code=0x%08x", req->ctl_code);
        return SMB2_STATUS_NOT_SUPPORTED;
    }
}

/* ------------------------------------------------------------------ */
/* Handler stubs: CANCEL, ECHO, LOCK                                   */
/* ------------------------------------------------------------------ */

static int smb_cancel(struct smb2_server *s, struct smb2_context *c) { return 0; }
static int smb_echo(struct smb2_server *s, struct smb2_context *c)   { return 0; }
static int smb_lock(struct smb2_server *s, struct smb2_context *c,
                    struct smb2_lock_request *r)                      { if(r == NULL) return SMB2_STATUS_INVALID_PARAMETER; return 0; }

/* ------------------------------------------------------------------ */
/* Handler table                                                        */
/* ------------------------------------------------------------------ */

static struct smb2_server_request_handlers smb_handlers = {
    .destruction_event      = smb_destruction,
    .authorize_user         = smb_authorize,
    .session_established    = smb_session_established,
    .logoff_cmd             = smb_logoff,
    .tree_connect_cmd       = smb_tree_connect,
    .tree_disconnect_cmd    = smb_tree_disconnect,
    .create_cmd             = smb_create,
    .close_cmd              = smb_close,
    .flush_cmd              = smb_flush,
    .read_cmd               = smb_read,
    .write_cmd              = smb_write,
    .oplock_break_cmd       = NULL,
    .lease_break_cmd        = NULL,
    .lock_cmd               = smb_lock,
    .ioctl_cmd              = smb_ioctl,
    .cancel_cmd             = smb_cancel,
    .echo_cmd               = smb_echo,
    .query_directory_cmd    = smb_query_directory,
    .change_notify_cmd      = NULL,
    .query_info_cmd         = smb_query_info,
    .set_info_cmd           = smb_set_info,
};

/* ------------------------------------------------------------------ */
/* Server thread                                                        */
/* ------------------------------------------------------------------ */

/*
 * smb2_serve_port() blocks in its own select() loop and binds the port
 * itself via smb2_bind_and_listen(). We must NOT pre-bind the socket.
 * It handles multiple clients internally via a global smb2 context list.
 *
 * max_connections=0 means unlimited (serve_port uses SOMAXCONN internally).
 */
static void
reset_thread_running(void *aux)
{
    smb_thread_running = 0;
}

static void *
smb_server_thread(void *aux)
{
    struct smb2_server *srv = aux;
    SMBINFO("Listening on port %u (anonymous=%s)",
            srv->port, srv->allow_anonymous ? "yes" : "no");
    usage_event("SMB Server", 1, NULL);

    int err = smb2_serve_port(srv, 0, NULL, NULL);
    SMBINFO("Server stopped (err=%d)", err);
    free(srv);
    asyncio_run_task(reset_thread_running, NULL);
    return NULL;
}

/* ------------------------------------------------------------------ */
/* Enable / disable (called from asyncio courier)                       */
/* ------------------------------------------------------------------ */

static void
enable_disable(void)
{
    if(smb_enable && smb_port > 0) {
        if(smb_thread_running)
            return;   /* already up; port changes require restart of Movian */

        struct smb2_server *srv = calloc(1, sizeof(*srv));
        if(srv == NULL) {
            SMBINFO("Failed to allocate SMB2 server context");
            return;
        }
        srv->handlers        = &smb_handlers;
        srv->signing_enabled = 0;
        srv->allow_anonymous = (smb_username == NULL) ? 1 : 0;
        srv->port            = (uint16_t)smb_port;

        hts_thread_create_detached("smb2-server", smb_server_thread,
                                   srv, THREAD_PRIO_MODEL);
        smb_thread_running = 1;
        SMBINFO("SMB2 server starting on port %d (anonymous=%s)",
                smb_port, smb_username == NULL ? "yes" : "no");
    }
    /* Stopping a running smb2_serve_port() requires closing the listen fd
     * which is private to libsmb2 after the call — not exposed.
     * For Movian's use case (toggle in settings) a restart of the app is
     * acceptable. Log a note if disabled while running. */
    else if(!smb_enable && smb_thread_running) {
        SMBINFO("SMB2 server disable requested; restart required to stop listener");
    }
}

static int enable_disable_pending = 0;

static void
deferred_enable_disable(void *aux)
{
    enable_disable_pending = 0;
    enable_disable();
}

static void
queue_enable_disable(void)
{
    if(!enable_disable_pending) {
        enable_disable_pending = 1;
        asyncio_run_task(deferred_enable_disable, NULL);
    }
}

static void set_enable(void *opaque, int v)
{
    smb_enable = v;
    queue_enable_disable();
}

static void set_port(void *opaque, const char *str)
{
    smb_port = atoi(str);
    /* Port change only takes effect on next start — note in trace */
    SMBTRACE("Port changed to %d%s", smb_port,
             smb_thread_running ? " (restart required to apply)" : "");
    queue_enable_disable();
}

static void set_username(void *opaque, const char *str)
{
    mystrset(&smb_username, (str && *str) ? str : NULL);
    SMBINFO("Auth mode: %s", smb_username ? "password-protected" : "anonymous");
}

static void set_password(void *opaque, const char *str)
{
    mystrset(&smb_password, (str && *str) ? str : NULL);
}

static void set_share_name(void *opaque, const char *str)
{
    mystrset(&smb_share_name, str);
}

static void set_share_root(void *opaque, const char *str)
{
    mystrset(&smb_share_root, str);
}

/* ------------------------------------------------------------------ */
/* Init                                                                 */
/* ------------------------------------------------------------------ */

void
smb_server_init(void)
{
    settings_create_separator(gconf.settings_network, _p("SMB server"));

    setting_create(SETTING_BOOL, gconf.settings_network,
                   SETTINGS_INITIAL_UPDATE,
                   SETTING_TITLE(_p("Enable SMB2 server")),
                   SETTING_VALUE(0),
                   SETTING_CALLBACK(set_enable, NULL),
                   SETTING_STORE("smbserver", "enable"),
                   SETTING_COURIER(asyncio_courier),
                   NULL);

    setting_create(SETTING_STRING, gconf.settings_network,
                   SETTINGS_INITIAL_UPDATE,
                   SETTING_TITLE(_p("Server TCP port")),
                   SETTING_VALUE("1445"),
                   SETTING_CALLBACK(set_port, NULL),
                   SETTING_STORE("smbserver", "port"),
                   SETTING_COURIER(asyncio_courier),
                   NULL);

    setting_create(SETTING_STRING, gconf.settings_network,
                   SETTINGS_INITIAL_UPDATE,
                   SETTING_TITLE(_p("Username (empty = allow anonymous)")),
                   SETTING_VALUE(""),
                   SETTING_CALLBACK(set_username, NULL),
                   SETTING_STORE("smbserver", "username"),
                   SETTING_COURIER(asyncio_courier),
                   NULL);

    setting_create(SETTING_STRING, gconf.settings_network,
                   SETTINGS_INITIAL_UPDATE | SETTINGS_PASSWORD,
                   SETTING_TITLE(_p("Password")),
                   SETTING_VALUE(""),
                   SETTING_CALLBACK(set_password, NULL),
                   SETTING_STORE("smbserver", "password"),
                   SETTING_COURIER(asyncio_courier),
                   NULL);

    setting_create(SETTING_STRING, gconf.settings_network,
                   SETTINGS_INITIAL_UPDATE,
                   SETTING_TITLE(_p("Share name")),
                   SETTING_VALUE("share"),
                   SETTING_CALLBACK(set_share_name, NULL),
                   SETTING_STORE("smbserver", "share"),
                   SETTING_COURIER(asyncio_courier),
                   NULL);

    setting_create(SETTING_STRING, gconf.settings_network,
                   SETTINGS_INITIAL_UPDATE,
                   SETTING_TITLE(_p("Share root path")),
                   SETTING_VALUE("/"),
                   SETTING_CALLBACK(set_share_root, NULL),
                   SETTING_STORE("smbserver", "root"),
                   SETTING_COURIER(asyncio_courier),
                   NULL);
}

INITME(INIT_GROUP_ASYNCIO, smb_server_init, NULL, 0);
