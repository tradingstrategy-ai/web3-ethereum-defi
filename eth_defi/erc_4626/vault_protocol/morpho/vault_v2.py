"""Morpho Vault V2 support.

Morpho Vault V2 is an upgraded version of Morpho vaults that introduces
an adapter-based architecture for flexible asset allocation across multiple yield sources.

- `Morpho V2 documentation <https://docs.morpho.org/learn/concepts/vault-v2/>`__
- `GitHub repository <https://github.com/morpho-org/vault-v2>`__
- `Example vault on Arbitrum <https://arbiscan.io/address/0xbeefff13dd098de415e07f033dae65205b31a894>`__

Key features of Morpho Vault V2:

- Adapter-based architecture for multi-protocol yield allocation
- Granular ID & Cap system for risk management
- Performance and management fees (up to 50% and 5% respectively)
- Timelocked governance with optional abdication
- Non-custodial exits via forceDeallocate
"""

import datetime
import logging
from decimal import Decimal
from functools import cached_property
from typing import Iterable

from eth_typing import BlockIdentifier
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.vault import ERC4626HistoricalReader, ERC4626Vault
from eth_defi.erc_4626.vault_protocol.morpho.flag_analytics import analyze_morpho_flags
from eth_defi.erc_4626.vault_protocol.morpho.offchain_metadata import MorphoVaultData, fetch_morpho_vault_data
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.types import Percent
from eth_defi.vault.base import (
    DEPOSIT_CLOSED_CAP_REACHED,
    REDEMPTION_CLOSED_INSUFFICIENT_LIQUIDITY,
    VaultHistoricalRead,
    VaultHistoricalReader,
)
from eth_defi.vault.flag import VaultFlag

logger = logging.getLogger(__name__)

#: Maximum performance fee in Morpho V2 (50%)
MAX_PERFORMANCE_FEE = 0.5

#: Maximum management fee in Morpho V2 (5% per year)
MAX_MANAGEMENT_FEE = 0.05

#: Fee denominator used in Morpho V2 contracts (1e18)
FEE_DENOMINATOR = 10**18

#: Keccak signatures for fee multicalls
PERFORMANCE_FEE_SIGNATURE = Web3.keccak(text="performanceFee()")[0:4]
MANAGEMENT_FEE_SIGNATURE = Web3.keccak(text="managementFee()")[0:4]


