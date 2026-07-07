/*
 *  Copyright (C) 2007-2015 Lonelycoder AB
 *
 *  This program is free software: you can redistribute it and/or modify
 *  it under the terms of the GNU General Public License as published by
 *  the Free Software Foundation, either version 3 of the License, or
 *  (at your option) any later version.
 *
 *  This program is distributed in the hope that it will be useful,
 *  but WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 *  GNU General Public License for more details.
 *
 *  You should have received a copy of the GNU General Public License
 *  along with this program.  If not, see <http://www.gnu.org/licenses/>.
 *
 *  This program is also available under a commercial proprietary license.
 *  For more information, contact andreas@lonelycoder.com
 */
#include <string.h>
#include <stdio.h>

#include "config.h"
#include "main.h"
#include "networking/asyncio.h"
#include "networking/net.h"
#include "misc/endian.h"
#include "misc/bytestream.h"
#include "task.h"
#include "smbv1.h"
#include "misc/str.h"
#include "service.h"
#include "nmb.h"

#if ENABLE_LIBSMB2
#include <smb2/smb2.h>
#include <smb2/libsmb2.h>
#endif

static hts_mutex_t nmb_mutex;
static hts_cond_t nmb_resolver_cond;

/*
 * Master-browser discovery is available whenever either SMB backend can
 * make use of it: the native backend enumerates via smb_enum_servers()
 * (RAP browse-list), the libsmb2 backend confirms via an SMB2 connect
 * probe (see nmb_smb2_connect_probe() below, Buksa/movian#69). Both
 * backends share the same nmb_server_t bookkeeping/dedup so a host
 * confirmed by either path only ever gets one entry.
 */
#if ENABLE_NATIVESMB || ENABLE_LIBSMB2
LIST_HEAD(nmb_server_list, nmb_server);

typedef struct nmb_server {
  LIST_ENTRY(nmb_server) ns_link;
  char *ns_workgroup;
  char *ns_name;
  int ns_mark;
  service_t *ns_service;
} nmb_server_t;

static struct nmb_server_list nmb_servers;
#endif

LIST_HEAD(nmb_resolve_list, nmb_resolve);

typedef struct nmb_resolve {
  LIST_ENTRY(nmb_resolve) nr_link;
  const char *nr_hostname;
  net_addr_t *nr_addr;
  uint16_t nr_txid;
  struct asyncio_timer nr_timeout;
  int nr_status;
} nmb_resolve_t;

static struct nmb_resolve_list nmb_resolve_pending;
static struct nmb_resolve_list nmb_resolve_sent;

static asyncio_fd_t *nmb_udp_fd;
static int nmb_resolver_signal;
static uint16_t nmb_transaction_id_tally;

#if ENABLE_NATIVESMB || ENABLE_LIBSMB2
static struct asyncio_timer nmb_timer;
static struct asyncio_timer nmb_flush_timer;
static uint16_t nmb_txid;
#if ENABLE_LIBSMB2
static uint16_t nmb_sweep_txid;
#endif
#endif

typedef struct {
  uint16_t transaction_id;
  uint16_t flags;
  uint16_t question_count;
  uint16_t answer_count;
  uint16_t name_service_count;
  uint16_t additional_record_count;

} __attribute__((packed)) nmbpkt_header_t;

typedef struct {
  nmbpkt_header_t h;
  char name[34];
  uint16_t type;
  uint16_t class;
} __attribute__((packed)) nmbpkt_question_t;


typedef struct {
  nmbpkt_header_t h;
  char name[34];
  uint16_t type;
  uint16_t class;
  uint32_t ttl;
  uint16_t length;
  uint16_t nodeflags;
  uint8_t addr[4];
} __attribute__((packed)) nmbpkt_answer_t;

#if ENABLE_LIBSMB2
/*
 * NBSTAT (node status, RFC 1002 4.2.18) response header. Shares its first
 * six fields (through 'length') byte-for-byte with nmbpkt_answer_t -- both
 * are just "RR_NAME/RR_TYPE/RR_CLASS/TTL/RDLENGTH" answer-record prefixes
 * -- but the payload that follows RDLENGTH differs: a positive NB name
 * query answer carries a fixed 6-byte nodeflags+address, while NBSTAT
 * carries a name count followed by that many 18-byte
 * (NETBIOS_NAME[15]+SUFFIX[1]+NAME_FLAGS[2]) entries. nmb_udp_input()
 * tells the two apart by RR_TYPE (0x20 vs 0x21) before picking which of
 * these two structs to interpret the packet as.
 */
typedef struct {
  nmbpkt_header_t h;
  char name[34];
  uint16_t type;
  uint16_t class;
  uint32_t ttl;
  uint16_t length;
  uint8_t num_names;
  /* num_names * 18-byte entries follow, then a statistics block we don't
   * need and don't parse. */
} __attribute__((packed)) nmbpkt_nbstat_reply_t;
#endif

