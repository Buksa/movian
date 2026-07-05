
ifeq ($(shell uname),Darwin)
GITVER_MD5 := md5
else
GITVER_MD5 := md5sum
endif

GITVER_VARGUARD = $(1)_GUARD_$(shell echo $($(1)) | ${GITVER_MD5} | cut -d ' ' -f 1)

# `git describe --dirty`'s dirty-check touches every submodule's gitlink.
# Those are relative paths computed for the checkout's original location,
# so they break (fatal: not a git repository: ...) whenever the tree is
# copied elsewhere first -- e.g. flatpak-builder's `type: dir` source copy.
# support/flatpak/build-local.sh works around this by computing the version
# on the host (where the copy hasn't happened yet) and dropping it in
# .movian-version-override for the sandboxed build to pick up here.
GIT_DESCRIBE_OUTPUT := $(shell (if [ -s "${C}/.movian-version-override" ]; then cat "${C}/.movian-version-override"; else git describe --dirty --abbrev=5 2>/dev/null; fi) | sed -e 's/-/./g')

${BUILDDIR}/version_git.h: ${BUILDDIR}/gitver/$(call GITVER_VARGUARD,GIT_DESCRIBE_OUTPUT)
	echo >$@ "#define VERSION_GIT \"${GIT_DESCRIBE_OUTPUT}\""

${BUILDDIR}/gitver/$(call GITVER_VARGUARD,GIT_DESCRIBE_OUTPUT):
	@rm -rf "${BUILDDIR}/gitver"
	@mkdir -p "${BUILDDIR}/gitver"
	@touch $@
