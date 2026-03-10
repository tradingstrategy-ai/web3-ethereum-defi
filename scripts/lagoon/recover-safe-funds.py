"""Recover USDC from a Lagoon vault's Safe.

This script transfers USDC from the Safe to the asset manager's wallet.
The guard owner is the Safe itself, so guard admin calls (whitelist/unwhitelist)
must be routed through the Safe's ``execTransaction`` signed by the Safe owner.

Steps:

1. Whitelist asset manager as a withdraw destination — via Safe ``execTransaction``
   calling ``allowWithdrawDestination`` on the module
2. Transfer USDC from Safe to asset manager — via ``performCall`` on the module
3. Remove asset manager from withdraw destinations (cleanup) — via Safe ``execTransaction``

The asset manager must be a Safe owner with sufficient signing threshold.

Example:

.. code-block:: shell

    export JSON_RPC_ARBITRUM=$ARBITRUM_CHAIN_JSON_RPC
    GMX_PRIVATE_KEY="0x..." \\
        python scripts/lagoon/recover-safe-funds.py \\
            --vault 0x05Ec266b7b85F8a28A271041c9b40a15941Bf81F \\
            --module 0xa53e31Da109fb47a5430EdB70d1AAA855fE1E58F

    # Dry-run to check balances
    ... --dry-run

    # Recover a specific amount (default: all USDC)
    ... --amount 5.0

Environment variables
---------------------

``JSON_RPC_ARBITRUM``
    Arbitrum mainnet RPC endpoint.

``GMX_PRIVATE_KEY``
    Private key of the asset manager (must be Safe owner).
"""

import argparse
import logging
import os
import sys
from decimal import Decimal

from eth_account import Account
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.abi import get_deployed_contract
from eth_defi.confirmation import broadcast_and_wait_transactions_to_complete
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.vault import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.gas import apply_gas, estimate_gas_price
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.utils import setup_console_logging

from safe_eth.eth import EthereumClient
from safe_eth.eth.contracts import get_safe_contract
from safe_eth.safe import Safe
from safe_eth.safe.safe_tx import SafeTx


logger = logging.getLogger(__name__)