/**
 *
 */
static void
encode_name(const char *in, char *out, char name_type)
{
  char buf[20] = {};
  char *p = out;
  int i;
  if(!strcmp(in, "*")) {
    buf[0] = '*';
  } else {
    snprintf(buf, sizeof(buf), "%-15.15s%c", in, name_type);
  }
  p[0] = 32;
  p++;
  for(i = 0; i < 16; i++) {
    uint8_t c = buf[i] >= 'a' && buf[i] <= 'z' ? buf[i] -32 : buf[i];
    p[i * 2    ] = (c >> 4)  + 'A';
    p[i * 2 + 1] = (c & 0xf) + 'A';
  }
  p += 32;
  p[0] = 0;
}



#if ENABLE_NATIVESMB || ENABLE_LIBSMB2
/**
 *
 */
static void
ns_destroy(nmb_server_t *ns)
{
  LIST_REMOVE(ns, ns_link);
  service_destroy(ns->ns_service);
  free(ns->ns_name);
  free(ns->ns_workgroup);
  free(ns);
}


/**
 *
 */
static void
remove_all_servers(void *aux)
{
  nmb_server_t *ns;
  while((ns = LIST_FIRST(&nmb_servers)) != NULL)
    ns_destroy(ns);
}


#if ENABLE_LIBSMB2
/*
 * NBSTAT (node status) resolver -- a small, purpose-built parallel to
 * nmb_resolve()'s broadcast name-query resolver above. Confirms a
 * master-browser candidate's NetBIOS name + workgroup with a *unicast*
 * query (RFC 1002 4.2.18) to the address itself, which works even
 * against hosts that refuse the SMB1 RAP browse-list (Buksa/movian#69):
 * "answers NetBIOS name/node-status" and "RAP browse-list works" are
 * different layers of the same modern-server lockdown.
 *
 * Shares nmb_mutex/nmb_resolver_cond with the resolver above (a stray
 * wakeup for the other queue is a harmless extra loop iteration, not a
 * correctness issue), but keeps its own pending/sent lists and worker
 * signal so this addition never touches the already-shipped nmb_resolve()
 * code path.
 */
typedef struct nmb_nbstat_query {
  LIST_ENTRY(nmb_nbstat_query) nq_link;
  net_addr_t nq_target;   /* unicast destination, port forced to 137 */
  uint16_t nq_txid;
  struct asyncio_timer nq_timeout;
  int nq_status;          /* 1 = pending, 0 = answered, -1 = failed/timeout */
  char nq_name[16];
  char nq_workgroup[16];
} nmb_nbstat_query_t;

LIST_HEAD(nmb_nbstat_query_list, nmb_nbstat_query);
static struct nmb_nbstat_query_list nmb_nbstat_pending; /* nmb_mutex-protected */
static struct nmb_nbstat_query_list nmb_nbstat_sent;    /* asyncio-thread only */
static int nmb_nbstat_signal;

#define NMB_NBSTAT_TIMEOUT_SEC 3
#define NMB_SMB2_PROBE_TIMEOUT_SEC 5

/**
 * Builds and sends one unicast NBSTAT query to 'target':137. The query
 * name is the wildcard "*" (encode_name() zero-pads the remaining 15
 * bytes for it), matching what `nmblookup -A` sends. Must run on the
 * asyncio thread (asyncio_udp_send() requires it) -- called only from
 * nmb_nbstat_process() below.
 */
static uint16_t
nmb_send_nbstat_query(const net_addr_t *target)
{
  net_addr_t na = *target;
  na.na_port = 137;

  nmbpkt_question_t pkt = {};
  uint16_t txid = ++nmb_transaction_id_tally;

  pkt.h.transaction_id = txid;
  pkt.h.question_count = htobe_16(1);
  encode_name("*", pkt.name, 0);
  pkt.type = htobe_16(0x21); /* NBSTAT */
  pkt.class = htobe_16(0x01);

  asyncio_udp_send(nmb_udp_fd, &pkt, sizeof(pkt), &na);
  return txid;
}


/**
 * Timer callback (asyncio thread): a query that got no NBSTAT reply
 * within NMB_NBSTAT_TIMEOUT_SEC. Mirrors nmb_resolve_timeout().
 */
static void
nmb_nbstat_timeout(void *aux)
{
  nmb_nbstat_query_t *nq = aux;

  hts_mutex_lock(&nmb_mutex);
  LIST_REMOVE(nq, nq_link);
  nq->nq_status = -1;
  hts_cond_broadcast(&nmb_resolver_cond);
  hts_mutex_unlock(&nmb_mutex);
}


/**
 * Worker callback (asyncio thread, invoked via asyncio_wakeup_worker()):
 * drains nmb_nbstat_pending, sends each query and arms its timeout.
 * Mirrors nmb_resolver_process().
 */
