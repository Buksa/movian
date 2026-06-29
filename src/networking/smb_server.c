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

#ifndef SMB2_FILE_OPENED
#define SMB2_FILE_OPENED 1
#endif
#ifndef SMB2_FILE_CREATED
#define SMB2_FILE_CREATED 2
#endif
#ifndef SMB2_FILE_SUPERSEDED
#define SMB2_FILE_SUPERSEDED 0
#endif
#ifndef SMB2_FILE_OVERWRITTEN
#define SMB2_FILE_OVERWRITTEN 3
#endif

/* ------------------------------------------------------------------ */
/* Tracing                                                              */
/* ------------------------------------------------------------------ */

/*
 * Two log levels, matching Movian convention (see SSDP, STPP, etc.):
 *   SMBINFO  — always visible (TRACE_INFO):  server lifecycle, connections, auth results
 *   SMBTRACE — gated on enable_smb_debug (TRACE_DEBUG): per-request detail
 */
#define SMBINFO(x, ...)  tracelog(0, TRACE_INFO,  "SMB2-SERVER", x, ##__VA_ARGS__)
#define SMBTRACE(x, ...) do { \
    if(gconf.enable_smb_debug) \
       tracelog(0, TRACE_DEBUG, "SMB2-SERVER", x, ##__VA_ARGS__); \
} while(0)
/* ------------------------------------------------------------------ */
/* File handle table                                                    */
/* ------------------------------------------------------------------ */

#define SMB2_MAX_FILES 64
#define SMB2_MAX_TREES 16

/*
 * We encode handle index (1-based) + generation counter into 8 bytes of
 * the 16-byte file_id so that smb_find_file() can validate handles quickly
 * without iterating the full table.
 *
 * Layout: file_id[0..3] = index (big-endian), file_id[4..7] = generation,
 *         file_id[8..15] = 0.
 * A NULL fe->path means the slot is free.
 */

typedef struct {
    uint8_t     file_id[SMB2_FD_SIZE];
    fa_handle_t *fa_fh;            /* non-NULL if regular file open     */
    fa_dir_t    *fa_dir;           /* non-NULL if directory open        */
    char        *path;             /* absolute vfs path (malloc'd)      */
    int64_t     size;              /* cached file size (from stat)      */
    int64_t     pos;               /* cached file offset position       */
    time_t      mtime;             /* mtime timestamp                   */
    uint32_t    tree_id;           /* tree that owns this handle        */
    int         is_dir;
    int         is_pipe;
    int         delete_on_close;   /* set from SMB2_FILE_DELETE_ON_CLOSE */
    int         dir_done;          /* set after first full dir listing  */
    fa_dir_entry_t *next_fde;      /* next entry to process in directory scan */
} smb_file_entry_t;

typedef struct {
    uint32_t tree_id;
    int is_ipc;
} smb_tree_entry_t;

typedef enum {
    SMB_TREE_UNKNOWN,
    SMB_TREE_DISK,
    SMB_TREE_IPC,
} smb_tree_type_t;

typedef struct smb_connection {
    char                *sc_share_root;   /* root path for this share   */
    smb_file_entry_t     sc_files[SMB2_MAX_FILES];
    smb_tree_entry_t     sc_trees[SMB2_MAX_TREES];
    uint32_t             sc_gen;          /* generation counter         */
    uint32_t             sc_next_tree_id;
    uint32_t             sc_session_generation;
    uint8_t              sc_related_file_id[SMB2_FD_SIZE];
    int                  sc_related_file_valid;
    struct smb2_ioctl_validate_negotiate_info sc_vni; /* for validate negotiate info ioctl */
    void                *sc_ioctl_output;
} smb_connection_t;

typedef struct {
    struct smb2_server srv;
    char *username;
    char *password;
    char *share_name;
    char *share_root;
    uint32_t session_generation;
} smb_server_t;

_Static_assert(offsetof(smb_server_t, srv) == 0,
               "srv must be the first member of smb_server_t for safe pointer casting");

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
static hts_mutex_t    smb_server_mutex;
static smb_server_t  *smb_active_server = NULL;

static char *
smb_strdup_or_null(const char *str)
{
    return str != NULL ? strdup(str) : NULL;
}