def broadcast_tx(
    web3: Web3,
    hot_wallet: HotWallet,
    bound_func,
    description: str,
    gas_limit: int = 500_000,
) -> str:
    """Sign, broadcast, and wait for a transaction.

    :param web3: Web3 instance.
    :param hot_wallet: Wallet to sign with.
    :param bound_func: Bound contract function to call.
    :param description: Human-readable description.
    :param gas_limit: Gas limit.
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
    if receipt["status"] != 1:
        print(f"    FAILED! Check tx on explorer.")
        sys.exit(1)

    gas_used = receipt["gasUsed"]
    print(f"    Gas used: {gas_used:,}")
    return tx.hash.hex()


def execute_safe_transaction(
    web3: Web3,
    hot_wallet: HotWallet,
    safe_address: HexAddress,
    to: HexAddress,
    data: bytes,
    description: str,
    value: int = 0,
    gas_limit: int = 800_000,
) -> str:
    """Execute a transaction through the Safe's execTransaction.

    The asset manager signs as the Safe owner (threshold=1) and calls
    ``execTransaction`` directly on the Safe contract.

    :param web3: Web3 instance.
    :param hot_wallet: Safe owner wallet.
    :param safe_address: Gnosis Safe address.
    :param to: Target contract address.
    :param data: Calldata for the target.
    :param description: Human-readable description.
    :param value: ETH value to send.
    :param gas_limit: Gas limit for the outer transaction.
    :return: Transaction hash hex string.
    """
    safe_contract = get_safe_contract(web3, safe_address)
    safe_nonce = safe_contract.functions.nonce().call()

    # Build SafeTx with zero gas price (we pay gas externally, not from Safe)
    ethereum_client = EthereumClient(web3.provider.endpoint_uri)
    safe_tx = SafeTx(
        ethereum_client,
        safe_address,
        to=to,
        value=value,
        data=data,
        operation=0,  # Call
        safe_tx_gas=0,
        base_gas=0,
        gas_price=0,
        gas_token=None,
        refund_receiver=None,
        safe_nonce=safe_nonce,
    )

    # Sign the Safe transaction hash with the owner's key
    safe_tx.sign(hot_wallet.private_key.hex())

    # Build the execTransaction call
    bound_func = safe_contract.functions.execTransaction(
        to,                     # to
        value,                  # value
        data,                   # data
        0,                      # operation (Call)
        0,                      # safeTxGas
        0,                      # baseGas
        0,                      # gasPrice
        "0x0000000000000000000000000000000000000000",  # gasToken
        "0x0000000000000000000000000000000000000000",  # refundReceiver
        safe_tx.signatures,     # signatures
    )

    gas_price_suggestion = estimate_gas_price(web3)
    tx_params = apply_gas({}, gas_price_suggestion)
    tx_params["gas"] = gas_limit

    tx = hot_wallet.sign_bound_call_with_new_nonce(bound_func, tx_params=tx_params)
    print(f"  Broadcasting (via Safe): {description}")
    print(f"    TX hash: {tx.hash.hex()}")

    broadcast_and_wait_transactions_to_complete(web3, [tx])

    receipt = web3.eth.get_transaction_receipt(tx.hash)
    if receipt["status"] != 1:
        print(f"    FAILED! Check tx on explorer.")
        sys.exit(1)

    gas_used = receipt["gasUsed"]
    print(f"    Gas used: {gas_used:,}")
    return tx.hash.hex()


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Recover USDC from a Lagoon vault's Safe.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  # Recover all USDC from Safe
  JSON_RPC_ARBITRUM="..." GMX_PRIVATE_KEY="0x..." \\
      python %(prog)s --vault 0x05Ec... --module 0xa53e...

  # Check balances without recovering
  ... --vault 0x05Ec... --module 0xa53e... --dry-run

  # Recover specific amount
  ... --vault 0x05Ec... --module 0xa53e... --amount 5.0
""",
    )
    parser.add_argument(
        "--vault",
        type=str,
        required=True,
        help="Lagoon vault contract address.",
    )
    parser.add_argument(
        "--module",
        type=str,
        required=True,
        help="TradingStrategyModuleV0 contract address.",
    )
    parser.add_argument(
        "--amount",
        type=float,
        default=None,
        help="USDC amount to recover. Default: all USDC in Safe.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only show balances, do not recover.",
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Skip removing asset manager from withdraw destinations after transfer.",
    )
    parser.add_argument(
        "--rpc-env",
        type=str,
        default="JSON_RPC_ARBITRUM",
        help="RPC environment variable name. Default: JSON_RPC_ARBITRUM.",
    )
    return parser.parse_args()


