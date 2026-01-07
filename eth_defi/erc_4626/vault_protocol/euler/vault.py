"""Euler Vault Kit and EulerEarn integrations.

- EVK (Euler Vault Kit): Credit vaults with borrowing functionality
  https://github.com/euler-xyz/euler-vault-kit
  Metadata repo: https://github.com/euler-xyz/euler-labels/blob/master/130/vaults.json

- EulerEarn: Metamorpho-based metavault for yield aggregation on top of EVK
  https://github.com/euler-xyz/euler-earn
  Documentation: https://docs.euler.finance/developers/euler-earn/
"""

import datetime
from functools import cached_property
import logging

from web3 import Web3

from eth_typing import BlockIdentifier

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.euler.offchain_metadata import EulerVaultMetadata, fetch_euler_vault_metadata
from eth_defi.event_reader.multicall_batcher import EncodedCall
from eth_defi.vault.base import VaultTechnicalRisk
from eth_defi.vault.flag import BAD_FLAGS, get_vault_special_flags

logger = logging.getLogger(__name__)


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
        return self.euler_metadata.get("description")

    @property
    def entity(self) -> str | None:
        return self.euler_metadata.get("entity")

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
