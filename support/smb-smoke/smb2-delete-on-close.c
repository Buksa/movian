/*
 * Raw SMB2 delete-on-close contract probe for the embedded Movian server.
 *
 * The harness deliberately exercises the wire CREATE/CLOSE and
 * FILE_DISPOSITION_INFORMATION paths instead of relying on smbclient's
 * higher-level command parser.  It reports server status; filesystem
 * assertions belong to the shell smoke so permission/race outcomes remain
 * observable without changing production behavior.
 */

#define _GNU_SOURCE

#include <errno.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <smb2/smb2.h>
#include <smb2/libsmb2.h>
#include <smb2/libsmb2-raw.h>

struct wait_state {
        int done;
        uint32_t status;
        smb2_file_id file_id;
};

static void
create_cb(struct smb2_context *smb2, int status, void *command_data,
          void *private_data)
{
        struct wait_state *state = private_data;
        struct smb2_create_reply *reply = command_data;

        (void)smb2;
        state->status = status;
        if (status == SMB2_STATUS_SUCCESS && reply != NULL) {
                memcpy(state->file_id, reply->file_id, SMB2_FD_SIZE);
        }
}

static void
create_only_cb(struct smb2_context *smb2, int status, void *command_data,
               void *private_data)
{
        struct wait_state *state = private_data;

        create_cb(smb2, status, command_data, private_data);
        state->done = 1;
}

static void
middle_cb(struct smb2_context *smb2, int status, void *command_data,
          void *private_data)
{
        struct wait_state *state = private_data;

        (void)smb2;
        (void)command_data;
        state->status = status;
}

static void
final_cb(struct smb2_context *smb2, int status, void *command_data,
         void *private_data)
{
        struct wait_state *state = private_data;

        (void)smb2;
        (void)command_data;
        state->status = status;
        state->done = 1;
}

static int
wait_for_reply(struct smb2_context *smb2, struct wait_state *state)
{
        time_t deadline = time(NULL) + 20;

        while (!state->done) {
                struct pollfd pfd = {
                        .fd = smb2_get_fd(smb2),
                        .events = smb2_which_events(smb2),
                };
                int rc = poll(&pfd, 1, 1000);

                if (rc < 0) {
                        if (errno == EINTR)
                                continue;
                        fprintf(stderr, "poll failed: %s\n", strerror(errno));
                        return -1;
                }
                if (rc == 0) {
                        if (time(NULL) >= deadline) {
                                fprintf(stderr, "timed out waiting for SMB2 reply\n");
                                return -1;
                        }
                        continue;
                }
                if (smb2_service(smb2, pfd.revents) < 0) {
                        fprintf(stderr, "smb2_service failed: %s\n",
                                smb2_get_error(smb2));
                        return -1;
                }
        }
        return 0;
}

static void
fill_create_request(struct smb2_create_request *request, const char *path,
                    int is_dir, int delete_on_close)
{
        memset(request, 0, sizeof(*request));
        request->requested_oplock_level = SMB2_OPLOCK_LEVEL_NONE;
        request->impersonation_level = SMB2_IMPERSONATION_IMPERSONATION;
        request->desired_access = SMB2_DELETE | SMB2_FILE_READ_ATTRIBUTES;
        if (is_dir)
                request->desired_access |= SMB2_FILE_LIST_DIRECTORY;
        request->file_attributes = is_dir ? SMB2_FILE_ATTRIBUTE_DIRECTORY
                                          : SMB2_FILE_ATTRIBUTE_NORMAL;
        request->share_access = SMB2_FILE_SHARE_READ |
                                SMB2_FILE_SHARE_WRITE |
                                SMB2_FILE_SHARE_DELETE;
        request->create_disposition = SMB2_FILE_OPEN;
        request->create_options = is_dir ? SMB2_FILE_DIRECTORY_FILE : 0;
        if (delete_on_close)
                request->create_options |= SMB2_FILE_DELETE_ON_CLOSE;
        request->name = path;
}

static int
send_delete_on_close(struct smb2_context *smb2, const char *path, int is_dir,
                      struct wait_state *state)
{
        struct smb2_create_request create_request;
        struct smb2_close_request close_request;
        struct smb2_pdu *pdu, *next_pdu;

