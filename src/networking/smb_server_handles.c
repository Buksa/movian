/* -*-  mode:c; tab-width:8; c-basic-offset:8; indent-tabs-mode:nil;  -*- */
/*
 * Movian SMB2 server — FileId + handle-table management.
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
smb_file_entry_t *
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

smb_file_entry_t *
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

void
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
    free(fe->dir_pattern);
    free(fe->path);
    memset(fe, 0, sizeof(*fe));
}

int
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

void
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

int
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
