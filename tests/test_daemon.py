"""Exercises Daemon's subscribe/dispatch/publish wiring without any real MQTT network
connection. Daemon.__init__ builds an unconnected paho Client; connect_async()/
loop_forever() are only called from run(), which these tests never call. Callbacks are
invoked directly with a FakeClient standing in for the real one, so publish()/
subscribe() calls can be asserted on without a broker."""
import json

from mqtt_exec_agent.config import Config
from mqtt_exec_agent.daemon import Daemon
from mqtt_exec_agent.runner import ActionResult


def make_config(**overrides):
    data = {
        "mqtt": {"host": "127.0.0.1", "port": 1},
        "actions": [{
            "name": "restart_vpn",
            "topic": "home/exec/box1/restart_vpn",
            "command": ["/bin/true"],
        }],
    }
    data.update(overrides)
    return Config.from_dict(data)


class FakeMsg:
    def __init__(self, payload: bytes, topic: str):
        self.topic = topic
        self.payload = payload


class FakeClient:
    def __init__(self):
        self.subscriptions = []
        self.published = []

    def subscribe(self, topic, qos=0):
        self.subscriptions.append(topic)

    def publish(self, topic, payload=None, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))


class FakeReasonCode:
    def __init__(self, is_failure=False):
        self.is_failure = is_failure

    def __str__(self):
        return "failure" if self.is_failure else "success"


def test_on_connect_subscribes_to_every_action_topic():
    daemon = Daemon(make_config())
    client = FakeClient()
    daemon._on_connect(client, None, None, FakeReasonCode())
    assert client.subscriptions == ["home/exec/box1/restart_vpn"]


def test_on_connect_publishes_online_status_and_discovery():
    daemon = Daemon(make_config())
    client = FakeClient()
    daemon._on_connect(client, None, None, FakeReasonCode())

    topics = [p[0] for p in client.published]
    assert daemon.status_topic in topics
    status_entry = next(p for p in client.published if p[0] == daemon.status_topic)
    assert status_entry[1] == "online"
    assert status_entry[3] is True  # retained

    discovery_topics = [t for t in topics if t.startswith("homeassistant/button/")]
    assert len(discovery_topics) == 1
    discovery_payload = json.loads(next(p[1] for p in client.published if p[0] == discovery_topics[0]))
    assert discovery_payload["command_topic"] == "home/exec/box1/restart_vpn"


def test_on_connect_failure_does_not_subscribe():
    daemon = Daemon(make_config())
    client = FakeClient()
    daemon._on_connect(client, None, None, FakeReasonCode(is_failure=True))
    assert client.subscriptions == []
    assert client.published == []


def test_on_connect_fail_logs_a_warning(caplog):
    daemon = Daemon(make_config())
    with caplog.at_level("WARNING", logger="mqtt_exec_agent"):
        daemon._on_connect_fail(None, None)
    assert any("failed at the network level" in r.message for r in caplog.records)


def test_on_message_runs_action_on_matching_payload(monkeypatch):
    daemon = Daemon(make_config())
    client = FakeClient()
    called = []

    def fake_run_action(command):
        called.append(command)
        return ActionResult(success=True, returncode=0, timestamp="t")

    monkeypatch.setattr("mqtt_exec_agent.daemon.run_action", fake_run_action)
    daemon._on_message(client, None, FakeMsg(b"PRESS", "home/exec/box1/restart_vpn"))
    assert called == [["/bin/true"]]


def test_on_message_ignores_wrong_payload(monkeypatch):
    daemon = Daemon(make_config())
    client = FakeClient()
    called = []
    monkeypatch.setattr("mqtt_exec_agent.daemon.run_action", lambda command: called.append(command))
    daemon._on_message(client, None, FakeMsg(b"something else", "home/exec/box1/restart_vpn"))
    assert called == []


def test_on_message_ignores_unknown_topic(monkeypatch):
    daemon = Daemon(make_config())
    client = FakeClient()
    called = []
    monkeypatch.setattr("mqtt_exec_agent.daemon.run_action", lambda command: called.append(command))
    daemon._on_message(client, None, FakeMsg(b"PRESS", "home/exec/box1/some_other_topic"))
    assert called == []


def test_on_message_publishes_result(monkeypatch):
    daemon = Daemon(make_config())
    client = FakeClient()

    monkeypatch.setattr(
        "mqtt_exec_agent.daemon.run_action",
        lambda command: ActionResult(success=True, returncode=0, timestamp="2026-01-01T00:00:00+00:00"),
    )
    daemon._on_message(client, None, FakeMsg(b"PRESS", "home/exec/box1/restart_vpn"))

    result_entry = next(p for p in client.published if p[0] == "home/exec/box1/restart_vpn/result")
    payload = json.loads(result_entry[1])
    assert payload["success"] is True
    assert payload["returncode"] == 0


def test_on_message_skips_result_publish_when_disabled(monkeypatch):
    config = make_config(actions=[{
        "name": "restart_vpn",
        "topic": "home/exec/box1/restart_vpn",
        "command": ["/bin/true"],
        "publish_result": False,
    }])
    daemon = Daemon(config)
    client = FakeClient()

    monkeypatch.setattr(
        "mqtt_exec_agent.daemon.run_action",
        lambda command: ActionResult(success=True, returncode=0, timestamp="t"),
    )
    daemon._on_message(client, None, FakeMsg(b"PRESS", "home/exec/box1/restart_vpn"))
    assert client.published == []


def test_on_message_respects_debounce(monkeypatch):
    config = make_config(actions=[{
        "name": "restart_vpn",
        "topic": "home/exec/box1/restart_vpn",
        "command": ["/bin/true"],
        "min_interval_seconds": 9999,
    }])
    daemon = Daemon(config)
    client = FakeClient()
    calls = []

    monkeypatch.setattr(
        "mqtt_exec_agent.daemon.run_action",
        lambda command: calls.append(command) or ActionResult(success=True, returncode=0, timestamp="t"),
    )
    daemon._on_message(client, None, FakeMsg(b"PRESS", "home/exec/box1/restart_vpn"))
    daemon._on_message(client, None, FakeMsg(b"PRESS", "home/exec/box1/restart_vpn"))
    assert len(calls) == 1
