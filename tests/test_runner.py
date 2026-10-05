import subprocess

from mqtt_exec_agent.runner import Debouncer, run_action


def test_successful_command():
    result = run_action(["/bin/true"])
    assert result.success is True
    assert result.returncode == 0
    assert result.error is None


def test_failing_command():
    result = run_action(["/bin/false"])
    assert result.success is False
    assert result.returncode == 1


def test_never_uses_a_shell(monkeypatch):
    """The whole security model depends on this: the argv list from config is passed
    straight through, never shell=True, never joined into a single string."""
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("mqtt_exec_agent.runner.subprocess.run", fake_run)
    run_action(["systemctl", "restart", "openvpn@myvpn"])
    assert captured["command"] == ["systemctl", "restart", "openvpn@myvpn"]
    assert captured["kwargs"].get("shell") is not True


def test_nonexistent_binary_reports_failure_not_an_exception():
    result = run_action(["/no/such/binary-xyz"])
    assert result.success is False
    assert result.returncode is None
    assert result.error


def test_timeout_reports_failure(monkeypatch):
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(cmd=command, timeout=kwargs.get("timeout"))

    monkeypatch.setattr("mqtt_exec_agent.runner.subprocess.run", fake_run)
    result = run_action(["sleep", "100"], timeout=0.01)
    assert result.success is False
    assert result.error == "timed out"


def test_debounce_denies_within_window():
    clock = iter([100.0, 100.5, 100.9]).__next__
    debouncer = Debouncer(clock=clock)
    assert debouncer.allow("a", 10) is True
    assert debouncer.allow("a", 10) is False
    assert debouncer.allow("a", 10) is False


def test_debounce_allows_after_window():
    clock = iter([100.0, 111.0]).__next__
    debouncer = Debouncer(clock=clock)
    assert debouncer.allow("a", 10) is True
    assert debouncer.allow("a", 10) is True


def test_debounce_zero_interval_always_allows():
    debouncer = Debouncer()
    assert debouncer.allow("a", 0) is True
    assert debouncer.allow("a", 0) is True


def test_debounce_keys_are_independent():
    clock = iter([100.0, 100.1]).__next__
    debouncer = Debouncer(clock=clock)
    assert debouncer.allow("a", 10) is True
    assert debouncer.allow("b", 10) is True
