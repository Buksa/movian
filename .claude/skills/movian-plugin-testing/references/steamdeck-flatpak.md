# Steam Deck and Flatpak Runtime Testing

Read this reference only for real Steam Deck or Flatpak validation — it does
not apply to the local `mdev`-driven desktop-Linux loop the other references
describe.

## Launch

Use SSH for copying artifacts, installing builds, reading logs, checking
sockets, and calling HTTP/STPP endpoints. A GLW UI started directly from an
SSH shell may exit because it lacks the active desktop environment.

If `flatpak run` exits immediately:

1. Prefer `systemd-run --user`.
2. Pass `DISPLAY=:0`.
3. Use the current `/run/user/1000/xauth_*` authority.
4. If desktop activation still fails, ask the user to launch Movian
   manually.
5. Continue observing through `/api/logfile/*`, STPP, HTTP props, `ss`, and
   protocol probes.

Use `ssh -n` in multi-step runners so SSH cannot consume the remaining local
script from stdin.

In Desktop Mode, an SSH GUI launch can be made persistent with
`systemd-run --user --unit=movian-test --collect` plus
`--setenv=DISPLAY=:0` and `--setenv=XAUTHORITY=<current xauth_*>`. Resolve
`/run/user/1000/xauth_*` dynamically after every reboot. Plain
`nohup flatpak run ... &` can start briefly and then disappear when SSH
exits.

## Credentials

Keep the private key under `~/.ssh` with mode `600`. OpenSSH on the
controlling machine can reject a key whose mount reports overly broad
permissions (e.g. `0777`) — copy it to a normal filesystem path with
correct permissions rather than using it in place.

Never copy keys, tokens, keyring contents, or raw profile data into public
artifacts.

## Deployment

- Copy Flatpak bundles into a working directory with a unique filename that
  includes a short SHA or timestamp. Verify `sha256sum` on both sides before
  installing.
- If `flatpak install --user -y <bundle>` appears to hang, inspect it
  remotely with `pgrep -a -f "flatpak install|<bundle>"` and
  `flatpak info --user dev.uzver.Movian`; it may still be staging objects
  in the Flatpak repo.

## Acceptance

Separate:

- package/install success;
- service/process launch;
- HTTP readiness;
- GLW rendering;
- protocol behavior;
- privileged-port constraints.

A failed remote GLW launch does not invalidate HTTP/protocol behavior, and a
privileged-port failure does not prove request handlers are incorrect.

A Deck sleep or Wi-Fi drop looks different from a Movian crash: ping, SSH,
and HTTP all fail together. Re-check `/api/logfile/0` and `/api/logfile/1`
after the Deck wakes before blaming the app.