static void
nmb_nbstat_process(void)
{
  nmb_nbstat_query_t *nq;
  hts_mutex_lock(&nmb_mutex);

  while((nq = LIST_FIRST(&nmb_nbstat_pending)) != NULL) {
    LIST_REMOVE(nq, nq_link);
    hts_mutex_unlock(&nmb_mutex);
    LIST_INSERT_HEAD(&nmb_nbstat_sent, nq, nq_link);
    nq->nq_txid = nmb_send_nbstat_query(&nq->nq_target);
    asyncio_timer_init(&nq->nq_timeout, nmb_nbstat_timeout, nq);
    asyncio_timer_arm_delta_sec(&nq->nq_timeout, NMB_NBSTAT_TIMEOUT_SEC);
    hts_mutex_lock(&nmb_mutex);
  }
  hts_mutex_unlock(&nmb_mutex);
}


/**
 * Parse one NBSTAT reply's name table, picking out the workgroup (a group
 * name with suffix 0x00) and the host's own name (preferring the unique
 * <20> "File Server Service" name nmblookup shows for real SMB servers;
 * falling back to the unique <00> name if no <20> is registered).
 * 'data'/'size' are the whole UDP payload, already known to start with a
 * valid nmbpkt_nbstat_reply_t (caller checked). Does not touch nmb_mutex
 * or any query list -- pure parsing, called from the asyncio thread.
 */
static void
nmb_parse_nbstat_names(const uint8_t *data, int size,
                       char *name, size_t namesize,
                       char *workgroup, size_t groupsize)
{
  const nmbpkt_nbstat_reply_t *rep = (const void *)data;
  const uint8_t *p = data + sizeof(nmbpkt_nbstat_reply_t);
  int remain = size - (int)sizeof(nmbpkt_nbstat_reply_t);
  char fallback[16] = {0};

  name[0] = 0;
  workgroup[0] = 0;

  for(int i = 0; i < rep->num_names && remain >= 18; i++) {
    char raw[16];
    memcpy(raw, p, 15);
    raw[15] = 0;
    uint8_t suffix = p[15];
    uint16_t flags = rd16_be(p + 16);
    int group = (flags & 0x8000) != 0;

    for(int j = 14; j >= 0 && raw[j] == ' '; j--)
      raw[j] = 0;

    if(raw[0] != 0) {
      if(suffix == 0x00 && group && workgroup[0] == 0)
        snprintf(workgroup, groupsize, "%s", raw);
      else if(suffix == 0x20 && !group && name[0] == 0)
        snprintf(name, namesize, "%s", raw);
      else if(suffix == 0x00 && !group && fallback[0] == 0)
        snprintf(fallback, sizeof(fallback), "%s", raw);
    }

    p += 18;
    remain -= 18;
  }

  if(name[0] == 0 && fallback[0] != 0)
    snprintf(name, namesize, "%s", fallback);
}


/**
 * Called from nmb_udp_input() (asyncio thread) when an incoming packet's
 * RR_TYPE is NBSTAT (0x21). Matches it against nmb_nbstat_sent by
 * transaction id and wakes up the blocked nmb_node_status() caller.
 */
static void
nmb_handle_nbstat_reply(uint16_t txid, const uint8_t *data, int size)
{
  if((size_t)size < sizeof(nmbpkt_nbstat_reply_t))
    return;

  char name[16], workgroup[16];
  nmb_parse_nbstat_names(data, size, name, sizeof(name),
                        workgroup, sizeof(workgroup));

  nmb_nbstat_query_t *nq;
  LIST_FOREACH(nq, &nmb_nbstat_sent, nq_link) {
    if(nq->nq_txid == txid) {
      LIST_REMOVE(nq, nq_link);
      asyncio_timer_disarm(&nq->nq_timeout);

      hts_mutex_lock(&nmb_mutex);
      if(name[0] != 0) {
        snprintf(nq->nq_name, sizeof(nq->nq_name), "%s", name);
        snprintf(nq->nq_workgroup, sizeof(nq->nq_workgroup), "%s",
                 workgroup[0] != 0 ? workgroup : "WORKGROUP");
        nq->nq_status = 0;
      } else {
        nq->nq_status = -1;
      }
      hts_cond_broadcast(&nmb_resolver_cond);
      hts_mutex_unlock(&nmb_mutex);
      break;
    }
  }
}


/**
 * Blocking NBSTAT node-status query, mirroring nmb_resolve()'s
 * pending-queue/condvar dance. Must not be called from the asyncio
 * thread (it blocks waiting for that same thread to answer). Returns 0
 * and fills 'name'/'workgroup' on success, negative on timeout/failure.
 */
