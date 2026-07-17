"""Historical reader for Asseto tokenised fund products."""

#: Reader classes intentionally mirror :class:`VaultHistoricalReader` signatures.
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
    from eth_defi.tokenised_fund.asseto.vault import AssetoVault


class AssetoVaultHistoricalReader(VaultHistoricalReader):
    """Read Asseto historical supply and NAV/share.

    Asseto's ``Pricer`` contract stores NAV/share in 18 decimal USD units.
    The token proxy exposes ERC-20 supply but is not an ERC-4626 vault, so the
    product TVL is calculated as ``totalSupply() * getLatestPrice()``.
    """

    def __init__(self, vault: "AssetoVault", stateful: bool):
        """Create a historical reader.

        :param vault:
            Asseto tokenised fund adapter.
        :param stateful:
            Whether to attach adaptive read state.
        """

        super().__init__(vault)
        self.reader_state = VaultReaderState(vault) if stateful else None

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Construct the supply and NAV/share multicalls.

        :return:
            Calls for ERC-20 supply and Asseto NAV/share.
        """

        yield EncodedCall.from_contract_call(
            self.vault.share_token.contract.functions.totalSupply(),
            extra_data={"function": "totalSupply", "vault": self.vault.address},
            first_block_number=self.first_block,
        )
        if self.vault.uses_onchain_pricer():
            yield EncodedCall.from_contract_call(
                self.vault.pricer_contract.functions.getLatestPrice(),
                extra_data={"function": "getLatestPrice", "vault": self.vault.address},
                first_block_number=self.first_block,
            )

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        """Convert supply and price call results to a price row.

        :param block_number:
            Block that was read.
        :param timestamp:
            Naive UTC block timestamp.
        :param call_results:
            Results of calls constructed by :py:meth:`construct_multicalls`.
        :return:
            Asseto share price, supply and total assets at the block.
        """

        total_supply: Decimal | None = None
        share_price: Decimal | None = None
        state_result: EncodedCallResult | None = None
        errors: list[str] = []

        for result in call_results:
            function = result.call.extra_data.get("function")
            if not result.success:
                errors.append(f"Asseto {function} call failed")
                continue

            if function == "totalSupply":
                total_supply = self.vault.share_token.convert_to_decimals(convert_int256_bytes_to_int(result.result))
            elif function == "getLatestPrice":
                share_price = Decimal(convert_int256_bytes_to_int(result.result)) / Decimal(10**18)
                state_result = result

        if share_price is None and not self.vault.uses_onchain_pricer():
            share_price = self.vault.fetch_offchain_share_price(timestamp)
            if share_price is None:
                errors.append("Asseto off-chain price history is not available for this timestamp")

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
