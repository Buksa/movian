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
#include "keyring.h"
#include "fileaccess/fa_proto.h"
#include "misc/callout.h"
#include "misc/queue.h"
#include "misc/rstr.h"

#define SMB2_DEFAULT_TIMEOUT 15

/*
 * Connection pool limits.
 *
 * libsmb2 is not thread safe and binds a context to a single TCP socket, so a
 * session cannot be shared between threads without serialization. We keep one
 * long lived session per (server, share, user, domain) key in a pool so the
 * TCP+negotiate+session-setup+tree-connect handshake happens once and is reused
 * across the many VFS calls Movian issues while browsing a share. The cifs
 * native backend achieves the same with cifs_connections/cc_refcount.
 *
 * Idle sessions are kept around for a while so that bouncing back into a share
 * does not pay the handshake cost again, up to SMB2_POOL_MAX_SESSIONS. Older
 * sessions are reaped first.
 */
#define SMB2_POOL_MAX_SESSIONS 8
#define SMB2_POOL_IDLE_TTL_SEC 60

/*
 * Keepalive cadence (seconds) and how many consecutive missed echoes are
 * tolerated before the session is torn down. Mirrors the cifs native
 * SMB_ECHO_INTERVAL / cc_wait_for_ping scheme.
 */
#define SMB2_ECHO_INTERVAL 30
#define SMB2_ECHO_MAX_MISSED 2

#define SMB2TRACE(x, ...) do {                                 \
    if(gconf.enable_smb_debug)                                 \
      tracelog(0, TRACE_DEBUG, "SMB2-CLIENT", x, ##__VA_ARGS__); \
  } while(0)

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

typedef struct movian_smb2_session {
  char *key;
  char *server;
  char *share;
  char *user;
  char *domain;
  struct smb2_context *smb2;
  hts_mutex_t lock;
  int refcount;
  int broken;
  int64_t last_used;
  callout_t keepalive;
  int echo_pending;
  int echo_missed;
  LIST_ENTRY(movian_smb2_session) link;
} movian_smb2_session_t;

static LIST_HEAD(, movian_smb2_session) smb2_sessions =
  LIST_HEAD_INITIALIZER(smb2_sessions);
static hts_mutex_t smb2_pool_mutex;
static int smb2_pool_inited;

