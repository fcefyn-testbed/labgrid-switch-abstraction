"""
TP-Link JetStream driver - CLI command builders for TP-Link JetStream switches.

Builds command lists for VLAN assignment, PoE control, PVID queries,
and dynamic VLAN creation. Commands are specific to the TP-Link JetStream CLI
(tested on SG2016P) but executed by client.py via Netmiko.

Netmiko device_type: "tplink_jetstream"
"""

from __future__ import annotations

DEVICE_TYPE = "tplink_jetstream"


def _normalize_pool(pool: str) -> str:
    """Validate and normalize a pool value."""
    normalized = pool.strip().lower()
    if normalized not in {"isolated", "shared"}:
        raise ValueError(
            f"Invalid pool value {pool!r}. Expected 'isolated' or 'shared'."
        )
    return normalized


def build_poe_commands(port: int, action: str) -> list[str]:
    """Build CLI commands to enable/disable PoE on a port.

    Raises ValueError if action is not 'on' or 'off'.
    """
    if action not in ("on", "off"):
        raise ValueError(f"Invalid PoE action '{action}'. Valid: on, off")

    poe_cmd = "enable" if action == "on" else "disable"
    return [
        f"interface gigabitEthernet 1/0/{port}",
        f"power inline supply {poe_cmd}",
        "exit",
    ]


def assign_port_vlan_commands(
    port: int,
    vlan_id: int,
    mode: str = "untagged",
    remove_vlans: list[int] | None = None,
) -> list[str]:
    """Build CLI commands to assign a port to a VLAN.

    Args:
        port: switch port number.
        vlan_id: VLAN to assign.
        mode: 'untagged' or 'tagged'.
        remove_vlans: VLANs to remove from the port before assigning.
    """
    cmds = [f"interface gigabitEthernet 1/0/{port}"]
    for vlan in remove_vlans or []:
        cmds.append(f"no switchport general allowed vlan {vlan}")
    cmds.append(f"switchport general allowed vlan {vlan_id} {mode}")
    if mode == "untagged":
        cmds.append(f"switchport pvid {vlan_id}")
    cmds.append("exit")
    return cmds


def ensure_vlan_commands(vlan_id: int, name: str | None = None) -> list[str]:
    """Build CLI commands to create a VLAN if it does not exist.

    The TP-Link CLI is idempotent for 'vlan <id>': it enters VLAN config
    mode whether the VLAN already exists or not.
    """
    cmds = [f"vlan {vlan_id}"]
    if name:
        cmds.append(f'name "{name}"')
    cmds.append("exit")
    return cmds


def build_hybrid_commands(
    port_assignments: list[tuple[int, str, int]],
    active_isolated_vlans: set[int],
    has_shared_duts: bool,
    uplink_ports: list[int],
    vlan_mesh: int = 200,
    ports_to_include: set[int] | None = None,
    include_uplinks: bool = True,
) -> list[str]:
    """Build CLI commands for hybrid VLAN assignment.

    Each DUT port is configured independently based on its pool.
    Uplink ports are tagged for all active VLANs.

    Args:
        port_assignments: list of (port, pool, isolated_vlan) tuples.
        active_isolated_vlans: set of VLAN IDs used by isolated-pool DUTs.
        has_shared_duts: whether any DUT is in the shared pool.
        uplink_ports: ports that carry tagged traffic to the host.
        vlan_mesh: VLAN ID for the shared network.
        ports_to_include: if set, only these ports are configured (differential apply).
        include_uplinks: if False, uplink port config is skipped.
    """
    cmds: list[str] = []

    if has_shared_duts:
        cmds.extend([f"vlan {vlan_mesh}", 'name "shared"', "exit"])

    for port, pool, isolated_vlan in port_assignments:
        if ports_to_include is not None and port not in ports_to_include:
            continue
        cmds.append(f"interface gigabitEthernet 1/0/{port}")
        if _normalize_pool(pool) == "isolated":
            cmds.append(f"no switchport general allowed vlan {vlan_mesh}")
            cmds.append(f"switchport general allowed vlan {isolated_vlan} untagged")
            cmds.append(f"switchport pvid {isolated_vlan}")
        else:
            cmds.append(f"no switchport general allowed vlan {isolated_vlan}")
            cmds.append(f"switchport general allowed vlan {vlan_mesh} untagged")
            cmds.append(f"switchport pvid {vlan_mesh}")
        cmds.append("exit")

    if include_uplinks and uplink_ports:
        all_vlans = sorted(active_isolated_vlans)
        if has_shared_duts:
            all_vlans.append(vlan_mesh)
        all_vlans = sorted(set(all_vlans))
        if all_vlans:
            vlan_str = ",".join(str(v) for v in all_vlans)
            for uplink_port in uplink_ports:
                cmds.append(f"interface gigabitEthernet 1/0/{uplink_port}")
                cmds.append(f"switchport general allowed vlan {vlan_str} tagged")
                cmds.append("exit")

    return cmds


def get_port_pvid_command(port: int) -> str:
    """Build the show command to query a port's PVID."""
    return f"show interface switchport gigabitEthernet 1/0/{port}"


def parse_port_pvid(output: str) -> int | None:
    """Parse PVID from TP-Link 'show interface switchport' output."""
    for line in output.splitlines():
        if "PVID" in line.upper():
            parts = line.split()
            for part in parts:
                if part.isdigit():
                    return int(part)
    return None
