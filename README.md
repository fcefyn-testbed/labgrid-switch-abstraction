# labgrid-switch-abstraction

Vendor-agnostic switch abstraction layer for [LabGrid](https://labgrid.readthedocs.io/) testbeds. Provides dynamic per-port VLAN switching for hardware-in-the-loop test environments.

Analogous to [PDUDaemon](https://github.com/pdudaemon/pdudaemon) for power control, this package handles network topology control.

## Install

```bash
pip install git+https://github.com/<org>/labgrid-switch-abstraction.git
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

### CLI

```bash
switch-vlan belkin_rt3200_2 200 --config /etc/testbed/dut-config.yaml
switch-vlan belkin_rt3200_2 --restore --config /etc/testbed/dut-config.yaml
```

### Environment variable

Set `SWITCH_DUT_CONFIG` to avoid passing `--config` every time:

```bash
export SWITCH_DUT_CONFIG=/etc/testbed/dut-config.yaml
switch-vlan belkin_rt3200_2 200
```

## Configuration

### Switch credentials

Create `~/.config/poe_switch_control.conf`:

```ini
POE_SWITCH_HOST=192.168.0.1
POE_SWITCH_USER=admin
POE_SWITCH_PASSWORD=secret
POE_SWITCH_DRIVER=tplink_jetstream
```

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
