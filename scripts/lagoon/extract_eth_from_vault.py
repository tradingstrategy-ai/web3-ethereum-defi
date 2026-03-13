"""Extract ETH from the Lagoon vault Safe to the asset manager address.

This script transfers ETH from the Lagoon vault's underlying Gnosis Safe to a
recipient address (defaulting to the asset manager itself).  It uses the
``TradingStrategyModuleV0.performCall(to, data, value)`` path so that no
additional Safe signing ceremony is required — the asset manager hot-wallet
signs a single transaction.

Required environment variables
-------------------------------

- ``JSON_RPC_ARBITRUM`` — Arbitrum RPC URL (space-separated multi-provider format)
- ``GMX_PRIVATE_KEY``   — Asset-manager private key (hex, e.g. ``0x...``)
- ``LAGOON_VAULT_ADDRESS`` — Lagoon ERC-4626 vault address

Optional environment variables
--------------------------------

- ``RECIPIENT_ADDRESS``  — Address to receive the ETH.  Defaults to the asset
  manager's own address (derived from ``GMX_PRIVATE_KEY``).
- ``ETH_AMOUNT_WEI``    — Amount of ETH to send, in wei.  Defaults to the full
  Safe ETH balance minus a 0.001 ETH reserve for gas.

Usage
-----

.. code-block:: shell

    export JSON_RPC_ARBITRUM=$ARBITRUM_CHAIN_JSON_RPC
    export GMX_PRIVATE_KEY=0x...
    export LAGOON_VAULT_ADDRESS=0xE3D5595707b2b75B3F25fBCc9A212A547d6E29ca

    # Withdraw all available ETH to asset manager (default)
    cd deps/web3-ethereum-defi
    poetry run python scripts/lagoon/extract_eth_from_vault.py

    # Withdraw a fixed amount to a custom recipient
    export RECIPIENT_ADDRESS=0x9Eecd13C4E0aeF29B321c49575601B9d33974aDB
    export ETH_AMOUNT_WEI=500000000000000000   # 0.5 ETH
    poetry run python scripts/lagoon/extract_eth_from_vault.py
"""

import logging
import os
import sys

from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.gmx.lagoon.wallet import LagoonGMXTradingWallet
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec

logger = logging.getLogger(__name__)
console = Console()

#: Minimum ETH reserve kept in the Safe so it can still pay keeper fees later.
#: Only applied when ``ETH_AMOUNT_WEI`` is not set (i.e. the default full-balance mode).
SAFE_ETH_RESERVE_WEI = int(0.001 * 10**18)


def _display_balances(
    web3,
    safe_address: str,
    asset_manager_address: str,
    recipient_address: str,
) -> None:
    """Print a rich table showing relevant ETH balances.

    :param web3:
        Connected Web3 instance.

    :param safe_address:
        Gnosis Safe address that holds the funds.

    :param asset_manager_address:
        Hot-wallet address that signs transactions.

    :param recipient_address:
        Address that will receive the ETH.
    """
    safe_eth = web3.eth.get_balance(safe_address)
    am_eth = web3.eth.get_balance(asset_manager_address)
    recipient_eth = web3.eth.get_balance(recipient_address) if recipient_address != asset_manager_address else am_eth

    table = Table(title="ETH Balances")
    table.add_column("Account", style="cyan")
    table.add_column("Address", style="white")
    table.add_column("ETH Balance", style="green")

    table.add_row("Safe (source)", safe_address, f"{web3.from_wei(safe_eth, 'ether'):.6f}")
    table.add_row("Asset manager (signer)", asset_manager_address, f"{web3.from_wei(am_eth, 'ether'):.6f}")

    if recipient_address != asset_manager_address:
        table.add_row("Recipient", recipient_address, f"{web3.from_wei(recipient_eth, 'ether'):.6f}")

    console.print(table)


