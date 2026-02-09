"""Silo Finance vault support."""

import datetime
from decimal import Decimal
from functools import cached_property
import logging
from typing import Iterable

from web3.contract import Contract

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626HistoricalReader, ERC4626Vault
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.types import Percent
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader, VaultTechnicalRisk


logger = logging.getLogger(__name__)


class SiloVault(ERC4626Vault):
    """Silo Finance.

    The Silo Protocol is a non-custodial lending primitive that creates programmable risk-isolated markets known as silos. Any user with a wallet can lend or borrow in a silo in a non-custodial manner. Silo markets use the peer-to-pool, overcollateralized model, where the value of a borrower's collateral always exceeds the value of their loan.

    Silo is the main component of the protocol. It implements lending logic, manages and isolates risk, acts as a vault for assets, and performs liquidations. Each Silo is composed of the unique asset for which it was created (ie. UNI) and bridge assets (ie. ETH and SiloDollar). There may be multiple bridge assets at any given time.

    TODO: Add fee data collection

    - `Has custom deposit/withdrawal functions <https://devdocs.silo.finance/smart-contracts-overview/core-protocol/silo#deposit>`__
    - `Github <https://github.com/silo-finance/silo-contracts-v2/blob/develop/silo-core/contracts/Silo.sol>`__
    - `Docs <https://devdocs.silo.finance/smart-contracts-overview/core-protocol/silo>`__
    """

    @cached_property
    def name(self) -> str:
        """Truncate protocol repeat in the name."""
        orig_name = super().name
        return orig_name.replace("Silo Finance ", "")

    def get_risk(self) -> VaultTechnicalRisk | None:
        return VaultTechnicalRisk.low

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment."""
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="silo/Silo.json",
        )

    def has_custom_fees(self) -> bool:
        """Deposit/withdrawal fees."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Fees are taken from AUM.

        https://docs.summer.fi/lazy-summer-protocol/governance/tip-streams
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """TODO: Currently unhandled"""
        return 0.0

    def get_estimated_lock_up(self) -> datetime.timedelta:
        """Buffered withdraws"""
        return datetime.timedelta(days=0)

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Get Silo-specific historical reader with utilisation metrics."""
        return SiloVaultHistoricalReader(self, stateful)

    def fetch_available_liquidity(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Get the amount of denomination token available for immediate withdrawal.

        Silo exposes `getLiquidity()` which returns the available liquidity.

        :param block_identifier:
            Block to query. Defaults to "latest".

        :return:
            Amount in denomination token units (human-readable Decimal).
        """
        try:
            denomination_token = self.denomination_token
            if denomination_token is None:
                return None
            liquidity_raw = self.vault_contract.functions.getLiquidity().call(block_identifier=block_identifier)
            return denomination_token.convert_to_decimals(liquidity_raw)
        except Exception:
            return None

    def fetch_utilisation_percent(self, block_identifier: BlockIdentifier = "latest") -> Percent | None:
        """Get the percentage of assets currently lent out.

        Silo exposes `getDebtAssets()` and `getCollateralAssets()` for utilisation calculation.
        Utilisation = debtAssets / collateralAssets

        :param block_identifier:
            Block to query. Defaults to "latest".

        :return:
            Utilisation as float between 0.0 and 1.0 (0% to 100%).
        """
        try:
            collateral_assets = self.vault_contract.functions.getCollateralAssets().call(block_identifier=block_identifier)
            debt_assets = self.vault_contract.functions.getDebtAssets().call(block_identifier=block_identifier)

            if collateral_assets == 0:
                return 0.0
            return debt_assets / collateral_assets
        except Exception:
            return None


class SiloVaultHistoricalReader(ERC4626HistoricalReader):
    """Read Silo vault core data + utilisation."""

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        yield from self.construct_core_erc_4626_multicall()
        yield from self.construct_utilisation_calls()

    def construct_utilisation_calls(self) -> Iterable[EncodedCall]:
        """Add Silo-specific utilisation calls.

        Silo uses getLiquidity(), getDebtAssets(), and getCollateralAssets().
        """
        # getLiquidity()
        yield EncodedCall.from_contract_call(
            self.vault.vault_contract.functions.getLiquidity(),
            extra_data={
                "function": "getLiquidity",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )

        # getDebtAssets()
        yield EncodedCall.from_contract_call(
            self.vault.vault_contract.functions.getDebtAssets(),
            extra_data={
                "function": "getDebtAssets",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )

        # getCollateralAssets()
        yield EncodedCall.from_contract_call(
            self.vault.vault_contract.functions.getCollateralAssets(),
            extra_data={
                "function": "getCollateralAssets",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )

    def process_utilisation_result(
        self,
        call_by_name: dict[str, EncodedCallResult],
        total_assets: Decimal | None,
    ) -> tuple[Decimal | None, Percent | None]:
        """Decode Silo utilisation data.

        Utilisation = debtAssets / collateralAssets
        """
        liquidity_result = call_by_name.get("getLiquidity")
        debt_result = call_by_name.get("getDebtAssets")
        collateral_result = call_by_name.get("getCollateralAssets")

        if liquidity_result is None:
            return None, None

        denomination_token = self.vault.denomination_token
        if denomination_token is None:
            return None, None

        liquidity_raw = int.from_bytes(liquidity_result.result[0:32], byteorder="big")
        available_liquidity = denomination_token.convert_to_decimals(liquidity_raw)

        utilisation = None
        if debt_result is not None and collateral_result is not None:
            debt_raw = int.from_bytes(debt_result.result[0:32], byteorder="big")
            collateral_raw = int.from_bytes(collateral_result.result[0:32], byteorder="big")
            if collateral_raw > 0:
                utilisation = debt_raw / collateral_raw
            else:
                utilisation = 0.0

        return available_liquidity, utilisation

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        call_by_name = self.dictify_multicall_results(block_number, call_results)

        # Decode common variables
        share_price, total_supply, total_assets, errors, max_deposit = self.process_core_erc_4626_result(call_by_name)
        available_liquidity, utilisation = self.process_utilisation_result(call_by_name, total_assets)

        return VaultHistoricalRead(
            vault=self.vault,
            block_number=block_number,
            timestamp=timestamp,
            share_price=share_price,
            total_assets=total_assets,
            total_supply=total_supply,
            performance_fee=0.0,
            management_fee=0.0,
            errors=errors,
            max_deposit=max_deposit,
            available_liquidity=available_liquidity,
            utilisation=utilisation,
        )
