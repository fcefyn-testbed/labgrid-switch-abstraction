"""
VLAN Manager - Per-DUT dynamic VLAN switching between isolated and shared VLANs.

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

from switch_abstraction.client import SwitchClient
from switch_abstraction.constants import VLAN_SHARED, default_dut_config_path

logger = logging.getLogger(__name__)

VALID_POOLS = {"isolated", "shared"}


def load_config(config_path: Path | str | None = None) -> dict:
    """Load the full YAML config file.

    Returns the parsed dict containing 'switch' and 'duts' sections.
    """
    path = Path(config_path) if config_path else default_dut_config_path()
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_dut_map(config_path: Path | str | None = None) -> dict[str, dict]:
    """Load DUT hardware database from a YAML config file.

    Returns a dict keyed by DUT name with at least:
        switch_port, switch_vlan_isolated
    """
    return load_config(config_path).get("duts", {})


def get_vlan_shared(config: dict | None = None) -> int:
    """Resolve the shared/topology VLAN from config, falling back to VLAN_SHARED."""
    if config:
        return config.get("switch", {}).get("vlan_topology", VLAN_SHARED)
    return VLAN_SHARED


def get_default_pool(hw: dict) -> str:
    """Return the configured default pool for a DUT.

    Missing `pool` defaults to `isolated` for backward compatibility.
    """
    pool = str(hw.get("pool", "isolated")).strip().lower()
    if pool not in VALID_POOLS:
        raise ValueError(
            f"Invalid pool value {pool!r}. Expected 'isolated' or 'shared'."
        )
    return pool


def get_default_vlan(
    _dut_name: str,
    hw: dict,
    *,
    config: dict | None = None,
) -> int:
    """Resolve the default VLAN for a DUT based on its configured pool."""
    pool = get_default_pool(hw)
    if pool == "shared":
        return get_vlan_shared(config)
    return hw["switch_vlan_isolated"]


def _switch_client_kwargs(config: dict | None = None) -> dict[str, object]:
    """Return SwitchClient keyword args derived from the parsed config."""
    if config is None:
        return {}
    return {"config": config.get("switch", {})}


def _resolve_client_and_driver(
    *,
    config: dict | None = None,
    client: SwitchClient | None = None,
    driver=None,
    driver_name: str | None = None,
):
    """Resolve the active client and driver for a VLAN operation."""
    if client is None:
        client = SwitchClient(
            driver=driver,
            driver_name=driver_name,
            **_switch_client_kwargs(config),
        )

    resolved_driver = driver or getattr(client, "driver", None)
    if resolved_driver is None:
        raise ValueError("Could not resolve an active switch driver")

    return client, resolved_driver


def _finalize_vlan_commands(driver) -> list[str]:
    """Return any driver-specific commands needed to apply pending VLAN changes."""
    finalize = getattr(driver, "finalize_vlan_commands", None)
    if not callable(finalize):
        return []
    return list(finalize() or [])


def set_port_vlan(
    dut_name: str,
    vlan_id: int,
    *,
    dut_map: dict[str, dict] | None = None,
    config: dict | None = None,
    config_path: Path | str | None = None,
    client: SwitchClient | None = None,
    driver=None,
    driver_name: str | None = None,
) -> bool:
    """Switch a DUT's port to the target VLAN.

    Args:
        dut_name: key in the duts section of config.
        vlan_id: target VLAN (e.g. 200 for the shared VLAN, or the isolated VLAN).
        dut_map: pre-loaded DUT map (avoids re-reading YAML per call).
        config: full parsed config dict (to read vlan_topology).
        config_path: override path to dut-config.yaml.

    Returns True on success, False on failure.
    """
    if config is None and dut_map is None:
        config = load_config(config_path)

    if dut_map is None:
        dut_map = config.get("duts", {})

    vlan_shared = get_vlan_shared(config)

    hw = dut_map.get(dut_name)
    if hw is None:
        logger.error("DUT '%s' not found in config", dut_name)
        return False

    port = hw["switch_port"]
    isolated_vlan = hw["switch_vlan_isolated"]

    if vlan_id == isolated_vlan:
        remove_vlans = [vlan_shared]
    elif vlan_id == vlan_shared:
        remove_vlans = [isolated_vlan]
    else:
        remove_vlans = [isolated_vlan, vlan_shared]

    try:
        client, resolved_driver = _resolve_client_and_driver(
            config=config,
            client=client,
            driver=driver,
            driver_name=driver_name,
        )
    except ValueError as e:
        logger.error("%s", e)
        return False

    cmds = resolved_driver.assign_port_vlan_commands(
        port, vlan_id, "untagged", remove_vlans=remove_vlans,
    )

    extra_port = hw.get("switch_port_poe")
    if extra_port and extra_port != port:
        cmds.extend(resolved_driver.assign_port_vlan_commands(
            extra_port, vlan_id, "untagged", remove_vlans=remove_vlans,
        ))
    cmds.extend(_finalize_vlan_commands(resolved_driver))

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
    config: dict | None = None,
    config_path: Path | str | None = None,
    client: SwitchClient | None = None,
    driver=None,
    driver_name: str | None = None,
) -> bool:
    """Restore a DUT's port to its configured default pool."""
    if config is None and dut_map is None:
        config = load_config(config_path)

    if dut_map is None:
        dut_map = config.get("duts", {})

    hw = dut_map.get(dut_name)
    if hw is None:
        logger.error("DUT '%s' not found in config", dut_name)
        return False

    try:
        default_vlan = get_default_vlan(dut_name, hw, config=config)
    except ValueError as e:
        logger.error("%s", e)
        return False

    set_kwargs = {
        "dut_map": dut_map,
        "config": config,
    }
    if client is not None:
        set_kwargs["client"] = client
    if driver is not None:
        set_kwargs["driver"] = driver
    if driver_name is not None:
        set_kwargs["driver_name"] = driver_name

    return set_port_vlan(
        dut_name,
        default_vlan,
        **set_kwargs,
    )