static void movian_smb2_keepalive_cb(callout_t *c, void *opaque);

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

  int has_share = parsed->share != NULL;
  int has_user = parsed->user != NULL;
  int has_domain = parsed->domain != NULL;

  target->server = strdup(parsed->server);
  target->share = has_share ? strdup(parsed->share) : NULL;
  target->path = parsed->path != NULL ? strdup(parsed->path) : strdup("");
  target->user = has_user ? strdup(parsed->user) : NULL;
  target->domain = has_domain ? strdup(parsed->domain) : NULL;

  smb2_destroy_url(parsed);
  smb2_destroy_context(smb2);

  if(target->server == NULL || target->path == NULL ||
     (has_share && target->share == NULL) ||
     (has_user && target->user == NULL) ||
     (has_domain && target->domain == NULL)) {
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
    strdup(target->domain != NULL ? target->domain : "WORKGROUP");
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


static int
movian_smb2_can_query_user(int flags)
{
  if(flags & (FA_NON_INTERACTIVE | FA_DISABLE_AUTH))
    return 0;

  char name[64];
  return strcmp(hts_thread_name(name, sizeof(name)), "asyncio");
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


static int
movian_smb2_session_key(const movian_smb2_target_t *target, const char *share,
                        char **keyp)
{
  const char *user = target->user != NULL ? target->user : "";
  const char *domain = target->domain != NULL ? target->domain : "";
  const char *sh = share != NULL ? share : "";

  size_t len = strlen(target->server) + strlen(sh) + strlen(user) +
    strlen(domain) + 4;
  char *key = malloc(len);
  if(key == NULL)
    return -1;

  snprintf(key, len, "%s/%s/%s/%s", target->server, sh, user, domain);
  *keyp = key;
  return 0;
}


static void
movian_smb2_session_destroy(movian_smb2_session_t *session)
{
  /*
   * Caller must hold neither the pool nor the session lock. The session is
   * already unlinked from the pool.
   */
  callout_disarm(&session->keepalive);
  if(session->smb2 != NULL) {
    smb2_disconnect_share(session->smb2);
    smb2_destroy_context(session->smb2);
  }
  hts_mutex_destroy(&session->lock);
  free(session->key);
  free(session->server);
  free(session->share);
  free(session->user);
  free(session->domain);
  free(session);
}


/*
 * Reap idle and broken sessions. Called under the pool mutex. The least
 * recently used idle session is dropped when the pool grows past the limit.
 */
static void
movian_smb2_pool_evict_locked(void)
{
  if(LIST_EMPTY(&smb2_sessions))
    return;

  int64_t now = arch_get_ts();
  int64_t cutoff = now - SMB2_POOL_IDLE_TTL_SEC * 1000000LL;

  movian_smb2_session_t *session = LIST_FIRST(&smb2_sessions);
  while(session != NULL) {
    movian_smb2_session_t *next = LIST_NEXT(session, link);
    if(session->refcount <= 0 &&
       (session->broken || session->last_used < cutoff)) {
      LIST_REMOVE(session, link);
      movian_smb2_session_destroy(session);
    }
    session = next;
  }

  int count = 0;
  LIST_FOREACH(session, &smb2_sessions, link)
    count++;

  while(count > SMB2_POOL_MAX_SESSIONS) {
    movian_smb2_session_t *oldest = NULL;
    LIST_FOREACH(session, &smb2_sessions, link) {
      if(session->refcount > 0)
        continue;
      if(oldest == NULL || session->last_used < oldest->last_used)
        oldest = session;
    }
    if(oldest == NULL)
      break;
    LIST_REMOVE(oldest, link);
    movian_smb2_session_destroy(oldest);
    count--;
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
    SMB2TRACE("Unable to initialize SMB2 context");
    return NULL;
  }

  SMB2TRACE("Connecting to %s/%s timeout=%d",
            target->server, share != NULL ? share : "", timeout);
  SMB2TRACE("SETUP %s:%s:%s", user,
            credentials->password != NULL ? "<set>" : "<unset>",
            credentials->domain != NULL ? credentials->domain : "<unset>");

  smb2_set_timeout(smb2, timeout);
  smb2_set_security_mode(smb2, SMB2_NEGOTIATE_SIGNING_ENABLED);
  smb2_set_user(smb2, user);
  smb2_set_password(smb2, credentials->password);
  if(credentials->domain != NULL)
    smb2_set_domain(smb2, credentials->domain);

  *status = smb2_connect_share(smb2, target->server, share, user);
  if(*status == 0) {
    SMB2TRACE("%s/%s Session setup", target->server,
              share != NULL ? share : "");
    return smb2;
  }

  snprintf(errbuf, errlen, "%s", smb2_get_error(smb2));
  SMB2TRACE("SETUP status=%d reason=%s", *status, errbuf);
  smb2_destroy_context(smb2);
  return NULL;
}


/*
 * Resolve credentials and perform the (interactive, possibly retrying) connect.
 * Runs without the pool lock so that a keyring prompt does not stall other
 * sessions. Returns a fresh connected context and, on success, leaves the
 * resolved credentials in *resolved for the session to retain.
 */
static struct smb2_context *
movian_smb2_connect_impl(const movian_smb2_target_t *target, const char *share,
                         int flags, int timeout, int *auth_needed,
                         movian_smb2_credentials_t *resolved,
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
  int have_saved_credentials = keyring_status == KEYRING_OK;
  SMB2TRACE("Keyring lookup %s for %s",
            have_saved_credentials ? "hit" : "miss", keyring_id);
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
    if(resolved != NULL) {
      resolved->user = strdup(credentials.user != NULL ? credentials.user : "");
      resolved->password =
        strdup(credentials.password != NULL ? credentials.password : "");
      resolved->domain =
        strdup(credentials.domain != NULL ? credentials.domain : "");
    }
    movian_smb2_credentials_fini(&credentials);
    free(keyring_id);
    return smb2;
  }

  int auth_error = movian_smb2_is_auth_error(status, reason);
  if(auth_error && auth_needed != NULL)
    *auth_needed = 1;

  int can_query_user = movian_smb2_can_query_user(flags);
  char thread_name[64];
  SMB2TRACE("Auth retry auth_error=%d can_query=%d flags=0x%x "
            "thread=%s status=%d reason=%s",
            auth_error, can_query_user, flags,
            hts_thread_name(thread_name, sizeof(thread_name)), status,
            reason);

  if(!can_query_user || !auth_error) {
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
  if(credentials.domain == NULL) {
    credentials.domain = strdup("WORKGROUP");
    if(credentials.domain == NULL) {
      snprintf(errbuf, errlen, "Out of memory while preparing SMB2 login");
      free(keyring_id);
      return NULL;
    }
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
                   NULL, source,
                   have_saved_credentials ? reason : "Login required",
                   KEYRING_QUERY_USER | KEYRING_SHOW_REMEMBER_ME |
                   KEYRING_REMEMBER_ME_SET);
  free(source);

  if(keyring_status != KEYRING_OK) {
    SMB2TRACE("Credential query rejected status=%d", keyring_status);
    snprintf(errbuf, errlen, "Authentication rejected by user");
    movian_smb2_credentials_fini(&credentials);
    free(keyring_id);
    return NULL;
  }
  SMB2TRACE("Credential query accepted");

  smb2 = movian_smb2_connect_once(target, share, &credentials, timeout,
                                  &status, reason, sizeof(reason));
  if(smb2 == NULL)
    snprintf(errbuf, errlen, "Unable to connect to SMB2 share: %s", reason);
  else if(resolved != NULL) {
    resolved->user = strdup(credentials.user != NULL ? credentials.user : "");
    resolved->password =
      strdup(credentials.password != NULL ? credentials.password : "");
    resolved->domain =
      strdup(credentials.domain != NULL ? credentials.domain : "");
  }

  movian_smb2_credentials_fini(&credentials);
  free(keyring_id);
  return smb2;
}


static void
movian_smb2_pool_init_once(void)
{
  if(smb2_pool_inited)
    return;
  hts_mutex_init(&smb2_pool_mutex);
  smb2_pool_inited = 1;
}


/*
 * Periodic keepalive. Runs on the main callout thread. Sends an SMB2 echo and,
 * if the previous echo never came back, counts it as a miss. After too many
 * misses (or a hard error) the session is marked broken so the next acquisition
 * reconnects. The echo itself is a synchronous libsmb2 call; it is short and
 * only ever sent for idle, refcount==0 sessions, so it never contends with a
 * streaming reader that already holds session->lock for its own data PDU.
 */
static void
movian_smb2_keepalive_cb(callout_t *c, void *opaque)
{
  movian_smb2_session_t *session = opaque;

  hts_mutex_lock(&smb2_pool_mutex);
  int still_alive = !session->broken && session->refcount <= 0;
  if(session->echo_pending)
    session->echo_missed++;
  if(session->echo_missed > SMB2_ECHO_MAX_MISSED) {
    SMB2TRACE("Keepalive %s/%s giving up (missed=%d) -> broken",
              session->server, session->share, session->echo_missed);
    session->broken = 1;
  }
  int broken = session->broken;
  hts_mutex_unlock(&smb2_pool_mutex);

  if(!broken && still_alive) {
    hts_mutex_lock(&session->lock);
    session->echo_pending = 1;
    if(smb2_echo(session->smb2) == 0)
      session->echo_missed = 0;
    session->echo_pending = 0;
    hts_mutex_unlock(&session->lock);
  }

  if(!broken) {
    hts_mutex_lock(&smb2_pool_mutex);
    if(!session->broken)
      callout_arm(&session->keepalive, movian_smb2_keepalive_cb, session,
                  SMB2_ECHO_INTERVAL);
    hts_mutex_unlock(&smb2_pool_mutex);
  }
}


/*
 * Acquire a pooled session for (target, share). On a cache miss the TCP +
 * negotiate + session setup + tree connect handshake runs once; subsequent
 * acquisitions reuse the live session. Returns a session with refcount bumped
 * (locked for the caller via session->lock semantics — see release) or NULL on
 * failure.
 *
 * The returned session has its pool refcount incremented; the caller must pair
 * this with movian_smb2_session_release(). Per-operation serialization on a
 * single context is the caller's responsibility: take session->lock around the
 * libsmb2 call.
 */
static movian_smb2_session_t *
movian_smb2_session_acquire(const movian_smb2_target_t *target,
                            const char *share, int flags, int timeout,
                            int *auth_needed, char *errbuf, size_t errlen)
{
  movian_smb2_pool_init_once();

  char *key;
  if(movian_smb2_session_key(target, share, &key)) {
    snprintf(errbuf, errlen, "Out of memory while opening SMB2 session");
    return NULL;
  }

  movian_smb2_session_t *session = NULL;

  hts_mutex_lock(&smb2_pool_mutex);
  LIST_FOREACH(session, &smb2_sessions, link) {
    if(!strcmp(session->key, key) && !session->broken) {
      session->refcount++;
      session->last_used = arch_get_ts();
      hts_mutex_unlock(&smb2_pool_mutex);
      free(key);
      SMB2TRACE("Pool reuse %s/%s refcount=%d",
                target->server, share != NULL ? share : "", session->refcount);
      return session;
    }
  }
  hts_mutex_unlock(&smb2_pool_mutex);

  movian_smb2_credentials_t resolved = {};
  struct smb2_context *smb2 =
    movian_smb2_connect_impl(target, share, flags, timeout, auth_needed,
                             &resolved, errbuf, errlen);
  if(smb2 == NULL) {
    free(key);
    return NULL;
  }

  session = calloc(1, sizeof(*session));
  if(session == NULL) {
    snprintf(errbuf, errlen, "Out of memory while opening SMB2 session");
    smb2_disconnect_share(smb2);
    smb2_destroy_context(smb2);
    movian_smb2_credentials_fini(&resolved);
    free(key);
    return NULL;
  }

  session->key = key;
  session->smb2 = smb2;
  session->refcount = 1;
  session->last_used = arch_get_ts();
  session->server = strdup(target->server);
  session->share = strdup(share != NULL ? share : "");
  session->user = resolved.user;
  session->domain = resolved.domain;
  if(session->server == NULL || session->share == NULL) {
    snprintf(errbuf, errlen, "Out of memory while opening SMB2 session");
    movian_smb2_credentials_fini(&resolved);
    movian_smb2_session_destroy(session);
    return NULL;
  }
  /*
   * resolved.password is intentionally not retained: the session never
   * re-authenticates (it is dropped when the server tears it down), and the
   * keyring still holds the credentials for the next acquisition.
   */
  free(resolved.password);
  resolved = (movian_smb2_credentials_t){};
  hts_mutex_init(&session->lock);

  hts_mutex_lock(&smb2_pool_mutex);
  LIST_INSERT_HEAD(&smb2_sessions, session, link);
  movian_smb2_pool_evict_locked();
  callout_arm(&session->keepalive, movian_smb2_keepalive_cb, session,
              SMB2_ECHO_INTERVAL);
  hts_mutex_unlock(&smb2_pool_mutex);

  SMB2TRACE("Pool create %s/%s refcount=1",
            target->server, share != NULL ? share : "");
  return session;
}


static void
movian_smb2_session_release(movian_smb2_session_t *session)
{
  if(session == NULL)
    return;

  hts_mutex_lock(&smb2_pool_mutex);
  session->refcount--;
  session->last_used = arch_get_ts();
  SMB2TRACE("Pool release %s/%s refcount=%d",
            session->server, session->share, session->refcount);
  if(session->refcount <= 0)
    movian_smb2_pool_evict_locked();
  hts_mutex_unlock(&smb2_pool_mutex);
}


/*
 * Mark a session as broken under the pool lock and drop it from the pool so the
 * next acquisition reconnects. Called when an operation fails in a way that
 * implies the context is no longer usable.
 */
static void
movian_smb2_session_invalidate(movian_smb2_session_t *session)
{
  if(session == NULL)
    return;

  hts_mutex_lock(&smb2_pool_mutex);
  session->broken = 1;
  if(session->refcount <= 0) {
    LIST_REMOVE(session, link);
    movian_smb2_session_destroy(session);
  }
  hts_mutex_unlock(&smb2_pool_mutex);
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


static int
movian_smb2_read(fa_handle_t *fh, void *buf, size_t size)
{
  movian_smb2_file_t *file = (movian_smb2_file_t *)fh;
  uint32_t count = size > INT_MAX ? INT_MAX : size;

  hts_mutex_lock(&file->session->lock);
  int status = smb2_read(file->session->smb2, file->fh, buf, count);
  hts_mutex_unlock(&file->session->lock);
  return status;
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

  movian_smb2_session_t *session =
    movian_smb2_session_acquire(&target, target.share, 0,
                                SMB2_DEFAULT_TIMEOUT, NULL, errbuf, errlen);
  if(session == NULL) {
    movian_smb2_target_fini(&target);
    return -1;
  }

  hts_mutex_lock(&session->lock);
  int status = smb2_unlink(session->smb2, target.path);
  if(status < 0)
    snprintf(errbuf, errlen, "Unable to delete SMB2 file: %s",
             smb2_get_error(session->smb2));
  hts_mutex_unlock(&session->lock);

  movian_smb2_session_release(session);
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

  movian_smb2_session_t *session =
    movian_smb2_session_acquire(&target, target.share, 0,
                                SMB2_DEFAULT_TIMEOUT, NULL, errbuf, errlen);
  if(session == NULL) {
    movian_smb2_target_fini(&target);
    return -1;
  }

  hts_mutex_lock(&session->lock);
  int status = smb2_rmdir(session->smb2, target.path);
  if(status < 0)
    snprintf(errbuf, errlen, "Unable to remove SMB2 directory: %s",
             smb2_get_error(session->smb2));
  hts_mutex_unlock(&session->lock);

  movian_smb2_session_release(session);
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

  movian_smb2_session_t *session =
    movian_smb2_session_acquire(&target, target.share, 0,
                                SMB2_DEFAULT_TIMEOUT, NULL,
                                errbuf, sizeof(errbuf));
  if(session == NULL) {
    movian_smb2_target_fini(&target);
    return FAP_ERROR;
  }

  hts_mutex_lock(&session->lock);
  int status = smb2_mkdir(session->smb2, target.path);
  hts_mutex_unlock(&session->lock);

  movian_smb2_session_release(session);
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
