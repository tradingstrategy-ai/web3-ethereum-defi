"""Fluid fToken vault support.

Fluid is a DeFi lending protocol by Instadapp featuring ERC-4626 compliant fToken vaults.
Users deposit assets to earn yield through the liquidity layer.

- `Protocol homepage <https://fluid.io/>`__
- `Documentation <https://docs.fluid.instadapp.io/>`__
- `GitHub repository <https://github.com/Instadapp/fluid-contracts-public>`__
- `Twitter <https://x.com/0xfluid>`__
- `Example fToken on Plasma <https://plasmascan.to/address/0x1DD4b13fcAE900C60a350589BE8052959D2Ed27B>`__

Fee structure:

- Fluid fTokens have fees internalised through the exchange price mechanism
- Interest accrues to the share price over time
- No explicit deposit/withdraw fees

"""

import datetime
import logging
from decimal import Decimal
from typing import Iterable

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626HistoricalReader, ERC4626Vault
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.types import Percent
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader

logger = logging.getLogger(__name__)


class FluidVault(ERC4626Vault):
    """Fluid fToken vault support.

    Fluid is a DeFi lending protocol by Instadapp where users can deposit assets
    to earn yield. The protocol uses ERC-4626 compliant fTokens to represent
    user deposits.

    - `Protocol homepage <https://fluid.io/>`__
    - `Documentation <https://docs.fluid.instadapp.io/>`__
    - `GitHub repository <https://github.com/Instadapp/fluid-contracts-public>`__
    - `Twitter <https://x.com/0xfluid>`__

    Key features:

    - ERC-4626 compliant fTokens for lending
    - Fees are internalised into the share price through interest accrual
    - No explicit deposit or withdrawal fees
    """

    def has_custom_fees(self) -> bool:
        """Fluid has no deposit/withdrawal fees."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Fluid has no management fee.

        Interest spread is handled at the protocol level, not as an explicit fee.
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Fluid has no explicit performance fee.

        Fees are internalised in the interest rate mechanism.
        """
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Fluid fTokens have instant liquidity - no lock-up period."""
        return datetime.timedelta(days=0)

    def get_link(self, referral: str | None = None) -> str:
        """Get the Fluid protocol link.

        Since Fluid doesn't have individual vault pages, we link to the main app.
        """
        return "https://fluid.io/"

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Get Fluid-specific historical reader with utilisation metrics."""
        return FluidVaultHistoricalReader(self, stateful)

    def fetch_available_liquidity(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Get the amount of denomination token available for immediate withdrawal.

        Uses the idle assets pattern: asset().balanceOf(vault) returns unallocated assets.

        :param block_identifier:
            Block to query. Defaults to "latest".

        :return:
            Amount in denomination token units (human-readable Decimal).
        """
        try:
            denomination_token = self.denomination_token
            if denomination_token is None:
                return None
            idle_raw = denomination_token.contract.functions.balanceOf(self.address).call(block_identifier=block_identifier)
            return denomination_token.convert_to_decimals(idle_raw)
        except Exception:
            return None

    def fetch_utilisation_percent(self, block_identifier: BlockIdentifier = "latest") -> Percent | None:
        """Get the percentage of assets currently lent out.

        Utilisation = (totalAssets - idle) / totalAssets

        :param block_identifier:
            Block to query. Defaults to "latest".

        :return:
            Utilisation as float between 0.0 and 1.0 (0% to 100%).
        """
        try:
            denomination_token = self.denomination_token
            if denomination_token is None:
                return None

            total_assets = self.vault_contract.functions.totalAssets().call(block_identifier=block_identifier)
            idle = denomination_token.contract.functions.balanceOf(self.address).call(block_identifier=block_identifier)

            if total_assets == 0:
                return 0.0
            return (total_assets - idle) / total_assets
        except Exception:
            return None


class FluidVaultHistoricalReader(ERC4626HistoricalReader):
    """Read Fluid vault core data + utilisation."""

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        yield from self.construct_core_erc_4626_multicall()
        yield from self.construct_utilisation_calls()

    def construct_utilisation_calls(self) -> Iterable[EncodedCall]:
        """Add idle assets call for utilisation calculation.

        Fluid uses idle assets pattern: asset().balanceOf(vault)
        """
        denomination_token = self.vault.denomination_token
        if denomination_token is None:
            return

        idle_call = EncodedCall.from_contract_call(
            denomination_token.contract.functions.balanceOf(self.vault.address),
            extra_data={
                "function": "idle_assets",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )
        yield idle_call

    def process_utilisation_result(
        self,
        call_by_name: dict[str, EncodedCallResult],
        total_assets: Decimal | None,
    ) -> tuple[Decimal | None, Percent | None]:
        """Decode Fluid utilisation data.

        Utilisation = (totalAssets - idle) / totalAssets
        """
        idle_result = call_by_name.get("idle_assets")

        if idle_result is None or total_assets is None:
            return None, None

        denomination_token = self.vault.denomination_token
        if denomination_token is None:
            return None, None

        idle_raw = int.from_bytes(idle_result.result[0:32], byteorder="big")
        available_liquidity = denomination_token.convert_to_decimals(idle_raw)

        if total_assets == 0:
            utilisation = 0.0
        else:
            total_assets_raw = denomination_token.convert_to_raw(total_assets)
            utilisation = (total_assets_raw - idle_raw) / total_assets_raw

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
