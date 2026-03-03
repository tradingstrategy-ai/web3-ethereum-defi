"""Read and display guard configuration for a Lagoon multichain deployment.

Decodes the cross-chain TradingStrategyModuleV0 guard configuration
by scanning on-chain events. Resolves token names, vault names, and
CCTP destinations for human-readable output.

Usage:

.. code-block:: shell

    source .local-test.env
    export CHAIN_ID=42161
    export SAFE_ADDRESS=0x62e6a0111f6DaeDf94d24197C32e469EA8eF1A8E
    poetry run python scripts/lagoon/read-guard-config.py

Environment variables:

- ``CHAIN_ID`` — EVM chain ID of the starting chain
- ``SAFE_ADDRESS`` — Safe multisig address
- ``FOLLOW_CCTP`` — set to ``false`` to skip following CCTP destinations (default: ``true``)
- ``LOG_LEVEL`` — logging level (default: ``info``)
"""

import logging
import os

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.vault_protocol.lagoon.config_event_scanner import (
    build_multichain_guard_config,
    fetch_guard_config_events,
    format_guard_config_report,
)
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging


logger = logging.getLogger(__name__)


def main():
    log_level = os.environ.get("LOG_LEVEL", "info").upper()
    setup_console_logging(log_level=getattr(logging, log_level, logging.INFO))

    chain_id = int(os.environ.get("CHAIN_ID", "0"))
    safe_address = os.environ.get("SAFE_ADDRESS", "")
    follow_cctp = os.environ.get("FOLLOW_CCTP", "true").lower() != "false"

    if not chain_id:
        raise ValueError("CHAIN_ID environment variable is required")
    if not safe_address:
        raise ValueError("SAFE_ADDRESS environment variable is required")

    logger.info(
        "Reading guard config for Safe %s on %s (chain %d), follow_cctp=%s",
        safe_address,
        get_chain_name(chain_id),
        chain_id,
        follow_cctp,
    )

    # Create web3 connection for the starting chain
    rpc_url = read_json_rpc_url(chain_id)
    web3 = create_multi_provider_web3(rpc_url)
    assert web3.eth.chain_id == chain_id, f"Chain ID mismatch: expected {chain_id}, got {web3.eth.chain_id}"

    # Scan events (follows CCTP chains automatically via env vars)
    events, module_addresses = fetch_guard_config_events(
        safe_address=safe_address,
        web3=web3,
        follow_cctp=follow_cctp,
    )

    # Build structured config
    config = build_multichain_guard_config(events, safe_address, module_addresses)

    # Build chain_web3 map for token resolution
    chain_web3_map: dict[int, object] = {chain_id: web3}
    for cid in config.chains:
        if cid not in chain_web3_map:
            try:
                chain_rpc = read_json_rpc_url(cid)
                chain_web3_map[cid] = create_multi_provider_web3(chain_rpc)
            except (ValueError, AssertionError):
                logger.warning("No RPC for chain %d, token names will not be resolved", cid)

    # Format and print the full report
    report = format_guard_config_report(
        config=config,
        events=events,
        chain_web3=chain_web3_map,
        token_cache=TokenDiskCache(),
    )
    print(report)


if __name__ == "__main__":
    main()
