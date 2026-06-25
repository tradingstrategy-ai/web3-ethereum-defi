"""Lighter guard whitelisting and library deployment.

Production helpers for enabling Lighter (a zk-rollup perpetuals/spot DEX on
Ethereum L1) deposits and withdrawals through an asset-managed Gnosis Safe
governed by :py:class:`GuardV0` / ``TradingStrategyModuleV0``.

This module is the single source of truth for Lighter guard configuration. It
is called both by the Lagoon vault deployment flow
(:py:mod:`eth_defi.erc_4626.vault_protocol.lagoon.deployment`) and by the
Anvil-fork tests (via :py:mod:`eth_defi.lighter.testing`).

The on-chain scope is the L1 custody flow only: ``deposit`` /
``withdraw`` / ``withdrawPendingBalance``. Account registration is off-chain
(EIP-712 / EIP-1271 Safe signature) and does not go through the guard.

See ``eth_defi/lighter/README-lighter-guard.md`` for the architecture and
security model.

Authoritative docs:

- Lighter: https://docs.lighter.xyz
- Lighter L1 contract (proxy — the address operators whitelist and call):
  https://etherscan.io/address/0x3b4d794a66304f130a4db8f2551b0070dfcf5ca7
- ``ZkLighter`` implementation (verified source behind the proxy):
  https://etherscan.io/address/0x831ef69bab8af8b1037a4961b8d0674b124e7008
"""

import logging
from dataclasses import dataclass
from typing import Callable

from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.contract import Contract
from web3.contract.contract import ContractFunction

from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import deploy_contract
from eth_defi.hotwallet import HotWallet
from eth_defi.lighter.constants import LIGHTER_L1_CONTRACT, LIGHTER_USDC_ETHEREUM
from eth_defi.trace import assert_transaction_success_with_explanation

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LighterDeployment:
    """Lighter L1 deployment configuration for guard whitelisting.

    :ivar zk_lighter:
        The ``ZkLighter`` L1 contract (proxy) address. Holds user deposits.

    :ivar usdc:
        The USDC token address used as the Lighter deposit asset.
    """

    #: ZkLighter L1 contract (proxy) address.
    zk_lighter: HexAddress

    #: USDC token address (Lighter deposit asset).
    usdc: HexAddress

    @classmethod
    def create_ethereum(cls) -> "LighterDeployment":
        """Create the canonical Ethereum mainnet Lighter deployment."""
        return cls(
            zk_lighter=Web3.to_checksum_address(LIGHTER_L1_CONTRACT),
            usdc=Web3.to_checksum_address(LIGHTER_USDC_ETHEREUM),
        )


@dataclass(slots=True, frozen=True)
class LighterWhitelistEntry:
    """A single Lighter whitelisting action, for deployment reporting.

    Deliberately Lagoon-independent: the Lagoon deployment module wraps these
    rows into its own ``WhitelistEntry`` type. ``eth_defi.lighter.deployment``
    must **not** import ``WhitelistEntry`` from the Lagoon module, as that would
    create a circular import (Lagoon imports this module).
    """

    #: Protocol category, e.g. ``"Lighter"``.
    category: str

    #: Human-readable name of the whitelisted target.
    name: str

    #: The whitelisted address.
    address: HexAddress


def deploy_lighter_lib(web3: Web3, deployer: HexAddress | LocalAccount | HotWallet) -> Contract:
    """Deploy the ``LighterLib`` external Forge library.

    The library must be deployed and linked into the guard / module at
    deployment time, exactly like ``GmxLib`` and ``HypercoreVaultLib``. The
    Lagoon deployment flow calls this to populate
    ``library_addresses["LighterLib"]``.

    :param web3:
        Web3 connection.

    :param deployer:
        Deployer account — a plain address (Anvil unlocked), a ``LocalAccount``
        or a :py:class:`~eth_defi.hotwallet.HotWallet`.

    :return:
        The deployed ``LighterLib`` contract.
    """
    return deploy_contract(web3, "guard/LighterLib.json", deployer)


