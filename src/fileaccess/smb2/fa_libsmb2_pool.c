/*
 *  Copyright (C) 2026 Movian contributors
 *
 *  This program is free software: you can redistribute it and/or modify
 *  it under the terms of the GNU General Public License as published by
 *  the Free Software Foundation, either version 3 of the License, or
 *  (at your option) any later version.
 */

/*
 * SMB2 pooled-session layer.
 *
 * Keeps one long lived libsmb2 context per (server, share, user, domain) so the
 * TCP+negotiate+session-setup+tree-connect handshake happens once and is reused
 * across the many VFS calls Movian issues while browsing a share. Sessions are
 * refcounted, reaped when idle past SMB2_POOL_IDLE_TTL_SEC, and probed with a
 * 30 s echo keepalive so a parked connection does not silently go stale. This
 * mirrors the cifs native cifs_connections / cc_refcount / cifs_periodic scheme.
 *
 * The VFS backend (fa_libsmb2.c) is the only consumer; it acquires a session,
 * takes session->lock around each libsmb2 call (libsmb2 is not thread safe and
 * a context is bound to one socket), and releases when done.
 */

#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include <smb2/smb2.h>
#include <smb2/libsmb2.h>

#include "main.h"
#include "keyring.h"
#include "misc/rstr.h"

#include "fa_libsmb2_pool.h"


static LIST_HEAD(, movian_smb2_session) smb2_sessions =
  LIST_HEAD_INITIALIZER(smb2_sessions);
static hts_mutex_t smb2_pool_mutex;

static void movian_smb2_keepalive_cb(callout_t *c, void *opaque);


/*
 * URL parsing.
 *
 * libsmb2's URL parser expects an smb:// scheme, so we temporarily rewrite the
 * smb2:// prefix and parse the share/path/user/domain out of the result.
 */
int
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


void
movian_smb2_target_fini(movian_smb2_target_t *target)
{
  free(target->server);
  free(target->share);
  free(target->path);
  free(target->user);
  free(target->domain);
}


/*
 * Credential resolution + connect.
 *
 * The keyring prompt can block the UI, so the connect runs without the pool
 * lock held; a miss then inserts the freshly built session under the lock.
 */

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


/*
 * Pool state + lifecycle.
 */