def main() -> None:
    """Main entry point for the ETH extraction script."""
    FORMAT = "%(message)s"
    logging.basicConfig(level="INFO", format=FORMAT, datefmt="[%X]", handlers=[RichHandler()])
    logging.getLogger("eth_defi").setLevel(logging.INFO)

    # --- Read configuration from environment ---
    rpc_url = os.environ.get("JSON_RPC_ARBITRUM")
    if not rpc_url:
        console.print("[red]JSON_RPC_ARBITRUM environment variable is not set.[/red]")
        sys.exit(1)

    private_key = os.environ.get("GMX_PRIVATE_KEY")
    if not private_key:
        console.print("[red]GMX_PRIVATE_KEY environment variable is not set.[/red]")
        sys.exit(1)

    vault_address_raw = os.environ.get("LAGOON_VAULT_ADDRESS")
    if not vault_address_raw:
        console.print("[red]LAGOON_VAULT_ADDRESS environment variable is not set.[/red]")
        sys.exit(1)

    recipient_address_raw = os.environ.get("RECIPIENT_ADDRESS")
    eth_amount_wei_raw = os.environ.get("ETH_AMOUNT_WEI")

    console.print("\n[bold]Lagoon Safe — Extract ETH[/bold]\n")
    console.print(f"Vault address  : {vault_address_raw}")

    # --- Connect to Arbitrum ---
    console.print("\nConnecting to Arbitrum...")
    web3 = create_multi_provider_web3(rpc_url)

    try:
        block_number = web3.eth.block_number
        chain_id = web3.eth.chain_id
        chain_name = get_chain_name(chain_id).lower()
        console.print(f"Connected — chain: {chain_name}, chain_id: {chain_id}, block: {block_number}")
    except Exception as e:
        console.print(f"[red]Failed to connect to RPC: {e}[/red]")
        sys.exit(1)

    # --- Initialise asset manager hot wallet ---
    hot_wallet = HotWallet.from_private_key(private_key)
    hot_wallet.sync_nonce(web3)

    am_address = hot_wallet.address
    am_eth_balance = web3.eth.get_balance(am_address)
    console.print(f"Asset manager  : {am_address}")
    console.print(f"  ETH balance  : {web3.from_wei(am_eth_balance, 'ether'):.6f}")

    if am_eth_balance < web3.to_wei(0.001, "ether"):
        console.print("[yellow]Warning: low ETH on asset manager — may not cover gas fees![/yellow]")

    # --- Resolve recipient ---
    if recipient_address_raw:
        recipient_address = web3.to_checksum_address(recipient_address_raw)
        console.print(f"Recipient      : {recipient_address} (from RECIPIENT_ADDRESS)")
    else:
        recipient_address = am_address
        console.print(f"Recipient      : {recipient_address} (asset manager default)")

    # --- Initialise Lagoon vault ---
    console.print("\nInitialising Lagoon vault...")
    vault_spec = VaultSpec(chain_id, vault_address_raw)
    vault = LagoonVault(web3, vault_spec)

    try:
        vault_info = vault.fetch_info()
    except Exception as e:
        console.print(f"[red]vault.fetch_info() failed: {e}[/red]")
        sys.exit(1)

    modules = vault_info.get("modules", [])
    if not modules:
        console.print("[red]No TradingStrategyModuleV0 found on the Safe. Cannot proceed.[/red]")
        sys.exit(1)

    vault.trading_strategy_module_address = modules[0]
    safe_address = vault.safe_address

    console.print(f"Safe address   : {safe_address}")
    console.print(f"Module address : {modules[0]}")

    # --- Check Safe ETH balance ---
    safe_eth_balance = web3.eth.get_balance(safe_address)
    console.print(f"\nSafe ETH balance: {web3.from_wei(safe_eth_balance, 'ether'):.6f} ETH ({safe_eth_balance} wei)")

    if safe_eth_balance == 0:
        console.print("[yellow]Safe has no ETH. Nothing to extract.[/yellow]")
        sys.exit(0)

    # --- Determine withdrawal amount ---
    if eth_amount_wei_raw:
        eth_amount_wei = int(eth_amount_wei_raw)
        console.print(f"Withdrawal amount: {web3.from_wei(eth_amount_wei, 'ether'):.6f} ETH (from ETH_AMOUNT_WEI)")
    else:
        eth_amount_wei = max(0, safe_eth_balance - SAFE_ETH_RESERVE_WEI)
        console.print(f"Withdrawal amount: {web3.from_wei(eth_amount_wei, 'ether'):.6f} ETH (full balance minus {web3.from_wei(SAFE_ETH_RESERVE_WEI, 'ether'):.3f} ETH reserve)")

    if eth_amount_wei <= 0:
        console.print("[yellow]Nothing to withdraw after reserve. Set ETH_AMOUNT_WEI to override.[/yellow]")
        sys.exit(0)

    if eth_amount_wei > safe_eth_balance:
        console.print(f"[red]Requested {web3.from_wei(eth_amount_wei, 'ether'):.6f} ETH but Safe only holds {web3.from_wei(safe_eth_balance, 'ether'):.6f} ETH.[/red]")
        sys.exit(1)

    # --- Show balance table ---
    console.print()
    _display_balances(web3, safe_address, am_address, recipient_address)

    # --- Confirmation prompt ---
    console.print(f"\n[yellow]About to send [bold]{web3.from_wei(eth_amount_wei, 'ether'):.6f} ETH[/bold] from Safe [cyan]{safe_address}[/cyan] to [cyan]{recipient_address}[/cyan][/yellow]")
    console.print("[yellow]Press Enter to continue or Ctrl+C to abort...[/yellow]")

    try:
        input()
    except KeyboardInterrupt:
        console.print("\n[yellow]Aborted by user.[/yellow]")
        sys.exit(0)

    # --- Build the LagoonGMXTradingWallet ---
    # forward_eth=False because we want the Safe's own ETH to be sent out,
    # not ETH forwarded from the asset manager into the Safe.
    lagoon_wallet = LagoonGMXTradingWallet(
        vault=vault,
        asset_manager=hot_wallet,
        gas_buffer=500_000,
        forward_eth=False,
    )

    # --- Build and sign the performCall transaction ---
    # performCall(to, data, value) — data is empty for a plain ETH transfer.
    # We call performCall directly via the hot wallet (asset manager) rather than
    # routing through LagoonGMXTradingWallet.sign_transaction_with_new_nonce, which
    # would double-wrap the call.
    console.print(f"\n[blue]Building performCall(to={recipient_address}, data=0x, value={eth_amount_wei})[/blue]")

    signed_tx = hot_wallet.sign_bound_call_with_new_nonce(
        vault.trading_strategy_module.functions.performCall(
            recipient_address,
            b"",
            eth_amount_wei,
        ),
        tx_params={"gas": 500_000},
        web3=web3,
        fill_gas_price=True,
    )

    tx_hash = web3.eth.send_raw_transaction(signed_tx.raw_transaction)
    console.print(f"Transaction sent: [yellow]{tx_hash.hex()}[/yellow]")
    console.print("Waiting for confirmation...")

    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)

    if receipt["status"] != 1:
        console.print(f"[red]Transaction reverted! Block: {receipt['blockNumber']}[/red]")
        console.print(f"[red]TX hash: {tx_hash.hex()}[/red]")
        sys.exit(1)

    console.print(f"[green]Transaction confirmed in block {receipt['blockNumber']}[/green]")
    console.print(f"  Gas used: {receipt['gasUsed']:,}")

    # --- Final balances ---
    safe_eth_after = web3.eth.get_balance(safe_address)
    recipient_eth_after = web3.eth.get_balance(recipient_address)
    am_eth_after = web3.eth.get_balance(am_address)

    console.print("\n[bold]Final balances[/bold]")
    final_table = Table(title="Post-Transfer ETH Balances")
    final_table.add_column("Account", style="cyan")
    final_table.add_column("Address", style="white")
    final_table.add_column("ETH Balance", style="green")

    final_table.add_row("Safe", safe_address, f"{web3.from_wei(safe_eth_after, 'ether'):.6f}")
    final_table.add_row("Asset manager", am_address, f"{web3.from_wei(am_eth_after, 'ether'):.6f}")

    if recipient_address != am_address:
        final_table.add_row("Recipient", recipient_address, f"{web3.from_wei(recipient_eth_after, 'ether'):.6f}")

    console.print(final_table)

    console.print(f"\n[green]Done. Transferred {web3.from_wei(eth_amount_wei, 'ether'):.6f} ETH from Safe to {recipient_address}.[/green]")


if __name__ == "__main__":
    main()
