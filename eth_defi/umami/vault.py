"""Umami gmUSDC vault support.
"""

from functools import cached_property
import logging

from web3 import Web3
from web3.contract import Contract
from eth_typing import BlockIdentifier

from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.event_reader.multicall_batcher import EncodedCall

logger = logging.getLogger(__name__)


class UmamiVault(ERC4626Vault):
    """Umami vault support.

    -
    """

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment."""
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="umami/AssetVault.json",
        )

    def fetch_aggregate_vault(self) -> Contract:
        addr = self.vault_contract.functions.aggregateVault().call()
        return get_deployed_erc_4626_contract(
            self.web3,
            addr,
            abi_fname="umami/AggregateVault.json",
        )

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
