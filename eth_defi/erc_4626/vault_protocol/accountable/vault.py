"""Accountable Capital vault support."""

import datetime
import logging
from decimal import Decimal
from functools import cached_property
from typing import Iterable

from eth_typing import BlockIdentifier
from web3.contract import Contract

from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626HistoricalReader, ERC4626Vault
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.types import Percent
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader

logger = logging.getLogger(__name__)


class AccountableHistoricalReader(ERC4626HistoricalReader):
    """Read Accountable vault core data with corrected NAV and available liquidity.

    Accountable's ``totalAssets()`` only returns idle liquidity in the vault contract,
    excluding capital deployed by the strategy via ``lockAssets()``. This means the
    standard ERC-4626 ``totalAssets()`` severely underreports the true vault NAV.

    This reader:

    - Computes the true NAV as ``share_price * total_supply``
      (derived from ``convertToAssets`` which uses ``sharePrice()``)
    - Exposes the raw ``totalAssets()`` value as ``available_liquidity``
      since it represents the idle capital available for immediate withdrawals
    """

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        yield from self.construct_core_erc_4626_multicall()

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        call_by_name = self.dictify_multicall_results(block_number, call_results)

        # Decode common variables (share_price, total_supply, total_assets from totalAssets(), errors, max_deposit)
        share_price, total_supply, idle_assets, errors, max_deposit = self.process_core_erc_4626_result(call_by_name)

        # idle_assets is the raw totalAssets() value = idle liquidity available for withdrawal
        available_liquidity = idle_assets

        # Override total_assets with the true NAV: share_price * total_supply
        # because totalAssets() only returns idle liquidity, not deployed capital.
        total_assets = idle_assets
        if share_price is not None and total_supply is not None and total_supply > 0:
            total_assets = share_price * total_supply

        # Utilisation = deployed capital / true NAV
        utilisation = None
        if total_assets is not None and available_liquidity is not None and total_assets > 0:
            utilisation = float((total_assets - available_liquidity) / total_assets)

        return VaultHistoricalRead(
            vault=self.vault,
            block_number=block_number,
            timestamp=timestamp,
            share_price=share_price,
            total_assets=total_assets,
            total_supply=total_supply,
            performance_fee=None,
            management_fee=None,
            errors=errors or None,
            max_deposit=max_deposit,
            available_liquidity=available_liquidity,
            utilisation=utilisation,
        )


