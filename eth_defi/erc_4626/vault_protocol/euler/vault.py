"""Euler Vault Kit and EulerEarn integrations.

- EVK (Euler Vault Kit): Credit vaults with borrowing functionality
  https://github.com/euler-xyz/euler-vault-kit
  Metadata repo: https://github.com/euler-xyz/euler-labels/blob/master/130/vaults.json

- EulerEarn: Metamorpho-based metavault for yield aggregation on top of EVK
  https://github.com/euler-xyz/euler-earn
  Documentation: https://docs.euler.finance/developers/euler-earn/
"""

import datetime
from decimal import Decimal
from functools import cached_property
import logging
from typing import Iterable

from web3 import Web3

from eth_typing import BlockIdentifier

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.vault import ERC4626HistoricalReader, ERC4626Vault
from eth_defi.erc_4626.vault_protocol.euler.offchain_metadata import EulerVaultMetadata, fetch_euler_vault_metadata
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.types import Percent
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader, VaultTechnicalRisk
from eth_defi.vault.flag import BAD_FLAGS, get_vault_special_flags

logger = logging.getLogger(__name__)

#: Keccak signatures for Euler EVK multicall
CASH_SIGNATURE = Web3.keccak(text="cash()")[0:4]
TOTAL_BORROWS_SIGNATURE = Web3.keccak(text="totalBorrows()")[0:4]
INTEREST_FEE_SIGNATURE = Web3.keccak(text="interestFee()")[0:4]


class EulerVaultHistoricalReader(ERC4626HistoricalReader):
    """Read Euler EVK vault core data + utilisation metrics.

    For EVK vaults:
    - cash() = underlying tokens currently held by vault
    - totalBorrows() = outstanding borrows including accrued interest
    - Utilisation = totalBorrows / (cash + totalBorrows)
    """

    def get_warmup_calls(self) -> Iterable[tuple[str, callable, any]]:
        """Yield warmup calls for Euler EVK vaults.

        Includes base ERC-4626 calls plus Euler-specific utilisation calls.
        """
        yield from super().get_warmup_calls()

        vault_contract = self.vault.vault_contract
        cash_call = vault_contract.functions.cash()
        yield ("cash", lambda: cash_call.call(), cash_call)

        total_borrows_call = vault_contract.functions.totalBorrows()
        yield ("totalBorrows", lambda: total_borrows_call.call(), total_borrows_call)

        interest_fee_call = vault_contract.functions.interestFee()
        yield ("interestFee", lambda: interest_fee_call.call(), interest_fee_call)

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        yield from self.construct_core_erc_4626_multicall()
        yield from self.construct_utilisation_calls()

    def construct_utilisation_calls(self) -> Iterable[EncodedCall]:
        """Add Euler EVK-specific utilisation calls."""
        if not self.should_skip_call("cash"):
            cash_call = EncodedCall.from_keccak_signature(
                address=self.vault.address,
                signature=CASH_SIGNATURE,
                function="cash",
                data=b"",
                extra_data={"vault": self.vault.address},
                first_block_number=self.first_block,
            )
            yield cash_call

        if not self.should_skip_call("totalBorrows"):
            total_borrows_call = EncodedCall.from_keccak_signature(
                address=self.vault.address,
                signature=TOTAL_BORROWS_SIGNATURE,
                function="totalBorrows",
                data=b"",
                extra_data={"vault": self.vault.address},
                first_block_number=self.first_block,
            )
            yield total_borrows_call

        if not self.should_skip_call("interestFee"):
            interest_fee_call = EncodedCall.from_keccak_signature(
                address=self.vault.address,
                signature=INTEREST_FEE_SIGNATURE,
                function="interestFee",
                data=b"",
                extra_data={"vault": self.vault.address},
                first_block_number=self.first_block,
            )
            yield interest_fee_call

    def process_utilisation_result(self, call_by_name: dict[str, EncodedCallResult]) -> tuple[Decimal | None, Percent | None]:
        """Decode Euler EVK utilisation data.

        Utilisation = totalBorrows / (cash + totalBorrows)
        """
        cash_result = call_by_name.get("cash")
        total_borrows_result = call_by_name.get("totalBorrows")

        if cash_result is None or total_borrows_result is None:
            return None, None

        denomination_token = self.vault.denomination_token
        if denomination_token is None:
            return None, None

        cash_raw = int.from_bytes(cash_result.result[0:32], byteorder="big")
        total_borrows_raw = int.from_bytes(total_borrows_result.result[0:32], byteorder="big")

        available_liquidity = denomination_token.convert_to_decimals(cash_raw)

        total_pool = cash_raw + total_borrows_raw
        if total_pool == 0:
            utilisation = 0.0
        else:
            utilisation = total_borrows_raw / total_pool

        return available_liquidity, utilisation

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        call_by_name = self.dictify_multicall_results(block_number, call_results)

        share_price, total_supply, total_assets, errors, max_deposit = self.process_core_erc_4626_result(call_by_name)
        available_liquidity, utilisation = self.process_utilisation_result(call_by_name)

        # Get interest fee if available
        interest_fee_result = call_by_name.get("interestFee")
        performance_fee = None
        if interest_fee_result is not None:
            performance_fee = float(int.from_bytes(interest_fee_result.result[0:32], byteorder="big") / (10**4))

        return VaultHistoricalRead(
            vault=self.vault,
            block_number=block_number,
            timestamp=timestamp,
            share_price=share_price,
            total_assets=total_assets,
            total_supply=total_supply,
            performance_fee=performance_fee,
            management_fee=0.0,
            errors=errors,
            max_deposit=max_deposit,
            available_liquidity=available_liquidity,
            utilisation=utilisation,
        )


