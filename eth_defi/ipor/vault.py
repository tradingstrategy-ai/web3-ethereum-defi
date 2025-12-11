"""IPOR vault reading implementation."""

import datetime
from functools import cached_property
from typing import Iterable

from cachetools import cached
from web3 import Web3
from web3.contract import Contract
from web3.types import BlockIdentifier

from eth_defi.abi import ZERO_ADDRESS_STR, get_deployed_contract
from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.vault import ERC4626HistoricalReader, ERC4626Vault
from eth_defi.event_reader.conversion import convert_int256_bytes_to_int
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult, MultiprocessMulticallReader
from eth_defi.vault.base import VaultHistoricalReader, VaultHistoricalRead
from eth_defi.vault.risk import VaultTechnicalRisk

#: function getPerformanceFeeData() external view returns (PlasmaVaultStorageLib.PerformanceFeeData memory feeData);
#: PlasmaVaultLib.sol
#: https://etherscan.io/address/0xabab980f0ecb232d52f422c6b68d25c3d0c18e3e#code
PERFORMANCE_FEE_CALL_SIGNATURE = Web3.keccak(text="getPerformanceFeeData()")[0:4]


#: function getManagementFeeData() external view returns (PlasmaVaultStorageLib.PerformanceFeeData memory feeData);
#: https://etherscan.io/address/0xabab980f0ecb232d52f422c6b68d25c3d0c18e3e#code
MANAGEGEMENT_FEE_CALL_SIGNATURE = Web3.keccak(text="getManagementFeeData()")[0:4]


