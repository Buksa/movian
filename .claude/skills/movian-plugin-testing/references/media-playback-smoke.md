# Movian Media Playback Smoke

Read this reference for Movian media playback, protocol, FFmpeg, HLS,
RTMP-family, screenshot, audio, or video smoke tests.

## Workflow

1. Define the smallest pass/fail criteria: scheme/input, expected backend,
   expected container, audio/video stream presence, playback start,
   screenshot state, and disallowed log patterns.
2. Confirm build flags before runtime when the feature is compile-time
   gated. For FFmpeg protocol work, inspect the generated FFmpeg `config.h`
   symbols and final linkage.
3. Probe the input outside Movian when possible. Direct FFmpeg probes are
   useful for distinguishing server/input failure from Movian playback
   failure.
4. Launch Movian with an isolated profile and cache (`mdev run`, see
   `movian-run`), then read the actual HTTP port from
   `http-server: Listening on port` (`mdev` parses this for you).
5. Open the media through the same path a user would use (`mdev open`), then
   collect props, log anchors, and a screenshot (`mdev shot`) for video.
6. Stop only the test-owned instance (`mdev stop`) and confirm no test
   Movian or publisher process remains.

## Evidence Levels

- `routed`: the URL reaches the expected backend or protocol handler.
- `probed`: the demuxer reports the expected container and streams.
- `decoded`: Movian creates audio/video codecs and packets continue after
  playback start.
- `rendered`: video screenshot shows the expected frame, or audio-only props
  prove active playback.

Do not report a protocol as fully proven when only routing or probing
passed. Leave proof gaps explicit in issue/PR notes.

## RTMP-Family Notes

- MediaMTX is a good local baseline for `rtmp` and `rtmps`; it does not
  cover the full legacy RTMP family.
- Red5 2.x can be a useful local positive `rtmpe` source when a live app is
  available.
- Red5 1.0.x and MonaServer2 were useful comparison candidates, but were not
  reliable positive `rtmpe` sources in local smoke.
- `rtmpte` needs a server that actually supports encrypted HTTP tunneling.
  Treat `Input/output error` from a local test server as a proof gap unless
  another server proves playback.
- For encrypted/tunneled FFmpeg protocols, prefer a direct
  `avformat_open_input()` path when testing Movian core behavior. A generic
  fileaccess wrapper can connect but still starve the demuxer of video
  packets.

## Artifact Pattern

Use predictable directories and keep enough data for handoff. When using
`mdev`, its own `/tmp/mdev/<name>/` state (log, shots) already covers most of
this — add a sibling directory for probe/server-side artifacts:

```text
/tmp/movian-<feature>-smoke/
  command.txt or summary.txt
  probe.log
  publisher.log or server.log
  props.json
```

(`movian.log` and `screenshot.png` come from `/tmp/mdev/<name>/movian.log`
and `mdev shot`'s output path.)

For video, inspect the screenshot. For audio-only streams, collect media
props and log anchors proving active playback. If sound continues after a
test, first check for stacked Movian instances or reused profiles before
changing code.
