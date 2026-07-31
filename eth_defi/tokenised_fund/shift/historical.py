"""Historical valuation reads for ShiftVault products."""

# Historical reader API follows :class:`VaultHistoricalReader`.
# ruff: noqa: FBT001

import datetime
from collections.abc import Iterable
from decimal import Decimal
from typing import TYPE_CHECKING

from eth_defi.erc_4626.vault import VaultReaderState
from eth_defi.event_reader.conversion import convert_int256_bytes_to_int
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader

if TYPE_CHECKING:
    from eth_defi.tokenised_fund.shift.vault import ShiftVault


class ShiftVaultHistoricalReader(VaultHistoricalReader):
    """Read Shift's TVL-feed share price and ERC-20 supply at historical blocks."""

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
            Shift ``getSharePrice()`` and ERC-20 ``totalSupply()`` calls.
        """

        yield EncodedCall.from_contract_call(
            self.vault.shift_contract.functions.getSharePrice(),
            extra_data={"function": "getSharePrice", "vault": self.vault.address},
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

        total_supply: Decimal | None = None
        share_price: Decimal | None = None
        state_result: EncodedCallResult | None = None
        errors: list[str] = []
        for result in call_results:
            function = result.call.extra_data.get("function")
            if not result.success:
                errors.append(f"ShiftVault {function} call failed")
                continue
            raw_value = convert_int256_bytes_to_int(result.result)
            if function == "getSharePrice":
                share_price = Decimal(raw_value) / Decimal(10**self.vault.tvl_feed_decimals)
                state_result = result
            elif function == "totalSupply":
                total_supply = self.vault.share_token.convert_to_decimals(raw_value)

        total_assets = share_price * total_supply if share_price is not None and total_supply is not None else None
        if self.reader_state is not None and state_result is not None and total_assets is not None:
            self.reader_state.on_called(state_result, total_assets=total_assets, share_price=share_price)

        return VaultHistoricalRead(
            vault=self.vault,
            block_number=block_number,
            timestamp=timestamp,
            share_price=share_price,
            total_assets=total_assets,
            total_supply=total_supply,
            performance_fee=self.vault.get_performance_fee(block_number),
            management_fee=self.vault.get_management_fee(block_number),
            errors=errors or None,
            deposits_open=False,
            redemption_open=False,
        )
