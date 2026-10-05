from __future__ import annotations

import socket
import tomllib
from dataclasses import dataclass
from pathlib import Path


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Action:
    name: str
    friendly_name: str
    topic: str
    command: list[str]
    payload_match: str
    min_interval_seconds: float
    publish_result: bool
    result_topic: str
    discovery: bool


@dataclass(frozen=True)
class Config:
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str | None
    mqtt_password: str | None
    mqtt_tls: bool
    log_level: str
    discovery_prefix: str
    device_name: str
    actions: list[Action]

    @classmethod
    def load(cls, path: Path) -> Config:
        try:
            data = tomllib.loads(path.read_text())
        except FileNotFoundError:
            raise ConfigError(f"config file not found: {path}") from None
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"invalid TOML in {path}: {exc}") from exc

        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> Config:
        mqtt = data.get("mqtt", {})
        logging_cfg = data.get("logging", {})
        ha = data.get("homeassistant", {})

        host = mqtt.get("host")
        if not host:
            raise ConfigError("[mqtt] host must be set in config")

        actions_raw = data.get("actions", [])
        if not actions_raw:
            raise ConfigError(
                "at least one [[actions]] entry must be configured -- nothing for this "
                "agent to do otherwise"
            )

        actions = [cls._parse_action(a) for a in actions_raw]

        names = [a.name for a in actions]
        if len(names) != len(set(names)):
            raise ConfigError("[[actions]] entries must have unique 'name' values")
        topics = [a.topic for a in actions]
        if len(topics) != len(set(topics)):
            raise ConfigError("[[actions]] entries must have unique 'topic' values")

        return cls(
            mqtt_host=str(host),
            mqtt_port=int(mqtt.get("port", 1883)),
            mqtt_username=(mqtt.get("username") or None),
            mqtt_password=(mqtt.get("password") or None),
            mqtt_tls=bool(mqtt.get("tls", False)),
            log_level=str(logging_cfg.get("level", "INFO")).upper(),
            discovery_prefix=str(ha.get("discovery_prefix") or "homeassistant"),
            device_name=str(ha.get("device_name") or socket.gethostname()),
            actions=actions,
        )

    @staticmethod
    def _parse_action(raw: dict) -> Action:
        name = str(raw.get("name") or "")
        if not name:
            raise ConfigError("every [[actions]] entry must set 'name'")

        topic = str(raw.get("topic") or "")
        if not topic:
            raise ConfigError(f"[[actions]] '{name}': 'topic' must be set")

        command = raw.get("command")
        if not isinstance(command, list) or not command:
            raise ConfigError(
                f"[[actions]] '{name}': 'command' must be a non-empty list of strings "
                "(e.g. [\"systemctl\", \"restart\", \"some.service\"]) -- never a single "
                "string, which would need shell=True and reopen the exact shell-injection "
                "risk this tool exists to avoid"
            )

        min_interval = float(raw.get("min_interval_seconds", 0))
        if min_interval < 0:
            raise ConfigError(f"[[actions]] '{name}': 'min_interval_seconds' must not be negative")

        return Action(
            name=name,
            friendly_name=str(raw.get("friendly_name") or name.replace("_", " ").title()),
            topic=topic,
            command=[str(c) for c in command],
            # Matches a HA button's default command_topic payload ("PRESS") -- a deliberate
            # exact-match default rather than "any payload triggers", so an action is never
            # accidentally fired by unrelated traffic on a guessed/misconfigured topic.
            payload_match=str(raw.get("payload_match", "PRESS")),
            min_interval_seconds=min_interval,
            publish_result=bool(raw.get("publish_result", True)),
            result_topic=str(raw.get("result_topic") or f"{topic}/result"),
            discovery=bool(raw.get("discovery", True)),
        )