class MorphoV2VaultHistoricalReader(ERC4626HistoricalReader):
    """Read Morpho V2 vault core data + fees + utilisation."""

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        yield from self.construct_core_erc_4626_multicall()
        yield from self.construct_fee_calls()
        yield from self.construct_utilisation_calls()

    def construct_fee_calls(self) -> Iterable[EncodedCall]:
        """Add Morpho V2 fee calls."""
        performance_fee_call = EncodedCall.from_keccak_signature(
            address=self.vault.address,
            signature=PERFORMANCE_FEE_SIGNATURE,
            function="performanceFee",
            data=b"",
            extra_data={
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
            state=self.reader_state,
        )
        yield performance_fee_call

        management_fee_call = EncodedCall.from_keccak_signature(
            address=self.vault.address,
            signature=MANAGEMENT_FEE_SIGNATURE,
            function="managementFee",
            data=b"",
            extra_data={
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
            state=self.reader_state,
        )
        yield management_fee_call

    def construct_utilisation_calls(self) -> Iterable[EncodedCall]:
        """Add idle assets call for utilisation calculation.

        Morpho V2 uses idle assets pattern: asset().balanceOf(vault)
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

    def process_fee_result(self, call_by_name: dict[str, EncodedCallResult]) -> tuple[float, float]:
        """Decode Morpho V2 fee data."""
        performance_data = call_by_name.get("performanceFee")
        management_data = call_by_name.get("managementFee")

        performance_fee = 0.0
        management_fee = 0.0

        if performance_data and performance_data.result:
            performance_fee = int.from_bytes(performance_data.result[0:32], byteorder="big") / FEE_DENOMINATOR

        if management_data and management_data.result:
            management_fee = int.from_bytes(management_data.result[0:32], byteorder="big") / FEE_DENOMINATOR

        return performance_fee, management_fee

    def process_utilisation_result(
        self,
        call_by_name: dict[str, EncodedCallResult],
        total_assets: Decimal | None,
    ) -> tuple[Decimal | None, Percent | None]:
        """Decode Morpho V2 utilisation data.

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
        performance_fee, management_fee = self.process_fee_result(call_by_name)
        available_liquidity, utilisation = self.process_utilisation_result(call_by_name, total_assets)

        return VaultHistoricalRead(
            vault=self.vault,
            block_number=block_number,
            timestamp=timestamp,
            share_price=share_price,
            total_assets=total_assets,
            total_supply=total_supply,
            performance_fee=performance_fee,
            management_fee=management_fee,
            errors=errors,
            max_deposit=max_deposit,
            available_liquidity=available_liquidity,
            utilisation=utilisation,
        )


class MorphoV2Vault(ERC4626Vault):
    """Morpho Vault V2 support.

    Morpho Vault V2 is a newer version of Morpho vaults with an adapter-based
    architecture that allows flexible allocation across multiple yield sources.

    - `Morpho V2 documentation <https://docs.morpho.org/learn/concepts/vault-v2/>`__
    - `GitHub repository <https://github.com/morpho-org/vault-v2>`__
    - `Example vault on Arbitrum <https://arbiscan.io/address/0xbeefff13dd098de415e07f033dae65205b31a894>`__

    Key differences from Morpho V1:

    - V2 uses adapters to allocate to multiple yield sources (not just Morpho markets)
    - V2 has both performance and management fees (V1 only had performance fee)
    - V2 uses ``adaptersLength()`` function while V1 uses ``MORPHO()`` function
    - V2 has timelocked governance with curator/allocator roles

    See also :py:class:`eth_defi.erc_4626.vault_protocol.morpho.vault_v1.MorphoV1Vault`
    for the original MetaMorpho architecture.
    """

    @cached_property
    def morpho_offchain_data(self) -> MorphoVaultData | None:
        """Vault and market warnings from the Morpho Blue GraphQL API (24h cached).

        Fetches vault-level governance warnings and market-level risk warnings via the
        Morpho Blue public GraphQL API.

        Note: the Morpho Blue ``vaultByAddress`` query currently returns ``NOT_FOUND``
        for V2 vault addresses — V2 vaults use an adapter-based architecture and are
        not yet indexed by this API endpoint. This method will return ``None`` for
        all current V2 vaults and is included for future compatibility.

        :return:
            :py:class:`~eth_defi.erc_4626.vault_protocol.morpho.offchain_metadata.MorphoVaultData`
            or ``None``.
        """
        return fetch_morpho_vault_data(self.web3, self.vault_address)

    def get_morpho_vault_flags(self) -> set[str]:
        """Return warning type strings from vault-level Morpho API warnings.

        :return:
            Set of warning type strings, e.g. ``{"short_timelock", "not_whitelisted"}``.
            Empty set if no data available (expected for V2 vaults currently).
        """
        data = self.morpho_offchain_data
        if not data:
            return set()
        return {w["type"] for w in data.get("vault_warnings", [])}

    def get_morpho_market_flags(self) -> set[str]:
        """Return warning type strings from Morpho API warnings on underlying markets.

        :return:
            Set of warning type strings, e.g. ``{"bad_debt_unrealized"}``.
            Empty set if no data available (expected for V2 vaults currently).
        """
        data = self.morpho_offchain_data
        if not data:
            return set()
        return {w["type"] for w in data.get("market_warnings", [])}

    def get_notes(self) -> str | None:
        """Return a human-readable note about RED-level Morpho warnings, if any.

        Checks the hardcoded flag table first (via the base class), then falls back
        to a dynamically generated note from the Morpho Blue GraphQL API when RED
        warnings are present.

        For current V2 vaults the Morpho Blue API returns ``NOT_FOUND``, so this
        will typically return ``None`` unless a hardcoded note exists.

        :return:
            Note string, or ``None`` if no hardcoded note and no RED warnings.
        """
        note = super().get_notes()
        if note:
            return note
        data = self.morpho_offchain_data
        if data is not None:
            return analyze_morpho_flags(data).note
        return None

    def get_flags(self) -> set[VaultFlag]:
        """Get vault flags, adding ``morpho_issues`` when RED warnings are detected.

        Calls the Morpho Blue GraphQL API (24h cached) to check for RED-level
        vault or market warnings. If any are found, adds
        :py:attr:`~eth_defi.vault.flag.VaultFlag.morpho_issues` to the flag set.

        For current V2 vaults this will return the base ERC-4626 flags only,
        as the Morpho Blue API does not yet index V2 vault addresses.

        :return:
            Set of :py:class:`~eth_defi.vault.flag.VaultFlag` values.
        """
        flags = super().get_flags()
        data = self.morpho_offchain_data
        if data:
            has_red = any(w.get("level") == "RED" for w in data.get("vault_warnings", []) + data.get("market_warnings", []))
            if has_red:
                flags = set(flags)
                flags.add(VaultFlag.morpho_issues)
        return flags

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get Morpho V2 management fee.

        Management fee is charged on total assets (up to 5% per year).

        :return:
            Management fee as a decimal (e.g. 0.02 for 2%), or None if reading fails
        """
        fee_call = EncodedCall.from_keccak_signature(
            address=self.address,
            signature=Web3.keccak(text="managementFee()")[0:4],
            function="managementFee",
            data=b"",
            extra_data={"vault": self.address},
        )
        try:
            data = fee_call.call(self.web3, block_identifier)
        except ValueError as e:
            logger.warning(
                "Management fee read reverted on Morpho V2 vault %s: %s",
                self,
                str(e),
            )
            return None

        # Management fee is stored as uint96, scaled by 1e18
        management_fee = int.from_bytes(data[0:32], byteorder="big") / FEE_DENOMINATOR
        return management_fee

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get Morpho V2 performance fee.

        Performance fee is charged on yield generated (up to 50%).

        :return:
            Performance fee as a decimal (e.g. 0.1 for 10%), or None if reading fails
        """
        fee_call = EncodedCall.from_keccak_signature(
            address=self.address,
            signature=Web3.keccak(text="performanceFee()")[0:4],
            function="performanceFee",
            data=b"",
            extra_data={"vault": self.address},
        )
        try:
            data = fee_call.call(self.web3, block_identifier)
        except ValueError as e:
            logger.warning(
                "Performance fee read reverted on Morpho V2 vault %s: %s",
                self,
                str(e),
            )
            return None

        # Performance fee is stored as uint96, scaled by 1e18
        performance_fee = int.from_bytes(data[0:32], byteorder="big") / FEE_DENOMINATOR
        return performance_fee

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Morpho V2 vaults have no lock-up period.

        Users can withdraw at any time using regular withdraw or forceDeallocate.
        """
        return datetime.timedelta(days=0)

    def get_link(self, referral: str | None = None) -> str:
        """Get link to the vault on Morpho app.

        :param referral:
            Optional referral code (not supported by Morpho)

        :return:
            URL to the vault page on app.morpho.org
        """
        chain_name = get_chain_name(self.chain_id).lower()
        return f"https://app.morpho.org/{chain_name}/vault/{self.vault_address}"

    def get_adapters_count(self, block_identifier: BlockIdentifier = "latest") -> int | None:
        """Get the number of adapters configured for this vault.

        :return:
            Number of adapters, or None if reading fails
        """
        adapters_call = EncodedCall.from_keccak_signature(
            address=self.address,
            signature=Web3.keccak(text="adaptersLength()")[0:4],
            function="adaptersLength",
            data=b"",
            extra_data={"vault": self.address},
        )
        try:
            data = adapters_call.call(self.web3, block_identifier)
        except ValueError as e:
            logger.warning(
                "adaptersLength() read reverted on Morpho V2 vault %s: %s",
                self,
                str(e),
            )
            return None

        return int.from_bytes(data[0:32], byteorder="big")

    def fetch_deposit_closed_reason(self) -> str | None:
        """Check maxDeposit to determine if deposits are closed.

        Morpho vaults are utilisation-based.
        """
        try:
            max_deposit = self.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
            if max_deposit == 0:
                return f"{DEPOSIT_CLOSED_CAP_REACHED} (maxDeposit=0)"
        except Exception:
            pass
        return None

    def fetch_redemption_closed_reason(self) -> str | None:
        """Check maxRedeem to determine if redemptions are closed.

        Morpho vaults are utilisation-based.
        """
        try:
            max_redeem = self.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
            if max_redeem == 0:
                return f"{REDEMPTION_CLOSED_INSUFFICIENT_LIQUIDITY} (maxRedeem=0)"
        except Exception:
            pass
        return None

    def fetch_deposit_next_open(self) -> datetime.datetime | None:
        """Deposit timing is unpredictable - utilisation-based."""
        return None

    def fetch_redemption_next_open(self) -> datetime.datetime | None:
        """Withdrawal timing is unpredictable - utilisation-based."""
        return None

    def can_check_redeem(self) -> bool:
        """Morpho V2 supports address(0) checks for redemption availability.

        - maxRedeem(address(0)) returns 0 when redemptions are blocked
        """
        return True

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Get Morpho V2-specific historical reader with utilisation metrics."""
        return MorphoV2VaultHistoricalReader(self, stateful)

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
        """Get the percentage of assets currently allocated to strategies.

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
