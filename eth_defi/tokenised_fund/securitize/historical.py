"""Historical reader for Securitize DSToken fund instruments."""

import datetime
from collections.abc import Iterable
from decimal import Decimal
from functools import cached_property
from typing import TYPE_CHECKING

from eth_abi import decode

from eth_defi.erc_4626.vault import VaultReaderState
from eth_defi.event_reader.conversion import convert_int256_bytes_to_int
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader

if TYPE_CHECKING:
    from eth_defi.tokenised_fund.securitize.vault import SecuritizeVault


class SecuritizeVaultReaderState(VaultReaderState):
    """Persist adaptive Securitize history scan state.

    Securitize fundamental feeds publish NAV in USD and the reader calculates
    ``total_assets`` in USD. There is no ERC-20 denomination token from which
    the generic state can derive an exchange rate.
    """

    @cached_property
    def exchange_rate(self) -> Decimal:
        """Return the exchange rate for already USD-denominated TVL.

        :return:
            One because no further currency conversion is needed.
        """

        return Decimal(1)


class SecuritizeVaultHistoricalReader(VaultHistoricalReader):
    """Read Securitize token supply and reviewed NAV/share.

    Securitize tokens expose ERC-20 supply but no common fund NAV interface.
    Fixed-price products use their reviewed adapter estimate; variable-NAV
    products read a RedStone push feed in the same historical multicall.
    """

    def __init__(self, vault: "SecuritizeVault", stateful: bool):  # noqa: FBT001
        """Create a historical reader.

        :param vault:
            Securitize DSToken vault adapter.
        :param stateful:
            Whether to attach shared adaptive reader state.
        """

        super().__init__(vault)
        self.reader_state = SecuritizeVaultReaderState(vault) if stateful else None

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Construct the supply and optional RedStone NAV multicalls.

        :return:
            Historical total-supply and NAV calls.
        """

        yield EncodedCall.from_contract_call(
            self.vault.share_token.contract.functions.totalSupply(),
            extra_data={"function": "totalSupply", "vault": self.vault.address},
            first_block_number=self.first_block,
        )
        if self.vault.redstone_feed is not None:
            yield EncodedCall.from_contract_call(
                self.vault.redstone_feed_contract.functions.latestRoundData(),
                extra_data={"function": "redstone_latestRoundData", "vault": self.vault.address},
                first_block_number=self.vault.redstone_feed.first_block,
            )

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        """Convert multicall results to a historical vault row.

        :param block_number:
            Historical block number.
        :param timestamp:
            Naive UTC block timestamp.
        :param call_results:
            Total-supply and optional RedStone multicall results.
        :return:
            Historical DSToken price and supply record.
        """

        total_supply: Decimal | None = None
        share_price = self.vault.product.estimated_nav_per_share if self.vault.product is not None else None
        state_result: EncodedCallResult | None = None
        errors: list[str] = []
        for result in call_results:
            function = result.call.extra_data.get("function")
            if function == "totalSupply":
                if result.success:
                    total_supply = self.vault.share_token.convert_to_decimals(convert_int256_bytes_to_int(result.result))
                    if share_price is not None:
                        state_result = result
                else:
                    errors.append("Securitize token totalSupply call failed")
            elif function == "redstone_latestRoundData":
                if result.success:
                    _round_id, answer, _started_at, updated_at, _answered_in_round = decode(
                        ["uint80", "int256", "uint256", "uint256", "uint80"],
                        bytes(result.result),
                    )
                    if answer > 0 and updated_at > 0:
                        share_price = Decimal(answer) / Decimal(10**self.vault.redstone_feed.decimals)
                        state_result = result
                    else:
                        errors.append(f"RedStone {self.vault.redstone_feed.feed_id} returned an invalid observation")
                else:
                    errors.append(f"RedStone {self.vault.redstone_feed.feed_id} latestRoundData call failed")

        if share_price is None:
            if self.vault.redstone_feed is not None and block_number < self.vault.redstone_feed.first_block:
                errors.append(f"RedStone {self.vault.redstone_feed.feed_id} has no observation before block {self.vault.redstone_feed.first_block}")
            elif self.vault.redstone_feed is None:
                errors.append(f"No on-chain NAV source configured for Securitize DSToken {self.vault.address}")

        total_assets = share_price * total_supply if share_price is not None and total_supply is not None else None
        if self.reader_state is not None and state_result is not None and total_assets is not None:
            self.reader_state.on_called(state_result, total_assets=total_assets, share_price=share_price)

        fee_data = self.vault.get_fee_data()
        return VaultHistoricalRead(
            vault=self.vault,
            block_number=block_number,
            timestamp=timestamp,
            share_price=share_price,
            total_assets=total_assets,
            total_supply=total_supply,
            performance_fee=fee_data.performance,
            management_fee=fee_data.management,
            errors=errors or None,
            deposits_open=False,
            redemption_open=False,
        )
