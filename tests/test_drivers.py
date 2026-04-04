from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from switch_abstraction.drivers import openwrt, tplink_jetstream


def test_openwrt_finalize_vlan_commands_returns_commit_and_reload():
    assert openwrt.finalize_vlan_commands() == [
        "uci commit network",
        "/etc/init.d/network reload",
    ]


def test_openwrt_build_hybrid_commands_ends_with_finalize_commands():
    commands = openwrt.build_hybrid_commands(
        port_assignments=[(1, "shared", 101)],
        active_isolated_vlans={101},
        has_shared_duts=True,
        uplink_ports=[9],
        vlan_shared=200,
    )

    assert commands[-2:] == openwrt.finalize_vlan_commands()


def test_tplink_finalize_vlan_commands_is_empty():
    assert tplink_jetstream.finalize_vlan_commands() == []


def test_tplink_build_hybrid_commands_does_not_append_extra_finalize_commands():
    commands = tplink_jetstream.build_hybrid_commands(
        port_assignments=[(1, "shared", 101)],
        active_isolated_vlans={101},
        has_shared_duts=True,
        uplink_ports=[9],
        vlan_shared=200,
    )

    assert commands[-1] == "exit"
