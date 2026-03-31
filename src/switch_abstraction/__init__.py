"""
Switch Abstraction - Vendor-agnostic switch management for LabGrid testbeds.

Provides SwitchClient for SSH-based switch operations and vlan_manager
for per-DUT dynamic VLAN switching at test time.
"""

from switch_abstraction.client import SwitchClient
from switch_abstraction.vlan_manager import (
    load_dut_map,
    restore_port_vlan,
    set_port_vlan,
)

__all__ = [
    "SwitchClient",
    "load_dut_map",
    "restore_port_vlan",
    "set_port_vlan",
]
