# CLAUDE.md

Accumulated project knowledge for working on this repo.

## What this is

A generic "MQTT trigger -> run a fixed local command" daemon, packaged as a `.deb`
with a systemd unit. Built after `~/git/esp32s3-apc-ups-monitor/ups-shutdown-agent/`,
reusing its packaging approach closely -- read that repo's `CLAUDE.md` first if
touching `build.sh`'s Python-vendoring block, it documents a multi-round real
production bug chase (dangling symlinks, uv's shared-Python-store ambiguity, a
`lib64` dereferencing bug) that this project's `build.sh` already incorporates the
fixes for. Don't re-derive those fixes from scratch; the comments in `build.sh` here
point back at it.

## Security model -- the one invariant not to relax

`runner.run_action()` is the *only* place `subprocess.run` is called, always with a
list-form argv taken verbatim from `config.toml`, never `shell=True`, never a string
built from an MQTT payload. The payload (`Action.payload_match`, exact string
comparison) only ever selects *whether* an already-configured action fires -- it can
never contribute to *what* runs. This is what makes it safe to trigger privileged
actions (e.g. `systemctl restart <unit>`) from the network side at all: the trust
boundary is "who can edit this machine's own config file" (root/sudo), not "who can
publish to this MQTT topic."

If a future change ever makes the command depend on message content in any way,
that's not a generalization of this tool -- it's a different, much riskier tool, and
should be a deliberate, explicit decision, not a side effect of some other feature.

## Why no watchdog thread (unlike the sibling)

`ups-shutdown-agent` needs a background thread polling a monotonic timer, because its
countdown has to keep advancing even without new MQTT messages arriving (a UPS
telemetry gap mid-outage shouldn't pause the shutdown countdown). This daemon has no
such state: every action is purely reactive to an incoming message, and `Debouncer`
is just a gate checked at message-arrival time, not a background timer. So
`Daemon.run()` is a single blocking `loop_forever(retry_first_connection=True)` call
with no separate thread -- simpler, and there's nothing it would need a thread for.

## Home Assistant discovery -- hostname-first, deliberately

Added specifically because the user plans to run this on more than one host. Every
discoverable identifier -- the HA `device.identifiers`, each entity's `unique_id`,
and the discovery topic's own node_id segment -- is derived from `socket.gethostname()`
(sanitized via `discovery.sanitize()` to a safe MQTT-topic/unique_id charset), not a
generic or configurable label. `[homeassistant] device_name` can override the
*display* name shown in HA, but the `identifiers` value (what HA actually keys the
device on) always traces back to the real hostname. Don't make device_name the sole
disambiguator if this is ever refactored -- two hosts with the same configured
device_name but different hostnames must still produce two distinct HA devices, and
relying only on a free-text field would break that.

## Action result topic default

`result_topic` defaults to `f"{action.topic}/result"` rather than anything
HA-discovery-shaped, since it's meant to be a generic "what happened" feed (useful
for a `mosquitto_sub` during manual testing, or a future HA sensor entity) --
independent of whether `discovery` is enabled for that action at all.

## Testing

`tests/test_daemon.py` uses a hand-rolled `FakeClient` (not the real paho `Client`)
for `subscribe`/`publish` assertions, since (unlike the sibling project, whose
`_on_message` never touches `client`) this daemon's `_on_connect`/`_on_message` both
call `client.publish`/`client.subscribe` directly. `Daemon.__init__` still builds a
real, unconnected paho `Client` internally (`daemon.client`) -- tests just call the
private `_on_connect`/`_on_message` handlers directly with a `FakeClient` standing in
for the `client` argument, never going through `daemon.client` or any real network
I/O.

## CI (`.gitea/workflows/build.yml`)

Runs on every push (tests, lint, build the `.deb`, upload as a Gitea Actions
artifact) and, on a tag push, additionally attaches the `.deb` to a Gitea Release
and best-effort scp's it to `s2.ejectedalien.net:/var/www/html/hidden_public/
mqtt-exec-agent/` for a plain-HTTP download. Needs two repo secrets (Settings >
Actions > Secrets), neither shared with any other repo:

- `RELEASE_TOKEN` -- a token with Releases permission, used by the release-attach
  step. Required for a tag push to succeed (not `continue-on-error`). **Not**
  named `GITEA_TOKEN`: Gitea's secret-creation UI rejects any name starting
  with `GITEA_` (reserved for its own injected variables, same reason GitHub
  reserves `GITHUB_`) -- confirmed by hitting exactly that rejection.
- `S2_DEPLOY_SSH_KEY` -- a dedicated ed25519 private key, generated specifically
  for this repo (never reuse one across projects/directories -- see the sibling
  esp32s3-apc-ups-monitor project's own `mycards`-style convention). Its
  `authorized_keys` entry on s2 is restricted to
  `scp -t /var/www/html/hidden_public/mqtt-exec-agent/` only, so a leaked key
  can't do anything beyond dropping files in that one directory. The scp step is
  `continue-on-error`, so a missing/invalid secret or an s2 outage never fails
  the build -- the Actions artifact and Release asset are the sources of truth
  regardless. See the comment above the "Copy .deb to s2" step in `build.yml`
  for the exact `authorized_keys` line and regeneration steps.

Pushed to `ssh://git@192.168.20.21:222/jason/mqtt-exec-agent.git` (2026-10-05).

## Not yet validated on real hardware

Built and unit-tested, not yet installed on a real target machine or exercised
against a real broker/real Home Assistant instance. Follow the README's "Testing
without touching a real privileged command" section (a dummy `touch` action first)
before wiring up the real `openvpn@myvpn` restart -- and confirm the exact systemd
unit name on that specific machine first (`systemctl list-units '*openvpn*'`), rather
than assuming it matches the name used anywhere else.