static int
nmb_node_status(const net_addr_t *target, char *name, size_t namesize,
               char *workgroup, size_t groupsize)
{
  nmb_nbstat_query_t nq = { .nq_target = *target, .nq_status = 1 };

  hts_mutex_lock(&nmb_mutex);
  LIST_INSERT_HEAD(&nmb_nbstat_pending, &nq, nq_link);
  asyncio_wakeup_worker(nmb_nbstat_signal);

  while(nq.nq_status == 1)
    hts_cond_wait(&nmb_resolver_cond, &nmb_mutex);

  hts_mutex_unlock(&nmb_mutex);

  if(nq.nq_status != 0)
    return -1;

  snprintf(name, namesize, "%s", nq.nq_name);
  snprintf(workgroup, groupsize, "%s", nq.nq_workgroup);
  return 0;
}


/**
 * Confirms 'addr' (dotted-quad) is a live SMB2 server with an anonymous
 * connect probe: negotiate + session setup only, no share/tree connect,
 * no credentials beyond guest/anonymous, no logging of anything
 * sensitive. smb2_set_passthrough(1) is what skips the implicit tree
 * connect that smb2_connect_share_async() would otherwise chain onto a
 * successful session setup -- so the "IPC$" share name below is never
 * actually sent on the wire, it only satisfies smb2_connect_share()'s
 * signature. smb2_set_timeout() bounds the whole synchronous call so an
 * unreachable or firewalled host can't stall the discovery pipeline.
 * Runs on the caller's task worker thread (query_master_browser() is
 * already task_run()'d off the asyncio thread by nmb_udp_input()).
 *
 * Two confirmation tiers (Buksa/movian#69, including its negotiate-only
 * "optional stretch"):
 *
 *  - The full connect completes: the host granted an anonymous/guest
 *    session (TRUENAS-style).
 *  - The connect fails but smb2_get_dialect() is non-zero afterwards:
 *    the SMB2 NEGOTIATE round-trip itself completed (the dialect is only
 *    ever set from a valid NEGOTIATE reply, and smb2_close_context()
 *    doesn't clear it), so the host provably speaks SMB2 -- it just
 *    refuses *anonymous session setup*, which is how locked-down Windows
 *    presents. Register it anyway: opening it goes through the smb2
 *    client's normal credential-prompt path, exactly like File Explorer
 *    showing a host it can't browse anonymously.
 *
 * Returns 0 if the host is confirmed to be an SMB2 server by either
 * tier, negative otherwise (no TCP, timeout, or not SMB2 at all).
 */
static int
nmb_smb2_connect_probe(const char *addr)
{
  struct smb2_context *smb2 = smb2_init_context();
  if(smb2 == NULL)
    return -1;

  smb2_set_timeout(smb2, NMB_SMB2_PROBE_TIMEOUT_SEC);
  smb2_set_passthrough(smb2, 1);
  smb2_set_security_mode(smb2, SMB2_NEGOTIATE_SIGNING_ENABLED);
  smb2_set_user(smb2, "guest");
  smb2_set_password(smb2, NULL);

  int status = smb2_connect_share(smb2, addr, "IPC$", "guest");
  if(status && smb2_get_dialect(smb2) != 0) {
    TRACE(TRACE_DEBUG, "NMB",
          "SMB2 probe of %s: negotiated dialect 0x%04x but anonymous "
          "session refused (%s) -- confirming via negotiate only",
          addr, smb2_get_dialect(smb2), smb2_get_error(smb2));
    status = 0;
  } else if(status) {
    TRACE(TRACE_DEBUG, "NMB", "SMB2 probe of %s failed -- %s",
          addr, smb2_get_error(smb2));
  }
  smb2_destroy_context(smb2);
  return status;
}


/**
 * The SMB2-probe fallback for a master-browser address whose RAP
 * browse-list came back empty/refused (see query_master_browser()
 * below): resolve its name/workgroup via NBSTAT, confirm it's really an
 * SMB2 server, then register it -- reusing the same nmb_servers dedup
 * list the RAP path uses, so a host later reachable via RAP collapses
 * into the same entry instead of duplicating it. The registered URL is
 * the address the master-browser reply and the probe both used, never
 * the NetBIOS name -- reachable-by-construction, per the WSD/#66 lesson.
 */
