"""Historical valuation reads for ShiftVault products."""

# Historical reader API follows :class:`VaultHistoricalReader`.
# ruff: noqa: FBT001

import datetime
from collections.abc import Iterable
from typing import TYPE_CHECKING

from eth_defi.erc_4626.vault import VaultReaderState
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader

if TYPE_CHECKING:
    from eth_defi.erc_4626.vault_protocol.shift.vault import ShiftVault


class ShiftVaultHistoricalReader(VaultHistoricalReader):
    """Read Shift's published valuation and ERC-20 supply at historical blocks."""

    def __init__(self, vault: "ShiftVault", stateful: bool) -> None:
        """Create a historical ShiftVault reader.

        :param vault:
            ShiftVault adapter to read.
        :param stateful:
            Whether adaptive reader state should be retained.
        """

        super().__init__(vault)
        self.reader_state = VaultReaderState(vault) if stateful else None

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Build share-price and supply reads.

        :return:
            Shift feed ``getLastTvl()`` and ERC-20 ``totalSupply()`` calls.
        """

        yield EncodedCall.from_contract_call(
            self.vault.tvl_feed.functions.getLastTvl(),
            extra_data={"function": "getLastTvl", "vault": self.vault.address},
            first_block_number=self.first_block,
        )
        yield EncodedCall.from_contract_call(
            self.vault.share_token.contract.functions.totalSupply(),
            extra_data={"function": "totalSupply", "vault": self.vault.address},
            first_block_number=self.first_block,
        )

    def process_result(self, block_number: int, timestamp: datetime.datetime, call_results: list[EncodedCallResult]) -> VaultHistoricalRead:
        """Convert Shift historical calls into a price row.

        :param block_number:
            Sampled EVM block.
        :param timestamp:
            Naive UTC timestamp for the sampled block.
        :param call_results:
            Responses from :meth:`construct_multicalls`.
        :return:
            ShiftVault valuation and diagnostic data.
        """

        results = {result.call.extra_data["function"]: result for result in call_results}
        try:
            for name, size in (("getLastTvl", 96), ("totalSupply", 32)):
                result = results.get(name)
                if result is None or not result.success or result.unavailable_error or result.block_identifier != block_number or len(result.result) != size:
                    raise ValueError(f"Unavailable Shift {name} at block {block_number}")
            feed_result = results["getLastTvl"]
            fields = self.vault.web3.codec.decode(["uint256", "uint256", "uint256"], feed_result.result)
            valuation = self.vault.decode_valuation(fields, timestamp)
            total_supply = self.vault.share_token.convert_to_decimals(int.from_bytes(results["totalSupply"].result))
        except (ValueError, OverflowError, OSError) as error:
            return VaultHistoricalRead(
                vault=self.vault,
                block_number=block_number,
                timestamp=timestamp,
                share_price=None,
                total_assets=None,
                total_supply=None,
                performance_fee=None,
                management_fee=None,
                errors=[str(error)],
            )

        if self.reader_state is not None:
            self.reader_state.on_called(feed_result, total_assets=valuation.total_assets, share_price=valuation.share_price)

        # Keep the original publication time when carrying a quote forward.
        # During the 25 November 2026 ltLLP outage, block 527445008 still
        # carried the 13:00 quote. Dating it at 14:14 would hide the outage.
        return VaultHistoricalRead(
            vault=self.vault,
            block_number=block_number,
            timestamp=timestamp,
            share_price=valuation.share_price,
            total_assets=valuation.total_assets,
            total_supply=total_supply,
            performance_fee=self.vault.get_performance_fee(block_number),
            management_fee=self.vault.get_management_fee(block_number),
            errors=None,
            nav_timestamp=valuation.published_at,
        )
