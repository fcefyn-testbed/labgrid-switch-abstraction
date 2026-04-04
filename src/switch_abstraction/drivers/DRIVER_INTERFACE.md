# Switch Driver Interface

This document defines the contract that a switch driver module must implement. Drivers are loaded dynamically based on `SWITCH_DRIVER` in `~/.config/switch.conf`.

To add support for a new switch:

1. Create `drivers/<name>.py` implementing the interface described below.
2. Set `SWITCH_DRIVER=<name>` and `SWITCH_DEVICE_TYPE=<netmiko_type>` in the config.
3. See [Netmiko PLATFORMS](https://github.com/ktbyers/netmiko/blob/develop/PLATFORMS.md) for supported `device_type` values.

## Required exports

### `build_poe_commands(port: int, action: str) -> list[str]`

Build CLI commands to enable/disable PoE on a port.

- **port**: Switch port number (1-based).
- **action**: `"on"` or `"off"`.
- **Returns**: List of CLI commands.
- **Raises**: `ValueError` if action is not `"on"` or `"off"`.

For switches without PoE, return an empty list or idempotent no-op commands.

### `assign_port_vlan_commands(port, vlan_id, mode, remove_vlans) -> list[str]`

Build CLI commands to assign a port to a VLAN.

- **port**: Switch port number.
- **vlan_id**: VLAN to assign.
- **mode**: `"untagged"` or `"tagged"`.
- **remove_vlans**: VLANs to remove from the port before assigning.

### `ensure_vlan_commands(vlan_id: int, name: str | None = None) -> list[str]`

Build CLI commands to create a VLAN if it does not exist. Optional; used for dynamic VLAN creation.

### `build_hybrid_commands(port_assignments, ...) -> list[str]`

Build CLI commands for hybrid VLAN assignment (mixed isolated + shared topology). Used by a higher-level pool manager for batch reconfiguration.

## Optional exports

### `finalize_vlan_commands() -> list[str]`

Build any final commands required to fully apply pending VLAN changes.

- Use this when a driver needs an explicit commit/reload/apply step after
  one or more `assign_port_vlan_commands(...)` calls.
- Return an empty list for drivers whose normal config flow already applies changes.

### `get_port_pvid_command(port: int) -> str`

Build the CLI command to query a port's current PVID. If not implemented, `SwitchClient.get_port_pvid()` will not work for that driver.

### `parse_port_pvid(output: str) -> int | None`

Parse the PVID value from the output of `get_port_pvid_command()`. Returns the PVID as int, or `None` if parsing fails.

## Reference implementations

- [tplink_jetstream.py](tplink_jetstream.py) - TP-Link JetStream switches (e.g. SG2016P). Netmiko device_type: `tplink_jetstream`. Uses vendor-specific interactive CLI and an empty `finalize_vlan_commands()`.
- [openwrt.py](openwrt.py) - Switches running OpenWrt (e.g. Zyxel GS1900-24EP). Netmiko device_type: `linux`. Uses UCI shell commands over SSH to manage DSA bridge-vlans and PoE, with `finalize_vlan_commands()` for commit + reload.
