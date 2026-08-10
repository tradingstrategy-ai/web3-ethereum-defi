"""Historical price reader for Spiko tokenised funds."""

# ruff: noqa: FBT001

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
    from eth_defi.tokenised_fund.spiko.vault import SpikoVault


class SpikoVaultReaderState(VaultReaderState):
    """Persist Spiko history state with USD-normalised accounting."""

    @cached_property
    def exchange_rate(self) -> Decimal:
        """Return the unit exchange rate for Spiko NAV observations.

        :return:
            One because the reader returns USD-normalised NAV/share directly.
        """

        return Decimal(1)


class SpikoHistoricalReader(VaultHistoricalReader):
    """Read Spiko supply and issuer-published NAV/share history.

    Spiko's verified Oracle uses the Chainlink AggregatorV3 surface. The reader
    derives the public TVL estimate by multiplying each token-supply observation
    by the official NAV observation available at the scanned block.
    """

    def __init__(self, vault: "SpikoVault", stateful: bool):
        """Create a Spiko historical reader.

        :param vault: Spiko adapter to read.
        :param stateful: Whether to retain the shared adaptive reader state.
        """
        super().__init__(vault)
        self.reader_state = SpikoVaultReaderState(vault) if stateful else None

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Construct Spiko supply and official oracle calls.

        :return: Token supply, issuer NAV and optional FX oracle calls.
        """
        yield EncodedCall.from_contract_call(self.vault.share_token.contract.functions.totalSupply(), extra_data={"function": "totalSupply", "vault": self.vault.address}, first_block_number=self.first_block)
        yield EncodedCall.from_contract_call(self.vault.price_oracle_contract.functions.latestRoundData(), extra_data={"function": "latestRoundData", "vault": self.vault.address}, first_block_number=self.vault.oracle_first_seen_at_block)
        if self.vault.usd_price_oracle_contract is not None:
            yield EncodedCall.from_contract_call(self.vault.usd_price_oracle_contract.functions.latestRoundData(), extra_data={"function": "latestUsdExchangeRate", "vault": self.vault.address}, first_block_number=self.vault.oracle_first_seen_at_block)

    def process_result(self, block_number: int, timestamp: datetime.datetime, call_results: list[EncodedCallResult]) -> VaultHistoricalRead:
        """Build one Spiko observation from multicall results.

        :param block_number: Sampled Ethereum block number.
        :param timestamp: Naive UTC timestamp of the sampled block.
        :param call_results: Results for the calls built by this reader.
        :return: Spiko supply, NAV/share, TVL and closed-flow status.
        """
        total_supply: Decimal | None = None
        source_share_price: Decimal | None = None
        usd_exchange_rate: Decimal | None = None
        price_result: EncodedCallResult | None = None
        errors: list[str] = []
        for result in call_results:
            function = result.call.extra_data["function"]
            if not result.success:
                errors.append(f"Spiko {self.vault.symbol} {function} call failed")
                continue
            if function == "totalSupply":
                total_supply = self.vault.share_token.convert_to_decimals(convert_int256_bytes_to_int(result.result))
            elif function == "latestRoundData":
                _round, answer, _started, updated_at, _answered = decode(["uint80", "int256", "uint256", "uint256", "uint80"], bytes(result.result))
                if answer > 0 and updated_at > 0:
                    source_share_price = self.vault.convert_raw_share_price(answer)
                    price_result = result
                else:
                    errors.append(f"Spiko {self.vault.symbol} oracle returned an invalid NAV observation")
            elif function == "latestUsdExchangeRate":
                _round, answer, _started, updated_at, _answered = decode(["uint80", "int256", "uint256", "uint256", "uint80"], bytes(result.result))
                if answer > 0 and updated_at > 0:
                    usd_exchange_rate = self.vault.convert_raw_usd_exchange_rate(answer)
                else:
                    errors.append(f"Spiko {self.vault.symbol} USD FX oracle returned an invalid observation")
        share_price = None
        if source_share_price is not None:
            if self.vault.usd_price_oracle_contract is None or usd_exchange_rate is not None:
                share_price = self.vault.convert_source_share_price_to_usd(source_share_price, usd_exchange_rate)
            else:
                errors.append(f"Spiko {self.vault.symbol} USD FX oracle call failed")
        total_assets = share_price * total_supply if share_price is not None and total_supply is not None else None
        if self.reader_state is not None and price_result is not None and total_assets is not None:
            self.reader_state.on_called(price_result, total_assets=total_assets, share_price=share_price)
        return VaultHistoricalRead(vault=self.vault, block_number=block_number, timestamp=timestamp, share_price=share_price, total_assets=total_assets, total_supply=total_supply, performance_fee=self.vault.get_performance_fee(block_number), management_fee=self.vault.get_management_fee(block_number), errors=errors or None, deposits_open=False, redemption_open=False)
