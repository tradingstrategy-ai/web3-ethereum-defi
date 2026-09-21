"""Claim accrued GMX V2 funding fees for an EOA or a Lagoon vault Safe.

GMX V2 accrues funding fees continuously per ``(market, token, account)`` and
releases them through ``ExchangeRouter.claimFundingFees(markets, tokens,
receiver)``. This script reads the per-account claimable funding receipts,
prints the nonzero ``(market, side)`` pairs, submits one ``multicall`` claim and
waits for the receipt.

The execution mode is detected from whether a Lagoon vault address is
configured, either with ``--vault`` or through
``exchange.ccxt_config.options.vaultAddress`` in the freqtrade config file:

- **EOA mode**: the hot wallet holds the GMX positions, signs and broadcasts the
  ``claimFundingFees`` call directly, and pays the gas.
- **Lagoon vault mode**: the vault's ``TradingStrategyModuleV0`` ``performCall``
  wrapper is used, so the Gnosis Safe holds the positions and is the
  ``msg.sender``, the funding account and the default receiver, while the asset
  manager signs and pays the gas.

The script prints the mode it detected, together with the funding account and
the signer, before reading anything.

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

Freqtrade configuration and secrets files
----------------------------------------

Rather than repeating the signing key on the command line, point the script at
the freqtrade configuration and its secrets file. The files are deep-merged in
the order given, so later files override earlier ones just like freqtrade's own
repeated ``--config``, and the key, RPC endpoint and vault address are read from
the merged result::

    poetry run python scripts/gmx/gmx_claim_funding_fees.py \\
        --config configs/gmx_eth.json --secrets configs/gmx_eth.secrets.json

Explicit command line flags always win over the files. The values are looked up
in this order:

- signing key: ``exchange.ccxt_config.privateKey``, then ``exchange.private_key``
- RPC endpoint: ``exchange.ccxt_config.rpcUrl``, then ``exchange.rpc_url``
- vault address: ``exchange.ccxt_config.options.vaultAddress``, which selects vault mode

Keeping the key in the secrets file also keeps it out of the shell history and
the process list.
"""

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

from tabulate import tabulate
from web3 import Web3

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
    parser.add_argument("--rpc-url", metavar="URL", help="JSON-RPC endpoint for the GMX chain (Arbitrum or Avalanche); read from the config file when omitted")
    parser.add_argument("--private-key", metavar="KEY", help="Signing key: the EOA, or the Lagoon asset manager in vault mode; read from the config file when omitted")
    parser.add_argument("--vault", metavar="ADDRESS", help="Lagoon vault address, which routes the claim through the vault Safe; read from the config file when omitted")
    parser.add_argument("--config", action="append", default=[], metavar="PATH", help="Freqtrade config JSON file; repeatable, later files override earlier ones")
    parser.add_argument("--secrets", action="append", default=[], metavar="PATH", help="Freqtrade secrets JSON file, merged after every --config file; repeatable")
    parser.add_argument("--dry-run", action="store_true", help="Only print the claimable funding fees; do not submit a claim")
    return parser.parse_args()


def deep_merge(base: dict, overlay: dict) -> dict:
    """Deep-merge two freqtrade configuration dictionaries.

    :param base:
        Dictionary to merge into.
    :param overlay:
        Dictionary whose values take precedence.
    :return:
        A new dictionary; neither argument is modified.
    """
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config_files(paths: Sequence[str]) -> dict:
    """Load and deep-merge freqtrade configuration and secrets files.

    Freqtrade keeps its public settings in a config JSON file and its
    credentials in a separate ``*.secrets.json`` file, merging the two at
    startup. Passing several files therefore behaves like freqtrade's own
    repeated ``--config``: files are merged left to right and later files win.

    :param paths:
        File paths in merge order.
    :return:
        Merged configuration, empty when no path was given.
    :raises FileNotFoundError:
        If a path does not exist.
    :raises ValueError:
        If a file does not contain a JSON object.
    :raises json.JSONDecodeError:
        If a file is not valid JSON.
    """
    merged: dict = {}
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"Config file not found: {path}")
        with path.open() as f:
            loaded = json.load(f)
        if not isinstance(loaded, dict):
            raise ValueError(f"Config file {path} must contain a JSON object")
        merged = deep_merge(merged, loaded)
        logger.info("Loaded config from %s", path)
    return merged


def lookup_str(config: dict, dotted_key: str) -> str | None:
    """Read a non-empty string from a nested configuration path.

    :param config:
        Merged freqtrade configuration.
    :param dotted_key:
        Dotted path, for example ``exchange.ccxt_config.privateKey``.
    :return:
        The configured string, or ``None`` when it is absent or blank.
    """
    value: object = config
    for part in dotted_key.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def resolve_setting(cli_value: str | None, config: dict, *dotted_keys: str) -> str | None:
    """Resolve a setting from the command line first, then the configuration files.

    :param cli_value:
        Value of the explicit command line flag, which always wins.
    :param config:
        Merged freqtrade configuration.
    :param dotted_keys:
        Candidate configuration paths, tried in order.
    :return:
        The resolved value, or ``None`` when it is configured nowhere.
    """
    if cli_value:
        return cli_value
    for dotted_key in dotted_keys:
        value = lookup_str(config, dotted_key)
        if value:
            return value
    return None


