"""
Switch Client - Centralized SSH management for managed switches via Netmiko.

All switch SSH operations (VLAN configuration, PoE control) go through this
module. Vendor-specific command building is delegated to driver modules in
drivers/. This module handles:
  - Credential loading from config files and environment variables
  - SSH connection via Netmiko (ConnectHandler)
  - Lockfile serialization to prevent concurrent SSH sessions
  - High-level operations: send_config_commands, poe_on/off/cycle

Requires: netmiko (pip install netmiko)
"""

from __future__ import annotations

import fcntl
import logging
import os
import time
from contextlib import contextmanager
from types import ModuleType
from typing import Generator, Mapping

from switch_abstraction.constants import (
    DEFAULT_SWITCH_HOST,
    DEFAULT_SWITCH_USER,
    user_config_dir,
)

logger = logging.getLogger(__name__)

DEFAULT_DEVICE_TYPE = "tplink_jetstream"

DEFAULT_LOCK_PATH = "/tmp/switch.lock"
DEFAULT_LOCK_TIMEOUT = 60.0
DEFAULT_CONN_TIMEOUT = 15


class SwitchLockTimeoutError(TimeoutError):
    """Raised when the switch lock cannot be acquired in time."""


def _config_value(
    config: Mapping[str, object] | None,
    *keys: str,
) -> str | None:
    """Return the first non-empty string-like config value for the given keys."""
    if config is None:
        return None
    for key in keys:
        value = config.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def get_lock_path() -> str:
    """Return the lock path from env or the built-in default."""
    return os.environ.get("SWITCH_LOCK_PATH", DEFAULT_LOCK_PATH)


def get_lock_timeout() -> float:
    """Return the lock timeout from env or the built-in default."""
    raw = os.environ.get("SWITCH_LOCK_TIMEOUT")
    if raw is None:
        return DEFAULT_LOCK_TIMEOUT
    try:
        timeout = float(raw)
    except ValueError:
        logger.warning(
            "Invalid SWITCH_LOCK_TIMEOUT=%r, using default %.1fs",
            raw,
            DEFAULT_LOCK_TIMEOUT,
        )
        return DEFAULT_LOCK_TIMEOUT
    if timeout < 0:
        logger.warning(
            "Negative SWITCH_LOCK_TIMEOUT=%r, using default %.1fs",
            raw,
            DEFAULT_LOCK_TIMEOUT,
        )
        return DEFAULT_LOCK_TIMEOUT
    return timeout


def _open_lock_file(lock_path: str):
    """Open lock file for flock. Creates with 0o666 to allow any user to open it."""
    try:
        return os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o666)
    except PermissionError:
        if os.path.exists(lock_path):
            try:
                os.chmod(lock_path, 0o666)
                return os.open(lock_path, os.O_RDWR)
            except (PermissionError, OSError) as e:
                raise PermissionError(
                    f"Cannot access lock file {lock_path}. "
                    f"If it was created by root, run: sudo chmod 666 {lock_path}"
                ) from e
        raise


@contextmanager
def switch_lock(
    timeout: float | None = None,
    lock_path: str | None = None,
) -> Generator[None, None, None]:
    """Acquire exclusive lock to serialize SSH sessions to the switch."""
    lock_fd = None
    resolved_timeout = get_lock_timeout() if timeout is None else timeout
    resolved_lock_path = get_lock_path() if lock_path is None else lock_path
    try:
        lock_fd = _open_lock_file(resolved_lock_path)
        deadline = time.time() + resolved_timeout
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                logger.debug("Acquired switch lock")
                break
            except BlockingIOError:
                if time.time() >= deadline:
                    raise SwitchLockTimeoutError(
                        f"Timed out after {resolved_timeout:.1f}s waiting for switch lock "
                        f"at {resolved_lock_path}"
                    )
                time.sleep(0.1)

        yield
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except Exception:
                pass
            os.close(lock_fd)
            logger.debug("Released switch lock")


def _get_config_paths() -> list[str]:
    """Return config file paths to check, accounting for sudo.

    Priority:
    1. SWITCH_CONFIG env var
    2. ~/.config/switch.conf
    3. /etc/switch.conf
    """
    paths: list[str] = []
    if explicit := os.environ.get("SWITCH_CONFIG"):
        paths.append(explicit)
    paths.append(str(user_config_dir() / "switch.conf"))
    paths.append("/etc/switch.conf")
    return paths