static void
nmb_smb2_probe_and_register(const net_addr_t *addr)
{
  char name[16], workgroup[16];
  nmb_server_t *ns;

  if(nmb_node_status(addr, name, sizeof(name), workgroup, sizeof(workgroup)))
    return;

  /* Already known (this path or RAP's)? Just refresh its sweep mark --
   * no point re-running a TCP connect against it every 15 s broadcast
   * round. The mark reset keeps remove_all_servers()/ns_mark aging from
   * expiring a host that still answers node-status. */
  hts_mutex_lock(&nmb_mutex);
  LIST_FOREACH(ns, &nmb_servers, ns_link) {
    if(!strcmp(ns->ns_name, name) && !strcmp(ns->ns_workgroup, workgroup)) {
      ns->ns_mark = 0;
      hts_mutex_unlock(&nmb_mutex);
      return;
    }
  }
  hts_mutex_unlock(&nmb_mutex);

  net_addr_t addr_only = *addr;
  addr_only.na_port = 0;
  char addrstr[32];
  snprintf(addrstr, sizeof(addrstr), "%s", net_addr_str(&addr_only));

  if(nmb_smb2_connect_probe(addrstr))
    return;

  char id[64];
  char url[64];
  snprintf(id, sizeof(id), "%s/%s", workgroup, name);
  snprintf(url, sizeof(url), "smb2://%s", addrstr);

  hts_mutex_lock(&nmb_mutex);

  /* Re-check under the lock: the probe ran unlocked, so another task
   * (a second master-browser reply in the same round, or the RAP path)
   * may have registered this host meanwhile. */
  LIST_FOREACH(ns, &nmb_servers, ns_link) {
    if(!strcmp(ns->ns_name, name) && !strcmp(ns->ns_workgroup, workgroup)) {
      ns->ns_mark = 0;
      hts_mutex_unlock(&nmb_mutex);
      return;
    }
  }

  ns = calloc(1, sizeof(nmb_server_t));
  ns->ns_name = strdup(name);
  ns->ns_workgroup = strdup(workgroup);
  LIST_INSERT_HEAD(&nmb_servers, ns, ns_link);
  ns->ns_service = service_create_managed(id, name, url, "server", NULL,
                                          0, 0, SVC_ORIGIN_DISCOVERED);
  hts_mutex_unlock(&nmb_mutex);

  TRACE(TRACE_DEBUG, "NMB", "Registered %s (%s) via SMB2 probe", url, id);
}


/*
 * Wildcard NBNS sweep (Buksa/movian#70): a broadcast NB name-query for "*"
 * (the nmblookup '*' technique) is sent alongside the periodic
 * __MSBROWSE__ query in nmb_send_msb_query() below, reaching every
 * NetBIOS host on the subnet that answers broadcast name queries -- not
 * just whichever host is the master browser. nmb_udp_input() routes
 * positive replies tagged with nmb_sweep_txid here instead of falling
 * into the master-browser or resolver paths.
 *
 * The candidate address is always the reply's SOURCE address
 * (remote_addr), never a payload address -- same reachable-by-construction
 * rule as the #66 lesson already applied to nmb_resolve()'s multi-homed
 * handling above; a wildcard reply's embedded address list is just as
 * unreliable.
 *
 * Bounding (per the #70 spec): nmb_sweep_seen dedupes by address so the
 * query re-firing every 15s doesn't re-spawn a probe task for a host
 * already probed or in flight; nmb_sweep_inflight caps concurrent probe
 * tasks so a subnet full of NetBIOS hosts answering at once can't spawn
 * unbounded task threads. Both the dedup check and the cap check happen
 * here, before task_run() -- the name/workgroup re-check already inside
 * nmb_smb2_probe_and_register() only catches duplicates *after* the
 * (comparatively expensive) NBSTAT+SMB2 round trip has already run.
 */
typedef struct nmb_sweep_seen {
  LIST_ENTRY(nmb_sweep_seen) ss_link;
  uint8_t ss_addr[4];
} nmb_sweep_seen_t;

static LIST_HEAD(nmb_sweep_seen_list, nmb_sweep_seen) nmb_sweep_seen;
static int nmb_sweep_inflight; /* nmb_mutex-protected */

#define NMB_SWEEP_MAX_INFLIGHT 8

/**
 * task_run() entry point for one sweep candidate. Runs on a task worker
 * thread, off the asyncio thread, same as query_master_browser()'s probe
 * path -- nmb_smb2_probe_and_register() already does NBSTAT + the
 * anonymous SMB2 probe + registration with its own internal dedup.
 */
static void
nmb_sweep_probe_task(void *aux)
{
  net_addr_t *na = aux;

  nmb_smb2_probe_and_register(na);
  free(na);

  hts_mutex_lock(&nmb_mutex);
  nmb_sweep_inflight--;
  hts_mutex_unlock(&nmb_mutex);
}

/**
 * Called from nmb_udp_input() (asyncio thread) for every positive reply
 * to the wildcard sweep query. 'addr' is the reply's source address
 * (na_port irrelevant, zeroed by the caller). Dedupes by address and caps
 * in-flight probe tasks before spawning a new one.
 */