class ClaimSettings(NamedTuple):
    """Resolved claim inputs, taken from the command line and configuration files."""

    #: Signing key: the EOA, or the Lagoon asset manager in vault mode.
    private_key: str

    #: JSON-RPC endpoint of the GMX chain.
    rpc_url: str

    #: Lagoon vault address, or ``None`` to claim as a plain EOA.
    vault_address: str | None


def resolve_claim_settings(args: argparse.Namespace, file_config: dict) -> ClaimSettings:
    """Resolve the signing key, RPC endpoint and vault address for a claim.

    Command line flags win over the merged freqtrade configuration and secrets
    files. A missing signing key or RPC endpoint is fatal, while a missing vault
    address simply selects EOA mode.

    :param args:
        Parsed command line arguments.
    :param file_config:
        Merged freqtrade configuration and secrets files.
    :return:
        Resolved settings, with a checksummed vault address when Lagoon mode applies.
    :raises SystemExit:
        If the signing key or RPC endpoint cannot be resolved anywhere, or the
        configured vault address is not a valid address.
    """
    private_key = resolve_setting(args.private_key, file_config, "exchange.ccxt_config.privateKey", "exchange.private_key")
    if not private_key:
        sys.exit("No signing key: pass --private-key, or set exchange.ccxt_config.privateKey (or exchange.private_key) through --config / --secrets")

    rpc_url = resolve_setting(args.rpc_url, file_config, "exchange.ccxt_config.rpcUrl", "exchange.rpc_url")
    if not rpc_url:
        sys.exit("No RPC endpoint: pass --rpc-url, or set exchange.ccxt_config.rpcUrl (or exchange.rpc_url) through --config / --secrets")

    vault_address = normalise_vault_address(resolve_setting(args.vault, file_config, "exchange.ccxt_config.options.vaultAddress"))
    return ClaimSettings(private_key, rpc_url, vault_address)


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


def normalise_vault_address(raw_vault: str | None) -> str | None:
    """Validate a Lagoon vault address and return it checksummed.

    Lagoon mode is selected purely by the presence of a vault address, matching
    the GMX freqtrade adapter, which enables Lagoon mode when
    ``exchange.ccxt_config.options.vaultAddress`` is configured.

    :param raw_vault:
        Vault address from ``--vault`` or the configuration files, or ``None``.
    :return:
        Checksummed vault address, or ``None`` when EOA mode applies.
    :raises SystemExit:
        If an address is present but is not a valid hex address. Failing loudly
        matters here: silently falling back to EOA mode would claim the funding
        to the asset manager's own account instead of the vault Safe.
    """
    if not raw_vault:
        return None
    try:
        return Web3.to_checksum_address(raw_vault)
    except (ValueError, TypeError) as e:
        sys.exit(f"Invalid Lagoon vault address {raw_vault!r}: {e}")


def build_wallet(web3, private_key: str, vault_address: str | None) -> tuple[object, str, str]:
    """Build the signing wallet and resolve the funding account.

    Two execution modes are supported, selected by whether a Lagoon vault
    address is configured:

    - **EOA mode**: the signing key's account holds the GMX positions, signs the
      claim and pays the gas.
    - **Lagoon mode**: the vault's Gnosis Safe holds the GMX positions and is the
      funding account, while the asset manager signs the ``performCall`` wrapper
      and pays the gas.

    :param web3:
        Connected Web3 instance.
    :param private_key:
        EOA (or asset-manager) signing key.
    :param vault_address:
        Lagoon vault address, or ``None`` for EOA mode.
    :return:
        Tuple of ``(wallet, funding_account, signer_address)``. The funding
        account holds the GMX positions; the signer pays the gas.
    """
    hot_wallet = HotWallet.from_private_key(private_key)
    hot_wallet.sync_nonce(web3)

    if not vault_address:
        logger.info("Detected EOA mode: account %s holds the positions and pays the gas", hot_wallet.address)
        return hot_wallet, hot_wallet.address, hot_wallet.address

    vault = LagoonVault(web3, VaultSpec(web3.eth.chain_id, vault_address))
    module_address = resolve_module_address(web3, vault, vault_address)
    vault.trading_strategy_module_address = module_address
    wallet = LagoonGMXTradingWallet(vault=vault, asset_manager=hot_wallet)
    logger.info("Detected Lagoon mode: vault=%s, safe=%s, module=%s, signer=%s", vault_address, wallet.address, module_address, hot_wallet.address)
    return wallet, wallet.address, hot_wallet.address


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

    try:
        file_config = load_config_files([*args.config, *args.secrets])
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
        sys.exit(str(e))

    settings = resolve_claim_settings(args, file_config)

    # Report the detected mode before touching the network, so a configuration
    # mistake fails immediately rather than after a connection attempt.
    mode = f"Lagoon vault {settings.vault_address}" if settings.vault_address else "EOA (no Lagoon vault configured)"
    print(f"Execution mode: {mode}")
    logger.info("Detected execution mode: %s", mode)

    web3 = create_multi_provider_web3(settings.rpc_url)
    chain = get_chain_name(web3.eth.chain_id).lower()
    logger.info("Connected to %s (chain id %s), block %s", chain, web3.eth.chain_id, web3.eth.block_number)

    wallet, account, signer = build_wallet(web3, settings.private_key, settings.vault_address)

    if settings.vault_address:
        print(f"Funding account: {account} (the vault Safe, which holds the GMX positions)")
        print(f"Signer and gas payer: {signer} (asset manager)")
    else:
        print(f"Funding account and signer: {account}")
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
