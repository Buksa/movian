#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#include <smb2/smb2.h>
#include <smb2/libsmb2.h>

int
main(int argc, char **argv)
{
  if(argc != 7) {
    fprintf(stderr,
            "usage: %s <server> <share> <path> <user> <password> <domain>\n",
            argv[0]);
    return 2;
  }

  struct smb2_context *smb2 = smb2_init_context();
  if(smb2 == NULL) {
    fprintf(stderr, "smb2_init_context failed\n");
    return 2;
  }

  smb2_set_version(smb2, SMB2_VERSION_0210);
  smb2_set_security_mode(smb2, SMB2_NEGOTIATE_SIGNING_ENABLED);
  smb2_set_user(smb2, argv[4]);
  smb2_set_password(smb2, argv[5]);
  smb2_set_domain(smb2, argv[6]);

  if(smb2_connect_share(smb2, argv[1], argv[2], argv[4]) != 0) {
    fprintf(stderr, "smb2_connect_share failed: %s\n", smb2_get_error(smb2));
    smb2_destroy_context(smb2);
    return 1;
  }

  struct smb2fh *fh = smb2_open(smb2, argv[3], O_RDWR);
  if(fh == NULL) {
    fprintf(stderr, "smb2_open failed: %s\n", smb2_get_error(smb2));
    smb2_disconnect_share(smb2);
    smb2_destroy_context(smb2);
    return 1;
  }

  if(smb2_close(smb2, fh) != 0) {
    fprintf(stderr, "smb2_close failed: %s\n", smb2_get_error(smb2));
    smb2_disconnect_share(smb2);
    smb2_destroy_context(smb2);
    return 1;
  }

  smb2_disconnect_share(smb2);
  smb2_destroy_context(smb2);
  return 0;
}
