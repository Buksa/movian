/* -*-  mode:c; tab-width:8; c-basic-offset:8; indent-tabs-mode:nil;  -*- */
/*
 * Movian SMB2 server — internal state shared across smb_server*.c
 *
 * This header is private to the smb_server_*.c translation units and is
 * not installed / exposed outside src/networking. See smb_server.h for
 * the public API (smb_server_init()).
 */

#ifndef SMB_SERVER_PRIVATE_H__
#define SMB_SERVER_PRIVATE_H__

#include <stdint.h>
#include <stddef.h>
#include <time.h>

#include "arch/threads.h"
#include "settings.h"
#include "fileaccess/fileaccess.h"
#include "fileaccess/fa_proto.h"

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
    char        *dir_pattern;      /* active QUERY_DIRECTORY pattern    */
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
/* Server state (protected by asyncio courier) — DEFINED in smb_server.c */
/* ------------------------------------------------------------------ */

extern int            smb_port;
extern int            smb_enable;
extern setting_t     *smb_port_setting;
extern int            smb_port_revert_pending;
extern char          *smb_username;   /* NULL = allow anonymous  */
extern char          *smb_password;   /* NULL = allow anonymous  */
extern char          *smb_share_name;
extern char          *smb_share_root;   /* configurable root  */
extern int            smb_thread_running;
extern hts_mutex_t    smb_server_mutex;
extern smb_server_t  *smb_active_server;

/* ------------------------------------------------------------------ */
/* Cross-file prototypes                                                */
/* (functions that stay static in their home .c are NOT listed here)   */
/* ------------------------------------------------------------------ */

/* smb_server.c */
char *smb_strdup_or_null(const char *str);
char *smb_normalize_share_root(const char *root);

/* smb_server_session.c */
int smb_authorize(struct smb2_server *srvr, struct smb2_context *smb2,
                   const char *user, const char *domain, const char *workstation);
int smb_session_established(struct smb2_server *srvr, struct smb2_context *smb2);
int smb_reject_stale_session(struct smb2_server *srvr, struct smb2_context *smb2);
int smb_logoff(struct smb2_server *srvr, struct smb2_context *smb2);
int smb_destruction(struct smb2_server *srvr, struct smb2_context *smb2);
int smb_is_srvsvc_pipe(const char *name);
smb_tree_type_t smb_tree_lookup(smb_connection_t *sc, uint32_t tree_id);
int smb_tree_connect(struct smb2_server *srvr, struct smb2_context *smb2,
                     struct smb2_tree_connect_request *req,
                     struct smb2_tree_connect_reply *rep);
int smb_tree_disconnect(struct smb2_server *srvr, struct smb2_context *smb2,
                        const uint32_t tree_id);

/* smb_server_handles.c */
smb_file_entry_t *smb_find_file(smb_connection_t *sc, const uint8_t *file_id);
smb_file_entry_t *smb_alloc_file(smb_connection_t *sc, char *path);
void smb_free_file(smb_connection_t *sc, smb_file_entry_t *fe);
int smb_close_file_entry(smb_connection_t *sc, smb_file_entry_t *fe);
void smb_close_all_files(smb_connection_t *sc);
int smb_close_tree_files(smb_connection_t *sc, uint32_t tree_id);

/* smb_server_vfs.c */
int vfs_unlink(const char *url, char *errbuf, size_t errlen);
int vfs_rmdir(const char *url, char *errbuf, size_t errlen);
int smb_create(struct smb2_server *srvr, struct smb2_context *smb2,
               struct smb2_create_request *req,
               struct smb2_create_reply *rep);
int smb_close(struct smb2_server *srvr, struct smb2_context *smb2,
             struct smb2_close_request *req,
             struct smb2_close_reply *rep);
int smb_flush(struct smb2_server *srvr, struct smb2_context *smb2,
             struct smb2_flush_request *req);
int smb_read(struct smb2_server *srvr, struct smb2_context *smb2,
            struct smb2_read_request *req,
            struct smb2_read_reply *rep);
int smb_write(struct smb2_server *srvr, struct smb2_context *smb2,
             struct smb2_write_request *req,
             struct smb2_write_reply *rep);
int smb_query_directory(struct smb2_server *srvr, struct smb2_context *smb2,
                        struct smb2_query_directory_request *req,
                        struct smb2_query_directory_reply *rep);
int smb_query_info(struct smb2_server *srvr, struct smb2_context *smb2,
                   struct smb2_query_info_request *req,
                   struct smb2_query_info_reply *rep);
int smb_set_info(struct smb2_server *srvr, struct smb2_context *smb2,
                 struct smb2_set_info_request *req);

/* smb_server_srvsvc.c */
int smb_ioctl(struct smb2_server *srvr, struct smb2_context *smb2,
             struct smb2_ioctl_request *req,
             struct smb2_ioctl_reply *rep);

#endif /* SMB_SERVER_PRIVATE_H__ */
