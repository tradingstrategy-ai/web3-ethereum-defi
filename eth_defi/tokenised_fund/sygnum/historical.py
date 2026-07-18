"""Historical reader for Sygnum FILQ shares."""

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
    from eth_defi.tokenised_fund.sygnum.vault import SygnumVault


class SygnumVaultHistoricalReader(VaultHistoricalReader):
    """Read FILQ token supply without fabricating unavailable NAV history.

    Sygnum configures a class-specific price-feed address, but the reviewed
    contract does not expose a public generic Chainlink ``latestRoundData`` or
    ``decimals`` response.  This reader therefore emits supply-only rows with
    a diagnostic error rather than inventing a one-dollar price.
    """

    def __init__(self, vault: "SygnumVault", stateful: bool):
        """Create the supply-only reader.

        :param vault: FILQ adapter.
        :param stateful: Retained for shared reader compatibility.
        """

        super().__init__(vault)
        self.reader_state = VaultReaderState(vault) if stateful else None

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Construct the ERC-20 total-supply call.

        :return: Supply call for the reviewed FILQ proxy.
        """

        yield EncodedCall.from_contract_call(
            self.vault.share_token.contract.functions.totalSupply(),
            extra_data={"function": "totalSupply", "vault": self.vault.address},
            first_block_number=self.first_block,
        )

    def process_result(self, block_number: int, timestamp: datetime.datetime, call_results: list[EncodedCallResult]) -> VaultHistoricalRead:
        """Convert a supply result to an explicitly unpriced history row.

        :param block_number: Historical block number.
        :param timestamp: Naive UTC block timestamp.
        :param call_results: Multicall results from :meth:`construct_multicalls`.
        :return: Supply-only FILQ history row.
        """

        total_supply: Decimal | None = None
        state_result: EncodedCallResult | None = None
        errors = [self.vault.nav_unavailable_reason]
        for result in call_results:
            if result.call.extra_data.get("function") == "totalSupply":
                if result.success:
                    total_supply = self.vault.share_token.convert_to_decimals(convert_int256_bytes_to_int(result.result))
                    state_result = result
                else:
                    errors.append("Sygnum FILQ totalSupply call failed")
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
