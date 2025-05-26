"""An example script to estimate the historical APY of an ERC-4626 vault.

- Archive JSON-RPC node needed, public endpoint may not work.

To run:

.. code-block:: shell

    python scripts/erc-4626/read-historical-apy.py
"""
import os
import datetime

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.profit_and_loss import estimate_4626_recent_profitability, estimate_4626_profitability
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.timestamp import estimate_block_number_for_timestamp_by_findblock


def main():

    JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")
    assert JSON_RPC_BASE, "Please set JSON_RPC_BASE environment variable to your JSON-RPC endpoint."

    web3 = create_multi_provider_web3(JSON_RPC_BASE)
    chain_id = web3.eth.chain_id

    assert chain_id == 8453, "This script is designed to run on Base chain (chain ID 8453)."

    # IPOR USDC Base
    # Lending Optimizer
    # https://app.ipor.io/fusion/base/0x45aa96f0b3188d47a1dafdbefce1db6b37f58216
    vault_address = "0x45aa96f0b3188d47a1dafdbefce1db6b37f58216"
    vault = create_vault_instance(web3, vault_address)

    start_at = datetime.datetime(2025, 3, 1, tzinfo=None)
    end_at = datetime.datetime(2025, 5, 1, tzinfo=None)

    # Use FindBlock.xyz to estimate block numbers for the given timestamps
    start_block_find = estimate_block_number_for_timestamp_by_findblock(chain_id, start_at)
    end_block_find  = estimate_block_number_for_timestamp_by_findblock(chain_id, end_at)

    profitability_data = estimate_4626_profitability(
        vault,
        start_block=start_block_find.block_number,
        end_block=end_block_find.block_number,
    )
    estimated_apy = profitability_data.calculate_profitability(annualise=True)
    start_block, end_block = profitability_data.get_block_range()
    start_at, end_at = profitability_data.get_time_range()
    start_price, end_price = profitability_data.get_share_price_range()
    diff = end_price - start_price

    print(f"Vault: {vault.name} ({vault_address})")
    print(f"Chain: {get_chain_name(chain_id)}")
    print(f"Estimated APY: {estimated_apy:.2%}")
    print(f"Period: {start_at} - {end_at} ({(end_at - start_at).days} days)")
    print(f"Block range: {start_block:,} - {end_block:,}")
    print(f"Share price at begin: {start_price} {vault.share_token.symbol} / {vault.denomination_token.symbol}")
    print(f"Share price at end: {end_price} {vault.share_token.symbol} / {vault.denomination_token.symbol}")
    print(f"Share price diff: {diff} {vault.share_token.symbol} / {vault.denomination_token.symbol}")


if __name__ == "__main__":
    main()