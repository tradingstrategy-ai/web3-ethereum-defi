"""Claim accrued GMX V2 funding fees for an EOA or a Lagoon vault Safe.

GMX V2 accrues funding fees continuously per ``(market, token, account)`` and
releases them through ``ExchangeRouter.claimFundingFees(markets, tokens,
receiver)``. This script reads the per-account claimable funding receipts,
prints the nonzero ``(market, side)`` pairs, submits one ``multicall`` claim and
waits for the receipt.

The same script serves both execution modes:

- **EOA mode** (no ``--vault``): the hot wallet signs and broadcasts the
  ``claimFundingFees`` call directly.
- **Vault mode** (``--vault``): the vault's ``TradingStrategyModuleV0``
  ``performCall`` wrapper is used, so the Gnosis Safe is the ``msg.sender`` and
  the default receiver.

Usage
-----

EOA claim::

    python scripts/gmx/gmx_claim_funding_fees.py \\
        --rpc-url "$JSON_RPC_ARBITRUM" --private-key 0x...

Lagoon vault claim (the asset manager's key signs for the Safe)::

    python scripts/gmx/gmx_claim_funding_fees.py \\
        --rpc-url "$JSON_RPC_ARBITRUM" --private-key 0x... --vault 0x...

Show the claimable funding without submitting a transaction::

    python scripts/gmx/gmx_claim_funding_fees.py \\
        --rpc-url "$JSON_RPC_ARBITRUM" --private-key 0x... --vault 0x... --dry-run

Run it through Poetry so the repository's ``eth_defi`` is imported::

    poetry run python scripts/gmx/gmx_claim_funding_fees.py --rpc-url ... --private-key ...
"""

import argparse
import logging
import sys

from tabulate import tabulate

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.gmx.claim import claim_all_funding_fees
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.core.claimable_funding_fees import GetClaimableFundingFees
from eth_defi.gmx.lagoon.wallet import LagoonGMXTradingWallet
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec

logger = logging.getLogger(__name__)

#: Sentinel module address used by Gnosis Safe for ``getModulesPaginated``.
SENTINEL_MODULES = "0x0000000000000000000000000000000000000001"


def parse_args() -> argparse.Namespace:
    """Parse command line arguments.

    :return: Parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description="Claim accrued GMX V2 funding fees for an EOA or a Lagoon vault Safe.",
    )
    parser.add_argument("--rpc-url", required=True, help="JSON-RPC endpoint for the GMX chain (Arbitrum or Avalanche)")
    parser.add_argument("--private-key", required=True, help="Signing key: the EOA, or the Lagoon asset manager in vault mode")
    parser.add_argument("--vault", help="Lagoon vault address; when set, the claim is routed through the vault Safe")
    parser.add_argument("--dry-run", action="store_true", help="Only print the claimable funding fees; do not submit a claim")
    return parser.parse_args()


def resolve_module_address(web3, vault, vault_address: str) -> str:
    """Resolve the ``TradingStrategyModuleV0`` address enabled on a vault's Safe.

    :param web3: Connected Web3 instance.
    :param vault: Lagoon vault whose Safe is inspected.
    :param vault_address: Vault address, used only for error messages.
    :return: Checksummed module address.
    :raises SystemExit: If the Safe has no enabled module.
    """
    module = vault.trading_strategy_module_address
    if module:
        return web3.to_checksum_address(module)

    # LagoonVault does not always resolve the module, so read the Safe's
    # enabled-module list directly. SENTINEL_MODULES == address(0x1).
    safe_abi = [
        {
            "inputs": [{"name": "start", "type": "address"}, {"name": "pageSize", "type": "uint256"}],
            "name": "getModulesPaginated",
            "outputs": [{"name": "array", "type": "address[]"}, {"name": "next", "type": "address"}],
            "stateMutability": "view",
            "type": "function",
        }
    ]
    safe = web3.eth.contract(address=vault.safe_address, abi=safe_abi)
    modules, _ = safe.functions.getModulesPaginated(SENTINEL_MODULES, 10).call()
    if not modules:
        sys.exit(f"Lagoon vault {vault_address} Safe {vault.safe_address} has no enabled Zodiac modules; deploy TradingStrategyModuleV0 first")
    return web3.to_checksum_address(modules[0])


def build_wallet(web3, private_key: str, vault_address: str | None) -> tuple[object, str]:
    """Build the signing wallet and resolve the funding account.

    :param web3: Connected Web3 instance.
    :param private_key: EOA (or asset-manager) signing key.
    :param vault_address: Lagoon vault address, or ``None`` for EOA mode.
    :return: Tuple of ``(wallet, funding_account)``.
    """
    hot_wallet = HotWallet.from_private_key(private_key)
    hot_wallet.sync_nonce(web3)

    if not vault_address:
        logger.info("EOA mode: signing account %s", hot_wallet.address)
        return hot_wallet, hot_wallet.address

    vault = LagoonVault(web3, VaultSpec(web3.eth.chain_id, vault_address))
    module_address = resolve_module_address(web3, vault, vault_address)
    vault.trading_strategy_module_address = module_address
    wallet = LagoonGMXTradingWallet(vault=vault, asset_manager=hot_wallet)
    logger.info("Lagoon vault mode: safe=%s, module=%s", wallet.address, module_address)
    return wallet, wallet.address


def collect_claim_rows(reader: GetClaimableFundingFees) -> list[dict]:
    """Collect the nonzero claimable funding pairs for the display table.

    :param reader: Configured :class:`~eth_defi.gmx.core.claimable_funding_fees.GetClaimableFundingFees`.
    :return: List of table rows, one per nonzero ``(market, side)`` pair.
    """
    rows: list[dict] = []
    for market_symbol, fee_data in reader.get_per_market_claimable_funding_fees().items():
        if fee_data["long_raw"] > 0:
            rows.append({"Market": market_symbol, "Side": "LONG", "Raw": f"{fee_data['long_raw']:,}", "USD": f"${fee_data['long']:,.2f}"})
        if fee_data["short_raw"] > 0:
            rows.append({"Market": market_symbol, "Side": "SHORT", "Raw": f"{fee_data['short_raw']:,}", "USD": f"${fee_data['short']:,.2f}"})
    return rows


def main() -> int:
    """Claim GMX V2 funding fees for the configured account.

    :return: Process exit code.
    """
    setup_console_logging()
    args = parse_args()

    web3 = create_multi_provider_web3(args.rpc_url)
    chain = get_chain_name(web3.eth.chain_id).lower()
    logger.info("Connected to %s (chain id %s), block %s", chain, web3.eth.chain_id, web3.eth.block_number)

    wallet, account = build_wallet(web3, args.private_key, args.vault)
    logger.info("Funding account: %s", account)

    config = GMXConfig(web3, user_wallet_address=account)
    reader = GetClaimableFundingFees(config, account)

    rows = collect_claim_rows(reader)
    if not rows:
        print(f"No claimable funding fees for {account} on {chain}; nothing to claim.")
        return 0

    print(f"Claimable funding fees for {account} on {chain}:")
    print(tabulate(rows, headers="keys", tablefmt="fancy_grid"))

    if args.dry_run:
        print("Dry run: no claim submitted.")
        return 0

    tx_hash = claim_all_funding_fees(config, wallet)
    if tx_hash is None:
        print("No claimable funding fees; no transaction submitted.")
        return 0

    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"Claim transaction {tx_hash.hex()} mined in block {receipt['blockNumber']} with status {receipt['status']}")
    return 0 if receipt["status"] == 1 else 1


if __name__ == "__main__":
    sys.exit(main())
