"""Run guarded, Anvil-only vault deposit probes configured through environment variables."""

from eth_defi.erc_4626.deposit_probe import run_from_environment
from eth_defi.utils import setup_console_logging

if __name__ == "__main__":
    setup_console_logging(default_log_level="info")
    raise SystemExit(run_from_environment())
