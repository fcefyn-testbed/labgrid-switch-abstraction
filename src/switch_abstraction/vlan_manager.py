"""
VLAN Manager - Per-DUT dynamic VLAN switching for unified pool architecture.

Provides set_port_vlan() to change a single switch port's VLAN assignment
at test time. Designed to be called from pytest fixtures or CLI.
Thread-safe via SwitchClient's flock serialization.

The switch driver's assign_port_vlan_commands() builds vendor-specific CLI;
this module resolves DUT names to physical switch ports via a YAML config.

Usage (library):
    from switch_abstraction.vlan_manager import set_port_vlan, load_dut_map
    dut_map = load_dut_map("/etc/testbed/dut-config.yaml")
    set_port_vlan("belkin_rt3200_2", 200, dut_map=dut_map)

Usage (CLI):
    switch-vlan belkin_rt3200_2 200
    switch-vlan belkin_rt3200_2 --restore
    switch-vlan belkin_rt3200_1 belkin_rt3200_2 belkin_rt3200_3 200
    switch-vlan belkin_rt3200_1 belkin_rt3200_2 --restore
    switch-vlan --restore-all
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import yaml

from switch_abstraction.client import SwitchClient, get_switch_driver
from switch_abstraction.constants import VLAN_MESH, default_dut_config_path

logger = logging.getLogger(__name__)


def load_dut_map(config_path: Path | str | None = None) -> dict[str, dict]:
    """Load DUT hardware database from a YAML config file.

    Returns a dict keyed by DUT name with at least:
        switch_port, switch_vlan_isolated
    """
    path = Path(config_path) if config_path else default_dut_config_path()
    with open(path) as f:
        config = yaml.safe_load(f)
    return config.get("duts", {})


def set_port_vlan(
    dut_name: str,
    vlan_id: int,
    *,
    dut_map: dict[str, dict] | None = None,
    config_path: Path | str | None = None,
) -> bool:
    """Switch a DUT's port to the target VLAN.

    Args:
        dut_name: key in the duts section of config.
        vlan_id: target VLAN (e.g. 200 for mesh, or the isolated VLAN).
        dut_map: pre-loaded DUT map (avoids re-reading YAML per call).
        config_path: override path to dut-config.yaml.

    Returns True on success, False on failure.
    """
    if dut_map is None:
        dut_map = load_dut_map(config_path)

    hw = dut_map.get(dut_name)
    if hw is None:
        logger.error("DUT '%s' not found in config", dut_name)
        return False

    port = hw["switch_port"]
    isolated_vlan = hw["switch_vlan_isolated"]

    if vlan_id == isolated_vlan:
        remove_vlans = [VLAN_MESH]
    elif vlan_id == VLAN_MESH:
        remove_vlans = [isolated_vlan]
    else:
        remove_vlans = [isolated_vlan, VLAN_MESH]

    driver = get_switch_driver()
    cmds = driver.assign_port_vlan_commands(
        port, vlan_id, "untagged", remove_vlans=remove_vlans,
    )

    extra_port = hw.get("switch_port_poe")
    if extra_port and extra_port != port:
        cmds.extend(driver.assign_port_vlan_commands(
            extra_port, vlan_id, "untagged", remove_vlans=remove_vlans,
        ))

    client = SwitchClient()
    success = client.send_config_commands(cmds)

    if success:
        logger.info("DUT '%s' port %d switched to VLAN %d", dut_name, port, vlan_id)
    else:
        logger.error("Failed to switch DUT '%s' port %d to VLAN %d", dut_name, port, vlan_id)

    return success


def restore_port_vlan(
    dut_name: str,
    *,
    dut_map: dict[str, dict] | None = None,
    config_path: Path | str | None = None,
) -> bool:
    """Restore a DUT's port to its default isolated VLAN."""
    if dut_map is None:
        dut_map = load_dut_map(config_path)

    hw = dut_map.get(dut_name)
    if hw is None:
        logger.error("DUT '%s' not found in config", dut_name)
        return False

    return set_port_vlan(
        dut_name, hw["switch_vlan_isolated"], dut_map=dut_map,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Switch DUT port(s) to a target VLAN",
        epilog="""
Examples:
  switch-vlan belkin_rt3200_1 200              # One DUT to VLAN 200
  switch-vlan belkin_rt3200_1 belkin_rt3200_2 200   # Multiple DUTs to VLAN 200
  switch-vlan belkin_rt3200_1 --restore        # Restore one DUT
  switch-vlan belkin_rt3200_1 belkin_rt3200_2 --restore  # Restore multiple
  switch-vlan --restore-all                    # Restore all DUTs to isolated VLANs
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "args", nargs="*",
        help="DUT name(s), optionally followed by a VLAN ID",
    )
    parser.add_argument(
        "--restore", action="store_true",
        help="Restore DUT(s) to their default isolated VLAN",
    )
    parser.add_argument(
        "--restore-all", action="store_true",
        help="Restore ALL DUTs in config to their default isolated VLANs",
    )
    parser.add_argument(
        "--config", type=Path, default=None,
        help="Path to dut-config.yaml (default: SWITCH_DUT_CONFIG or /etc/testbed/dut-config.yaml)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")

    parsed = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if parsed.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if parsed.restore_all:
        dut_map = load_dut_map(parsed.config)
        all_ok = True
        for name in dut_map:
            if not restore_port_vlan(name, dut_map=dut_map):
                all_ok = False
        sys.exit(0 if all_ok else 1)

    positional = parsed.args
    if not positional:
        parser.error("Provide DUT name(s) or use --restore-all")

    vlan_id: int | None = None
    dut_names: list[str]

    if not parsed.restore:
        try:
            vlan_id = int(positional[-1])
            dut_names = positional[:-1]
        except ValueError:
            vlan_id = None
            dut_names = positional

        if vlan_id is None:
            parser.error("Last argument must be a VLAN ID, or use --restore")
        if not dut_names:
            parser.error("Provide at least one DUT name before the VLAN ID")
    else:
        dut_names = positional

    dut_map = load_dut_map(parsed.config)
    all_ok = True
    for name in dut_names:
        if parsed.restore:
            if not restore_port_vlan(name, dut_map=dut_map):
                all_ok = False
        else:
            if not set_port_vlan(name, vlan_id, dut_map=dut_map):
                all_ok = False

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
