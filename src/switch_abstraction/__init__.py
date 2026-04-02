"""
Switch Abstraction - Vendor-agnostic switch management for LabGrid testbeds.

Provides SwitchClient for SSH-based switch operations and vlan_manager
for per-DUT dynamic VLAN switching at test time.
"""

from switch_abstraction.client import SwitchClient
from switch_abstraction.vlan_manager import (
    load_config,
    load_dut_map,
    restore_port_vlan,
    set_port_vlan,
)

__all__ = [
    "SwitchClient",
    "load_config",
    "load_dut_map",
    "restore_port_vlan",
    "set_port_vlan",
]


def get_port_pvid(port: int) -> int | None:
    """Query the switch for the current PVID of a port. Convenience wrapper."""
    return SwitchClient().get_port_pvid(port)
