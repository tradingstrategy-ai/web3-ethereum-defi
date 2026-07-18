"""Historical reader for Franklin Templeton Benji fund shares."""

# Reader classes intentionally mirror :class:`VaultHistoricalReader` signatures.
# ruff: noqa: FBT001

import datetime
from collections.abc import Iterable
from decimal import Decimal
from functools import cached_property
from typing import TYPE_CHECKING

from eth_defi.erc_4626.vault import VaultReaderState
from eth_defi.event_reader.conversion import convert_int256_bytes_to_int
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader

if TYPE_CHECKING:
    from eth_defi.tokenised_fund.franklin.vault import FranklinVault


class FranklinVaultReaderState(VaultReaderState):
    """Persist adaptive Benji history state with synthetic USD accounting."""

    @cached_property
    def exchange_rate(self) -> Decimal:
        """Return the USD exchange rate for already USD-denominated prices.

        :return:
            One because Benji's on-chain reference price is USD per share.
        """

        return Decimal(1)


class FranklinVaultHistoricalReader(VaultHistoricalReader):
    """Read historical Benji share supply and issuer-published reference price."""

    def __init__(self, vault: "FranklinVault", stateful: bool):
        """Create the historical reader.

        :param vault:
            Franklin Templeton Benji tokenised-fund adapter.
        :param stateful:
            Whether to attach the shared adaptive reader state.
        """

        super().__init__(vault)
        self.reader_state = FranklinVaultReaderState(vault) if stateful else None

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Construct total-supply and reference-price calls.

        :return:
            ERC-20 supply and ``lastKnownPrice`` calls for every sample block.
        """

        yield EncodedCall.from_contract_call(
            self.vault.share_token.contract.functions.totalSupply(),
            extra_data={"function": "totalSupply", "vault": self.vault.address},
            first_block_number=self.first_block,
        )
        yield EncodedCall.from_contract_call(
            self.vault.fund_contract.functions.lastKnownPrice(),
            extra_data={"function": "lastKnownPrice", "vault": self.vault.address},
            first_block_number=self.first_block,
        )

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        """Convert supply and price calls into a historical fund row.

        :param block_number:
            Historical Ethereum block number.
        :param timestamp:
            Naive UTC block timestamp.
        :param call_results:
            Results of :meth:`construct_multicalls`.
        :return:
            Benji share price, supply and USD TVL at the sampled block.
        """

        total_supply: Decimal | None = None
        share_price: Decimal | None = None
        state_result: EncodedCallResult | None = None
        errors: list[str] = []
        for result in call_results:
            function = result.call.extra_data.get("function")
            if not result.success:
                errors.append(f"Franklin Benji {function} call failed")
                continue
            if function == "totalSupply":
                total_supply = self.vault.share_token.convert_to_decimals(convert_int256_bytes_to_int(result.result))
            elif function == "lastKnownPrice":
                share_price = Decimal(convert_int256_bytes_to_int(result.result)) / Decimal(10**18)
                state_result = result

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
            performance_fee=None,
            management_fee=None,
            errors=errors or None,
            deposits_open=False,
            redemption_open=False,
        )
