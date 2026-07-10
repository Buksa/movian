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

#include "smb_server_private.h"

/* ------------------------------------------------------------------ */
/* Server state (protected by asyncio courier)                          */
/* ------------------------------------------------------------------ */

int            smb_port   = 1445;
int            smb_enable = 0;
setting_t     *smb_port_setting = NULL;
int            smb_port_revert_pending = 0;
char          *smb_username   = NULL;   /* NULL = allow anonymous  */
char          *smb_password   = NULL;   /* NULL = allow anonymous  */
char          *smb_share_name  = NULL;
char          *smb_share_root  = NULL;   /* configurable root  */
int            smb_thread_running = 0;
hts_mutex_t    smb_server_mutex;
smb_server_t  *smb_active_server = NULL;

char *
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

char *
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

static void
deferred_revert_port_setting(void *aux)
{
    char buf[16];

    smb_port_revert_pending = 0;
    if(smb_port_setting == NULL)
        return;

    snprintf(buf, sizeof(buf), "%d", smb_port);
    setting_set(smb_port_setting, SETTING_STRING, buf);
}

static int
parse_port(const char *input, int *portp)
{
    char *end = NULL;

    if(input == NULL || *input == '\0')
        return -1;

    errno = 0;
    long port = strtol(input, &end, 10);
    if(errno || end == input || *end != '\0' || port < 1 || port > 65535)
        return -1;

    *portp = (int)port;
    return 0;
}

static void set_port(void *opaque, const char *str)
{
    int port;

    if(parse_port(str, &port)) {
        SMBINFO("Invalid SMB2 server port '%s'; keeping %d",
                str ?: "", smb_port);
        if(!smb_port_revert_pending) {
            smb_port_revert_pending = 1;
            asyncio_run_task(deferred_revert_port_setting, NULL);
        }
        return;
    }

    smb_port = port;
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

    smb_port_setting =
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
