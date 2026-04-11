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

    def ensure_vlan_commands(self, vlan_id, name=None):
        return [f"ensure_vlan={vlan_id}"]

    def build_hybrid_commands(self, port_assignments, active_isolated_vlans,
                              has_shared_duts, uplink_ports, vlan_shared=200,
                              ports_to_include=None, include_uplinks=True):
        cmds = []
        for port, pool, vlan in port_assignments:
            cmds.append(f"hybrid port={port} pool={pool} vlan={vlan}")
        if include_uplinks and uplink_ports:
            all_vlans = sorted(active_isolated_vlans | {vlan_shared})
            cmds.append(f"uplinks={uplink_ports} tagged={all_vlans}")
        return cmds


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


def test_apply_full_topology_creates_vlans_and_configures_ports():
    driver = FakeDriver()
    client = FakeClient(driver)
    config = {
        "switch": {
            "vlan_topology": 200,
            "uplink_ports": [9, 10],
        },
        "duts": {
            "dut-a": {"switch_port": 1, "switch_vlan_isolated": 101, "pool": "isolated"},
            "dut-b": {"switch_port": 2, "switch_vlan_isolated": 102, "pool": "shared"},
        },
    }

    assert vlan_manager.apply_full_topology(config=config, client=client)

    sent = client.commands[0]
    assert "ensure_vlan=101" in sent
    assert "ensure_vlan=102" in sent
    assert "ensure_vlan=200" in sent
    assert "hybrid port=1 pool=isolated vlan=101" in sent
    assert "hybrid port=2 pool=shared vlan=102" in sent
    assert "uplinks=[9, 10] tagged=[101, 102, 200]" in sent
    assert sent[-1] == "finalize"


def test_apply_full_topology_includes_poe_ports():
    driver = FakeDriver()
    client = FakeClient(driver)
    config = {
        "switch": {"vlan_topology": 200, "uplink_ports": []},
        "duts": {
            "dut-a": {
                "switch_port": 1,
                "switch_port_poe": 3,
                "switch_vlan_isolated": 101,
            },
        },
    }

    assert vlan_manager.apply_full_topology(config=config, client=client)

    sent = client.commands[0]
    assert "hybrid port=1 pool=isolated vlan=101" in sent
    assert "hybrid port=3 pool=isolated vlan=101" in sent


def test_apply_full_topology_returns_false_when_no_duts():
    client = FakeClient(FakeDriver())
    config = {"switch": {}, "duts": {}}

    assert not vlan_manager.apply_full_topology(config=config, client=client)
    assert client.commands == []


def test_apply_full_topology_returns_false_when_driver_lacks_hybrid():
    class MinimalDriver:
        def finalize_vlan_commands(self):
            return []

        def ensure_vlan_commands(self, vlan_id, name=None):
            return []

    client = FakeClient(MinimalDriver())
    config = {
        "switch": {"vlan_topology": 200},
        "duts": {"dut-a": {"switch_port": 1, "switch_vlan_isolated": 101}},
    }

    assert not vlan_manager.apply_full_topology(config=config, client=client)
