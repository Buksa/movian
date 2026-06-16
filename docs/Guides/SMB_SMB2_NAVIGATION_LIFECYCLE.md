# SMB And SMB2 Navigation Lifecycle

This note captures the observed navigation behavior for SMB1 and SMB2 host
roots, and the points that should stay comparable when debugging auth or share
listing regressions.

## SMB1 Lifecycle

SMB1 host navigation is centered on `cifs_resolve()` in
`src/fileaccess/smb/fa_nativesmb.c`.

- `smb_stat()` and `smb_scandir()` both resolve the URL through
  `cifs_resolve()`.
- Host-root resolution first attempts a guest/default connection with
  `CC_F_AS_GUEST`.
- `cifs_get_connection()` logs the TCP connect, protocol negotiation, session
  setup, and disconnect lifecycle through `SMBTRACE`.
- `smb_setup_andX()` logs the attempted setup identity as
  `SETUP user:password-state:domain`, then logs the SMB status code.
- If guest/default setup fails with an auth-style error, `cifs_resolve()`
  retries without `CC_F_AS_GUEST`, which can trigger the keyring prompt.
- Host-root `stat` reports `CONTENT_SHARE`, and host-root `scandir` enumerates
  shares.

The expected first-auth user experience is therefore a clean login prompt with
domain `WORKGROUP`, not a raw server status string before the user has entered
credentials.

## SMB2 Lifecycle

SMB2 navigation is implemented in `src/fileaccess/smb2/fa_libsmb2.c`.

- URL parsing is handled by `movian_smb2_target_parse()`.
- Auth and connection retry are centralized in `movian_smb2_connect_impl()`.
- One connection attempt is performed by `movian_smb2_connect_once()`, which
  wraps `libsmb2` `smb2_connect_share()`.
- Host-root scans connect to `IPC$` and call `smb2_share_enum_async()`.
- Directory scans connect to the target share and use `smb2_opendir()` /
  `smb2_readdir()`.

For parity with SMB1, SMB2 now traces the key lifecycle points under the `SMB2`
debug component: keyring hit/miss, setup identity with password redacted,
connect result, auth retry decision, prompt result, redirect, and share enum
add/filter decisions.

## Navigation Parity Notes

- `smb://host` and `smb2://host` should both navigate to the host root. SMB2
  redirects the no-trailing-slash host URL to `smb2://host/`.
- `smb://host/` and `smb2://host/` should both show the normal disk shares
  visible to the authenticated user.
- SMB2 share enumeration intentionally adds only shares whose type is exactly
  `SHARE_TYPE_DISKTREE`. Hidden/admin shares such as `ADMIN$`, `C$`, or `F$`
  are filtered from normal root navigation.
- A first interactive SMB2 auth prompt with no saved credentials should show
  `Login required` and prefill domain `WORKGROUP`.
- Background or startup probes on the `asyncio` thread must not open an
  interactive auth popup.

## Validated Decisions

- Use `movian_smb2_connect_impl()` as the single SMB2 auth lifecycle point.
  It is called by normal SMB2 connects and `stat`, so keyring behavior,
  auth-retry decisions, and non-interactive suppression stay consistent.
- Show `Login required` on the first interactive prompt when no saved
  credentials were present. Preserve the raw SMB2 failure reason only after
  saved or user-entered credentials fail.
- Prefill the prompt domain with `WORKGROUP` when the URL does not specify a
  domain. This matches the SMB1 first-auth experience and avoids an empty domain
  field on Windows shares.
- Suppress interactive credential prompts from the `asyncio` thread. Startup
  and background probes may discover that auth is needed, but they must not
  steal focus with an auth popup.
- Treat `smb2://host` as a host-root URL and redirect it to `smb2://host/`.
  This closes the no-trailing-slash mismatch with SMB1 navigation.
- Report SMB2 host-root `stat` as `CONTENT_SHARE`, matching SMB1 host-root
  behavior.
- Filter SMB2 root shares by exact `SHARE_TYPE_DISKTREE`. This keeps normal
  navigation aligned with SMB1-visible shares and hides admin/hidden shares.