def main():
    """Recover funds from a Lagoon vault's Safe."""
    setup_console_logging()
    args = parse_args()

    json_rpc_url = os.environ.get(args.rpc_env)
    if not json_rpc_url:
        print(f"Error: {args.rpc_env} environment variable required.", file=sys.stderr)
        sys.exit(1)

    private_key = os.environ.get("GMX_PRIVATE_KEY")
    if not private_key:
        print("Error: GMX_PRIVATE_KEY environment variable required.", file=sys.stderr)
        sys.exit(1)

    web3 = create_multi_provider_web3(json_rpc_url)
    chain_id = web3.eth.chain_id
    print(f"Connected to chain {chain_id}, block {web3.eth.block_number:,}")

    hot_wallet = HotWallet.from_private_key(private_key)
    hot_wallet.sync_nonce(web3)

    vault_address = Web3.to_checksum_address(args.vault)
    module_address = Web3.to_checksum_address(args.module)

    # Instantiate vault
    vault = create_vault_instance(
        web3,
        vault_address,
        features={ERC4626Feature.lagoon_like},
        default_block_identifier="latest",
        require_denomination_token=True,
    )
    assert isinstance(vault, LagoonVault), f"Address {vault_address} is not a Lagoon vault"

    safe_address = vault.safe_address
    usdc = vault.underlying_token

    # Get module contract
    module_contract = get_deployed_contract(
        web3,
        "safe-integration/TradingStrategyModuleV0.json",
        module_address,
    )

    # Check ownership
    guard_owner = module_contract.functions.owner().call()
    safe_contract = get_safe_contract(web3, safe_address)
    safe_owners = safe_contract.functions.getOwners().call()
    safe_threshold = safe_contract.functions.getThreshold().call()

    print(f"\nVault:         {vault_address}")
    print(f"Safe:          {safe_address}")
    print(f"Module:        {module_address}")
    print(f"Guard owner:   {guard_owner}")
    print(f"Safe owners:   {safe_owners}")
    print(f"Safe threshold: {safe_threshold}")
    print(f"Asset manager: {hot_wallet.address}")

    # Verify asset manager is a Safe owner
    am_checksummed = Web3.to_checksum_address(hot_wallet.address)
    safe_owners_checksummed = [Web3.to_checksum_address(o) for o in safe_owners]
    if am_checksummed not in safe_owners_checksummed:
        print(f"\nError: asset manager ({hot_wallet.address}) is not a Safe owner.", file=sys.stderr)
        sys.exit(1)

    if safe_threshold > 1:
        print(f"\nError: Safe threshold is {safe_threshold}, need threshold=1 for single-signer execution.", file=sys.stderr)
        sys.exit(1)

    # Check balances
    safe_usdc_balance = usdc.fetch_balance_of(safe_address)
    safe_eth_balance = web3.eth.get_balance(safe_address)
    am_usdc_balance = usdc.fetch_balance_of(hot_wallet.address)
    am_eth_balance = web3.eth.get_balance(hot_wallet.address)

    print(f"\nSafe balances:")
    print(f"  USDC: {safe_usdc_balance}")
    print(f"  ETH:  {web3.from_wei(safe_eth_balance, 'ether')}")
    print(f"\nAsset manager balances:")
    print(f"  USDC: {am_usdc_balance}")
    print(f"  ETH:  {web3.from_wei(am_eth_balance, 'ether')}")

    has_usdc = safe_usdc_balance > 0
    has_eth = safe_eth_balance > 0

    if not has_usdc and not has_eth:
        print("\nNo USDC or ETH in Safe. Nothing to recover.")
        sys.exit(0)

    # Determine USDC amount to recover
    if has_usdc:
        if args.amount is not None:
            recover_amount = Decimal(str(args.amount))
            if recover_amount > safe_usdc_balance:
                print(f"\nError: requested {recover_amount} but Safe only has {safe_usdc_balance} USDC.", file=sys.stderr)
                sys.exit(1)
        else:
            recover_amount = safe_usdc_balance
        raw_recover = usdc.convert_to_raw(recover_amount)

    recover_eth = has_eth
    recover_eth_wei = safe_eth_balance
    recover_eth_display = web3.from_wei(safe_eth_balance, "ether")

    print(f"\nWill recover:")
    if has_usdc:
        print(f"  USDC: {recover_amount}")
    if recover_eth:
        print(f"  ETH:  {recover_eth_display}")

    if args.dry_run:
        print("\n--dry-run: skipping recovery.")
        sys.exit(0)

    # Confirm
    parts = []
    if has_usdc:
        parts.append(f"{recover_amount} USDC")
    if recover_eth:
        parts.append(f"{recover_eth_display} ETH")
    response = input(f"\nRecover {' + '.join(parts)} from Safe to {hot_wallet.address}? [y/N] ").strip().lower()
    if response != "y":
        print("Aborted.")
        sys.exit(0)

    print("\n" + "=" * 60)
    print("RECOVERING FUNDS")
    print("=" * 60)

    step = 0

    # Step: Whitelist asset manager as withdraw destination (only needed for USDC)
    already_whitelisted = False
    if has_usdc:
        already_whitelisted = module_contract.functions.allowedWithdrawDestinations(hot_wallet.address).call()

        # Guard owner = Safe, so we route through Safe's execTransaction
        if not already_whitelisted:
            step += 1
            print(f"\nStep {step}: Whitelist asset manager as withdraw destination (via Safe)")

            whitelist_calldata = module_contract.encode_abi(
                "allowWithdrawDestination",
                args=[hot_wallet.address, "Temporary: recover Safe funds"],
            )

            execute_safe_transaction(
                web3=web3,
                hot_wallet=hot_wallet,
                safe_address=safe_address,
                to=module_address,
                data=whitelist_calldata,
                description=f"allowWithdrawDestination({hot_wallet.address})",
            )

            # Verify it worked
            is_whitelisted = module_contract.functions.allowedWithdrawDestinations(hot_wallet.address).call()
            assert is_whitelisted, "Failed to whitelist asset manager"
            print("    Verified: asset manager is now whitelisted")
        else:
            step += 1
            print(f"\nStep {step}: Asset manager already whitelisted (skipping)")

    # Step: Transfer USDC from Safe via performCall
    if has_usdc:
        step += 1
        print(f"\nStep {step}: Transfer {recover_amount} USDC from Safe to asset manager (via performCall)")

        transfer_calldata = usdc.contract.encode_abi("transfer", args=[hot_wallet.address, raw_recover])

        broadcast_tx(
            web3,
            hot_wallet,
            module_contract.functions.performCall(
                usdc.address,
                transfer_calldata,
                0,
            ),
            f"performCall: transfer({hot_wallet.address}, {recover_amount} USDC)",
            gas_limit=800_000,
        )

    # Step: Transfer ETH from Safe via execTransaction (native value transfer)
    if recover_eth:
        step += 1
        print(f"\nStep {step}: Transfer {recover_eth_display} ETH from Safe to asset manager (via Safe execTransaction)")

        execute_safe_transaction(
            web3=web3,
            hot_wallet=hot_wallet,
            safe_address=safe_address,
            to=hot_wallet.address,
            data=b"",
            value=recover_eth_wei,
            description=f"Send {recover_eth_display} ETH to {hot_wallet.address}",
        )

    # Step: Remove whitelist (cleanup)
    if has_usdc and not args.no_cleanup and not already_whitelisted:
        step += 1
        print(f"\nStep {step}: Remove asset manager from withdraw destinations (via Safe)")

        remove_calldata = module_contract.encode_abi(
            "removeWithdrawDestination",
            args=[hot_wallet.address, "Cleanup: recover Safe funds complete"],
        )

        execute_safe_transaction(
            web3=web3,
            hot_wallet=hot_wallet,
            safe_address=safe_address,
            to=module_address,
            data=remove_calldata,
            description=f"removeWithdrawDestination({hot_wallet.address})",
        )

    # Final balances
    final_safe_usdc = usdc.fetch_balance_of(safe_address)
    final_safe_eth = web3.eth.get_balance(safe_address)
    final_am_usdc = usdc.fetch_balance_of(hot_wallet.address)
    final_am_eth = web3.eth.get_balance(hot_wallet.address)

    print("\n" + "=" * 60)
    print("RECOVERY COMPLETE")
    print("=" * 60)
    if has_usdc:
        print(f"\n  USDC recovered:      {recover_amount}")
    if recover_eth:
        print(f"  ETH recovered:       {recover_eth_display}")
    print(f"\n  Safe USDC:           {final_safe_usdc}")
    print(f"  Safe ETH:            {web3.from_wei(final_safe_eth, 'ether')}")
    print(f"  Asset manager USDC:  {final_am_usdc}")
    print(f"  Asset manager ETH:   {web3.from_wei(final_am_eth, 'ether')}")


if __name__ == "__main__":
    main()