class IPORVaultHistoricalReader(ERC4626HistoricalReader):
    """Read IPOR vault core data + fees"""

    def get_risk(self) -> VaultTechnicalRisk | None:
        return VaultTechnicalRisk.low

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        yield from self.construct_core_erc_4626_multicall()
        yield from self.construct_fee_calls()

    def construct_fee_calls(self) -> Iterable[EncodedCall]:
        performance_fee_call = EncodedCall.from_keccak_signature(
            address=self.vault.address,
            signature=PERFORMANCE_FEE_CALL_SIGNATURE,
            function="getPerformanceFeeData",
            data=b"",
            extra_data={
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )
        yield performance_fee_call

        management_fee_call = EncodedCall.from_keccak_signature(
            address=self.vault.address,
            signature=MANAGEGEMENT_FEE_CALL_SIGNATURE,
            function="getManagementFeeData",
            data=b"",
            extra_data={
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )
        yield management_fee_call

    def process_ipor_fee_result(self, call_by_name: dict[str, EncodedCallResult]) -> tuple:
        """Decode IPOR specific data."""

        # File 21 of 47 : PlasmaVaultStorageLib.sol
        #     /// @custom:storage-location erc7201:io.ipor.PlasmaVaultPerformanceFeeData
        #     struct PerformanceFeeData {
        #         address feeManager;
        #         uint16 feeInPercentage;
        #     }
        data = call_by_name["getPerformanceFeeData"].result
        performance_fee = int.from_bytes(data[32:64], byteorder="big") / 10_000

        #
        #     /// @custom:storage-location erc7201:io.ipor.PlasmaVaultManagementFeeData
        #     struct ManagementFeeData {
        #         address feeManager;
        #         uint16 feeInPercentage;
        #         uint32 lastUpdateTimestamp;
        #     }
        #
        data = call_by_name["getManagementFeeData"].result
        management_fee = int.from_bytes(data[32:64], byteorder="big") / 10_000

        return performance_fee, management_fee

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        call_by_name = self.dictify_multicall_results(block_number, call_results)

        # Decode common variables
        share_price, total_supply, total_assets, errors = self.process_core_erc_4626_result(call_by_name)
        performance_fee, management_fee = self.process_ipor_fee_result(call_by_name)

        # Subclass
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
        )


class IPORVault(ERC4626Vault):
    """IPOR vault support.

    - Add specialised reader with fees support

    - `See Fusion vaults here <https://app.ipor.io/fusion>`__

    - `Example contract <https://etherscan.io/address/0xabab980f0ecb232d52f422c6b68d25c3d0c18e3e#code>`__
      and ` Example vault <https://app.ipor.io/fusion/base/0x45aa96f0b3188d47a1dafdbefce1db6b37f58216>`__

    - `IPOR custom error codes <https://www.codeslaw.app/contracts/ethereum/0x1f8397de7c32cc7f042477326892953ca102ded0?tab=abi>`__ like `0x1425ea42` ABI-decoded
    """

    @cached_property
    def plasma_vault(self) -> Contract:
        """Get IPOR's proprietary PlasmaVault implementation."""
        #
        return get_deployed_contract(
            self.web3,
            fname="ipor/PlasmaVaultBase.json",
            address=self.vault_address,
        )

    @cached_property
    def access_manager(self) -> Contract | None:
        """Get IPOR's contract managing vault access rules.

        - Redemption delay, and such
        """
        plasma_vault = self.plasma_vault

        try:
            access_manager = plasma_vault.functions.getAccessManagerAddress().call()
        except ValueError:
            return None

        return get_deployed_contract(
            self.web3,
            fname="ipor/AccessManager.json",
            address=access_manager,
        )

    def get_historical_reader(self, stateful) -> VaultHistoricalReader:
        return IPORVaultHistoricalReader(self, stateful)

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Get the current management fee as a percent.

        :return:
            0.1 = 10%
        """
        management_fee_call = EncodedCall.from_keccak_signature(
            address=self.address,
            function="getPerformanceFeeData",
            signature=MANAGEGEMENT_FEE_CALL_SIGNATURE,
            data=b"",
            extra_data=None,
        )
        data = management_fee_call.call(self.web3, block_identifier)
        management_fee = int.from_bytes(data[32:64], byteorder="big") / 10_000
        return management_fee

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float:
        """Get the current performancae fee as a percent.

        :return:
            0.1 = 10%
        """
        performance_fee_call = EncodedCall.from_keccak_signature(
            address=self.address,
            function="getPerformanceFeeData",
            signature=PERFORMANCE_FEE_CALL_SIGNATURE,
            data=b"",
            extra_data=None,
        )
        data = performance_fee_call.call(self.web3, block_identifier=block_identifier)
        performance_fee = int.from_bytes(data[32:64], byteorder="big") / 10_000
        return performance_fee

    def get_redemption_delay(self) -> datetime.timedelta | None:
        """Get the redemption delay for the vault.

        :return: Redemption delay as a timedelta.
        """
        # IPOR vaults do not have a redemption delay
        # https://basescan.org/address/0x187937aab9b2d57D606D0C3fB98816301fcE0d1f#readContract
        access_manager = self.access_manager
        if not access_manager:
            # Buggy vault without access manager
            return None
        seconds = access_manager.functions.REDEMPTION_DELAY_IN_SECONDS().call()
        return datetime.timedelta(seconds=seconds)

    def get_redemption_delay_over(self, address: str) -> datetime.datetime | None:
        """Get the redemption delay left for an account.

        :return: When the account can redeem.
        """
        # IPOR vaults do not have a redemption delay
        # https://basescan.org/address/0x187937aab9b2d57D606D0C3fB98816301fcE0d1f#readContract
        access_manager = self.access_manager
        if not access_manager:
            # Buggy vault without access manager
            return None
        unix_timestamp = access_manager.functions.getAccountLockTime(address).call()
        return datetime.datetime.fromtimestamp(unix_timestamp, tz=datetime.timezone.utc).replace(tzinfo=None)

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        return self.get_redemption_delay()

    # https://app.ipor.io/fusion/arbitrum/0x4c4f752fa54dafb6d51b4a39018271c90ba1156f
    def get_link(self, referral: str | None = None) -> str:
        chain_name = get_chain_name(self.chain_id).lower()
        return f"https://app.ipor.io/fusion/{chain_name}/{self.vault_address_checksumless}"
