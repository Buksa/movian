/* -*-  mode:c; tab-width:8; c-basic-offset:8; indent-tabs-mode:nil;  -*- */
/*
 * Movian SMB2 server — auth, session lifecycle, tree registry.
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
/* Handler: AUTHORIZE                                                   */
/* ------------------------------------------------------------------ */

int
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

int
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

int
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

int
smb_logoff(struct smb2_server *srvr, struct smb2_context *smb2)
{
    return smb_cleanup_session(smb2, "logged off");
}

int
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

int
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

smb_tree_type_t
smb_tree_lookup(smb_connection_t *sc, uint32_t tree_id)
{
    for(int i = 0; i < SMB2_MAX_TREES; i++) {
        smb_tree_entry_t *tree = &sc->sc_trees[i];
        if(tree->tree_id == tree_id)
            return tree->is_ipc ? SMB_TREE_IPC : SMB_TREE_DISK;
    }
    return SMB_TREE_UNKNOWN;
}

int
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

int
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
