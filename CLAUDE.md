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

## CI -- two parallel, independently-maintained workflows

This repo is pushed to both a self-hosted Gitea instance and GitHub, each with its
own workflow file kept in sync by hand (not generated from a shared template) --
see the comment block at the bottom of `.github/workflows/build.yml` for exactly
what differs and why. If the test/lint/build steps change, update both files.

### `.gitea/workflows/build.yml` (the original, self-hosted homelab instance)

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
  `scp -t -d /var/www/html/hidden_public/mqtt-exec-agent/` only, so a leaked key
  can't do anything beyond dropping files in that one directory. The scp step is
  `continue-on-error`, so a missing/invalid secret or an s2 outage never fails
  the build -- the Actions artifact and Release asset are the sources of truth
  regardless. See the comment above the "Copy .deb to s2" step in `build.yml`
  for the exact `authorized_keys` line and regeneration steps.

**Gotcha, already hit once (2026-10-08)**: a restricted `command=` entry replaces
whatever the real client sent with the exact forced command -- it does NOT forward
the client's own flags. The client scp invocation here targets a directory, but the
forced command initially omitted `-d` (`command="scp -t /path/.../"`), and the
server-side scp then tried to `open()` that directory path directly as the
destination *file* and failed with `scp: /path/...: Is a directory`. Fixed by adding
`-d` to the forced command (`scp -t -d /path/.../`), which tells it to treat the
target as a directory and write the incoming file inside it by name. If this
authorized_keys entry is ever recreated from scratch, don't drop the `-d` again.

Pushed to `ssh://git@192.168.20.21:222/jason/mqtt-exec-agent.git` (2026-10-05).
Gitea web UI is at `https://gitea.ejectedalien.net` (not reachable by IP:port
without TLS -- confirmed the plain-IP:3000 address serves HTTPS, not HTTP, and
`https://192.168.20.21/api/...` on 443 doesn't respond at all; use the real
hostname). The Actions API appears to 404 without an auth token even for
basic read endpoints (`/api/v1/repos/<owner>/<repo>/actions/*`) -- likely because
the repo is private; needs a token to check CI run status programmatically rather
than via the web UI.

### `.github/workflows/build.yml` (added 2026-10-10, for the GitHub mirror/home)

Same test/lint/build steps, but deliberately simpler: no s2 scp step (that's
specific to the Gitea homelab setup, not meaningful on GitHub), `actions/
upload-artifact@v4` (not pinned to v3 -- that pin was only ever needed to work
around Gitea's act_runner not fully implementing v4's upload protocol; GitHub's own
hosted runners support v4 fine), and `softprops/action-gh-release@v2` instead of
`akkuman/gitea-release-action` for the tag-push Release-attach step. No
`RELEASE_TOKEN`-equivalent secret needed here -- GitHub's own auto-injected
`GITHUB_TOKEN` already has Release permission once the workflow declares
`permissions: contents: write` at the top level, so there's no Gitea-style
reserved-prefix naming gotcha to work around either.

Pushed (2026-10-10) to `github.com/bensonrodney/mqtt-exec-agent` -- note the
pseudonymous account, matching `ha-warning-aggregator`'s convention of using
`bensonrodney` for public-facing GitHub/LICENSE identity while git commit
authorship stays the real `Jason Milen <jpmilen@gmail.com>` (confirmed consistent
with that repo's own commit history, not just its LICENSE copyright line -- no
actual inconsistency to fix here). Pushed over SSH
(`git@github.com:bensonrodney/mqtt-exec-agent.git`), not the HTTPS URL originally
given -- no HTTPS credential helper was configured in this environment, but an SSH
key for this exact GitHub account already was (confirmed via `ssh -T git@github.com`
greeting "Hi bensonrodney!").

## Validated end-to-end on real hardware (2026-10-08)

User-confirmed working in full: CI build, the s2 copy, `dpkg -i` on the real target
Ubuntu host, a real `systemctl restart` action for an OpenVPN unit, and Home Assistant picking up the
discovery-published button entity over real MQTT and successfully triggering the
restart. No known open issues as of this date.