static void
nmb_sweep_candidate(const net_addr_t *addr)
{
  nmb_sweep_seen_t *ss;

  hts_mutex_lock(&nmb_mutex);

  LIST_FOREACH(ss, &nmb_sweep_seen, ss_link) {
    if(!memcmp(ss->ss_addr, addr->na_addr, 4)) {
      /* Already probed (or currently being probed) this round -- skip,
       * don't re-run NBSTAT+connect against a host we already know. */
      hts_mutex_unlock(&nmb_mutex);
      return;
    }
  }

  if(nmb_sweep_inflight >= NMB_SWEEP_MAX_INFLIGHT) {
    TRACE(TRACE_DEBUG, "NMB",
          "Wildcard sweep: %d probes already in flight, dropping candidate %s",
          nmb_sweep_inflight, net_addr_str(addr));
    hts_mutex_unlock(&nmb_mutex);
    return;
  }

  ss = calloc(1, sizeof(nmb_sweep_seen_t));
  memcpy(ss->ss_addr, addr->na_addr, 4);
  LIST_INSERT_HEAD(&nmb_sweep_seen, ss, ss_link);
  nmb_sweep_inflight++;

  hts_mutex_unlock(&nmb_mutex);

  TRACE(TRACE_DEBUG, "NMB", "Wildcard sweep: new candidate %s, probing",
        net_addr_str(addr));

  net_addr_t *na = malloc(sizeof(net_addr_t));
  *na = *addr;
  na->na_port = 0;
  task_run(nmb_sweep_probe_task, na);
}
#endif /* ENABLE_LIBSMB2 */


/**
 * Handles one master-browser reply address (see nmb_udp_input()'s
 * nmb_txid dispatch below). The RAP browse-list (smb_enum_servers(), SMB1
 * over IPC$) is tried first when the native backend is present -- modern
 * servers refuse it (Buksa/movian#69), in which case, or when the native
 * backend isn't built at all, the SMB2 connect probe below is the only
 * way this address ever gets registered.
 */
static void
query_master_browser(void *a)
{
  net_addr_t na = {.na_family = 4};
  memcpy(na.na_addr, a, 4);
  free(a);

#if ENABLE_NATIVESMB
  nmb_server_t *ns, *next;
  char **servers = smb_enum_servers(net_addr_str(&na));
  if(servers != NULL) {
    /*
     * A modern server's browse-list lockdown doesn't only show up as
     * smb_enum_servers() failing outright (session-setup/tree-connect
     * refused) -- RAP can also complete successfully and just answer with
     * zero entries (observed live against a Windows 10 master browser,
     * Buksa/movian#69). Treat that the same as an outright failure: fall
     * through to the probe below instead of silently registering nothing.
     */
    int got_any = servers[0] != NULL;

    hts_mutex_lock(&nmb_mutex);

    LIST_FOREACH(ns, &nmb_servers, ns_link)
      ns->ns_mark++;

    for(int i = 0; servers[i] != NULL; i+=2) {
      const char *name = servers[i];
      const char *workgroup = servers[i+1];
      LIST_FOREACH(ns, &nmb_servers, ns_link) {
        if(!strcmp(ns->ns_name, name) && !strcmp(ns->ns_workgroup, workgroup))
          break;
      }
      if(ns == NULL) {
        char id[64];
        char url[64];

        ns = calloc(1, sizeof(nmb_server_t));
        ns->ns_name = strdup(name);
        ns->ns_workgroup = strdup(workgroup);
        LIST_INSERT_HEAD(&nmb_servers, ns, ns_link);

        snprintf(id, sizeof(id), "%s/%s", workgroup, name);
#if ENABLE_LIBSMB2
        snprintf(url, sizeof(url), "smb2://%s", name);
#else
        snprintf(url, sizeof(url), "smb://%s", name);
#endif

        ns->ns_service = service_create_managed(id, name, url, "server", NULL,
                                                0, 0, SVC_ORIGIN_DISCOVERED);
      } else {
        ns->ns_mark = 0;
      }
    }

    strvec_free(servers);

    for(ns = LIST_FIRST(&nmb_servers); ns != NULL; ns = next) {
      next = LIST_NEXT(ns, ns_link);
      if(ns->ns_mark >= 2)
        ns_destroy(ns);
    }
    hts_mutex_unlock(&nmb_mutex);

    if(got_any)
      return;
  }
#endif /* ENABLE_NATIVESMB */

#if ENABLE_LIBSMB2
  /* No native backend, smb_enum_servers() failed outright, or its RAP
   * browse-list came back empty: fall back to confirming this address
   * ourselves. */
  nmb_smb2_probe_and_register(&na);
#endif
}
#endif /* ENABLE_NATIVESMB || ENABLE_LIBSMB2 */

/**
 * Address entry 'i' (0-based) of an NBNS positive name-query answer.
 *
 * Entry 0 (2-byte nodeflags + 4-byte IPv4) is embedded directly in
 * nmbpkt_answer_t right after the RDLENGTH field. A multi-homed host
 * (VirtualBox/VMware/Hyper-V/WSL adapters alongside the real LAN NIC)
 * answers a broadcast name query with one such 6-byte entry per
 * interface; entries 1..N-1 follow contiguously in the trailing packet
 * bytes. Caller must bounds-check 'i' against the RDLENGTH-derived
 * count (and that count against the actual packet size) first.
 */
static const uint8_t *
nmb_answer_addr(const nmbpkt_answer_t *pkt, int i)
{
  if(i == 0)
    return pkt->addr;
  return (const uint8_t *)pkt + sizeof(nmbpkt_answer_t) + (i - 1) * 6 + 2;
}