void
movian_smb2_pool_init(void)
{
  hts_mutex_init(&smb2_pool_mutex);
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
movian_smb2_session_free(movian_smb2_session_t *session)
{
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


static void
movian_smb2_session_retain_lifetime(movian_smb2_session_t *session)
{
  atomic_inc(&session->lifetime_refcount);
}


static void
movian_smb2_session_release_lifetime(movian_smb2_session_t *session)
{
  if(atomic_dec(&session->lifetime_refcount))
    return;
  movian_smb2_session_free(session);
}


static int
movian_smb2_session_lockmgr(void *ptr, lockmgr_op_t op)
{
  movian_smb2_session_t *session = ptr;

  switch(op) {
  case LOCKMGR_UNLOCK:
  case LOCKMGR_LOCK:
  case LOCKMGR_TRY:
    return 0;
  case LOCKMGR_RETAIN:
    movian_smb2_session_retain_lifetime(session);
    return 0;
  case LOCKMGR_RELEASE:
    movian_smb2_session_release_lifetime(session);
    return 0;
  }
  abort();
}


static void
movian_smb2_session_destroy(movian_smb2_session_t *session)
{
  callout_disarm(&session->keepalive);
  movian_smb2_session_release_lifetime(session);
}


static int
movian_smb2_session_unlink_locked(movian_smb2_session_t *session)
{
  if(session->linked) {
    LIST_REMOVE(session, link);
    session->linked = 0;
    return 1;
  }
  return 0;
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
      if(movian_smb2_session_unlink_locked(session))
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
    if(movian_smb2_session_unlink_locked(oldest))
      movian_smb2_session_destroy(oldest);
    count--;
  }
}


movian_smb2_session_t *
movian_smb2_session_acquire(const movian_smb2_target_t *target,
                            const char *share, int flags, int timeout,
                            int *auth_needed, char *errbuf, size_t errlen)
{
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

  hts_mutex_init(&session->lock);
  atomic_set(&session->lifetime_refcount, 1);
  session->key = key;
  session->smb2 = smb2;
  session->refcount = 1;
  session->last_used = arch_get_ts();
  session->server = strdup(target->server);
  session->share = strdup(share != NULL ? share : "");
  session->user = resolved.user;
  session->domain = resolved.domain;
  resolved.user = NULL;
  resolved.domain = NULL;
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

  hts_mutex_lock(&smb2_pool_mutex);
  movian_smb2_session_t *existing;
  LIST_FOREACH(existing, &smb2_sessions, link) {
    if(!strcmp(existing->key, key) && !existing->broken) {
      existing->refcount++;
      existing->last_used = arch_get_ts();
      hts_mutex_unlock(&smb2_pool_mutex);
      SMB2TRACE("Pool reuse after connect race %s/%s refcount=%d",
                target->server, share != NULL ? share : "",
                existing->refcount);
      movian_smb2_session_destroy(session);
      return existing;
    }
  }
  LIST_INSERT_HEAD(&smb2_sessions, session, link);
  session->linked = 1;
  movian_smb2_pool_evict_locked();
  callout_arm_managed(&session->keepalive, movian_smb2_keepalive_cb, session,
                      SMB2_ECHO_INTERVAL * 1000000LL,
                      movian_smb2_session_lockmgr);
  hts_mutex_unlock(&smb2_pool_mutex);

  SMB2TRACE("Pool create %s/%s refcount=1",
            target->server, share != NULL ? share : "");
  return session;
}


void
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


void
movian_smb2_session_invalidate(movian_smb2_session_t *session)
{
  if(session == NULL)
    return;

  hts_mutex_lock(&smb2_pool_mutex);
  session->broken = 1;
  if(session->refcount <= 0) {
    int unlinked = movian_smb2_session_unlink_locked(session);
    if(unlinked)
      movian_smb2_session_destroy(session);
  }
  hts_mutex_unlock(&smb2_pool_mutex);
}


/*
 * Periodic keepalive. Runs on the callout thread. The managed callout holds a
 * lifetime ref while queued or running, so an idle-session eviction can unlink
 * and logically destroy the session without freeing memory out from under this
 * callback. Echoes only run for idle, refcount==0 sessions.
 *
 * smb2_echo() blocks (up to SMB2_DEFAULT_TIMEOUT) with the pool mutex
 * dropped, so a concurrent acquire()/evict_locked() can unlink (and
 * logically destroy) this very session while the echo is in flight. All
 * outcomes of that race are resolved in one place, at the end of the
 * callback, under the pool mutex: session->linked is the source of truth
 * for "someone else already tore this down", not session->broken alone.
 */
static void
movian_smb2_keepalive_cb(callout_t *c, void *opaque)
{
  movian_smb2_session_t *session = opaque;
  int do_echo = 0;
  int destroy = 0;

  hts_mutex_lock(&smb2_pool_mutex);
  if(!session->broken && session->refcount <= 0 &&
     session->echo_missed > SMB2_ECHO_MAX_MISSED) {
    SMB2TRACE("Keepalive %s/%s giving up (missed=%d) -> broken",
              session->server, session->share, session->echo_missed);
    session->broken = 1;
  }
  do_echo = !session->broken && session->refcount <= 0;
  hts_mutex_unlock(&smb2_pool_mutex);

  int rc = 0;
  int missed = 0;
  if(do_echo) {
    hts_mutex_lock(&session->lock);
    rc = smb2_echo(session->smb2);
    hts_mutex_unlock(&session->lock);
  }

  hts_mutex_lock(&smb2_pool_mutex);
  if(do_echo && !session->broken) {
    if(rc == 0)
      missed = session->echo_missed = 0;
    else
      missed = ++session->echo_missed;
    if(session->echo_missed > SMB2_ECHO_MAX_MISSED) {
      SMB2TRACE("Keepalive %s/%s giving up (missed=%d) -> broken",
                session->server, session->share, session->echo_missed);
      session->broken = 1;
    }
  }

  /* Single end-of-callback decision point (see block comment above). */
  if(!session->linked) {
    /* Unlinked (and logically destroyed) by someone else while the echo
     * was in flight: no re-arm, no extra release. The callout machinery
     * drops its own running ref once we return. */
  } else if(session->broken && session->refcount <= 0) {
    destroy = movian_smb2_session_unlink_locked(session);
  } else {
    callout_arm_managed(&session->keepalive, movian_smb2_keepalive_cb,
                        session, SMB2_ECHO_INTERVAL * 1000000LL,
                        movian_smb2_session_lockmgr);
  }
  hts_mutex_unlock(&smb2_pool_mutex);

  if(do_echo)
    SMB2TRACE("Keepalive %s/%s echo rc=%d missed=%d",
              session->server, session->share, rc, missed);

  if(destroy)
    movian_smb2_session_destroy(session);
}
