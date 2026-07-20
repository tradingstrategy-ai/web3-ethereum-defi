"""Expose the established Midas history migration through the shared registry."""

import importlib.util
from pathlib import Path


def main() -> None:
    """Run Midas's address-scoped backfill implementation.

    The Midas adapter predates the tokenised-fund package and intentionally
    keeps its production migration under ``scripts/midas``. Loading that
    module here preserves its mature replacement-safety behaviour while
    making ``PROTOCOLS=midas`` available from the aggregate entry point.

    :return: ``None``.
    """

    script = Path(__file__).parents[3] / "scripts" / "midas" / "backfill-history.py"
    spec = importlib.util.spec_from_file_location("eth_defi_tokenised_fund_midas_backfill", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load Midas backfill script: {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.main()