/**
 *
 */
static void
nmb_udp_input(void *opaque, const void *data, int size,
              const net_addr_t *remote_addr)
{
  // Enough bytes to read RR_NAME/RR_TYPE/RR_CLASS (the nmbpkt_question_t
  // prefix, common to every answer shape this socket receives) before
  // deciding which full struct actually applies below.
  if(size < sizeof(nmbpkt_question_t))
    return;

  const nmbpkt_answer_t *pkt = data;
  uint16_t flags = betoh_16(pkt->h.flags);
  if(!(flags & 0x8000))
    return;
  if(betoh_16(pkt->h.answer_count) < 1)
    return;

#if ENABLE_LIBSMB2
  // NBSTAT (0x21) replies carry a variable-length name table, not the
  // fixed nodeflags+address layout nmbpkt_answer_t assumes below -- route
  // them to their own parser before any of that code looks at the packet.
  if(betoh_16(pkt->type) == 0x21) {
    nmb_handle_nbstat_reply(pkt->h.transaction_id, data, size);
    return;
  }
#endif

  if(size < sizeof(nmbpkt_answer_t))
    return;

  // RDLENGTH is 6 bytes (nodeflags + IPv4) per returned address; a
  // multi-homed host sends RDLENGTH = 6*N for its N interfaces. Reject
  // anything that isn't a positive multiple of 6, then make sure the
  // packet actually carries that many trailing bytes before touching them.
  uint16_t rdlength = betoh_16(pkt->length);
  if(rdlength == 0 || rdlength % 6 != 0)
    return;

  int count = rdlength / 6;
  if((size_t)size < sizeof(nmbpkt_answer_t) + (size_t)(count - 1) * 6)
    return;

#if ENABLE_NATIVESMB || ENABLE_LIBSMB2
  if(pkt->h.transaction_id == nmb_txid) {
    void *a = malloc(4);
    memcpy(a, pkt->addr, 4);
    task_run(query_master_browser, a);
    asyncio_timer_arm_delta_sec(&nmb_flush_timer, 60);
    return;
  }

#if ENABLE_LIBSMB2
  if(pkt->h.transaction_id == nmb_sweep_txid) {
    if(remote_addr != NULL && remote_addr->na_family == 4)
      nmb_sweep_candidate(remote_addr);
    return;
  }
#endif
#endif

  nmb_resolve_t *nr;

  LIST_FOREACH(nr, &nmb_resolve_sent, nr_link) {
    if(nr->nr_txid == pkt->h.transaction_id) {
      LIST_REMOVE(nr, nr_link);
      asyncio_timer_disarm(&nr->nr_timeout);

      // Address selection for multi-homed replies: prefer the entry that
      // matches the reply's source address (remote_addr) -- that's the
      // interface the host actually answered from, so it's reachable by
      // construction. If none of the entries match it exactly, still
      // prefer remote_addr itself over any of the (possibly unreachable
      // virtual-adapter) entries. Only fall back to the first entry if
      // remote_addr isn't a usable IPv4 address. This keeps the
      // single-address path byte-identical, since there entry 0 already
      // equals remote_addr in practice.
      const uint8_t *chosen;

      if(remote_addr != NULL && remote_addr->na_family == 4) {
        chosen = remote_addr->na_addr;
        for(int i = 0; i < count; i++) {
          const uint8_t *a = nmb_answer_addr(pkt, i);
          net_addr_t entry = { .na_family = 4, .na_port = remote_addr->na_port };
          memcpy(entry.na_addr, a, 4);
          if(!net_addr_cmp(&entry, remote_addr)) {
            chosen = a;
            break;
          }
        }
      } else {
        chosen = nmb_answer_addr(pkt, 0);
      }

      nr->nr_addr->na_family = 4;
      memcpy(nr->nr_addr->na_addr, chosen, 4);
      hts_mutex_lock(&nmb_mutex);
      nr->nr_status = 0;
      hts_cond_broadcast(&nmb_resolver_cond);
      hts_mutex_unlock(&nmb_mutex);

      break;
    }
  }
}


/**
 *
 */
static uint16_t
nmb_send_query(const char *name, uint8_t type, int dups)
{
  net_addr_t na = {.na_family=4, .na_port=137, .na_addr={0xff,0xff,0xff,0xff}};

  nmbpkt_question_t pkt = {};

  uint16_t txid =  ++nmb_transaction_id_tally;

  pkt.h.transaction_id = txid;
  pkt.h.flags = htobe_16(0x0110);
  pkt.h.question_count = htobe_16(1);
  encode_name(name, pkt.name, type);
  pkt.type = htobe_16(0x20);
  pkt.class = htobe_16(0x01);

  for(int i = 0; i <= dups; i++) {
    asyncio_udp_send(nmb_udp_fd, &pkt, sizeof(pkt), &na);
  }
  return txid;
}