def load_config() -> dict[str, str]:
    """Load switch credentials from config file or environment.

    Config file format (key=value, one per line):
        SWITCH_HOST=192.168.0.1
        SWITCH_USER=admin
        SWITCH_PASSWORD=secret
    """
    config: dict[str, str] = {}
    for path in _get_config_paths():
        if os.path.isfile(path) and os.access(path, os.R_OK):
            try:
                with open(path) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            key, _, value = line.partition("=")
                            config[key.strip()] = value.strip().strip("'\"")
            except OSError:
                pass
            break
    return config


def load_switch_password() -> str:
    """Load switch password from config file or SWITCH_PASSWORD env var."""
    config = load_config()
    return os.environ.get("SWITCH_PASSWORD") or config.get("SWITCH_PASSWORD", "")


def get_switch_driver(
    name: str | None = None,
    *,
    config: Mapping[str, object] | None = None,
):
    """Load the configured driver module from drivers/."""
    from switch_abstraction.drivers import get_driver

    resolved_name = (
        name
        or os.environ.get("SWITCH_DRIVER")
        or _config_value(config, "driver", "SWITCH_DRIVER")
        or load_config().get("SWITCH_DRIVER", "tplink_jetstream")
    )
    return get_driver(resolved_name)


def get_credentials(
    host: str | None = None,
    user: str | None = None,
    password: str | None = None,
    device_type: str | None = None,
    *,
    config: Mapping[str, object] | None = None,
) -> dict[str, str]:
    """Build a credentials dict, filling gaps from config/env."""
    resolved_config: Mapping[str, object] = config or load_config()
    return {
        "host": (
            host
            or os.environ.get("SWITCH_HOST")
            or _config_value(resolved_config, "host", "SWITCH_HOST")
            or DEFAULT_SWITCH_HOST
        ),
        "user": (
            user
            or os.environ.get("SWITCH_USER")
            or _config_value(resolved_config, "user", "SWITCH_USER")
            or DEFAULT_SWITCH_USER
        ),
        "password": (
            password
            or os.environ.get("SWITCH_PASSWORD")
            or _config_value(resolved_config, "password", "SWITCH_PASSWORD")
            or ""
        ),
        "device_type": (
            device_type
            or os.environ.get("SWITCH_DEVICE_TYPE")
            or _config_value(resolved_config, "device_type", "SWITCH_DEVICE_TYPE")
            or DEFAULT_DEVICE_TYPE
        ),
    }


def _resolve_conn_timeout(config: Mapping[str, object] | None = None) -> int:
    """Return SSH conn_timeout from env, config, or built-in default."""
    raw = os.environ.get("SWITCH_CONN_TIMEOUT")
    if raw is None and config:
        raw = _config_value(config, "conn_timeout", "SWITCH_CONN_TIMEOUT")
    if raw is None:
        return DEFAULT_CONN_TIMEOUT
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid SWITCH_CONN_TIMEOUT=%r, using default %d",
            raw,
            DEFAULT_CONN_TIMEOUT,
        )
        return DEFAULT_CONN_TIMEOUT
    return max(1, value)


