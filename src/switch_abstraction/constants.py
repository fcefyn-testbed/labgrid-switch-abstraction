"""
Shared constants and helpers for the switch abstraction package.

Centralizes network constants, switch defaults, and config path resolution.
"""

import os
from pathlib import Path


# -- VLAN defaults ------------------------------------------------------------

VLAN_SHARED = 200

# -- Switch defaults ----------------------------------------------------------

DEFAULT_SWITCH_HOST = "192.168.0.1"
DEFAULT_SWITCH_USER = "admin"


# -- Path helpers -------------------------------------------------------------

def user_config_dir() -> Path:
    """Return ~/.config/ for the real user, respecting SUDO_USER when running as root."""
    if os.geteuid() == 0:
        sudo_user = os.environ.get("SUDO_USER")
        if sudo_user:
            try:
                import pwd
                home = Path(pwd.getpwnam(sudo_user).pw_dir)
                return home / ".config"
            except (ImportError, KeyError):
                pass
    return Path(os.path.expanduser("~/.config"))


def default_dut_config_path() -> Path:
    """Return path to DUT config file from env or system default."""
    return Path(
        os.environ.get("SWITCH_DUT_CONFIG", "/etc/testbed/dut-config.yaml")
    )