class EulerVault(ERC4626Vault):
    """Euler vault support.

    - Handle special offchain metadata
    - Example vault https://etherscan.io/address/0x1e548CfcE5FCF17247E024eF06d32A01841fF404#code
    - Euler ABIs https://github.com/euler-xyz/euler-interfaces

    TODO: Fees
    """

    def get_risk(self) -> VaultTechnicalRisk | None:
        # Check for vault-specific flags (e.g., xUSD exposure) first
        flags = get_vault_special_flags(self.address)
        if flags & BAD_FLAGS:
            return VaultTechnicalRisk.blacklisted
        return VaultTechnicalRisk.low

    @cached_property
    def euler_metadata(self) -> EulerVaultMetadata:
        return fetch_euler_vault_metadata(self.web3, self.vault_address)

    @property
    def name(self) -> str:
        if self.euler_metadata:
            # Euler metadata might not have an entry for this vault yet
            return self.euler_metadata.get("name", super().name)
        return super().name

    @property
    def description(self) -> str | None:
        if self.euler_metadata:
            return self.euler_metadata.get("description")
        return None

    @property
    def entity(self) -> str | None:
        if self.euler_metadata:
            return self.euler_metadata.get("entity")
        return None

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Euler vault kit vaults never have management fee"""
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get Euler fee.

        - Euler vaults have only fee called "interest fee"
        - This is further split to "governor fee" and "protocol fee" but this distinction is not relevant for the vault user
        - See https://github.com/euler-xyz/euler-vault-kit/blob/5b98b42048ba11ae82fb62dfec06d1010c8e41e6/src/EVault/EVault.sol

        :return:
            None if fee reading is broken
        """

        # https://github.com/euler-xyz/euler-vault-kit/blob/5b98b42048ba11ae82fb62dfec06d1010c8e41e6/src/EVault/IEVault.sol#L378
        fee_call = EncodedCall.from_keccak_signature(
            address=self.address,
            signature=Web3.keccak(text="interestFee()")[0:4],
            function="interestFee",
            data=b"",
            extra_data=None,
        )
        try:
            data = fee_call.call(self.web3, block_identifier)
        except ValueError as e:
            logger.warning(
                "interestFee() read reverted on Euler vault %s: %s",
                self,
                str(e),
                exc_info=e,
            )
            return None

        performance_fee = float(int.from_bytes(data[0:32], byteorder="big") / (10**4))
        return performance_fee

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        return datetime.timedelta(days=0)

    def get_link(self, referral: str | None = None) -> str:
        chain_name = get_chain_name(self.chain_id).lower()
        return f"https://app.euler.finance/earn/{self.vault_address}?network={chain_name}"

    def can_check_redeem(self) -> bool:
        """Euler EVK does NOT support address(0) checks for redemption availability."""
        return False

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Get Euler EVK-specific historical reader with utilisation metrics."""
        return EulerVaultHistoricalReader(self, stateful)

    def fetch_available_liquidity(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Get the amount of denomination token available for immediate withdrawal.

        Uses Euler's `cash()` function which returns the underlying tokens currently
        held by the vault (not lent out).

        :param block_identifier:
            Block to query. Defaults to "latest".

        :return:
            Amount in denomination token units (human-readable Decimal).
        """
        cash_call = EncodedCall.from_keccak_signature(
            address=self.address,
            signature=CASH_SIGNATURE,
            function="cash",
            data=b"",
            extra_data=None,
        )
        try:
            data = cash_call.call(self.web3, block_identifier)
            cash_raw = int.from_bytes(data[0:32], byteorder="big")
            denomination_token = self.denomination_token
            if denomination_token is None:
                return None
            return denomination_token.convert_to_decimals(cash_raw)
        except Exception:
            return None

    def fetch_utilisation_percent(self, block_identifier: BlockIdentifier = "latest") -> Percent | None:
        """Get the percentage of assets currently lent out.

        Utilisation = totalBorrows / (cash + totalBorrows)

        :param block_identifier:
            Block to query. Defaults to "latest".

        :return:
            Utilisation as float between 0.0 and 1.0 (0% to 100%).
        """
        try:
            cash_call = EncodedCall.from_keccak_signature(
                address=self.address,
                signature=CASH_SIGNATURE,
                function="cash",
                data=b"",
                extra_data=None,
            )
            total_borrows_call = EncodedCall.from_keccak_signature(
                address=self.address,
                signature=TOTAL_BORROWS_SIGNATURE,
                function="totalBorrows",
                data=b"",
                extra_data=None,
            )

            cash_data = cash_call.call(self.web3, block_identifier)
            total_borrows_data = total_borrows_call.call(self.web3, block_identifier)

            cash = int.from_bytes(cash_data[0:32], byteorder="big")
            total_borrows = int.from_bytes(total_borrows_data[0:32], byteorder="big")

            total_pool = cash + total_borrows
            if total_pool == 0:
                return 0.0
            return total_borrows / total_pool
        except Exception:
            return None


