"""Publishes Home Assistant MQTT-discovery config for each configured action, so the
HA side needs ~zero manual configuration: a `button` entity just appears, grouped under
a device named after this host.

Hostname-first by design (per explicit instruction): `device` identifiers, the
discovery topic's node_id, and each entity's unique_id all key off the machine's own
hostname, specifically so running this agent on several hosts never merges or
confuses their actions in HA -- each host gets its own distinctly-named device.
"""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import Action, Config

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9_-]")


def sanitize(value: str) -> str:
    """MQTT discovery topic segments / unique_ids are safest restricted to
    alphanumerics, dash and underscore -- replace anything else rather than risk an
    invalid topic (e.g. a hostname containing '.')."""
    return _UNSAFE_CHARS.sub("_", value)


def discovery_topic(config: Config, action: Action, hostname: str) -> str:
    return f"{config.discovery_prefix}/button/{sanitize(hostname)}/{sanitize(action.name)}/config"


def build_payload(config: Config, action: Action, hostname: str, status_topic: str) -> dict[str, Any]:
    node_id = sanitize(hostname)
    return {
        "name": action.friendly_name,
        "unique_id": f"{node_id}_{sanitize(action.name)}",
        "command_topic": action.topic,
        "payload_press": action.payload_match,
        "availability_topic": status_topic,
        "payload_available": "online",
        "payload_not_available": "offline",
        "device": {
            "identifiers": [node_id],
            "name": config.device_name,
            "manufacturer": "mqtt-exec-agent",
        },
        "qos": 1,
    }


def publish_all(client, config: Config, hostname: str, status_topic: str) -> None:
    for action in config.actions:
        if not action.discovery:
            continue
        topic = discovery_topic(config, action, hostname)
        payload = build_payload(config, action, hostname, status_topic)
        # Retained: HA (or any subscriber) picks this up even if it wasn't listening at
        # the exact moment this was published -- e.g. HA restarting after this daemon.
        client.publish(topic, json.dumps(payload), qos=1, retain=True)