- Preseed `persistent/settings/dev` with `{ "smbdebug": 1 }` for runtime
  lifecycle tests. `-d` enables verbose process logging, but SMB/SMB2 trace
  lines still require the developer `smbdebug` setting.
- Seed runtime credentials for automation through
  `persistent/settings/keyring` using the real keyring JSON shape. This gives a
  deterministic profile without relying on old `/tmp` state or manual popup
  input.
- Inspect active auth popups through `/api/prop/global/popups/*0`. The HTTP
  `*0` segment is the exported view of the first unnamed popup child.
- To fill popup fields through STPP, subscribe to the `popups` directory, read
  the exported popup child propref from the child-add event, then send `SET`
  relative to that propref, for example `[3, child_ref, "username", "user"]`.
  This was verified to update `/api/prop/global/popups/*0/username` and
  `/api/prop/global/popups/*0/domain`.
- Wait for the expected page URL before asserting `loading=0` or node content.
  Without the URL check, tests can sample the previous page and produce false
  failures during fast route transitions.

## Rejected Or Failed Approaches

- Do not show raw `STATUS_INVALID_PARAMETER` or similar libsmb2/server errors
  on the first prompt when no credentials have been entered. That message is
  useful after a failed credential attempt, but it is misleading as the initial
  login reason.
- Do not allow startup/background SMB2 probes to invoke `KEYRING_QUERY_USER`.
  A startup popup was observed from the `asyncio` thread for
  `smb2:connection:<server>`; the correct behavior is to report auth
  needed without displaying UI.
- Do not copy old isolated profiles from `/tmp` as proof of saved credentials.
  A copied profile may not contain `persistent/settings/keyring`, which caused
  SMB1 to fall back to the login prompt during testing.
- Do not use `/api/prop` `POST value=...` to set settings or auth fields. The
  HTTP prop endpoint only handles `action` and `debug`; direct value writes
  return `400`.
- Do not rely on STPP wildcard paths such as `popups.*0.username` to fill the
  auth popup. `*0` is an HTTP `/api/prop` display alias for an unnamed child,
  not a real dot-path segment. STPP reported a value for that wildcard path,
  but the actual HTTP popup prop still showed `username` as void.
- Do not parse STPP directory child-add events with strict JSON only. A
  `popups` subscription returned `[5,1,0,[1]` during testing, missing the final
  bracket. A task-specific probe can still recover the child propref with a
  tolerant parse, then use propref-relative `SET`.
- Do not use STPP popup filling as the only fallback. If propref export or
  field writes are flaky, drive the visible dialog with real X11 keypresses via
  `movian-plugin-testing/scripts/x11_keypress.py`.
- Do not treat `loading=0` alone as a route-ready signal after `/api/open`.
  One failed run sampled the previous page before the user-directory navigation
  had settled. Use current URL plus title/nodes/loading.
- Do not change SMB2 directory-entry type mapping in this pass. The current
  code maps `entry->st.smb2_type`; a later raw attribute test is needed before
  claiming a Movian-side metadata bug.

## Current Directory Metadata Finding

The observed user-directory node type/count differences between SMB1 and SMB2
are treated as a server/libsmb2 metadata difference in this pass. SMB2 maps
directory entries from `entry->st.smb2_type`, which libsmb2 derives from SMB2
file attributes. Do not change Movian-side type mapping unless a later raw
attribute probe shows that Movian is discarding correct directory metadata.

## Debug Checklist

When investigating a new SMB navigation report, collect:

- URL opened and whether it has a trailing slash.
- Isolated profile/keyring state: no credentials, saved credentials, or
  user-entered credentials.
- `SMB` and `SMB2` debug lines around connect/setup/share enum.
- Page title, loading state, popup type/reason/domain, and node list.
- Screenshots for the SMB1 and SMB2 host roots.
- For automated authenticated navigation, create a fresh isolated profile and
  write only the needed keyring entries; remove that keyring file from reusable
  artifacts after the run.
- For popup automation, first inspect `/api/prop/global/popups/*0`. Prefer
  seeded keyring for deterministic auth tests, use STPP propref-relative field
  writes when the test must exercise the dialog, and fall back to real X11
  keypress navigation when property writes do not match visible UI behavior.