        memset(state, 0, sizeof(*state));
        state->status = UINT32_MAX;
        fill_create_request(&create_request, path, is_dir, 1);

        pdu = smb2_cmd_create_async(smb2, &create_request, create_cb, state);
        if (pdu == NULL) {
                fprintf(stderr, "CREATE construction failed: %s\n",
                        smb2_get_error(smb2));
                return -1;
        }

        memset(&close_request, 0, sizeof(close_request));
        close_request.flags = SMB2_CLOSE_FLAG_POSTQUERY_ATTRIB;
        memcpy(close_request.file_id, compound_file_id, SMB2_FD_SIZE);
        next_pdu = smb2_cmd_close_async(smb2, &close_request, final_cb, state);
        if (next_pdu == NULL) {
                fprintf(stderr, "CLOSE construction failed: %s\n",
                        smb2_get_error(smb2));
                smb2_free_pdu(smb2, pdu);
                return -1;
        }
        smb2_add_compound_pdu(smb2, pdu, next_pdu);
        smb2_queue_pdu(smb2, pdu);
        return wait_for_reply(smb2, state);
}

static int
send_disposition_and_close(struct smb2_context *smb2, const char *path,
                            struct wait_state *state)
{
        struct smb2_create_request create_request;
        struct smb2_set_info_request set_request;
        struct smb2_file_disposition_info disposition = {
                .delete_pending = 1,
        };
        struct smb2_close_request close_request;
        struct smb2_pdu *pdu, *next_pdu;

        memset(state, 0, sizeof(*state));
        state->status = UINT32_MAX;
        fill_create_request(&create_request, path, 0, 0);

        pdu = smb2_cmd_create_async(smb2, &create_request, create_cb, state);
        if (pdu == NULL) {
                fprintf(stderr, "CREATE construction failed: %s\n",
                        smb2_get_error(smb2));
                return -1;
        }

        memset(&set_request, 0, sizeof(set_request));
        set_request.info_type = SMB2_0_INFO_FILE;
        set_request.file_info_class = SMB2_FILE_DISPOSITION_INFORMATION;
        set_request.buffer_length = sizeof(disposition);
        set_request.additional_information = 0;
        memcpy(set_request.file_id, compound_file_id, SMB2_FD_SIZE);
        set_request.input_data = &disposition;
        next_pdu = smb2_cmd_set_info_async(smb2, &set_request, middle_cb,
                                           state);
        if (next_pdu == NULL) {
                fprintf(stderr, "SET_INFO construction failed: %s\n",
                        smb2_get_error(smb2));
                smb2_free_pdu(smb2, pdu);
                return -1;
        }
        smb2_add_compound_pdu(smb2, pdu, next_pdu);

        memset(&close_request, 0, sizeof(close_request));
        close_request.flags = SMB2_CLOSE_FLAG_POSTQUERY_ATTRIB;
        memcpy(close_request.file_id, compound_file_id, SMB2_FD_SIZE);
        next_pdu = smb2_cmd_close_async(smb2, &close_request, final_cb, state);
        if (next_pdu == NULL) {
                fprintf(stderr, "CLOSE construction failed: %s\n",
                        smb2_get_error(smb2));
                smb2_free_pdu(smb2, pdu);
                return -1;
        }
        smb2_add_compound_pdu(smb2, pdu, next_pdu);
        smb2_queue_pdu(smb2, pdu);
        return wait_for_reply(smb2, state);
}

static int
send_create_only(struct smb2_context *smb2, const char *path, int is_dir,
                  struct wait_state *state)
{
        struct smb2_create_request request;
        struct smb2_pdu *pdu;

        memset(state, 0, sizeof(*state));
        state->status = UINT32_MAX;
        fill_create_request(&request, path, is_dir, 1);
        pdu = smb2_cmd_create_async(smb2, &request, create_only_cb, state);
        if (pdu == NULL) {
                fprintf(stderr, "CREATE construction failed: %s\n",
                        smb2_get_error(smb2));
                return -1;
        }
        smb2_queue_pdu(smb2, pdu);
        if (wait_for_reply(smb2, state) < 0)
                return -1;
        if (state->status != SMB2_STATUS_SUCCESS) {
                fprintf(stderr, "CREATE failed: 0x%08x %s\n", state->status,
                        nterror_to_str(state->status));
                return -1;
        }
        return 0;
}

