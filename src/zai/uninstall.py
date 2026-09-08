"""Uninstall hook for zai-agent.

Usage:
    zai-uninstall
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def get_zai_home() -> Path:
    """Get the Zai home directory."""
    if home := os.getenv("ZAI_HOME"):
        return Path(home).expanduser().resolve()
    return Path.home() / ".zai"


def run_uninstall() -> int:
    """Run uninstallation: remove package and user data."""
    zai_home = get_zai_home()

    print("Uninstalling zai-agent...")
    print()

    # Ask for confirmation
    try:
        response = input(f"Delete user data {zai_home}? [y/N]: ").strip().lower()
        if response not in ("y", "yes"):
            print("Cancelled")
            return 0
    except EOFError:
        # Non-interactive mode, assume yes
        pass

    # Uninstall the pip package
    print("Uninstalling pip package...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "uninstall", "zai-agent", "-y"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print("[OK] pip package removed")
    else:
        print("[WARN] pip returned non-zero (may already be uninstalled)")

    # Remove user data directory
    if zai_home.exists():
        print(f"Deleting user data: {zai_home}")
        try:
            shutil.rmtree(zai_home)
            print("[OK] user data removed")
        except Exception as e:
            print(f"[WARN] Failed to delete user data: {e}")
    else:
        print("[OK] user data directory does not exist")

    print()
    print("Uninstall complete!")
    return 0


if __name__ == "__main__":
    sys.exit(run_uninstall())
