"""Historical reader for Mellow Core Vaults."""

from __future__ import annotations

import datetime
from collections.abc import Iterable
from typing import TYPE_CHECKING

from eth_defi.erc_4626.vault import VaultReaderState
from eth_defi.event_reader.conversion import convert_int256_bytes_to_int
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader

if TYPE_CHECKING:
    from eth_defi.mellow.vault import MellowVault


class MellowVaultHistoricalReader(VaultHistoricalReader):
    """Read the currently supported Mellow historical state.

    The first implementation intentionally reads only the tokenised
    ``ShareManager.totalSupply()``. Mellow share price and TVL depend on oracle
    report orientation and asset/subvault accounting that must be pinned with a
    fixed-block test before production use.
    """

    def __init__(self, vault: MellowVault, stateful: bool):  # noqa: FBT001
        """Create a Mellow historical reader.

        :param vault:
            Mellow vault adapter.

        :param stateful:
            Whether to attach adaptive reader state used by the shared
            historical multicaller.
        """

        super().__init__(vault)
        if stateful:
            self.reader_state = VaultReaderState(vault)
        else:
            self.reader_state = None

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Construct Mellow historical multicalls.

        :return:
            Multicall batch reading ``ShareManager.totalSupply()``.
        """

        yield EncodedCall.from_contract_call(
            self.vault.share_manager_contract.functions.totalSupply(),
            extra_data={
                "function": "share_manager_total_supply",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        """Convert Mellow multicall results to a vault price row.

        :param block_number:
            Historical block number.

        :param timestamp:
            Naive UTC block timestamp.

        :param call_results:
            Multicall results for calls from :py:meth:`construct_multicalls`.

        :return:
            Partial :py:class:`VaultHistoricalRead` with explicit unsupported
            price/TVL errors.
        """

        total_supply = None
        errors = [
            "Mellow share price and TVL require oracle report orientation and subvault accounting confirmation",
        ]

        for result in call_results:
            if result.call.extra_data.get("function") == "share_manager_total_supply":
                if result.success:
                    raw_total_supply = convert_int256_bytes_to_int(result.result)
                    total_supply = self.vault.share_token.convert_to_decimals(raw_total_supply)
                else:
                    errors.append("share_manager_total_supply call failed")

        return VaultHistoricalRead(
            vault=self.vault,
            block_number=block_number,
            timestamp=timestamp,
            share_price=None,
            total_assets=None,
            total_supply=total_supply,
            performance_fee=None,
            management_fee=None,
            errors=errors,
        )
