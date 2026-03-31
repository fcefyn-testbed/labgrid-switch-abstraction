# labgrid-switch-abstraction

Vendor-agnostic switch abstraction layer for [LabGrid](https://labgrid.readthedocs.io/) testbeds. Provides dynamic per-port VLAN switching for hardware-in-the-loop test environments.

Analogous to [PDUDaemon](https://github.com/pdudaemon/pdudaemon) for power control, this package handles network topology control.

## Install

```bash
pip install git+https://github.com/fcefyn-testbed/labgrid-switch-abstraction.git
```

## Usage

### Library (from pytest fixtures)

```python
from switch_abstraction.vlan_manager import set_port_vlan, restore_port_vlan, load_dut_map

dut_map = load_dut_map("/etc/testbed/dut-config.yaml")
set_port_vlan("belkin_rt3200_2", 200, dut_map=dut_map)
# ... run test ...
restore_port_vlan("belkin_rt3200_2", dut_map=dut_map)
```

### Querying current port VLAN (PVID)

```python
from switch_abstraction.client import SwitchClient

client = SwitchClient()
pvid = client.get_port_pvid(port=16)
# pvid is an int (e.g. 104) or None on failure
```

### CLI

```bash
# Single DUT
switch-vlan belkin_rt3200_2 200
switch-vlan belkin_rt3200_2 --restore

# Multiple DUTs
switch-vlan belkin_rt3200_1 belkin_rt3200_2 belkin_rt3200_3 200
switch-vlan belkin_rt3200_1 belkin_rt3200_2 --restore

# All DUTs at once
switch-vlan --restore-all
```

### Environment variable

Set `SWITCH_DUT_CONFIG` to avoid passing `--config` every time:

```bash
export SWITCH_DUT_CONFIG=/etc/testbed/dut-config.yaml
switch-vlan belkin_rt3200_2 200
```

## Configuration

### Switch credentials

Create `~/.config/switch.conf`:

```ini
SWITCH_HOST=192.168.0.1
SWITCH_USER=admin
SWITCH_PASSWORD=secret
SWITCH_DRIVER=tplink_jetstream
```

Environment variables `SWITCH_HOST`, `SWITCH_USER`, `SWITCH_PASSWORD` override the config file.

### DUT config (YAML)

```yaml
switch:
  host: "192.168.0.1"
  user: "admin"
  uplink_ports: [9, 10]

duts:
  my_device:
    switch_port: 11
    switch_vlan_isolated: 100
```

## Adding a new switch driver

See [DRIVER_INTERFACE.md](src/switch_abstraction/drivers/DRIVER_INTERFACE.md).
