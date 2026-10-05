from pathlib import Path

import pytest

from mqtt_exec_agent.config import Config, ConfigError


def minimal_data(**action_overrides):
    action = {
        "name": "restart_vpn",
        "topic": "home/exec/box1/restart_vpn",
        "command": ["systemctl", "restart", "openvpn@myvpn"],
    }
    action.update(action_overrides)
    return {
        "mqtt": {"host": "broker.local"},
        "actions": [action],
    }


def test_minimal_valid_config():
    config = Config.from_dict(minimal_data())
    assert config.mqtt_host == "broker.local"
    assert config.mqtt_port == 1883
    assert config.mqtt_tls is False
    assert config.log_level == "INFO"
    assert config.discovery_prefix == "homeassistant"

    action = config.actions[0]
    assert action.name == "restart_vpn"
    assert action.friendly_name == "Restart Vpn"
    assert action.command == ["systemctl", "restart", "openvpn@myvpn"]
    assert action.payload_match == "PRESS"
    assert action.min_interval_seconds == 0
    assert action.publish_result is True
    assert action.result_topic == "home/exec/box1/restart_vpn/result"
    assert action.discovery is True


def test_full_config_overrides_defaults():
    data = minimal_data(
        friendly_name="Restart VPN",
        payload_match="RESTART",
        min_interval_seconds=10,
        publish_result=False,
        result_topic="home/exec/box1/restart_vpn/status",
        discovery=False,
    )
    data["mqtt"] = {"host": "10.0.0.5", "port": 8883, "username": "u", "password": "p", "tls": True}
    data["logging"] = {"level": "debug"}
    data["homeassistant"] = {"discovery_prefix": "ha", "device_name": "box1"}

    config = Config.from_dict(data)
    assert config.mqtt_port == 8883
    assert config.mqtt_username == "u"
    assert config.mqtt_password == "p"
    assert config.mqtt_tls is True
    assert config.log_level == "DEBUG"
    assert config.discovery_prefix == "ha"
    assert config.device_name == "box1"

    action = config.actions[0]
    assert action.friendly_name == "Restart VPN"
    assert action.payload_match == "RESTART"
    assert action.min_interval_seconds == 10
    assert action.publish_result is False
    assert action.result_topic == "home/exec/box1/restart_vpn/status"
    assert action.discovery is False


def test_missing_host_raises():
    data = minimal_data()
    del data["mqtt"]
    with pytest.raises(ConfigError):
        Config.from_dict(data)


def test_no_actions_raises():
    with pytest.raises(ConfigError):
        Config.from_dict({"mqtt": {"host": "broker.local"}, "actions": []})


def test_action_missing_name_raises():
    data = minimal_data()
    del data["actions"][0]["name"]
    with pytest.raises(ConfigError):
        Config.from_dict(data)


def test_action_missing_topic_raises():
    data = minimal_data()
    del data["actions"][0]["topic"]
    with pytest.raises(ConfigError):
        Config.from_dict(data)


def test_action_command_must_be_a_list():
    data = minimal_data(command="systemctl restart openvpn@myvpn")
    with pytest.raises(ConfigError):
        Config.from_dict(data)


def test_action_command_must_not_be_empty():
    data = minimal_data(command=[])
    with pytest.raises(ConfigError):
        Config.from_dict(data)


def test_negative_min_interval_raises():
    data = minimal_data(min_interval_seconds=-1)
    with pytest.raises(ConfigError):
        Config.from_dict(data)


def test_duplicate_action_names_raise():
    data = minimal_data()
    other = dict(data["actions"][0])
    other["topic"] = "home/exec/box1/other"
    data["actions"].append(other)
    with pytest.raises(ConfigError):
        Config.from_dict(data)


def test_duplicate_action_topics_raise():
    data = minimal_data()
    other = dict(data["actions"][0])
    other["name"] = "other_action"
    data["actions"].append(other)
    with pytest.raises(ConfigError):
        Config.from_dict(data)


def test_missing_file_raises(tmp_path: Path):
    with pytest.raises(ConfigError):
        Config.load(tmp_path / "does-not-exist.toml")


def test_invalid_toml_raises(tmp_path: Path):
    bad = tmp_path / "config.toml"
    bad.write_text("this is not [valid toml")
    with pytest.raises(ConfigError):
        Config.load(bad)