static void
smb_apply_active_auth(void)
{
    char *username = smb_strdup_or_null(smb_username);
    char *password = smb_strdup_or_null(smb_password);

    if((smb_username != NULL && username == NULL) ||
       (smb_password != NULL && password == NULL)) {
        free(username);
        free(password);
        SMBINFO("Unable to apply SMB2 auth settings: out of memory");
        return;
    }

    hts_mutex_lock(&smb_server_mutex);
    smb_server_t *srv = smb_active_server;
    if(srv == NULL) {
        hts_mutex_unlock(&smb_server_mutex);
        free(username);
        free(password);
        return;
    }

    int auth_changed =
        (srv->username == NULL) != (username == NULL) ||
        (srv->username != NULL && username != NULL &&
         strcmp(srv->username, username)) ||
        (srv->password == NULL) != (password == NULL) ||
        (srv->password != NULL && password != NULL &&
         strcmp(srv->password, password));
    free(srv->username);
    free(srv->password);
    srv->username = username;
    srv->password = password;
    if(auth_changed)
        srv->session_generation++;
    srv->srv.signing_enabled = srv->username != NULL ? 1 : 0;
    srv->srv.allow_anonymous = srv->username == NULL ? 1 : 0;
    hts_mutex_unlock(&smb_server_mutex);
}

static void
smb_apply_active_share_name(void)
{
    char *share_name = smb_strdup_or_null(smb_share_name);
    if(smb_share_name != NULL && share_name == NULL) {
        SMBINFO("Unable to apply SMB2 share name: out of memory");
        return;
    }

    hts_mutex_lock(&smb_server_mutex);
    smb_server_t *srv = smb_active_server;
    if(srv == NULL) {
        hts_mutex_unlock(&smb_server_mutex);
        free(share_name);
        return;
    }

    int share_name_changed =
        (srv->share_name == NULL) != (share_name == NULL) ||
        (srv->share_name != NULL && share_name != NULL &&
         strcmp(srv->share_name, share_name));
    free(srv->share_name);
    srv->share_name = share_name;
    if(share_name_changed)
        srv->session_generation++;
    hts_mutex_unlock(&smb_server_mutex);
}

static void
smb_apply_active_share_root(void)
{
    char *share_root = smb_strdup_or_null(smb_share_root);
    if(smb_share_root != NULL && share_root == NULL) {
        SMBINFO("Unable to apply SMB2 share root: out of memory");
        return;
    }

    hts_mutex_lock(&smb_server_mutex);
    smb_server_t *srv = smb_active_server;
    if(srv == NULL) {
        hts_mutex_unlock(&smb_server_mutex);
        free(share_root);
        return;
    }

    int share_root_changed =
        (srv->share_root == NULL) != (share_root == NULL) ||
        (srv->share_root != NULL && share_root != NULL &&
         strcmp(srv->share_root, share_root));
    free(srv->share_root);
    srv->share_root = share_root;
    if(share_root_changed)
        srv->session_generation++;
    hts_mutex_unlock(&smb_server_mutex);
}

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

