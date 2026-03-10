"""Withdraw all funds from a Lagoon vault.

Performs the full ERC-7540 async redemption flow:

1. **Request redemption** — approve shares and call ``requestRedeem()``
2. **Settle** — post new valuation and settle deposits/redemptions
3. **Finalise** — claim USDC from the vault silo to the redeemer's wallet

The script uses the asset manager's hot wallet to sign all transactions.
The redeemer (share holder) defaults to the asset manager address but can
be overridden with ``--redeemer``.

Example:

.. code-block:: shell

    # Withdraw from vault (mainnet)
    JSON_RPC_ARBITRUM="https://arb1.arbitrum.io/rpc" \\
        GMX_PRIVATE_KEY="0x..." \\
        python scripts/lagoon/withdraw-lagoon-vault.py \\
            --vault 0x05Ec266b7b85F8a28A271041c9b40a15941Bf81F

    # Withdraw with a different redeemer address
    JSON_RPC_ARBITRUM="https://arb1.arbitrum.io/rpc" \\
        GMX_PRIVATE_KEY="0x..." \\
        python scripts/lagoon/withdraw-lagoon-vault.py \\
            --vault 0x05Ec... --redeemer 0xABC...

    # Dry-run to check balances without withdrawing
    JSON_RPC_ARBITRUM="https://arb1.arbitrum.io/rpc" \\
        GMX_PRIVATE_KEY="0x..." \\
        python scripts/lagoon/withdraw-lagoon-vault.py \\
            --vault 0x05Ec... --dry-run

Environment variables
---------------------

``JSON_RPC_ARBITRUM``
    Arbitrum mainnet RPC endpoint.

``GMX_PRIVATE_KEY``
    Private key of the asset manager wallet.
"""

import argparse
import logging
import os
import sys
from decimal import Decimal

from eth_typing import HexAddress
from web3 import Web3

from eth_defi.confirmation import broadcast_and_wait_transactions_to_complete
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.vault_protocol.lagoon.testing import redeem_vault_shares
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.erc_4626.vault import ERC4626Feature
from eth_defi.gas import apply_gas, estimate_gas_price
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.utils import setup_console_logging


logger = logging.getLogger(__name__)


def broadcast_tx(
    web3: Web3,
    hot_wallet: HotWallet,
    bound_func,
    description: str,
    gas_limit: int = 1_000_000,
) -> str:
    """Sign, broadcast, and wait for a transaction.

    :param web3: Web3 instance.
    :param hot_wallet: Wallet to sign with.
    :param bound_func: Bound contract function to call.
    :param description: Human-readable description for logging.
    :param gas_limit: Gas limit for the transaction.
    :return: Transaction hash hex string.
    """
    gas_price_suggestion = estimate_gas_price(web3)
    tx_params = apply_gas({}, gas_price_suggestion)
    tx_params["gas"] = gas_limit

    tx = hot_wallet.sign_bound_call_with_new_nonce(bound_func, tx_params=tx_params)
    print(f"  Broadcasting: {description}")
    print(f"    TX hash: {tx.hash.hex()}")

    broadcast_and_wait_transactions_to_complete(web3, [tx])

    receipt = web3.eth.get_transaction_receipt(tx.hash)
    gas_used = receipt["gasUsed"]
    print(f"    Gas used: {gas_used:,}")

    return tx.hash.hex()


def show_vault_info(
    web3: Web3,
    vault: LagoonVault,
    redeemer: HexAddress,
) -> tuple[Decimal, Decimal]:
    """Display vault and balance information.

    :param web3: Web3 instance.
    :param vault: LagoonVault instance.
    :param redeemer: Address of the share holder.
    :return: Tuple of (share_balance, safe_usdc_balance).
    """
    usdc = vault.underlying_token
    share_token = vault.share_token

    shares = share_token.fetch_balance_of(redeemer)
    safe_usdc = usdc.fetch_balance_of(vault.safe_address)

    print(f"\nVault information:")
    print(f"  Vault address:   {vault.address}")
    print(f"  Safe address:    {vault.safe_address}")
    print(f"  Module address:  {vault.trading_strategy_module_address}")
    print(f"  Underlying:      {usdc.symbol} ({usdc.address})")

    print(f"\nBalances:")
    print(f"  Safe {usdc.symbol} balance:     {safe_usdc}")
    print(f"  Redeemer shares:          {shares} {share_token.symbol}")
    print(f"  Redeemer address:         {redeemer}")

    return shares, safe_usdc