def _build_dut_commands(
    dut_name: str,
    vlan_id: int,
    dut_map: dict[str, dict],
    vlan_shared: int,
    driver,
) -> list[str] | None:
    """Build CLI commands for a single DUT VLAN change. Returns None if DUT not found."""
    hw = dut_map.get(dut_name)
    if hw is None:
        logger.error("DUT '%s' not found in config", dut_name)
        return None

    port = hw["switch_port"]
    isolated_vlan = hw["switch_vlan_isolated"]

    if vlan_id == isolated_vlan:
        remove_vlans = [vlan_shared]
    elif vlan_id == vlan_shared:
        remove_vlans = [isolated_vlan]
    else:
        remove_vlans = [isolated_vlan, vlan_shared]

    cmds = driver.assign_port_vlan_commands(
        port, vlan_id, "untagged", remove_vlans=remove_vlans,
    )

    extra_port = hw.get("switch_port_poe")
    if extra_port and extra_port != port:
        cmds.extend(driver.assign_port_vlan_commands(
            extra_port, vlan_id, "untagged", remove_vlans=remove_vlans,
        ))
    return cmds


def set_ports_vlan_batch(
    dut_names: list[str],
    vlan_id: int,
    *,
    dut_map: dict[str, dict] | None = None,
    config: dict | None = None,
    config_path: Path | str | None = None,
    client: SwitchClient | None = None,
    driver=None,
    driver_name: str | None = None,
) -> bool:
    """Switch multiple DUT ports to a VLAN in a single SSH session.

    Collects all CLI commands, sends them in one connection.
    Returns True if all commands succeed, False on any error.
    """
    if config is None and dut_map is None:
        config = load_config(config_path)
    if dut_map is None:
        dut_map = config.get("duts", {})

    vlan_shared = get_vlan_shared(config)
    try:
        client, resolved_driver = _resolve_client_and_driver(
            config=config,
            client=client,
            driver=driver,
            driver_name=driver_name,
        )
    except ValueError as e:
        logger.error("%s", e)
        return False

    all_cmds: list[str] = []
    valid_duts: list[str] = []

    for name in dut_names:
        cmds = _build_dut_commands(
            name, vlan_id, dut_map, vlan_shared, resolved_driver
        )
        if cmds is None:
            return False
        all_cmds.extend(cmds)
        valid_duts.append(name)

    if not all_cmds:
        return True

    all_cmds.extend(_finalize_vlan_commands(resolved_driver))
    success = client.send_config_commands(all_cmds)

    if success:
        for name in valid_duts:
            port = dut_map[name]["switch_port"]
            logger.info("DUT '%s' port %d switched to VLAN %d", name, port, vlan_id)
    else:
        logger.error("Batch VLAN switch failed for DUTs: %s", valid_duts)

    return success