def _default_broadcast(web3: Web3, owner: HexAddress | str) -> Callable[[ContractFunction], HexBytes]:
    """Build the default transaction broadcaster.

    Sends the bound contract function from ``owner`` and asserts success.
    """

    def broadcast(func: ContractFunction) -> HexBytes:
        tx_hash = func.transact({"from": owner})
        assert_transaction_success_with_explanation(web3, tx_hash)
        return tx_hash

    return broadcast


def setup_lighter_whitelisting(
    web3: Web3,
    module: Contract,
    owner: HexAddress | str,
    deployment: LighterDeployment,
    safe_address: HexAddress | str,
    notes: str = "Lighter deposits",
    broadcast: Callable[[ContractFunction], HexBytes] | None = None,
) -> list[LighterWhitelistEntry]:
    """Whitelist the Lighter L1 contract on a guard / module.

    Reads the USDC asset index from the ``ZkLighter`` contract
    (``USDC_ASSET_INDEX()``) and calls
    ``whitelistLighter(zkLighter, usdc, assetIndex, notes)``.

    The Safe must be an allowed receiver (for ``deposit._to`` /
    ``withdrawPendingBalance._owner``). In the Lagoon flow ``setup_guard``
    already calls ``allowReceiver(safe)`` globally, so this helper calls it
    **only if** ``isAllowedReceiver(safe)`` is currently false — avoiding a
    duplicate receiver event. Standalone/test callers still get the Safe
    whitelisted.

    :param web3:
        Web3 connection.

    :param module:
        Deployed ``GuardV0`` or ``TradingStrategyModuleV0`` contract exposing
        ``whitelistLighter`` / ``allowReceiver`` / ``isAllowedReceiver``.

    :param owner:
        Guard owner address (used by the default broadcaster).

    :param deployment:
        Lighter deployment configuration (contract + USDC addresses).

    :param safe_address:
        The Safe address — the Lighter account owner and the allowed receiver.

    :param notes:
        Annotation stored in the whitelisting event logs.

    :param broadcast:
        Optional transaction broadcaster taking a bound contract function and
        returning a tx hash (lets the Lagoon flow reuse its own broadcaster).
        Defaults to ``func.transact({"from": owner})`` + success assertion.

    :return:
        Lagoon-independent :py:class:`LighterWhitelistEntry` rows.
    """

    if broadcast is None:
        broadcast = _default_broadcast(web3, owner)

    zk_lighter = Web3.to_checksum_address(deployment.zk_lighter)
    usdc = Web3.to_checksum_address(deployment.usdc)
    safe_address = Web3.to_checksum_address(safe_address)

    # Read the USDC asset index from the live ZkLighter contract.
    zk = get_deployed_contract(web3, "lighter/ZkLighter.json", zk_lighter)
    asset_index = zk.functions.USDC_ASSET_INDEX().call()
    logger.info(
        "Whitelisting Lighter %s (USDC %s, asset index %d)",
        zk_lighter,
        usdc,
        asset_index,
    )

    broadcast(module.functions.whitelistLighter(zk_lighter, usdc, asset_index, notes))

    entries = [
        LighterWhitelistEntry("Lighter", "ZkLighter contract", zk_lighter),
        LighterWhitelistEntry("Lighter", "USDC", usdc),
    ]

    # Only add the Safe as a receiver if it is not already whitelisted, to avoid
    # emitting a duplicate receiver event (setup_guard does this globally).
    if not module.functions.isAllowedReceiver(safe_address).call():
        broadcast(module.functions.allowReceiver(safe_address, notes))
        entries.append(LighterWhitelistEntry("Lighter", "Safe receiver", safe_address))

    logger.info("Lighter whitelisting complete: %d entries", len(entries))
    return entries
