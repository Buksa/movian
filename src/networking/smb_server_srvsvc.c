/* -*-  mode:c; tab-width:8; c-basic-offset:8; indent-tabs-mode:nil;  -*- */
/*
 * Movian SMB2 server — IOCTL handler (srvsvc / IPC$ named-pipe transceive).
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
/* Handler: IOCTL                                                       */
/* ------------------------------------------------------------------ */

int
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
