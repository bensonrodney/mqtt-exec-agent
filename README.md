# mqtt-exec-agent

A systemd-managed daemon for Linux machines: subscribes to MQTT and runs a
**locally-configured, fixed** command when triggered. An incoming MQTT message only
ever selects *which* already-configured action runs -- it can never supply the
command itself. That's the whole security model: this stays safe to expose to a
button in Home Assistant (or anything else that can publish MQTT) even for
privileged actions like restarting a systemd unit, because the attack surface is
"can this publisher trigger one of the actions I already defined locally", never
"can this publisher run arbitrary code on my machine".

Publishes [Home Assistant MQTT discovery](https://www.home-assistant.io/integrations/mqtt/#discovery-messages)
config for each action, so a matching `button` entity appears in HA automatically --
no manual YAML or dashboard wiring needed on the HA side.

## How it works

Each action in `config.toml` maps one MQTT topic to one fixed command:

```toml
[[actions]]
name = "restart_vpn"
topic = "home/exec/myhost/restart_vpn"
command = ["systemctl", "restart", "openvpn@myvpn"]
```

Publishing the exact `payload_match` string (default `"PRESS"`) to that topic runs
the command. See `config/config.toml.example` for every option (friendly name,
per-action debounce, result publishing, disabling discovery for one action).

### Home Assistant integration

On every (re)connect, this agent publishes:

- An `online`/`offline` availability topic for itself, `home/exec/<hostname>/status`
  (set as its MQTT Last Will, so it flips to `offline` automatically if the agent or
  the whole machine goes down -- not just on a clean shutdown).
- A retained HA MQTT-discovery `button` config per action, under
  `<discovery_prefix>/button/<hostname>/<action name>/config`.

Both are **hostname-first by design**: the HA `device` each action's button is
grouped under is named after the host running this agent (configurable via
`[homeassistant] device_name`, but defaults to the hostname), so running this agent
on several machines never merges or confuses their actions in HA -- each host gets
its own clearly-named device, e.g. `vpn-gateway`, `nas01`, etc.

As long as HA's MQTT integration has discovery enabled (the default), the button
just appears under Settings -> Devices & Services -> MQTT. No further HA-side
configuration is needed.

## Building the .deb

Requires [uv](https://docs.astral.sh/uv/) and `dpkg-deb` on the build machine (not
the target -- the resulting package is self-contained).

```
./build.sh
```

Produces `build/mqtt-exec-agent_<version>_<arch>.deb` for the architecture of the
machine you build it on. The package vendors an entire standalone Python interpreter
(via `uv python install`) plus dependencies under `/opt/mqtt-exec-agent`, so install
is fully offline: no system Python version requirement, no network access, no `uv`
needed on the target machine. See `CLAUDE.md` for why this is done this way (and the
production bugs that shaped it, inherited from a sibling project).

## Installing

```
sudo dpkg -i build/mqtt-exec-agent_*.deb
sudo editor /etc/mqtt-exec-agent/config.toml   # set your MQTT broker + at least one action
sudo systemctl enable --now mqtt-exec-agent
```

The service is **not** started automatically on install, since the shipped config is
a placeholder with no real broker/actions configured.

## Testing without touching a real privileged command

Point a dummy action at something harmless first, e.g.:

```toml
[[actions]]
name = "test"
topic = "home/exec/myhost/test"
command = ["touch", "/tmp/mqtt-exec-agent-test"]
```

Then, with the service running:

```
mosquitto_pub -h <broker> -t home/exec/myhost/test -m PRESS
```

Watch `journalctl -u mqtt-exec-agent -f`, confirm `/tmp/mqtt-exec-agent-test` was
created, and (if `publish_result` is on, the default) `mosquitto_sub` on
`home/exec/myhost/test/result` to see the JSON result. Once that round-trip works,
swap in the real command.

**Before wiring up a real `systemctl restart <unit>` action**, confirm the exact
unit name on that machine -- e.g. `systemctl list-units '*openvpn*'` -- rather than
assuming it matches what's configured elsewhere.

## Running the unit tests

No MQTT/network/root required:

```
uv run --python 3.12 --with '.[dev]' pytest
```