static int
close_created_file(struct smb2_context *smb2, struct wait_state *state)
{
        struct smb2fh *fh = smb2_fh_from_file_id(smb2, &state->file_id);
        int status;

        if (fh == NULL) {
                fprintf(stderr, "cannot create file handle: %s\n",
                        smb2_get_error(smb2));
                return -1;
        }
        status = smb2_close(smb2, fh);
        state->status = status;
        return status == 0 ? 0 : -1;
}

static int
connect_context(struct smb2_context **out, const char *host, int port,
                const char *share, const char *user, const char *password,
                const char *domain)
{
        char server[256];
        struct smb2_context *smb2 = smb2_init_context();

        if (smb2 == NULL) {
                fprintf(stderr, "smb2_init_context failed\n");
                return -1;
        }
        if (snprintf(server, sizeof(server), "%s:%d", host, port) >=
            (int)sizeof(server)) {
                fprintf(stderr, "server address is too long\n");
                smb2_destroy_context(smb2);
                return -1;
        }
        smb2_set_security_mode(smb2, SMB2_NEGOTIATE_SIGNING_ENABLED);
        smb2_set_user(smb2, user);
        smb2_set_password(smb2, password);
        smb2_set_domain(smb2, domain);
        if (smb2_connect_share(smb2, server, share, user) != 0) {
                fprintf(stderr, "smb2_connect_share failed: %s\n",
                        smb2_get_error(smb2));
                smb2_destroy_context(smb2);
                return -1;
        }
        *out = smb2;
        return 0;
}

static void
usage(const char *program)
{
        fprintf(stderr,
                "usage: %s HOST PORT SHARE USER PASSWORD DOMAIN OP PATH [LOCAL_PATH]\n"
                "operations: delete-file delete-dir disposition race disconnect\n",
                program);
}

int
main(int argc, char **argv)
{
        struct smb2_context *smb2 = NULL;
        struct wait_state state;
        const char *host;
        const char *share;
        const char *user;
        const char *password;
        const char *domain;
        const char *operation;
        const char *path;
        int port;
        int rc = 1;

        if (argc < 9 || argc > 10) {
                usage(argv[0]);
                return 2;
        }
        host = argv[1];
        port = atoi(argv[2]);
        share = argv[3];
        user = argv[4];
        password = argv[5];
        domain = argv[6];
        operation = argv[7];
        path = argv[8];
        if (port < 1 || port > 65535) {
                fprintf(stderr, "invalid port: %s\n", argv[2]);
                return 2;
        }
        if (connect_context(&smb2, host, port, share, user, password,
                            domain) < 0)
                return 1;

        if (strcmp(operation, "delete-file") == 0) {
                rc = send_delete_on_close(smb2, path, 0, &state);
        } else if (strcmp(operation, "delete-dir") == 0) {
                rc = send_delete_on_close(smb2, path, 1, &state);
        } else if (strcmp(operation, "disposition") == 0) {
                rc = send_disposition_and_close(smb2, path, &state);
        } else if (strcmp(operation, "race") == 0) {
                if (argc != 10) {
                        usage(argv[0]);
                        goto out;
                }
                rc = send_create_only(smb2, path, 0, &state);
                if (rc == 0 && unlink(argv[9]) < 0 && errno != ENOENT) {
                        fprintf(stderr, "unlink race target failed: %s\n",
                                strerror(errno));
                        rc = -1;
                }
                if (rc == 0)
                        rc = close_created_file(smb2, &state);
        } else if (strcmp(operation, "disconnect") == 0) {
                rc = send_create_only(smb2, path, 0, &state);
                if (rc == 0)
                        rc = smb2_disconnect_share(smb2);
        } else {
                usage(argv[0]);
                goto out;
        }

        printf("operation=%s status=0x%08x\n", operation, state.status);
out:
        if (smb2 != NULL)
                smb2_destroy_context(smb2);
        return rc == 0 ? 0 : 1;
}
