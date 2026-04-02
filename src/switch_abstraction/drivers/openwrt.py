"""
OpenWrt UCI driver - command builders for switches running OpenWrt.

Builds shell command lists that manipulate OpenWrt's UCI configuration
for VLAN assignment (DSA bridge-vlans) and PoE control. Commands are
executed over SSH by client.py via Netmiko with device_type="linux".

UCI bridge-VLAN model (DSA):
    config bridge-vlan
        option device 'switch'
        option vlan '<ID>'
        list ports '<lanN>'          # untagged
        list ports '<uplink>:t'      # tagged

UCI PoE model:
    config port
        option enable '1'
        option id '<N>'
        option name 'lan<N>'

Port naming convention: physical ports are named 'lan<N>' where N is the
1-based port number passed to the driver functions.

Netmiko device_type: "linux"
"""

from __future__ import annotations

DEVICE_TYPE = "linux"

BRIDGE_DEVICE = "switch"
UPLINK_SUFFIX = ":t"


def _port_name(port: int) -> str:
    return f"lan{port}"


def build_poe_commands(port: int, action: str) -> list[str]:
    """Build UCI commands to enable/disable PoE on a port.

    Finds the UCI poe section by matching option name == 'lan<port>',
    then sets enable to '1' or '0'.
    """
    if action not in ("on", "off"):
        raise ValueError(f"Invalid PoE action '{action}'. Valid: on, off")

    enable_val = "1" if action == "on" else "0"
    name = _port_name(port)

    return [
        f"uci show poe | grep -n \"={name}\" | head -1 | "
        f"sed 's/@port\\[\\([0-9]*\\)\\].*/\\1/' | "
        f"while read idx; do "
        f"uci set poe.@port[$idx].enable='{enable_val}'; done",
        "uci commit poe",
    ]


def assign_port_vlan_commands(
    port: int,
    vlan_id: int,
    mode: str = "untagged",
    remove_vlans: list[int] | None = None,
) -> list[str]:
    """Build UCI commands to assign a port to a VLAN.

    For each VLAN to remove, deletes the port from that bridge-vlan section.
    Then adds the port to the target VLAN section.
    A final 'uci commit network' and service reload is NOT included here
    so that multiple port changes can be batched before a single commit.
    Use ensure_commit_commands() after all port changes.
    """
    name = _port_name(port)
    port_entry = name if mode == "untagged" else f"{name}{UPLINK_SUFFIX}"
    cmds: list[str] = []

    for vlan in remove_vlans or []:
        cmds.append(
            f"vlan_sec=$(uci show network | grep \"bridge-vlan\" | "
            f"grep \"vlan='{vlan}'\" | head -1 | cut -d. -f2); "
            f"[ -n \"$vlan_sec\" ] && "
            f"uci del_list network.$vlan_sec.ports='{name}' 2>/dev/null; "
            f"[ -n \"$vlan_sec\" ] && "
            f"uci del_list network.$vlan_sec.ports='{name}{UPLINK_SUFFIX}' 2>/dev/null; "
            f"true"
        )

    cmds.append(
        f"vlan_sec=$(uci show network | grep \"bridge-vlan\" | "
        f"grep \"vlan='{vlan_id}'\" | head -1 | cut -d. -f2); "
        f"[ -n \"$vlan_sec\" ] && "
        f"uci add_list network.$vlan_sec.ports='{port_entry}'"
    )

    return cmds


def ensure_vlan_commands(vlan_id: int, name: str | None = None) -> list[str]:
    """Build UCI commands to create a bridge-vlan if it does not exist.

    Checks whether a bridge-vlan with the given VLAN ID already exists.
    If not, creates one under the bridge device.
    """
    cmds = [
        f"existing=$(uci show network | grep \"bridge-vlan\" | "
        f"grep \"vlan='{vlan_id}'\" | head -1); "
        f"if [ -z \"$existing\" ]; then "
        f"sec=$(uci add network bridge-vlan); "
        f"uci set network.$sec.device='{BRIDGE_DEVICE}'; "
        f"uci set network.$sec.vlan='{vlan_id}'; "
        f"fi"
    ]
    return cmds


def ensure_commit_commands() -> list[str]:
    """Build commands to commit UCI changes and reload the network service.

    Call this once after batching all VLAN changes.
    """
    return [
        "uci commit network",
        "/etc/init.d/network reload",
    ]


def build_hybrid_commands(
    port_assignments: list[tuple[int, str, int]],
    active_isolated_vlans: set[int],
    has_libremesh_duts: bool,
    uplink_ports: list[int],
    vlan_mesh: int = 200,
    ports_to_include: set[int] | None = None,
    include_uplinks: bool = True,
) -> list[str]:
    """Build UCI commands for hybrid VLAN assignment.

    Each DUT port is configured independently based on its pool.
    Uplink ports are tagged for all active VLANs.
    """
    cmds: list[str] = []

    if has_libremesh_duts:
        cmds.extend(ensure_vlan_commands(vlan_mesh))

    for port, pool, isolated_vlan in port_assignments:
        if ports_to_include is not None and port not in ports_to_include:
            continue
        if pool == "isolated":
            cmds.extend(assign_port_vlan_commands(
                port, isolated_vlan, "untagged", remove_vlans=[vlan_mesh],
            ))
        else:
            cmds.extend(assign_port_vlan_commands(
                port, vlan_mesh, "untagged", remove_vlans=[isolated_vlan],
            ))

    if include_uplinks and uplink_ports:
        all_vlans = sorted(active_isolated_vlans)
        if has_libremesh_duts:
            all_vlans.append(vlan_mesh)
        all_vlans = sorted(set(all_vlans))
        for vlan in all_vlans:
            for uplink_port in uplink_ports:
                cmds.extend(assign_port_vlan_commands(
                    uplink_port, vlan, "tagged",
                ))

    cmds.extend(ensure_commit_commands())
    return cmds


def get_port_pvid_command(port: int) -> str:
    """Build the command to query which untagged VLAN a port belongs to."""
    name = _port_name(port)
    return (
        f"uci show network | grep 'bridge-vlan' | grep 'ports' | "
        f"grep \"'{name}'\" | grep -v ':t' | head -1 | "
        f"sed \"s/.*\\[\\([0-9]*\\)\\].*/\\1/\" | "
        f"while read idx; do "
        f"uci show network | grep \"bridge-vlan\" | "
        f"grep \"@bridge-vlan[$idx].vlan\" | cut -d\\' -f2; done"
    )


def parse_port_pvid(output: str) -> int | None:
    """Parse VLAN ID from the get_port_pvid_command output."""
    for line in output.strip().splitlines():
        stripped = line.strip()
        if stripped.isdigit():
            return int(stripped)
    return None
