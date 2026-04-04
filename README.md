# labgrid-switch-abstraction

Vendor-agnostic switch management for [Labgrid](https://labgrid.readthedocs.io/) testbeds. Provides dynamic per-port VLAN switching for hardware-in-the-loop test environments.

Analogous to [PDUDaemon](https://github.com/pdudaemon/pdudaemon) for power control, this package handles **network topology control**: reassigning DUT switch ports between isolated VLANs (one DUT per VLAN) and a shared VLAN (multiple DUTs on the same VLAN).

Switch-specific CLI commands are delegated to **driver modules** (pluggable, one per vendor). SSH connections use [Netmiko](https://github.com/ktbyers/netmiko).

## Quick start

```bash
# 1. Install
pip install git+https://github.com/fcefyn-testbed/labgrid-switch-abstraction.git

# 2. Create switch credentials (TP-Link example; see below for OpenWrt)
mkdir -p ~/.config
cat > ~/.config/switch.conf << 'EOF'
SWITCH_HOST=192.168.0.1
SWITCH_USER=admin
SWITCH_PASSWORD=secret
SWITCH_DRIVER=tplink_jetstream
SWITCH_DEVICE_TYPE=tplink_jetstream
EOF
chmod 600 ~/.config/switch.conf

# For a switch running OpenWrt, use:
# SWITCH_DRIVER=openwrt
# SWITCH_DEVICE_TYPE=linux

# 3. Create DUT config
sudo mkdir -p /etc/testbed
sudo tee /etc/testbed/dut-config.yaml << 'EOF'
switch:
  host: "192.168.0.1"
  user: "admin"
  uplink_ports: [9, 10]
  vlan_topology: 200

duts:
  my_router_1:
    switch_port: 1
    switch_vlan_isolated: 101
    pool: "isolated"
  my_router_2:
    switch_port: 2
    switch_vlan_isolated: 102
    pool: "shared"
EOF

# 4. Verify - restore all DUTs to their default isolated VLANs
switch-vlan --restore-all
```

## CLI usage

```bash
# Switch a single DUT to VLAN 200
switch-vlan my_router_1 200

# Switch multiple DUTs at once
switch-vlan my_router_1 my_router_2 200

# Restore one DUT to its default isolated VLAN
switch-vlan my_router_1 --restore

# Restore all DUTs
switch-vlan --restore-all

# Use a custom config path
switch-vlan --config /path/to/dut-config.yaml my_router_1 200

# Verbose output
switch-vlan -v my_router_1 200
```

## Library usage

### From pytest fixtures (single DUT)

```python
from switch_abstraction.vlan_manager import set_port_vlan, restore_port_vlan, load_config

config = load_config("/etc/testbed/dut-config.yaml")
dut_map = config.get("duts", {})

set_port_vlan("my_router_1", 200, dut_map=dut_map, config=config)
# ... run test ...
restore_port_vlan("my_router_1", dut_map=dut_map, config=config)
```

### Batch operations (multiple DUTs, single SSH session)

When switching multiple DUTs, use the batch functions to avoid one SSH connection per DUT:

```python
from switch_abstraction.vlan_manager import (
    set_ports_vlan_batch, restore_ports_vlan_batch, load_config,
)

config = load_config()
dut_map = config.get("duts", {})
dut_names = ["my_router_1", "my_router_2", "my_router_3"]

set_ports_vlan_batch(dut_names, 200, dut_map=dut_map, config=config)
# ... run shared-network tests ...
restore_ports_vlan_batch(dut_names, dut_map=dut_map, config=config)
```

The CLI also batches automatically when multiple DUT names are given:

```bash
# All three switch in one SSH session
switch-vlan my_router_1 my_router_2 my_router_3 200
```

### Querying current port VLAN (PVID)

```python
from switch_abstraction.client import SwitchClient

client = SwitchClient()
pvid = client.get_port_pvid(port=1)  # returns int or None
```

## Configuration

### `switch.conf` - switch credentials

Located at `~/.config/switch.conf` (or `/etc/switch.conf`, or path set by `SWITCH_CONFIG` env var). Key-value format, one per line:

| Key | Default | Description |
|---|---|---|
| `SWITCH_HOST` | `192.168.0.1` | Switch management IP |
| `SWITCH_USER` | `admin` | SSH username |
| `SWITCH_PASSWORD` | *(required)* | SSH password |
| `SWITCH_DRIVER` | `tplink_jetstream` | Driver module name (filename in `drivers/`) |
| `SWITCH_DEVICE_TYPE` | `tplink_jetstream` | Netmiko device type |

Config file search order: `SWITCH_CONFIG` env var > `~/.config/switch.conf` > `/etc/switch.conf`.

### `dut-config.yaml` - DUT hardware map

Default path: `/etc/testbed/dut-config.yaml` (override with `SWITCH_DUT_CONFIG` env var).

#### `switch` section

| Key | Required | Default | Description |
|---|---|---|---|
| `host` | no | from `switch.conf` | Switch management IP |
| `user` | no | from `switch.conf` | SSH username |
| `uplink_ports` | yes | - | Ports carrying tagged traffic to the lab host (list of ints) |
| `vlan_topology` | no | `200` | VLAN ID for the shared/topology network |

#### `duts` section

Each key is a DUT name (must match Labgrid place names). Fields:

| Key | Required | Description |
|---|---|---|
| `switch_port` | yes | Physical switch port the DUT is connected to |
| `switch_vlan_isolated` | yes | Dedicated VLAN for this DUT when in isolated mode |
| `pool` | no | `"isolated"` or `"shared"` - determines the default network pool. Legacy value `"libremesh"` is accepted as an alias for `"shared"` |
| `switch_port_poe` | no | Separate PoE port if different from `switch_port` |

#### Full example

```yaml
switch:
  host: "192.168.0.1"
  user: "admin"
  uplink_ports: [9, 10]
  vlan_topology: 200

duts:
  belkin_rt3200_1:
    switch_port: 1
    switch_vlan_isolated: 101
    pool: "isolated"
  belkin_rt3200_2:
    switch_port: 2
    switch_vlan_isolated: 102
    pool: "shared"
    switch_port_poe: 3
  tplink_wdr3500_1:
    switch_port: 4
    switch_vlan_isolated: 104
    pool: "isolated"
```

## Environment variables

| Variable | Description |
|---|---|
| `SWITCH_HOST` | Overrides switch IP from config file |
| `SWITCH_USER` | Overrides SSH username from config file |
| `SWITCH_PASSWORD` | Overrides SSH password from config file |
| `SWITCH_CONFIG` | Path to `switch.conf` (overrides default locations) |
| `SWITCH_DUT_CONFIG` | Path to `dut-config.yaml` (overrides `/etc/testbed/dut-config.yaml`) |

## Adding a new switch driver

1. Create `src/switch_abstraction/drivers/<name>.py`.
2. Implement the required functions (see [DRIVER_INTERFACE.md](src/switch_abstraction/drivers/DRIVER_INTERFACE.md)):

```python
def build_poe_commands(port: int, action: str) -> list[str]: ...
def assign_port_vlan_commands(port, vlan_id, mode, remove_vlans) -> list[str]: ...
def ensure_vlan_commands(vlan_id: int, name: str | None = None) -> list[str]: ...
def build_hybrid_commands(port_assignments, ...) -> list[str]: ...

# Optional - needed for get_port_pvid() support:
def get_port_pvid_command(port: int) -> str: ...
def parse_port_pvid(output: str) -> int | None: ...
```

3. Set `SWITCH_DRIVER=<name>` and `SWITCH_DEVICE_TYPE=<netmiko_type>` in `switch.conf`.

**Example `switch.conf` for an OpenWrt switch:**

```ini
SWITCH_HOST=192.168.128.2
SWITCH_USER=root
SWITCH_PASSWORD=
SWITCH_DRIVER=openwrt
SWITCH_DEVICE_TYPE=linux
```

> **Note for UCI-based drivers:** The `openwrt` driver also exports `ensure_commit_commands()` to emit a final `uci commit network` + service reload. Drivers using interactive CLI (like `tplink_jetstream`) don't need this because `send_config_set()` applies changes on exit. See [DRIVER_INTERFACE.md](src/switch_abstraction/drivers/DRIVER_INTERFACE.md) for details.

### Netmiko device types

The `SWITCH_DEVICE_TYPE` value is passed directly to Netmiko's `ConnectHandler(device_type=...)`. See the [Netmiko PLATFORMS list](https://github.com/ktbyers/netmiko/blob/develop/PLATFORMS.md) for all supported types. Common examples: `tplink_jetstream`, `cisco_ios`, `arista_eos`, `hp_procurve`, `linux`.

## Tested switches

| Switch | Driver | Netmiko device_type | Notes |
|---|---|---|---|
| TP-Link SG2016P (JetStream) | `tplink_jetstream` | `tplink_jetstream` | Vendor CLI over SSH |
| Zyxel GS1900-24EP (OpenWrt) | `openwrt` | `linux` | UCI commands over SSH (DSA bridge-vlans) |

Contributions of drivers for other switches are welcome.

## Architecture

```
switch_abstraction/
├── client.py           # SwitchClient: SSH via Netmiko, flock serialization
├── vlan_manager.py     # set_port_vlan / restore: DUT name → port → driver commands
├── constants.py        # defaults (VLAN_MESH, config paths)
└── drivers/
    ├── __init__.py     # dynamic driver loader
    ├── tplink_jetstream.py  # TP-Link JetStream CLI commands
    ├── openwrt.py           # OpenWrt UCI commands (DSA bridge-vlans)
    └── DRIVER_INTERFACE.md  # driver contract
```
