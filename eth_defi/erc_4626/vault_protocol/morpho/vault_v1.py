"""Morpho Vault V1 (MetaMorpho) support.

Morpho V1 vaults directly integrate with Morpho markets and are identified
by the ``MORPHO()`` function call.

- `Morpho documentation <https://docs.morpho.org/>`__
- `Example vault on Base <https://basescan.org/address/0x6b13c060F13Af1fdB319F52315BbbF3fb1D88844>`__
- `Example on Gauntlet <https://app.gauntlet.xyz/vaults/eth:0x4881ef0bf6d2365d3dd6499ccd7532bcdbce0658>`__
"""

import datetime
from typing import Iterable
import logging

from eth_typing import BlockIdentifier
from web3 import Web3

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.vault import ERC4626HistoricalReader, ERC4626Vault
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.vault.base import VaultHistoricalReader, VaultHistoricalRead

logger = logging.getLogger(__name__)


class MorphoV1VaultHistoricalReader(ERC4626HistoricalReader):
    """Read Morpho V1 vault core data + fees."""

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        yield from self.construct_core_erc_4626_multicall()
        yield from self.construct_fee_calls()

    def construct_fee_calls(self) -> Iterable[EncodedCall]:
        # Morpho V1 has single fee variable
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
        """Decode Morpho V1 fee data."""

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
        share_price, total_supply, total_assets, errors, max_deposit = self.process_core_erc_4626_result(call_by_name)
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
            max_deposit=max_deposit,
        )


class MorphoV1Vault(ERC4626Vault):
    """Morpho Vault V1 (MetaMorpho) support.

    Morpho V1 vaults directly integrate with Morpho markets. They are identified
    by the ``MORPHO()`` function call which returns the address of the Morpho
    protocol contract.

    - `Morpho documentation <https://docs.morpho.org/>`__
    - `Example vault on Base <https://basescan.org/address/0x6b13c060F13Af1fdB319F52315BbbF3fb1D88844>`__
    - `Example on Gauntlet <https://app.gauntlet.xyz/vaults/eth:0x4881ef0bf6d2365d3dd6499ccd7532bcdbce0658>`__

    See also :py:class:`eth_defi.erc_4626.vault_protocol.morpho.vault_v2.MorphoV2Vault`
    for the newer adapter-based architecture.
    """

    def get_historical_reader(self, stateful) -> VaultHistoricalReader:
        return MorphoV1VaultHistoricalReader(self, stateful)

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Morpho V1 vaults have no management fee."""
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get Morpho V1 performance fee.

        :return:
            Performance fee as a decimal, or None if fee reading is broken
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
                "Performance read reverted on Morpho V1 vault %s: %s",
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


# Backwards compatibility aliases
MorphoVaultHistoricalReader = MorphoV1VaultHistoricalReader
MorphoVault = MorphoV1Vault