def withdraw_all(
    web3: Web3,
    hot_wallet: HotWallet,
    vault_address: HexAddress,
    redeemer: HexAddress,
) -> Decimal:
    """Execute the full 3-phase ERC-7540 withdrawal.

    :param web3: Web3 instance.
    :param hot_wallet: Asset manager wallet for signing.
    :param vault_address: Lagoon vault contract address.
    :param redeemer: Address holding shares to redeem.
    :return: Final USDC balance of the redeemer.
    """
    # Phase 1: Request redemption
    print("\n" + "-" * 60)
    print("PHASE 1: Request redemption")
    print("-" * 60)

    vault = redeem_vault_shares(
        web3,
        vault_address=vault_address,
        redeemer=redeemer,
        hot_wallet=hot_wallet,
    )
    print("  Redemption requested.")

    # Phase 2: Settle the vault
    print("\n" + "-" * 60)
    print("PHASE 2: Settle vault")
    print("-" * 60)

    hot_wallet.sync_nonce(web3)

    usdc = vault.underlying_token
    safe_usdc_balance = usdc.fetch_balance_of(vault.safe_address)
    print(f"  Safe {usdc.symbol} balance: {safe_usdc_balance}")

    broadcast_tx(
        web3,
        hot_wallet,
        vault.post_new_valuation(safe_usdc_balance),
        "Post vault valuation",
    )

    broadcast_tx(
        web3,
        hot_wallet,
        vault.settle_via_trading_strategy_module(safe_usdc_balance),
        "Settle deposits/redemptions",
    )

    # Phase 3: Finalise redemption
    print("\n" + "-" * 60)
    print("PHASE 3: Finalise redemption")
    print("-" * 60)

    broadcast_tx(
        web3,
        hot_wallet,
        vault.finalise_redeem(redeemer),
        f"Claim {usdc.symbol} for {redeemer}",
    )

    # Check final balances
    final_usdc = usdc.fetch_balance_of(redeemer)
    remaining_shares = vault.share_token.fetch_balance_of(redeemer)

    print(f"\n  Redeemer {usdc.symbol} balance: {final_usdc}")
    print(f"  Remaining shares: {remaining_shares}")

    return final_usdc


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Withdraw all funds from a Lagoon vault.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  # Withdraw from vault
  JSON_RPC_ARBITRUM="..." GMX_PRIVATE_KEY="0x..." \\
      python %(prog)s --vault 0x05Ec...

  # Check balances without withdrawing
  JSON_RPC_ARBITRUM="..." GMX_PRIVATE_KEY="0x..." \\
      python %(prog)s --vault 0x05Ec... --dry-run
""",
    )
    parser.add_argument(
        "--vault",
        type=str,
        required=True,
        help="Lagoon vault contract address (not the Safe address).",
    )
    parser.add_argument(
        "--redeemer",
        type=str,
        default=None,
        help="Address of the share holder to redeem for. Defaults to asset manager address.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only show balances, do not withdraw.",
    )
    parser.add_argument(
        "--rpc-env",
        type=str,
        default="JSON_RPC_ARBITRUM",
        help="Name of the RPC environment variable. Default: JSON_RPC_ARBITRUM.",
    )
    return parser.parse_args()


def main():
    """Withdraw from a Lagoon vault."""
    setup_console_logging()

    args = parse_args()

    # Load RPC URL
    json_rpc_url = os.environ.get(args.rpc_env)
    if not json_rpc_url:
        print(f"Error: {args.rpc_env} environment variable required.", file=sys.stderr)
        sys.exit(1)

    # Load private key
    private_key = os.environ.get("GMX_PRIVATE_KEY")
    if not private_key:
        print("Error: GMX_PRIVATE_KEY environment variable required.", file=sys.stderr)
        sys.exit(1)

    # Connect
    web3 = create_multi_provider_web3(json_rpc_url)
    chain_id = web3.eth.chain_id
    print(f"Connected to chain {chain_id}, block {web3.eth.block_number:,}")

    # Setup wallet
    hot_wallet = HotWallet.from_private_key(private_key)
    hot_wallet.sync_nonce(web3)

    vault_address = Web3.to_checksum_address(args.vault)
    redeemer = Web3.to_checksum_address(args.redeemer) if args.redeemer else hot_wallet.address

    print(f"\nAsset manager: {hot_wallet.address}")

    # Instantiate vault
    vault = create_vault_instance(
        web3,
        vault_address,
        features={ERC4626Feature.lagoon_like},
        default_block_identifier="latest",
        require_denomination_token=True,
    )
    assert isinstance(vault, LagoonVault), f"Address {vault_address} is not a Lagoon vault"

    # Show info
    shares, safe_usdc = show_vault_info(web3, vault, redeemer)

    if shares == 0:
        print("\nNo shares to redeem. Nothing to withdraw.")
        sys.exit(0)

    if args.dry_run:
        print("\n--dry-run: skipping withdrawal.")
        sys.exit(0)

    # Confirm
    print(f"\nAbout to withdraw {shares} shares from vault {vault_address}")
    print(f"Redeemer: {redeemer}")
    response = input("Proceed? [y/N] ").strip().lower()
    if response != "y":
        print("Aborted.")
        sys.exit(0)

    # Execute withdrawal
    print("\n" + "=" * 60)
    print("WITHDRAWING FROM VAULT")
    print("=" * 60)

    final_usdc = withdraw_all(web3, hot_wallet, vault_address, redeemer)

    print("\n" + "=" * 60)
    print("WITHDRAWAL COMPLETE")
    print("=" * 60)
    print(f"\n  Final {vault.underlying_token.symbol} balance: {final_usdc}")


if __name__ == "__main__":
    main()
