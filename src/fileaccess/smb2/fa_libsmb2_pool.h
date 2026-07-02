/*
 *  Copyright (C) 2026 Movian contributors
 *
 *  This program is free software: you can redistribute it and/or modify
 *  it under the terms of the GNU General Public License as published by
 *  the Free Software Foundation, either version 3 of the License, or
 *  (at your option) any later version.
 */
#ifndef FA_LIBSMB2_POOL_H__
#define FA_LIBSMB2_POOL_H__

#include <smb2/smb2.h>
#include <smb2/libsmb2.h>

#include "fileaccess/fa_proto.h"
#include "misc/callout.h"
#include "misc/queue.h"

/*
 * Tunables shared between the SMB2 fileaccess backend (fa_libsmb2.c) and the
 * pooled-session layer (fa_libsmb2_pool.c).
 */
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


/*
 * Parsed components of an smb2:// URL.
 */
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

/*
 * A pooled, reusable libsmb2 session: one TCP connection + negotiated session
 * + tree connect to a single share, kept alive across VFS calls and re-used by
 * refcount.
 */
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
  int echo_missed;
  LIST_ENTRY(movian_smb2_session) link;
} movian_smb2_session_t;


void movian_smb2_target_fini(movian_smb2_target_t *target);

int movian_smb2_target_parse(const char *url, movian_smb2_target_t *target,
                             char *errbuf, size_t errlen);

/*
 * Acquire a pooled session for (target, share). On a cache miss the TCP +
 * negotiate + session setup + tree connect handshake runs once; subsequent
 * acquisitions reuse the live session. Returns a session with refcount bumped,
 * or NULL on failure (errbuf filled).
 *
 * The returned session has its pool refcount incremented; the caller must pair
 * this with movian_smb2_session_release(). Per-operation serialization on a
 * single context is the caller's responsibility: take session->lock around the
 * libsmb2 call.
 *
 * If auth_needed is non-NULL it is set to 1 when the failure was an auth error
 * the caller may want to surface as FAP_NEED_AUTH.
 */
movian_smb2_session_t *
movian_smb2_session_acquire(const movian_smb2_target_t *target,
                            const char *share, int flags, int timeout,
                            int *auth_needed, char *errbuf, size_t errlen);

void movian_smb2_session_release(movian_smb2_session_t *session);

/*
 * Mark a session as broken under the pool lock and drop it from the pool so the
 * next acquisition reconnects. Called when an operation fails in a way that
 * implies the context is no longer usable.
 */
void movian_smb2_session_invalidate(movian_smb2_session_t *session);

#endif /* FA_LIBSMB2_POOL_H__ */
