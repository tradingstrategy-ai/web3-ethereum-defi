"""Historical reads for Theo iToken share tokens."""

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
    from eth_defi.tokenised_fund.theo.vault import TheoITokenVault


class TheoITokenHistoricalReader(VaultHistoricalReader):
    """Read thBILL supply without fabricating NAV or TVL.

    Theo's iToken conversion interface returns a basket of assets rather than
    one scalar price. A price would need reviewed basket valuations, so this
    reader retains only the verifiable ERC-20 supply observation.
    """

    def __init__(self, vault: "TheoITokenVault", stateful: bool):
        """Create a supply-only iToken reader.

        :param vault: Theo iToken adapter.
        :param stateful: Whether to retain supply-only adaptive reader state.
        """

        super().__init__(vault)
        self.reader_state = VaultReaderState(vault) if stateful else None

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Construct the ERC-20 supply read.

        :return: A ``totalSupply()`` multicall.
        """

        yield EncodedCall.from_contract_call(
            self.vault.share_token.contract.functions.totalSupply(),
            extra_data={"function": "totalSupply", "vault": self.vault.address},
            first_block_number=self.first_block,
        )

    def process_result(self, block_number: int, timestamp: datetime.datetime, call_results: list[EncodedCallResult]) -> VaultHistoricalRead:
        """Convert a supply read to an explicitly unpriced history row.

        :param block_number: Sampled block.
        :param timestamp: Naive UTC timestamp for the sampled block.
        :param call_results: Responses to :meth:`construct_multicalls`.
        :return: Supply-only thBILL observation.
        """

        total_supply: Decimal | None = None
        state_result: EncodedCallResult | None = None
        errors = ["Theo thBILL iToken has no reviewed scalar NAV/share source; basket valuation is not configured"]
        for result in call_results:
            if result.call.extra_data.get("function") != "totalSupply":
                continue
            if result.success:
                total_supply = self.vault.share_token.convert_to_decimals(convert_int256_bytes_to_int(result.result))
                state_result = result
            else:
                errors.append("Theo thBILL totalSupply call failed")

        if self.reader_state is not None and state_result is not None:
            self.reader_state.on_unpriced_call(state_result)

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
            deposits_open=False,
            redemption_open=False,
        )
