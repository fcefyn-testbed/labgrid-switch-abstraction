from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from switch_abstraction import vlan_manager


class FakeDriver:
    def assign_port_vlan_commands(self, port, vlan_id, mode, remove_vlans=None):
        remove_vlans = remove_vlans or []
        return [f"port={port} vlan={vlan_id} mode={mode} remove={remove_vlans}"]

    def finalize_vlan_commands(self):
        return ["finalize"]


class FakeClient:
    def __init__(self, driver):
        self.driver = driver
        self.commands = []

    def send_config_commands(self, commands):
        self.commands.append(commands)
        return True


def test_get_default_pool_defaults_to_isolated():
    assert vlan_manager.get_default_pool({}) == "isolated"


def test_get_default_pool_accepts_shared():
    assert vlan_manager.get_default_pool({"pool": "shared"}) == "shared"


def test_get_default_pool_rejects_unknown_values():
    with pytest.raises(ValueError, match="Invalid pool value"):
        vlan_manager.get_default_pool({"pool": "unexpected"})


def test_get_default_vlan_uses_shared_pool_vlan_topology():
    hw = {"switch_vlan_isolated": 101, "pool": "shared"}
    config = {"switch": {"vlan_topology": 200}}
    assert vlan_manager.get_default_vlan("dut-a", hw, config=config) == 200


def test_get_default_vlan_uses_isolated_vlan_when_pool_missing():
    hw = {"switch_vlan_isolated": 101}
    config = {"switch": {"vlan_topology": 200}}
    assert vlan_manager.get_default_vlan("dut-a", hw, config=config) == 101


def test_get_default_vlan_uses_shared_fallback_when_topology_missing():
    hw = {"switch_vlan_isolated": 101, "pool": "shared"}
    assert vlan_manager.get_default_vlan("dut-a", hw) == 200


def test_restore_port_vlan_uses_default_pool(monkeypatch):
    calls = []

    def fake_set_port_vlan(dut_name, vlan_id, **kwargs):
        calls.append((dut_name, vlan_id, kwargs))
        return True

    monkeypatch.setattr(vlan_manager, "set_port_vlan", fake_set_port_vlan)

    config = {"switch": {"vlan_topology": 200}}
    dut_map = {"dut-a": {"switch_port": 1, "switch_vlan_isolated": 101, "pool": "shared"}}

    assert vlan_manager.restore_port_vlan("dut-a", dut_map=dut_map, config=config)
    assert calls == [("dut-a", 200, {"dut_map": dut_map, "config": config})]


def test_set_port_vlan_appends_driver_finalize_commands():
    client = FakeClient(FakeDriver())
    config = {"switch": {"vlan_topology": 200}}
    dut_map = {"dut-a": {"switch_port": 1, "switch_vlan_isolated": 101}}

    assert vlan_manager.set_port_vlan(
        "dut-a",
        200,
        dut_map=dut_map,
        config=config,
        client=client,
    )

    assert client.commands == [[
        "port=1 vlan=200 mode=untagged remove=[101]",
        "finalize",
    ]]


def test_restore_ports_vlan_batch_uses_mixed_default_pools():
    client = FakeClient(FakeDriver())
    config = {"switch": {"vlan_topology": 200}}
    dut_map = {
        "dut-a": {"switch_port": 1, "switch_vlan_isolated": 101, "pool": "isolated"},
        "dut-b": {"switch_port": 2, "switch_vlan_isolated": 102, "pool": "shared"},
    }

    assert vlan_manager.restore_ports_vlan_batch(
        ["dut-a", "dut-b"],
        dut_map=dut_map,
        config=config,
        client=client,
    )

    assert client.commands == [[
        "port=1 vlan=101 mode=untagged remove=[200]",
        "port=2 vlan=200 mode=untagged remove=[102]",
        "finalize",
    ]]


def test_restore_port_vlan_returns_false_for_invalid_pool():
    client = FakeClient(FakeDriver())
    config = {"switch": {"vlan_topology": 200}}
    dut_map = {
        "dut-a": {"switch_port": 1, "switch_vlan_isolated": 101, "pool": "unexpected"},
    }

    assert not vlan_manager.restore_port_vlan(
        "dut-a",
        dut_map=dut_map,
        config=config,
        client=client,
    )
    assert client.commands == []


def test_restore_ports_vlan_batch_returns_false_for_missing_dut():
    client = FakeClient(FakeDriver())
    config = {"switch": {"vlan_topology": 200}}
    dut_map = {"dut-a": {"switch_port": 1, "switch_vlan_isolated": 101}}

    assert not vlan_manager.restore_ports_vlan_batch(
        ["dut-a", "missing"],
        dut_map=dut_map,
        config=config,
        client=client,
    )
    assert client.commands == []
