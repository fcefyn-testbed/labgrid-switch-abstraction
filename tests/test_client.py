from contextlib import contextmanager
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from switch_abstraction import client as switch_client


@contextmanager
def raising_switch_lock(*args, **kwargs):
    raise switch_client.SwitchLockTimeoutError("lock timeout")
    yield


def test_get_credentials_accepts_plain_config_keys():
    creds = switch_client.get_credentials(
        config={
            "host": "10.0.0.2",
            "user": "operator",
            "password": "secret",
            "device_type": "linux",
        }
    )

    assert creds == {
        "host": "10.0.0.2",
        "user": "operator",
        "password": "secret",
        "device_type": "linux",
    }


def test_send_config_commands_returns_false_on_lock_timeout(monkeypatch):
    monkeypatch.setattr(switch_client, "switch_lock", raising_switch_lock)
    monkeypatch.setattr(switch_client, "get_switch_driver", lambda *args, **kwargs: object())

    client = switch_client.SwitchClient(
        password="secret",
        driver=object(),
    )

    assert client.send_config_commands(["noop"]) is False


def test_switch_lock_raises_on_timeout(monkeypatch):
    monkeypatch.setattr(switch_client, "_open_lock_file", lambda path: 123)
    monkeypatch.setattr(switch_client.os, "close", lambda fd: None)
    monkeypatch.setattr(switch_client.time, "sleep", lambda _: None)

    timestamps = iter([0.0, 0.0, 0.2])
    monkeypatch.setattr(switch_client.time, "time", lambda: next(timestamps))

    def fake_flock(fd, operation):
        if operation & switch_client.fcntl.LOCK_NB:
            raise BlockingIOError

    monkeypatch.setattr(switch_client.fcntl, "flock", fake_flock)

    with pytest.raises(switch_client.SwitchLockTimeoutError, match="Timed out"):
        with switch_client.switch_lock(timeout=0.1, lock_path="/tmp/test.lock"):
            pass