#if ENABLE_NATIVESMB || ENABLE_LIBSMB2
/**
 *
 */
static void
nmb_send_msb_query(void *aux)
{
  asyncio_timer_arm_delta_sec(&nmb_timer, 15);

  nmb_txid = nmb_send_query("\001\002__MSBROWSE__\002\001", 1, 0);

#if ENABLE_LIBSMB2
  /* Wildcard NBNS sweep (#70): one extra broadcast packet per tick, no
   * new timer. encode_name() special-cases "*" (see its NBSTAT use above)
   * so the name_type argument here is unused. */
  nmb_sweep_txid = nmb_send_query("*", 0, 0);
#endif
}
#endif /* ENABLE_NATIVESMB || ENABLE_LIBSMB2 */


/**
 *
 */
int
nmb_resolve(const char *hostname, struct net_addr *na)
{
  if(nmb_udp_fd == NULL) {
    // UDP socket bind failed at runtime (nmb_init() is unconditional, but
    // asyncio_udp_bind() can still fail) -- nothing to send the query on,
    // fail cleanly instead of touching a NULL fd.
    return -1;
  }

  nmb_resolve_t nr = {
    .nr_hostname = hostname,
    .nr_addr = na,
    .nr_status = 1
  };

  hts_mutex_lock(&nmb_mutex);

  LIST_INSERT_HEAD(&nmb_resolve_pending, &nr, nr_link);
  asyncio_wakeup_worker(nmb_resolver_signal);

  while(nr.nr_status == 1)
    hts_cond_wait(&nmb_resolver_cond, &nmb_mutex);

  hts_mutex_unlock(&nmb_mutex);
  return nr.nr_status;
}


/**
 *
 */
static void
nmb_resolve_timeout(void *aux)
{
  nmb_resolve_t *nr = aux;

  hts_mutex_lock(&nmb_mutex);
  LIST_REMOVE(nr, nr_link);
  nr->nr_status = -1;
  hts_cond_broadcast(&nmb_resolver_cond);
  hts_mutex_unlock(&nmb_mutex);
}

/**
 *
 */
static void
nmb_resolver_process(void)
{
  nmb_resolve_t *nr;
  hts_mutex_lock(&nmb_mutex);

  while((nr = LIST_FIRST(&nmb_resolve_pending)) != NULL) {
    LIST_REMOVE(nr, nr_link);
    hts_mutex_unlock(&nmb_mutex);
    LIST_INSERT_HEAD(&nmb_resolve_sent, nr, nr_link);
    nr->nr_txid = nmb_send_query(nr->nr_hostname, 0x20, 1);
    asyncio_timer_init(&nr->nr_timeout, nmb_resolve_timeout, nr);
    asyncio_timer_arm_delta_sec(&nr->nr_timeout, 3);
    hts_mutex_lock(&nmb_mutex);
  }
  hts_mutex_unlock(&nmb_mutex);
}


/**
 *
 */
static void
nmb_resolver_init(void)
{
  nmb_resolver_signal = asyncio_add_worker(nmb_resolver_process);
  hts_mutex_init(&nmb_mutex);
  hts_cond_init(&nmb_resolver_cond, &nmb_mutex);
}


INITME(INIT_GROUP_NET, nmb_resolver_init, NULL, 0);

/**
 * Binds the shared NBT UDP socket that all NBT queries (both the
 * nmb_resolve() DNS-fallback resolver and LAN master-browser discovery)
 * are sent/received on. Unconditional: both backends need nmb_resolve()
 * to work, so the socket must exist even in a build with neither SMB
 * backend's discovery wired up (which can't happen today -- nmb.c is
 * only compiled when at least one of CONFIG_NATIVESMB/CONFIG_LIBSMB2 is
 * set -- but keeps this function correct if that ever changes).
 *
 * The master-browser discovery loop itself (query_master_browser(), the
 * only consumer of the discovery replies below) runs whenever either
 * backend can make use of it: the native backend enumerates via
 * smb_enum_servers() (fa_nativesmb.c), the libsmb2 backend confirms via
 * the SMB2 connect probe (Buksa/movian#69) when that enumeration is
 * unavailable or refused.
 */
static void
nmb_init(void)
{
  nmb_udp_fd = asyncio_udp_bind("nmb", 0, nmb_udp_input, NULL, 0, 1);

#if ENABLE_LIBSMB2
  nmb_nbstat_signal = asyncio_add_worker(nmb_nbstat_process);
#endif

#if ENABLE_NATIVESMB || ENABLE_LIBSMB2
  asyncio_timer_init(&nmb_timer, nmb_send_msb_query, NULL);
  asyncio_timer_init(&nmb_flush_timer, remove_all_servers, NULL);

  nmb_send_msb_query(NULL);
#endif /* ENABLE_NATIVESMB || ENABLE_LIBSMB2 */
}

INITME(INIT_GROUP_ASYNCIO, nmb_init, NULL, 0);
