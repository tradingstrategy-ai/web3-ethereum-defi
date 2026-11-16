"""Historical Multicall reads for Wildcat markets.

Follows :py:class:`eth_defi.erc_4626.vault.ERC4626HistoricalReader`. Two
calls per sampled block are enough: ``currentState()`` holds the accrued scale
factor, scaled supply, deposit cap and pending withdrawals, and
``totalAssets()`` holds the market's cash.
"""

# Historical reader API follows :class:`VaultHistoricalReader`.
# ruff: noqa: FBT001

import datetime
import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING

from eth_defi.erc_4626.vault import VaultPollFrequency, VaultReaderState
from eth_defi.erc_4626.vault_protocol.wildcat.constants import WILDCAT_READ_INTERVAL
from eth_defi.erc_4626.vault_protocol.wildcat.state import WildcatMarketState
from eth_defi.event_reader.conversion import convert_int256_bytes_to_int
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader

if TYPE_CHECKING:
    from eth_defi.erc_4626.vault_protocol.wildcat.vault import WildcatMarket

logger = logging.getLogger(__name__)


class WildcatMarketReaderState(VaultReaderState):
    """Adaptive reader state that reads a Wildcat market at most daily."""

    def get_frequency(self) -> tuple[VaultPollFrequency, datetime.timedelta | None]:
        """Return the shared adaptive cadence, slowed to at most one read a day.

        Weekly cadences for tiny, peaked and faded markets are kept.

        :return:
            Poll-frequency label and minimum interval between calls.
        """
        label, interval = super().get_frequency()
        # Lending markets do not move fast, so daily reads avoid unnecessary churn
        if interval is None or interval < WILDCAT_READ_INTERVAL:
            interval = WILDCAT_READ_INTERVAL
        return label, interval


class WildcatHistoricalReader(VaultHistoricalReader):
    """Read a Wildcat market's share price, supply and liquidity at historical blocks."""

    def __init__(self, vault: "WildcatMarket", stateful: bool) -> None:
        """Create a historical Wildcat market reader.

        :param vault:
            Wildcat market adapter to read.
        :param stateful:
            Whether adaptive reader state should be retained.
        """
        super().__init__(vault)
        self.reader_state = WildcatMarketReaderState(vault) if stateful else None

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Build the market state and cash reads.

        :return:
            ``currentState()`` and ``totalAssets()`` calls.
        """
        contract = self.vault.market_contract
        yield EncodedCall.from_contract_call(
            contract.functions.currentState(),
            extra_data={"function": "current_state", "vault": self.vault.address},
            first_block_number=self.first_block,
        )
        yield EncodedCall.from_contract_call(
            contract.functions.totalAssets(),
            extra_data={"function": "total_assets", "vault": self.vault.address},
            first_block_number=self.first_block,
        )

    def process_result(self, block_number: int, timestamp: datetime.datetime, call_results: list[EncodedCallResult]) -> VaultHistoricalRead:
        """Convert the market reads into a price row.

        :param block_number:
            Sampled EVM block.
        :param timestamp:
            Naive UTC timestamp of the sampled block.
        :param call_results:
            Responses from :meth:`construct_multicalls`.
        :return:
            Share price, lender value, scaled supply, deposit capacity,
            available liquidity and utilisation.
        """
        call_by_name = {r.call.extra_data["function"]: r for r in call_results}
        assert all(c.block_identifier == block_number for c in call_by_name.values()), f"Mismatch of block numbers in multicall results for {self.vault}, expected {block_number:,}"

        errors = []
        share_token = self.vault.share_token
        state_result = call_by_name.get("current_state")
        cash_result = call_by_name.get("total_assets")

        state = None
        if state_result is None or not state_result.success:
            errors.append("currentState call failed")
        else:
            state = WildcatMarketState.decode(state_result.result)

        cash = None
        if cash_result is None or not cash_result.success:
            errors.append("totalAssets call failed")
        else:
            cash = convert_int256_bytes_to_int(cash_result.result)

        share_price = total_assets = total_supply = max_deposit = available_liquidity = utilisation = None
        if state is not None:
            share_price = state.share_price
            total_assets = share_token.convert_to_decimals(state.total_supply)
            total_supply = share_token.convert_to_decimals(state.scaled_total_supply)
            max_deposit = share_token.convert_to_decimals(state.max_deposit)
            if total_supply == 0:
                errors.append("scaledTotalSupply zero")
            if cash is not None:
                raw_available, utilisation = state.calculate_lending_metrics(cash)
                available_liquidity = share_token.convert_to_decimals(raw_available)

        # Adaptive polling is updated only from a decoded share price
        if state_result is not None and state_result.state and share_price is not None:
            state_result.state.on_called(state_result, total_assets=total_assets, share_price=share_price)

        return VaultHistoricalRead(
            vault=self.vault,
            block_number=block_number,
            timestamp=timestamp,
            share_price=share_price,
            total_assets=total_assets,
            total_supply=total_supply,
            performance_fee=None,
            management_fee=None,
            errors=errors or None,
            max_deposit=max_deposit,
            available_liquidity=available_liquidity,
            utilisation=utilisation,
        )