class AccountableVault(ERC4626Vault):
    """Accountable Capital vault support.

    Accountable Capital develops blockchain-based financial verification technology
    that enables organisations and investors to demonstrate solvency, liquidity,
    and compliance through transparent, verifiable attestations. The platform
    combines cryptographic proofs with auditable financial data to enhance trust
    across Web3 and traditional finance.

    Accountable vaults implement ERC-7540 async redemption pattern with a queue
    system for processing withdrawal requests.

    NAV calculation
    ~~~~~~~~~~~~~~~

    Accountable's ``totalAssets()`` only returns the **idle liquidity** held by the vault
    contract. When the strategy deploys capital via ``lockAssets()``, those assets are
    subtracted from ``totalAssets()``. This means ``totalAssets()`` severely underreports
    the true vault value.

    The true NAV is computed as ``convertToAssets(totalSupply())``, which uses
    ``sharePrice()`` — the oracle/strategy-set price that reflects all capital
    including deployed positions. Both :py:meth:`fetch_total_assets` and
    :py:class:`AccountableHistoricalReader` use this corrected calculation.

    The raw ``totalAssets()`` value is exposed via :py:meth:`fetch_idle_capital`
    and :py:meth:`fetch_available_liquidity` as it represents capital available
    for immediate withdrawals.

    Key contract functions for NAV:

    - ``sharePrice()`` — current price per share (reflects deployed capital)
    - ``totalSupply()`` — total shares outstanding
    - ``convertToAssets(shares)`` — converts shares to assets using share price
    - ``totalAssets()`` — idle liquidity only (excludes deployed capital)
    - ``lockAssets(assets, sender)`` — strategy deploys capital (reduces ``totalAssets``)
    - ``releaseAssets(assets, receiver)`` — strategy returns capital (increases ``totalAssets``)
    - ``reservedLiquidity()`` — assets reserved for pending redemptions

    - Homepage: https://www.accountable.capital/
    - Twitter: https://x.com/AccountableData
    - No public GitHub repository available for smart contracts
    - Example contract: https://monadscan.com/address/0x58ba69b289De313E66A13B7D1F822Fc98b970554
    """

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment with Accountable-specific ABI."""
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="accountable/AccountableAsyncRedeemVault.json",
        )

    def get_historical_reader(self, stateful) -> VaultHistoricalReader:
        return AccountableHistoricalReader(self, stateful=stateful)

    def fetch_idle_capital(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Fetch idle capital held by the vault contract.

        This is the raw ``totalAssets()`` value — assets sitting in the vault
        that have not been deployed by the strategy via ``lockAssets()``.
        This is the capital available for immediate withdrawals.

        :param block_identifier:
            Block number to read.

        :return:
            Idle capital in underlying token, or None if denomination token is unavailable.
        """
        if self.underlying_token is None:
            return None

        raw_amount = self.vault_contract.functions.totalAssets().call(block_identifier=block_identifier)
        return self.underlying_token.convert_to_decimals(raw_amount)

    def fetch_total_assets(self, block_identifier: BlockIdentifier) -> Decimal | None:
        """Fetch the true vault NAV including deployed capital.

        Accountable's ``totalAssets()`` only returns idle liquidity.
        We compute the true NAV as ``convertToAssets(totalSupply())``,
        which uses the strategy-set ``sharePrice()`` to account for
        all capital including deployed positions.

        :param block_identifier:
            Block number to read.

        :return:
            The vault NAV in underlying token, or None if denomination token is unavailable.
        """
        if self.underlying_token is None:
            return None

        raw_total_supply = self.vault_contract.functions.totalSupply().call(block_identifier=block_identifier)
        if raw_total_supply == 0:
            return Decimal(0)

        raw_nav = self.vault_contract.functions.convertToAssets(raw_total_supply).call(block_identifier=block_identifier)
        return self.underlying_token.convert_to_decimals(raw_nav)

    def fetch_nav(self, block_identifier=None) -> Decimal:
        """Fetch the most recent onchain NAV value.

        Uses ``convertToAssets(totalSupply())`` instead of ``totalAssets()``
        because Accountable's ``totalAssets()`` excludes deployed capital.

        :return:
            Vault NAV, denominated in :py:meth:`denomination_token`
        """
        token = self.denomination_token
        raw_total_supply = self.vault_contract.functions.totalSupply().call(block_identifier=block_identifier)
        if raw_total_supply == 0:
            return Decimal(0)
        raw_nav = self.vault_contract.functions.convertToAssets(raw_total_supply).call(block_identifier=block_identifier)
        return token.convert_to_decimals(raw_nav)

    def fetch_available_liquidity(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Get the amount of denomination token available for immediate withdrawal.

        For Accountable vaults, this is ``totalAssets()`` which returns only idle
        capital not deployed by the strategy.

        :param block_identifier:
            Block to query. Defaults to "latest".

        :return:
            Amount in denomination token units (human-readable Decimal).
        """
        return self.fetch_idle_capital(block_identifier)

    def fetch_utilisation_percent(self, block_identifier: BlockIdentifier = "latest") -> Percent | None:
        """Get the percentage of assets currently deployed by the strategy.

        Utilisation = (true NAV - idle capital) / true NAV

        :param block_identifier:
            Block to query. Defaults to "latest".

        :return:
            Utilisation as float between 0.0 and 1.0 (0% to 100%).
        """
        nav = self.fetch_total_assets(block_identifier)
        idle = self.fetch_idle_capital(block_identifier)
        if nav is None or idle is None or nav == 0:
            return None
        return float((nav - idle) / nav)

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Management fee is not publicly available.

        Accountable vaults do not expose fee information on-chain.
        """
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Performance fee is not publicly available.

        Accountable vaults do not expose fee information on-chain.
        """
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Accountable vaults use async redemption queue.

        Lock-up period depends on the vault strategy and available liquidity.
        """
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Return the protocol homepage link.

        Accountable does not have individual vault pages.
        """
        return "https://www.accountable.capital/"
