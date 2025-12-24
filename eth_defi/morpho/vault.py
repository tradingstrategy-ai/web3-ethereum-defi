"""Morpho vault reading implementation."""

import datetime
from typing import Iterable
import logging

from eth_typing import BlockIdentifier
from web3 import Web3

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.vault import ERC4626HistoricalReader, ERC4626Vault
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.vault.base import VaultHistoricalReader, VaultHistoricalRead
from eth_defi.vault.risk import VaultTechnicalRisk

logger = logging.getLogger(__name__)


class MorphoVaultHistoricalReader(ERC4626HistoricalReader):
    """Read Morpho vault core data + fees"""

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        yield from self.construct_core_erc_4626_multicall()
        yield from self.construct_fee_calls()

    def construct_fee_calls(self) -> Iterable[EncodedCall]:
        # Morpo has single fee variable
        # https://basescan.org/address/0x6b13c060F13Af1fdB319F52315BbbF3fb1D88844#readContract
        fee_call = EncodedCall.from_keccak_signature(
            address=self.vault.address,
            signature=Web3.keccak(text="fee()")[0:4],
            function="fee",
            data=b"",
            extra_data={
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
            state=self.reader_state,
        )
        yield fee_call

    def process_morpho_fee_result(self, call_by_name: dict[str, EncodedCallResult]) -> float:
        """Decode IPOR specific data."""

        # https://app.gauntlet.xyz/vaults/eth:0x4881ef0bf6d2365d3dd6499ccd7532bcdbce0658
        # 100000000000000000
        data = call_by_name["fee"].result
        performance_fee = int.from_bytes(data[0:32], byteorder="big") / (10**18)
        return performance_fee

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        call_by_name = self.dictify_multicall_results(block_number, call_results)
        assert all(c.block_identifier == block_number for c in call_by_name.values()), "Sanity check for call block numbering"

        # Decode common variables
        share_price, total_supply, total_assets, errors = self.process_core_erc_4626_result(call_by_name)
        performance_fee = self.process_morpho_fee_result(call_by_name)

        # Subclass
        return VaultHistoricalRead(
            vault=self.vault,
            block_number=block_number,
            timestamp=timestamp,
            share_price=share_price,
            total_assets=total_assets,
            total_supply=total_supply,
            performance_fee=performance_fee,
            management_fee=0,
            errors=errors,
        )


class MorphoVault(ERC4626Vault):
    """Morpho vault support.

    - Add specialised reader with fees support
    - `See an example vault here <https://app.gauntlet.xyz/vaults/eth:0x4881ef0bf6d2365d3dd6499ccd7532bcdbce0658>`__
    - `Example contract <https://basescan.org/address/0x6b13c060F13Af1fdB319F52315BbbF3fb1D88844#readContract>`__
    """

    def get_historical_reader(self, stateful) -> VaultHistoricalReader:
        return MorphoVaultHistoricalReader(self, stateful)

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Morpho vaults have no management fee"""
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get Morpho fee.

        :return:
            None if fee reading is broken
        """
        fee_call = EncodedCall.from_keccak_signature(
            address=self.address,
            signature=Web3.keccak(text="fee()")[0:4],
            function="fee",
            data=b"",
            extra_data={
                "vault": self.address,
            },
        )
        try:
            data = fee_call.call(self.web3, block_identifier)
        except ValueError as e:
            logger.warning(
                "Performance read reverted on Morpho vault %s: %s",
                self,
                str(e),
            )
            return None

        performance_fee = int.from_bytes(data[0:32], byteorder="big") / (10**18)
        return performance_fee

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        return datetime.timedelta(days=0)

    def get_link(self, referral: str | None = None) -> str:
        chain_name = get_chain_name(self.chain_id).lower()
        return f"https://app.morpho.org/{chain_name}/vault/{self.vault_address}/"
