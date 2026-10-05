"""Subscribes to MQTT and runs a locally-configured command when triggered.

Run as: python3 -m mqtt_exec_agent --config /etc/mqtt-exec-agent/config.toml
"""
from __future__ import annotations

import argparse
import json
import logging
import socket
import sys
from pathlib import Path

import paho.mqtt.client as mqtt

from . import discovery
from .config import Action, Config, ConfigError
from .runner import Debouncer, run_action

log = logging.getLogger("mqtt_exec_agent")

DEFAULT_CONFIG_PATH = Path("/etc/mqtt-exec-agent/config.toml")


class Daemon:
    def __init__(self, config: Config):
        self.config = config
        self.hostname = socket.gethostname()
        self.status_topic = f"home/exec/{self.hostname}/status"
        self._actions_by_topic: dict[str, Action] = {a.topic: a for a in config.actions}
        self._debouncer = Debouncer()

        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            # Must be unique per machine: with several hosts potentially running this
            # same daemon, a client_id collision would have the broker disconnect
            # whichever one connected first, in a loop.
            client_id=f"mqtt-exec-agent-{self.hostname}",
        )
        if config.mqtt_username:
            self.client.username_pw_set(config.mqtt_username, config.mqtt_password or "")
        if config.mqtt_tls:
            self.client.tls_set()
        # Set before connect_async so the broker already has this session's LWT even if
        # the very first connection attempt never completes.
        self.client.will_set(self.status_topic, "offline", qos=1, retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_connect_fail = self._on_connect_fail
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)

    def _on_connect_fail(self, client, userdata):
        # Fires for a network-level failure (unreachable host, wrong port, firewalled) --
        # without this, paho's automatic reconnect loop retries silently forever with no
        # other callback ever firing, which looks identical to "the daemon is stuck" from
        # the journal alone.
        log.warning(
            "MQTT connection attempt to %s:%s failed at the network level - retrying automatically",
            self.config.mqtt_host, self.config.mqtt_port,
        )

    def _on_connect(self, client, userdata, connect_flags, reason_code, properties=None):
        if reason_code.is_failure:
            log.error("MQTT connect failed: %s", reason_code)
            return
        log.info("connected to MQTT broker %s:%s", self.config.mqtt_host, self.config.mqtt_port)
        for action in self.config.actions:
            log.info("subscribing to %s (action '%s')", action.topic, action.name)
            client.subscribe(action.topic, qos=1)
        client.publish(self.status_topic, "online", qos=1, retain=True)
        discovery.publish_all(client, self.config, self.hostname, self.status_topic)

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties=None):
        log.warning("MQTT disconnected: %s", reason_code)

    def _on_message(self, client, userdata, msg):
        action = self._actions_by_topic.get(msg.topic)
        if action is None:
            return

        try:
            payload = msg.payload.decode()
        except UnicodeDecodeError:
            log.warning("ignoring undecodable payload on %s", msg.topic)
            return

        if payload != action.payload_match:
            log.debug(
                "ignoring payload %r on %s (action '%s' requires exact match %r)",
                payload, msg.topic, action.name, action.payload_match,
            )
            return

        if not self._debouncer.allow(action.name, action.min_interval_seconds):
            log.info(
                "action '%s' triggered again within %.0fs of its last run - ignoring",
                action.name, action.min_interval_seconds,
            )
            return

        log.warning("action '%s' triggered via %s - running %r", action.name, msg.topic, action.command)
        result = run_action(action.command)
        if result.success:
            log.info("action '%s' completed successfully (rc=%s)", action.name, result.returncode)
        else:
            log.error("action '%s' failed: rc=%s error=%s", action.name, result.returncode, result.error)

        if action.publish_result:
            client.publish(
                action.result_topic,
                json.dumps({
                    "success": result.success,
                    "returncode": result.returncode,
                    "timestamp": result.timestamp,
                    "error": result.error,
                }),
                qos=1,
            )

    def run(self):
        # connect_async + loop_forever(retry_first_connection=True): the first connection
        # attempt and every reconnect after are handled internally, so this doesn't crash
        # just because the broker isn't reachable yet at boot. No separate watchdog thread
        # is needed here (unlike the sibling ups-shutdown-agent) -- every action is purely
        # message-triggered, there's no time-based countdown to poll independently of
        # MQTT traffic.
        self.client.connect_async(self.config.mqtt_host, self.config.mqtt_port, keepalive=60)
        self.client.loop_forever(retry_first_connection=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="path to config.toml (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    try:
        config = Config.load(args.config)
    except ConfigError as exc:
        print(f"mqtt-exec-agent: {exc}", file=sys.stderr)
        return 1

    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    log.info(
        "starting: %d action(s) configured: %s",
        len(config.actions), ", ".join(a.name for a in config.actions),
    )

    Daemon(config).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
