"""Morpho vault reading implementation."""
import datetime
from typing import Iterable

from eth_typing import BlockIdentifier
from web3 import Web3

from eth_defi.erc_4626.vault import ERC4626HistoricalReader, ERC4626Vault
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.vault.base import VaultHistoricalReader, VaultHistoricalRead


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
            extra_data = {
                "vault": self.vault.address,
            }
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

        # Decode common variables
        share_price, total_supply, total_assets = self.process_core_erc_4626_result(call_by_name)
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
        )



class MorphoVault(ERC4626Vault):
    """Morpho vault support.

    - Add specialised reader with fees support
    - `See an example vault here <https://app.gauntlet.xyz/vaults/eth:0x4881ef0bf6d2365d3dd6499ccd7532bcdbce0658>`__
    - `Example contract <https://basescan.org/address/0x6b13c060F13Af1fdB319F52315BbbF3fb1D88844#readContract>`__
    """

    def get_historical_reader(self) -> VaultHistoricalReader:
        return MorphoVaultHistoricalReader(self)

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Morpho vaults have no management fee"""
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float:
        """Get Morpho fee"""
        fee_call = EncodedCall.from_keccak_signature(
            address=self.address,
            signature=Web3.keccak(text="fee()")[0:4],
            function="fee",
            data=b"",
            extra_data = {
                "vault": self.address,
            }
        )
        data = fee_call.call(self.web3, block_identifier)
        performance_fee = int.from_bytes(data[0:32], byteorder="big") / (10**18)
        return performance_fee