class SwitchClient:
    """Central SSH client for managed switch operations.

    Uses Netmiko for SSH transport and vendor-specific drivers for
    command building. All operations are serialized via a lockfile.
    """

    def __init__(
        self,
        host: str | None = None,
        user: str | None = None,
        password: str | None = None,
        device_type: str | None = None,
        *,
        conn_timeout: int | None = None,
        config: Mapping[str, object] | None = None,
        driver: ModuleType | None = None,
        driver_name: str | None = None,
    ):
        resolved_config = load_config()
        if config:
            resolved_config.update(
                {
                    key: value
                    for key, value in config.items()
                    if value not in (None, "")
                }
            )
        creds = get_credentials(
            host,
            user,
            password,
            device_type,
            config=resolved_config,
        )
        self.host = creds["host"]
        self.user = creds["user"]
        self.password = creds["password"]
        self.device_type = creds["device_type"]
        self.conn_timeout = conn_timeout or _resolve_conn_timeout(resolved_config)
        self.driver = driver or get_switch_driver(driver_name, config=resolved_config)

        if not self.password:
            raise ValueError(
                "Switch password required. Set SWITCH_PASSWORD in "
                "~/.config/switch.conf or via env var."
            )

    def _connect(self):
        """Create a Netmiko connection (caller must disconnect).

        Disables SSH agent and key-file scanning to force password auth,
        avoiding connection drops on devices that reject unsolicited key
        offers (e.g. TP-Link JetStream firmware).
        """
        from netmiko import ConnectHandler

        return ConnectHandler(
            device_type=self.device_type,
            host=self.host,
            username=self.user,
            password=self.password,
            conn_timeout=self.conn_timeout,
            allow_agent=False,
            use_keys=False,
        )

    def send_config_commands(self, commands: list[str]) -> bool:
        """Send configuration commands to the switch."""
        if not commands:
            logger.info("No commands to send, skipping SSH session")
            return True

        try:
            with switch_lock():
                try:
                    conn = self._connect()
                except Exception as e:
                    logger.error("SSH connection to switch failed: %s", e)
                    return False

                try:
                    output = conn.send_config_set(
                        commands,
                        cmd_verify=False,
                    )
                    logger.debug("Switch output:\n%s", output)
                    logger.info(
                        "Switch configuration applied successfully (%d commands)",
                        len(commands),
                    )
                    return True
                except Exception as e:
                    logger.error("Switch command execution failed: %s", e)
                    return False
                finally:
                    conn.disconnect()
        except SwitchLockTimeoutError as e:
            logger.error("%s", e)
            return False

    def send_command(self, command: str) -> str | None:
        """Send a single show command and return output, or None on failure."""
        try:
            with switch_lock():
                try:
                    conn = self._connect()
                except Exception as e:
                    logger.error("SSH connection to switch failed: %s", e)
                    return None

                try:
                    output = conn.send_command(command)
                    return output
                except Exception as e:
                    logger.error("Switch command failed: %s", e)
                    return None
                finally:
                    conn.disconnect()
        except SwitchLockTimeoutError as e:
            logger.error("%s", e)
            return None

    def get_port_pvid(self, port: int) -> int | None:
        """Query the switch for the current PVID of a port.

        Returns the PVID as int, or None on failure.
        Delegates command building and output parsing to the active driver.
        """
        if not hasattr(self.driver, "get_port_pvid_command") or not hasattr(
            self.driver, "parse_port_pvid"
        ):
            logger.error(
                "Active driver %s does not support PVID queries",
                getattr(self.driver, "__name__", self.driver),
            )
            return None

        cmd = self.driver.get_port_pvid_command(port)
        output = self.send_command(cmd)
        if output is None:
            return None
        pvid = self.driver.parse_port_pvid(output)
        if pvid is None:
            logger.warning("Could not parse PVID from switch output for port %d", port)
        return pvid

    def poe_on(self, port: int) -> bool:
        """Enable PoE on a switch port."""
        return self.poe_on_multi([port])

    def poe_off(self, port: int) -> bool:
        """Disable PoE on a switch port."""
        return self.poe_off_multi([port])

    def poe_cycle(self, port: int, delay_sec: float = 3.0) -> bool:
        """Power cycle a PoE port: off, wait, on - in a single locked session."""
        return self.poe_cycle_multi([port], delay_sec)

    def poe_on_multi(self, ports: list[int]) -> bool:
        """Enable PoE on one or more switch ports in a single SSH session."""
        cmds: list[str] = []
        for port in ports:
            cmds.extend(self.driver.build_poe_commands(port, "on"))
        success = self.send_config_commands(cmds)
        if success:
            logger.info("PoE enabled on port(s) %s", ports)
        return success

    def poe_off_multi(self, ports: list[int]) -> bool:
        """Disable PoE on one or more switch ports in a single SSH session."""
        cmds: list[str] = []
        for port in ports:
            cmds.extend(self.driver.build_poe_commands(port, "off"))
        success = self.send_config_commands(cmds)
        if success:
            logger.info("PoE disabled on port(s) %s", ports)
        return success

    def poe_cycle_multi(self, ports: list[int], delay_sec: float = 3.0) -> bool:
        """Power cycle one or more PoE ports: off all, wait, on all."""
        off_cmds: list[str] = []
        on_cmds: list[str] = []
        for port in ports:
            off_cmds.extend(self.driver.build_poe_commands(port, "off"))
            on_cmds.extend(self.driver.build_poe_commands(port, "on"))

        try:
            with switch_lock():
                try:
                    conn = self._connect()
                except Exception as e:
                    logger.error("SSH connection to switch failed: %s", e)
                    return False

                try:
                    conn.send_config_set(off_cmds, cmd_verify=False)
                    logger.info("PoE off on port(s) %s, waiting %.1fs", ports, delay_sec)
                    time.sleep(delay_sec)

                    conn.send_config_set(on_cmds, cmd_verify=False)
                    logger.info("PoE cycle on port(s) %s completed successfully", ports)
                    return True
                except Exception as e:
                    logger.error("PoE cycle failed on port(s) %s: %s", ports, e)
                    return False
                finally:
                    conn.disconnect()
        except SwitchLockTimeoutError as e:
            logger.error("%s", e)
            return False