class EulerEarnVault(ERC4626Vault):
    """EulerEarn metavault support.

    EulerEarn is a protocol for noncustodial risk management on top of accepted ERC-4626 vaults,
    especially the EVK (Euler Vault Kit) vaults. Based on Metamorpho architecture.

    - EulerEarn allows only accepted ERC-4626 vaults to be used as strategies
    - EulerEarn vaults are themselves ERC-4626 vaults
    - One EulerEarn vault is related to one underlying asset
    - Users can supply or withdraw assets at any time, depending on the available liquidity
    - A maximum of 30 strategies can be enabled on a given EulerEarn vault
    - There are 4 different roles: owner, curator, guardian & allocator
    - The vault owner can set a performance fee up to 50% of the generated interest

    Links:

    - GitHub: https://github.com/euler-xyz/euler-earn
    - Documentation: https://docs.euler.finance/developers/euler-earn/
    - Integrator guide: https://docs.euler.finance/developers/euler-earn/integrator-guide/
    - Example vault: https://snowtrace.io/address/0xE1A62FDcC6666847d5EA752634E45e134B2F824B
    """

    def get_risk(self) -> VaultTechnicalRisk | None:
        """EulerEarn vaults have negligible risk due to battle-tested infrastructure.

        Based on Metamorpho architecture with extensive audits.
        However, individual vaults may be blacklisted due to specific issues (e.g., xUSD exposure).
        """
        # Check for vault-specific flags (e.g., xUSD exposure) first
        flags = get_vault_special_flags(self.address)
        if flags & BAD_FLAGS:
            return VaultTechnicalRisk.blacklisted
        return VaultTechnicalRisk.negligible

    def has_custom_fees(self) -> bool:
        """EulerEarn has on-chain readable performance fees."""
        return True

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """EulerEarn vaults do not have management fees.

        Only performance fee is charged on generated interest.
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get EulerEarn performance fee.

        - EulerEarn charges a performance fee on generated interest
        - The fee is stored as a uint96 in WAD (1e18) format
        - Maximum fee is 50% (0.5e18)

        See: https://github.com/euler-xyz/euler-earn/blob/main/src/EulerEarn.sol

        :return:
            Performance fee as a decimal (e.g., 0.10 for 10%), or None if reading fails
        """
        fee_call = EncodedCall.from_keccak_signature(
            address=self.address,
            signature=Web3.keccak(text="fee()")[0:4],
            function="fee",
            data=b"",
            extra_data=None,
        )
        try:
            data = fee_call.call(self.web3, block_identifier)
        except ValueError as e:
            logger.warning(
                "fee() read reverted on EulerEarn vault %s: %s",
                self,
                str(e),
                exc_info=e,
            )
            return None

        # Fee is stored in WAD format (1e18)
        fee_wad = int.from_bytes(data[0:32], byteorder="big")
        performance_fee = fee_wad / (10**18)
        return performance_fee

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """EulerEarn vaults allow instant withdrawals.

        Users can withdraw at any time depending on available liquidity.
        """
        return datetime.timedelta(days=0)

    def get_link(self, referral: str | None = None) -> str:
        """Get link to EulerEarn vault on Euler app.

        EulerEarn vaults are shown on the Euler Finance app under the "earn" section.
        """
        chain_name = get_chain_name(self.chain_id).lower()
        return f"https://app.euler.finance/earn/{self.vault_address}?network={chain_name}"

    def get_supply_queue_length(self, block_identifier: BlockIdentifier = "latest") -> int | None:
        """Get the number of strategies in the supply queue.

        :return:
            Number of strategies in the supply queue, or None if reading fails
        """
        supply_queue_length_call = EncodedCall.from_keccak_signature(
            address=self.address,
            signature=Web3.keccak(text="supplyQueueLength()")[0:4],
            function="supplyQueueLength",
            data=b"",
            extra_data=None,
        )
        try:
            data = supply_queue_length_call.call(self.web3, block_identifier)
        except ValueError as e:
            logger.warning(
                "supplyQueueLength() read reverted on EulerEarn vault %s: %s",
                self,
                str(e),
                exc_info=e,
            )
            return None

        return int.from_bytes(data[0:32], byteorder="big")

    def get_withdraw_queue_length(self, block_identifier: BlockIdentifier = "latest") -> int | None:
        """Get the number of strategies in the withdraw queue.

        :return:
            Number of strategies in the withdraw queue, or None if reading fails
        """
        withdraw_queue_length_call = EncodedCall.from_keccak_signature(
            address=self.address,
            signature=Web3.keccak(text="withdrawQueueLength()")[0:4],
            function="withdrawQueueLength",
            data=b"",
            extra_data=None,
        )
        try:
            data = withdraw_queue_length_call.call(self.web3, block_identifier)
        except ValueError as e:
            logger.warning(
                "withdrawQueueLength() read reverted on EulerEarn vault %s: %s",
                self,
                str(e),
                exc_info=e,
            )
            return None

        return int.from_bytes(data[0:32], byteorder="big")

    def get_curator(self, block_identifier: BlockIdentifier = "latest") -> str | None:
        """Get the curator address for this vault.

        The curator can manage vault parameters and strategy allocations.

        :return:
            Curator address, or None if reading fails
        """
        curator_call = EncodedCall.from_keccak_signature(
            address=self.address,
            signature=Web3.keccak(text="curator()")[0:4],
            function="curator",
            data=b"",
            extra_data=None,
        )
        try:
            data = curator_call.call(self.web3, block_identifier)
        except ValueError as e:
            logger.warning(
                "curator() read reverted on EulerEarn vault %s: %s",
                self,
                str(e),
                exc_info=e,
            )
            return None

        return Web3.to_checksum_address(data[12:32])

    def can_check_redeem(self) -> bool:
        """EulerEarn does NOT support address(0) checks for redemption availability."""
        return False

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Get EulerEarn-specific historical reader with utilisation metrics."""
        return EulerEarnVaultHistoricalReader(self, stateful)

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


#: Keccak signature for EulerEarn fee
FEE_SIGNATURE = Web3.keccak(text="fee()")[0:4]


class EulerEarnVaultHistoricalReader(ERC4626HistoricalReader):
    """Read EulerEarn vault core data + utilisation metrics.

    For EulerEarn (metavault):
    - Idle assets = asset().balanceOf(vault)
    - Utilisation = (totalAssets - idle) / totalAssets

    .. warning::

        EulerEarn vaults have maxDeposit() disabled because it uses excessive gas.

        **Research notes on TelosC Surge vault (0xa9C251F8304b1B3Fc2b9e8fcae78D94Eff82Ac66)**:

        The EulerEarn architecture (based on Metamorpho) has loop-heavy operations that
        cause maxDeposit(address(0)) to consume 21-36M gas - nearly the entire Plasma
        block gas limit of 36M.

        The gas consumption comes from:

        1. **_maxDeposit() in EulerEarnVaultModule.sol** iterates through supplyQueue
           to calculate maxTotalDeposit for each strategy

        2. **_convertToShares() in EulerEarnBase.sol** triggers _accruedFeeAndAssets()
           which loops through the entire withdrawQueue to calculate accrued fees

        3. **Maximum queue length of 30 strategies** (defined in ConstantsLib.MAX_QUEUE_LENGTH)
           means up to 60 external contract calls per maxDeposit() invocation

        Source code references:

        - EulerEarnVaultModule.sol: _maxDeposit() at supplyQueue iteration
        - EulerEarnBase.sol: _accruedFeeAndAssets() at withdrawQueue iteration
        - ConstantsLib.sol: MAX_QUEUE_LENGTH = 30

        Plasmascan: https://plasmascan.to/address/0xa9C251F8304b1B3Fc2b9e8fcae78D94Eff82Ac66

        Since Multicall3 does not support per-call gas limits, and calling maxDeposit()
        would consume the entire block gas limit, we unconditionally skip this call
        for all EulerEarn vaults.
    """

    def should_skip_call(self, function_name: str) -> bool:
        """Check if a specific function call should be skipped.

        EulerEarn vaults always skip maxDeposit due to excessive gas usage.
        See class docstring for detailed research notes.
        """
        if function_name == "maxDeposit":
            return True
        return super().should_skip_call(function_name)

    def get_warmup_calls(self) -> Iterable[tuple[str, callable, any]]:
        """Yield warmup calls for EulerEarn vaults.

        Includes base ERC-4626 calls (except maxDeposit) plus idle_assets and fee calls.
        maxDeposit is excluded because EulerEarn vaults use excessive gas for this call.
        """
        # Yield base calls but filter out maxDeposit
        for warmup_item in super().get_warmup_calls():
            function_name = warmup_item[0]
            if function_name == "maxDeposit":
                continue  # Skip maxDeposit - uses excessive gas on EulerEarn
            yield warmup_item

        denomination_token = self.vault.denomination_token
        if denomination_token is not None:
            idle_call = denomination_token.contract.functions.balanceOf(self.vault.address)
            yield ("idle_assets", lambda: idle_call.call(), idle_call)

        vault_contract = self.vault.vault_contract
        fee_call = vault_contract.functions.fee()
        yield ("fee", lambda: fee_call.call(), fee_call)

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        yield from self.construct_core_erc_4626_multicall()
        yield from self.construct_utilisation_calls()
        yield from self.construct_fee_calls()

    def construct_utilisation_calls(self) -> Iterable[EncodedCall]:
        """Add idle assets call for utilisation calculation.

        Note: We use the asset token's balanceOf to get idle assets.
        This requires an additional call to the asset token contract.
        """
        if self.should_skip_call("idle_assets"):
            return

        denomination_token = self.vault.denomination_token
        if denomination_token is None:
            return

        # Get idle assets via balanceOf on the asset token
        idle_call = EncodedCall.from_contract_call(
            denomination_token.contract.functions.balanceOf(self.vault.address),
            extra_data={
                "function": "idle_assets",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )
        yield idle_call

    def construct_fee_calls(self) -> Iterable[EncodedCall]:
        """Add EulerEarn fee call."""
        if self.should_skip_call("fee"):
            return

        fee_call = EncodedCall.from_keccak_signature(
            address=self.vault.address,
            signature=FEE_SIGNATURE,
            function="fee",
            data=b"",
            extra_data={"vault": self.vault.address},
            first_block_number=self.first_block,
        )
        yield fee_call

    def process_utilisation_result(
        self,
        call_by_name: dict[str, EncodedCallResult],
        total_assets: Decimal | None,
    ) -> tuple[Decimal | None, Percent | None]:
        """Decode EulerEarn utilisation data.

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

        share_price, total_supply, total_assets, errors, max_deposit = self.process_core_erc_4626_result(call_by_name)
        available_liquidity, utilisation = self.process_utilisation_result(call_by_name, total_assets)

        # Get performance fee if available
        fee_result = call_by_name.get("fee")
        performance_fee = None
        if fee_result is not None:
            fee_wad = int.from_bytes(fee_result.result[0:32], byteorder="big")
            performance_fee = fee_wad / (10**18)

        return VaultHistoricalRead(
            vault=self.vault,
            block_number=block_number,
            timestamp=timestamp,
            share_price=share_price,
            total_assets=total_assets,
            total_supply=total_supply,
            performance_fee=performance_fee,
            management_fee=0.0,
            errors=errors,
            max_deposit=max_deposit,
            available_liquidity=available_liquidity,
            utilisation=utilisation,
        )
