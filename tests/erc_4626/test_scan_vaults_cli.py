"""Regression tests for the vault lead scanner command script."""

# ruff: noqa: S404

import os
import subprocess
import sys
from pathlib import Path


def test_scan_vaults_rejects_removed_reset_leads_option() -> None:
    """Fail before scanner initialisation when a removed reset option is set."""

    repository_root = Path(__file__).parents[2]
    script = repository_root / "scripts" / "erc-4626" / "scan-vaults.py"
    environment = os.environ | {"RESET_LEADS": "1"}
    result = subprocess.run(  # noqa: S603 - invokes the current test interpreter and a fixed repository script.
        [sys.executable, str(script)],
        cwd=repository_root,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode != 0
    assert "RESET_LEADS has been removed" in result.stderr
