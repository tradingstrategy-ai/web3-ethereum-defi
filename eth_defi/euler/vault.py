"""Euler Vault Kit specific integrations.

- Metadata repo https://github.com/euler-xyz/euler-labels/blob/master/130/vaults.json
"""

import datetime
from functools import cached_property
import logging

from web3 import Web3

from eth_typing import BlockIdentifier

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.euler.offchain_metadata import EulerVaultMetadata, fetch_euler_vault_metadata
from eth_defi.event_reader.multicall_batcher import EncodedCall
from eth_defi.vault.base import VaultTechnicalRisk

logger = logging.getLogger(__name__)


class EulerVault(ERC4626Vault):
    """Euler vault support.

    - Handle special offchain metadata
    - Example vault https://etherscan.io/address/0x1e548CfcE5FCF17247E024eF06d32A01841fF404#code
    - Euler ABIs https://github.com/euler-xyz/euler-interfaces

    TODO: Fees
    """

    def get_risk(self) -> VaultTechnicalRisk | None:
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