static char *
smb_normalize_share_root(const char *root)
{
    if(root == NULL || root[0] == '\0' || !strcmp(root, "/"))
        return strdup("vfs:///");

    if(!strncmp(root, "vfs://", 6) || !strncmp(root, "file://", 7))
        return strdup(root);

    size_t len = strlen(root) + sizeof("file://");
    char *url = malloc(len);
    if(url == NULL)
        return NULL;

    snprintf(url, len, "file://%s", root);
    return url;
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

static int
smb_file_id_is_compound(const uint8_t *file_id)
{
    for(int i = 0; i < SMB2_FD_SIZE; i++) {
        if(file_id[i] != 0xff)
            return 0;
    }
    return 1;
}

/* Returns NULL if file_id is invalid or slot is free */
static smb_file_entry_t *
smb_find_file(smb_connection_t *sc, const uint8_t *file_id)
{
    if(smb_file_id_is_compound(file_id)) {
        if(!sc->sc_related_file_valid)
            return NULL;
        file_id = sc->sc_related_file_id;
    }

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
    if(sc->sc_related_file_valid &&
       !memcmp(sc->sc_related_file_id, fe->file_id, SMB2_FD_SIZE)) {
        memset(sc->sc_related_file_id, 0, sizeof(sc->sc_related_file_id));
        sc->sc_related_file_valid = 0;
    }

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

static int
smb_vfs_error_to_ntstatus(int status, const char *reason);

static int
smb_close_file_entry(smb_connection_t *sc, smb_file_entry_t *fe)
{
    int ntstatus = 0;

    if(fe->delete_on_close && fe->path) {
        char errbuf[256];
        int delete_status;
        SMBINFO("Delete-on-close: '%s' (%s)", fe->path, fe->is_dir ? "dir" : "file");
        SMBTRACE("Close+delete executing unlink/rmdir");
        if(fe->is_dir)
            delete_status = vfs_rmdir(fe->path, errbuf, sizeof(errbuf));
        else
            delete_status = vfs_unlink(fe->path, errbuf, sizeof(errbuf));
        if(delete_status) {
            ntstatus = smb_vfs_error_to_ntstatus(delete_status, errbuf);
            SMBINFO("Delete-on-close FAILED: '%s': %s", fe->path, errbuf);
        }
    }

    SMBTRACE("Close: '%s' (%s)", fe->path,
             fe->is_pipe ? "PIPE" : fe->is_dir ? "DIR" : "FILE");
    smb_free_file(sc, fe);
    return ntstatus;
}

static void
smb_close_all_files(smb_connection_t *sc)
{
    int n_closed = 0;
    for(int i = 0; i < SMB2_MAX_FILES; i++) {
        smb_file_entry_t *fe = &sc->sc_files[i];
        if(fe->path != NULL) {
            SMBTRACE("Cleanup: closing leaked handle '%s'", fe->path);
            smb_close_file_entry(sc, fe);
            n_closed++;
        }
    }
    if(n_closed)
        SMBTRACE("Cleanup: closed %d leaked file handle(s)", n_closed);
}

static int
smb_close_tree_files(smb_connection_t *sc, uint32_t tree_id)
{
    int n_closed = 0;
    int first_status = 0;

    for(int i = 0; i < SMB2_MAX_FILES; i++) {
        smb_file_entry_t *fe = &sc->sc_files[i];
        if(fe->path != NULL && fe->tree_id == tree_id) {
            SMBTRACE("Tree disconnect: closing handle '%s' for tree_id=0x%08x",
                     fe->path, tree_id);
            int status = smb_close_file_entry(sc, fe);
            if(first_status == 0 && status != 0)
                first_status = status;
            n_closed++;
        }
    }
    if(n_closed)
        SMBTRACE("Tree disconnect: closed %d handle(s) for tree_id=0x%08x",
                 n_closed, tree_id);
    return first_status;
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

static int
smb_vfs_error_to_ntstatus(int err, const char *errbuf)
{
    if(err != -1 || errbuf == NULL)
        return smb_errno_to_ntstatus(err);

    if(!strcmp(errbuf, strerror(ENOTEMPTY)))
        return SMB2_STATUS_DIRECTORY_NOT_EMPTY;
    if(!strcmp(errbuf, strerror(ENOENT)))
        return SMB2_STATUS_OBJECT_NAME_NOT_FOUND;
    if(!strcmp(errbuf, strerror(EACCES)) || !strcmp(errbuf, strerror(EPERM)))
        return SMB2_STATUS_ACCESS_DENIED;
    if(!strcmp(errbuf, strerror(EROFS)))
        return SMB2_STATUS_MEDIA_WRITE_PROTECTED;

    return smb_errno_to_ntstatus(err);
}

/* ------------------------------------------------------------------ */
/* Handler: AUTHORIZE                                                   */
/* ------------------------------------------------------------------ */

static int
smb_authorize(struct smb2_server *srvr, struct smb2_context *smb2,
              const char *user, const char *domain, const char *workstation)
{
    smb_server_t *srv = (smb_server_t *)srvr;
    char *expected_user = NULL;
    char *expected_password = NULL;
    int password_mode = 0;
    int password_present = 0;
    /*
     * libsmb2 server NTLM auth flow (verified against ntlmssp.c):
     *
     * 1. This callback is called with the username from the AUTHENTICATION_MESSAGE.
     * 2. We must call smb2_set_password(smb2, password) to give libsmb2 the
     *    expected password — it will then verify NTLMv2 via HMAC-MD5 internally.
     * 3. Return 0 to proceed, -1 to reject immediately.
     *
     * Auth matrix:
     *   srv->username == NULL → anonymous mode: accept everyone, no password check.
     *   srv->username != NULL → require matching user + password.
     *     - If user doesn't match → return -1.
     *     - If user matches → call smb2_set_password() so NTLMv2 can be verified.
     *     - allow_anonymous on the server struct must be 0 to enforce this.
     */
    hts_mutex_lock(&smb_server_mutex);
    if(srv->username != NULL) {
        password_mode = 1;
        password_present = srv->password != NULL;
        expected_user = strdup(srv->username);
        expected_password = smb_strdup_or_null(srv->password);
    }
    hts_mutex_unlock(&smb_server_mutex);

    if(password_mode && (expected_user == NULL ||
                         (password_present && expected_password == NULL))) {
        SMBINFO("Auth: rejected connection while applying credentials");
        free(expected_user);
        free(expected_password);
        return -1;
    }

    if(expected_user == NULL) {
        /* Anonymous mode: accept any connection */
        SMBINFO("Auth: anonymous access granted");
        SMBTRACE("Auth: anonymous details (user='%s', domain='%s', workstation='%s')",
                 user ? user : "guest", domain ? domain : "", workstation ? workstation : "");
        if(user)
            smb2_set_user(smb2, user);
        return 0;
    }

    /* Password-protected mode */
    if(user == NULL || user[0] == '\0') {
        /* Client didn't send a username — reject */
        SMBINFO("Auth: rejected anonymous connection (server requires credentials)");
        free(expected_user);
        free(expected_password);
        return -1;
    }

    if(strcmp(user, expected_user) != 0) {
        SMBINFO("Auth: rejected unknown user '%s'", user);
        free(expected_user);
        free(expected_password);
        return -1;
    }

    /*
     * Username matches. Keep the context identity in sync with the
     * authenticated NTLM message, then provide the password so libsmb2 can
     * verify NTLMv2. These setters make their own copies.
     */
    smb2_set_user(smb2, user);
    if(domain != NULL && domain[0] != '\0')
        smb2_set_domain(smb2, domain);
    smb2_set_password(smb2, expected_password ? expected_password : "");
    SMBTRACE("Auth: user '%s' domain='%s' wks='%s' → NTLMv2 pending",
             user, domain ? domain : "", workstation ? workstation : "");
    SMBINFO("Auth: NTLMv2 challenge for user '%s'", user);
    free(expected_user);
    free(expected_password);
    return 0;
}

/* ------------------------------------------------------------------ */
/* Handler: SESSION_ESTABLISHED                                         */
/* ------------------------------------------------------------------ */

static int
smb_session_established(struct smb2_server *srvr, struct smb2_context *smb2)
{
    smb_server_t *srv = (smb_server_t *)srvr;
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
    hts_mutex_lock(&smb_server_mutex);
    char *share_root = smb_strdup_or_null(srv->share_root);
    uint32_t session_generation = srv->session_generation;
    hts_mutex_unlock(&smb_server_mutex);
    sc->sc_share_root = smb_normalize_share_root(share_root);
    free(share_root);
    if(sc->sc_share_root == NULL) {
        free(sc);
        return -1;
    }
    sc->sc_next_tree_id = 0x1000;
    sc->sc_session_generation = session_generation;
    smb2_set_opaque(smb2, sc);
    return 0;
}

/* ------------------------------------------------------------------ */
/* Handler: LOGOFF / DESTRUCTION                                        */
/* ------------------------------------------------------------------ */

static int
smb_cleanup_session(struct smb2_context *smb2, const char *reason)
{
    smb_connection_t *sc = smb2_get_opaque(smb2);
    if(sc) {
        smb_close_all_files(sc);
        free(sc->sc_ioctl_output);
        free(sc->sc_share_root);
        free(sc);
        smb2_set_opaque(smb2, NULL);
        const char *_u = smb2_get_user(smb2);
        SMBINFO("Client (%s) %s", _u ? _u : "anonymous", reason);
    }
    return 0;
}

static int
smb_reject_stale_session(struct smb2_server *srvr, struct smb2_context *smb2)
{
    smb_connection_t *sc = smb2_get_opaque(smb2);
    if(sc == NULL)
        return SMB2_STATUS_INTERNAL_ERROR;

    smb_server_t *srv = (smb_server_t *)srvr;
    hts_mutex_lock(&smb_server_mutex);
    int stale = sc->sc_session_generation != srv->session_generation;
    hts_mutex_unlock(&smb_server_mutex);

    if(!stale)
        return 0;

    SMBINFO("Closing stale SMB2 session after auth settings changed");
    smb_cleanup_session(smb2, "closed after auth settings changed");
    smb2_close_context(smb2);
    return SMB2_STATUS_ACCESS_DENIED;
}

static int
smb_logoff(struct smb2_server *srvr, struct smb2_context *smb2)
{
    return smb_cleanup_session(smb2, "logged off");
}

static int
smb_destruction(struct smb2_server *srvr, struct smb2_context *smb2)
{
    /* Called on abrupt disconnect without LOGOFF */
    return smb_cleanup_session(smb2, "disconnected (abrupt)");
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
smb_is_ipc_share(const char *share)
{
    return share != NULL && !strcasecmp(share, "IPC$");
}

static int
smb_is_srvsvc_pipe(const char *name)
{
    if(name == NULL)
        return 0;

    while(*name == '/' || *name == '\\')
        name++;
    if(!strncasecmp(name, "PIPE", 4) &&
       (name[4] == '/' || name[4] == '\\')) {
        name += 5;
        while(*name == '/' || *name == '\\')
            name++;
    }
    return !strcasecmp(name, "srvsvc");
}

static uint32_t
smb_register_tree(smb_connection_t *sc, int is_ipc)
{
    for(int i = 0; i < SMB2_MAX_TREES; i++) {
        smb_tree_entry_t *tree = &sc->sc_trees[i];
        if(tree->tree_id == 0) {
            uint32_t tree_id = sc->sc_next_tree_id++;
            if(sc->sc_next_tree_id == 0)
                sc->sc_next_tree_id = 0x1000;
            tree->tree_id = tree_id;
            tree->is_ipc = is_ipc;
            return tree_id;
        }
    }
    return 0;
}

static void
smb_unregister_tree(smb_connection_t *sc, uint32_t tree_id)
{
    for(int i = 0; i < SMB2_MAX_TREES; i++) {
        smb_tree_entry_t *tree = &sc->sc_trees[i];
        if(tree->tree_id == tree_id) {
            memset(tree, 0, sizeof(*tree));
            return;
        }
    }
}

static smb_tree_type_t
smb_tree_lookup(smb_connection_t *sc, uint32_t tree_id)
{
    for(int i = 0; i < SMB2_MAX_TREES; i++) {
        smb_tree_entry_t *tree = &sc->sc_trees[i];
        if(tree->tree_id == tree_id)
            return tree->is_ipc ? SMB_TREE_IPC : SMB_TREE_DISK;
    }
    return SMB_TREE_UNKNOWN;
}

static int
smb_tree_connect(struct smb2_server *srvr, struct smb2_context *smb2,
                 struct smb2_tree_connect_request *req,
                 struct smb2_tree_connect_reply *rep)
{
    if(req == NULL || rep == NULL)
        return -1;
    int auth_status = smb_reject_stale_session(srvr, smb2);
    if(auth_status)
        return auth_status;
    smb_connection_t *sc = smb2_get_opaque(smb2);
    if(sc == NULL)
        return SMB2_STATUS_INTERNAL_ERROR;
    smb_server_t *srv = (smb_server_t *)srvr;
    rep->maximal_access = 0x101f01ff;
    rep->share_flags    = 0;
    rep->capabilities   = 0;
    /* Note: smb2_utf16_to_utf8 returns a malloc'd string that must be freed */
    const char *path_utf8 = req->path ? smb2_utf16_to_utf8(req->path, req->path_length / 2) : NULL;
    const char *share = smb_get_share_name(path_utf8);
    hts_mutex_lock(&smb_server_mutex);
    char *expected = strdup(srv->share_name ? srv->share_name : "share");
    hts_mutex_unlock(&smb_server_mutex);
    if(expected == NULL) {
        free((void *)path_utf8);
        return SMB2_STATUS_INSUFFICIENT_RESOURCES;
    }
    if(smb_is_ipc_share(share)) {
        rep->tree_id = smb_register_tree(sc, 1);
        if(rep->tree_id == 0) {
            free(expected);
            free((void *)path_utf8);
            return SMB2_STATUS_INSUFFICIENT_RESOURCES;
        }
        rep->share_type = SMB2_SHARE_TYPE_PIPE;
        SMBTRACE("Tree connect: share_type=PIPE tree_id=0x%08x access=0x%08x path='%s'",
                 rep->tree_id, rep->maximal_access, path_utf8 ? path_utf8 : "?");
        free(expected);
        free((void *)path_utf8);
        return 0;
    }
    if(share == NULL || strcasecmp(share, expected) != 0) {
        SMBTRACE("Tree connect: bad network name '%s' (expected '%s')", share ? share : "?", expected);
        free(expected);
        free((void *)path_utf8);
        return SMB2_STATUS_BAD_NETWORK_NAME;
    }
    rep->tree_id = smb_register_tree(sc, 0);
    if(rep->tree_id == 0) {
        free(expected);
        free((void *)path_utf8);
        return SMB2_STATUS_INSUFFICIENT_RESOURCES;
    }
    rep->share_type = SMB2_SHARE_TYPE_DISK;
    SMBTRACE("Tree connect: share_type=DISK tree_id=0x%08x access=0x%08x path='%s'",
             rep->tree_id, rep->maximal_access, path_utf8 ? path_utf8 : "?");
    free(expected);
    free((void *)path_utf8);
    return 0;
}

static int
smb_tree_disconnect(struct smb2_server *srvr, struct smb2_context *smb2,
                    const uint32_t tree_id)
{
    smb_connection_t *sc = smb2_get_opaque(smb2);
    int status = 0;
    if(sc != NULL) {
        status = smb_close_tree_files(sc, tree_id);
        smb_unregister_tree(sc, tree_id);
    }
    SMBTRACE("Tree disconnect: tree_id=0x%08x status=0x%08x", tree_id, status);
    return status;
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

    uint64_t win_time = smb_time_to_win(fe->mtime);
    rep->creation_time = rep->last_access_time = rep->last_write_time = rep->change_time = win_time;

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
            SMBINFO("QueryDir FAILED: '%s': %s", fe->path, errbuf);
            return smb_vfs_error_to_ntstatus(-1, errbuf);
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

        if(req->name && req->name[0] != '\0') {
            if(!pattern_match(name, req->name))
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

static int
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
    smb_connection_t *sc = smb2_get_opaque(smb2);
    if(sc == NULL)
        return SMB2_STATUS_INTERNAL_ERROR;
    int auth_status = smb_reject_stale_session(srvr, smb2);
    if(auth_status)
        return auth_status;

    memset(rep, 0, sizeof(*rep));
    rep->ctl_code = req->ctl_code;
    memcpy(rep->file_id, req->file_id, SMB2_FD_SIZE);

    switch(req->ctl_code) {
    case SMB2_FSCTL_PIPE_TRANSCEIVE: {
        smb_server_t *srv = (smb_server_t *)srvr;
        hts_mutex_lock(&smb_server_mutex);
        char *share_name = strdup(srv->share_name ? srv->share_name : "share");
        hts_mutex_unlock(&smb_server_mutex);
        if(share_name == NULL)
            return SMB2_STATUS_INSUFFICIENT_RESOURCES;

        smb_file_entry_t *fe = smb_find_file(sc, req->file_id);
        if(fe == NULL || !fe->is_pipe) {
            free(share_name);
            return SMB2_STATUS_INVALID_DEVICE_REQUEST;
        }

        SMBTRACE("IOCTL: PIPE_TRANSCEIVE pipe='%s' input=%u",
                 fe->path, req->input_count);
        free(sc->sc_ioctl_output);
        sc->sc_ioctl_output = NULL;

        int status = dcerpc_server_process_srvsvc(smb2, req->input,
                                                  req->input_count,
                                                  share_name,
                                                  &sc->sc_ioctl_output,
                                                  &rep->output_count);
        free(share_name);
        if(status)
            return status;

        rep->output = sc->sc_ioctl_output;
        SMBTRACE("IOCTL: srvsvc response %u bytes", rep->output_count);
        return 0;
    }
    case SMB2_FSCTL_VALIDATE_NEGOTIATE_INFO: {
        SMBTRACE("IOCTL: VALIDATE_NEGOTIATE_INFO");
        memset(&sc->sc_vni, 0, sizeof(sc->sc_vni));
        sc->sc_vni.capabilities = srvr->capabilities;
        sc->sc_vni.security_mode = srvr->security_mode;
        memcpy(sc->sc_vni.guid, srvr->guid, sizeof(sc->sc_vni.guid));
        sc->sc_vni.dialect = smb2_get_dialect(smb2);

        rep->output = &sc->sc_vni;
        rep->output_count = sizeof(sc->sc_vni);
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
                    struct smb2_lock_request *r)                      { if(r == NULL) return SMB2_STATUS_INVALID_PARAMETER; return SMB2_STATUS_NOT_SUPPORTED; }

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
static void *
smb_server_thread(void *aux)
{
    smb_server_t *srv = aux;
    SMBINFO("Listening on port %u (anonymous=%s)",
            srv->srv.port, srv->srv.allow_anonymous ? "yes" : "no");
    usage_event("SMB Server", 1, NULL);

    int err = smb2_serve_port((struct smb2_server *)srv, 0, NULL, NULL);
    SMBINFO("Server stopped (err=%d)", err);
    hts_mutex_lock(&smb_server_mutex);
    if(smb_active_server == srv)
        smb_active_server = NULL;
    free(srv->username);
    free(srv->password);
    free(srv->share_name);
    free(srv->share_root);
    hts_mutex_unlock(&smb_server_mutex);
    free(srv);
    __atomic_store_n(&smb_thread_running, 0, __ATOMIC_SEQ_CST);
    return NULL;
}

/* ------------------------------------------------------------------ */
/* Enable / disable (called from asyncio courier)                       */
/* ------------------------------------------------------------------ */

static void
enable_disable(void)
{
    int running = __atomic_load_n(&smb_thread_running, __ATOMIC_SEQ_CST);
    if(smb_enable && smb_port > 0) {
        if(running)
            return;   /* already up; port changes require restart of Movian */

        smb_server_t *srv = calloc(1, sizeof(smb_server_t));
        if(srv == NULL) {
            SMBINFO("Failed to allocate SMB2 server context");
            return;
        }
        srv->srv.handlers        = &smb_handlers;
        srv->srv.signing_enabled = smb_username != NULL ? 1 : 0;
        srv->srv.allow_anonymous = (smb_username == NULL) ? 1 : 0;
        srv->srv.port            = (uint16_t)smb_port;
        srv->username            = smb_username ? strdup(smb_username) : NULL;
        srv->password            = smb_password ? strdup(smb_password) : NULL;
        srv->share_name          = smb_share_name ? strdup(smb_share_name) : NULL;
        srv->share_root          = smb_share_root ? strdup(smb_share_root) : NULL;

        hts_mutex_lock(&smb_server_mutex);
        smb_active_server = srv;
        hts_mutex_unlock(&smb_server_mutex);

        __atomic_store_n(&smb_thread_running, 1, __ATOMIC_SEQ_CST);
        hts_thread_create_detached("smb2-server", smb_server_thread,
                                   srv, THREAD_PRIO_MODEL);
        SMBINFO("SMB2 server starting on port %d (anonymous=%s)",
                smb_port, smb_username == NULL ? "yes" : "no");
    }
    /* Stopping a running smb2_serve_port() requires closing the listen fd
     * which is private to libsmb2 after the call — not exposed.
     * For Movian's use case (toggle in settings) a restart of the app is
     * acceptable. Log a note if disabled while running. */
    else if(!smb_enable && running) {
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
    int running = __atomic_load_n(&smb_thread_running, __ATOMIC_SEQ_CST);
    /* Port change only takes effect on next start — note in trace */
    SMBTRACE("Port changed to %d%s", smb_port,
             running ? " (restart required to apply)" : "");
    queue_enable_disable();
}

static void set_username(void *opaque, const char *str)
{
    mystrset(&smb_username, (str && *str) ? str : NULL);
    smb_apply_active_auth();
    SMBINFO("Auth mode: %s", smb_username ? "password-protected" : "anonymous");
}

static void set_password(void *opaque, const char *str)
{
    mystrset(&smb_password, (str && *str) ? str : NULL);
    smb_apply_active_auth();
}

static void set_share_name(void *opaque, const char *str)
{
    mystrset(&smb_share_name, str);
    smb_apply_active_share_name();
}

static void set_share_root(void *opaque, const char *str)
{
    mystrset(&smb_share_root, str);
    smb_apply_active_share_root();
}

/* ------------------------------------------------------------------ */
/* Init                                                                 */
/* ------------------------------------------------------------------ */

void
smb_server_init(void)
{
    hts_mutex_init(&smb_server_mutex);

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