def restore_ports_vlan_batch(
    dut_names: list[str],
    *,
    dut_map: dict[str, dict] | None = None,
    config: dict | None = None,
    config_path: Path | str | None = None,
    client: SwitchClient | None = None,
    driver=None,
    driver_name: str | None = None,
) -> bool:
    """Restore multiple DUT ports to their configured default pools in one SSH session."""
    if config is None and dut_map is None:
        config = load_config(config_path)
    if dut_map is None:
        dut_map = config.get("duts", {})

    vlan_shared = get_vlan_shared(config)
    try:
        client, resolved_driver = _resolve_client_and_driver(
            config=config,
            client=client,
            driver=driver,
            driver_name=driver_name,
        )
    except ValueError as e:
        logger.error("%s", e)
        return False

    all_cmds: list[str] = []
    valid_duts: list[str] = []

    for name in dut_names:
        hw = dut_map.get(name)
        if hw is None:
            logger.error("DUT '%s' not found in config", name)
            return False
        try:
            default_vlan = get_default_vlan(name, hw, config=config)
        except ValueError as e:
            logger.error("%s", e)
            return False
        cmds = _build_dut_commands(
            name, default_vlan, dut_map, vlan_shared, resolved_driver
        )
        if cmds is None:
            return False
        all_cmds.extend(cmds)
        valid_duts.append(name)

    if not all_cmds:
        return True

    all_cmds.extend(_finalize_vlan_commands(resolved_driver))
    success = client.send_config_commands(all_cmds)

    if success:
        for name in valid_duts:
            port = dut_map[name]["switch_port"]
            default_vlan = get_default_vlan(name, dut_map[name], config=config)
            logger.info("DUT '%s' port %d restored to VLAN %d", name, port, default_vlan)
    else:
        logger.error("Batch VLAN restore failed for DUTs: %s", valid_duts)

    return success


def main():
    parser = argparse.ArgumentParser(
        description="Switch DUT port(s) to a target VLAN",
        epilog="""
Examples:
  switch-vlan belkin_rt3200_1 200              # One DUT to VLAN 200
  switch-vlan belkin_rt3200_1 belkin_rt3200_2 200   # Multiple DUTs to VLAN 200
  switch-vlan belkin_rt3200_1 --restore        # Restore one DUT to its configured pool
  switch-vlan belkin_rt3200_1 belkin_rt3200_2 --restore  # Restore multiple
  switch-vlan --restore-all                    # Restore all DUTs to their configured pools
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "args", nargs="*",
        help="DUT name(s), optionally followed by a VLAN ID",
    )
    parser.add_argument(
        "--restore", action="store_true",
        help="Restore DUT(s) to their configured default pool",
    )
    parser.add_argument(
        "--restore-all", action="store_true",
        help="Restore ALL DUTs in config to their configured default pools",
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

    config = load_config(parsed.config)
    dut_map = config.get("duts", {})

    if parsed.restore_all:
        all_names = list(dut_map.keys())
        all_ok = restore_ports_vlan_batch(
            all_names, dut_map=dut_map, config=config,
        )
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

    if parsed.restore:
        all_ok = restore_ports_vlan_batch(
            dut_names, dut_map=dut_map, config=config,
        )
    else:
        all_ok = set_ports_vlan_batch(
            dut_names, vlan_id, dut_map=dut_map, config=config,
        )

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
