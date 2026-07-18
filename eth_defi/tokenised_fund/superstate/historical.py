"""Historical supply and NAV reader for Superstate fund tokens."""

# Reader classes intentionally mirror :class:`VaultHistoricalReader` signatures.
# ruff: noqa: FBT001

import datetime
from collections.abc import Iterable
from decimal import Decimal
from functools import cached_property
from typing import TYPE_CHECKING

import eth_abi

from eth_defi.erc_4626.vault import VaultReaderState
from eth_defi.event_reader.conversion import convert_int256_bytes_to_int
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader

if TYPE_CHECKING:
    from eth_defi.tokenised_fund.superstate.vault import SuperstateVault


class SuperstateVaultReaderState(VaultReaderState):
    """Persist Superstate reader state for USD-denominated NAV results."""

    @cached_property
    def exchange_rate(self) -> Decimal:
        """Return the USD exchange rate used by Superstate NAV results.

        :return:
            One because the continuous-price oracle reports USD per share.
        """

        return Decimal(1)


class SuperstateVaultHistoricalReader(VaultHistoricalReader):
    """Read Superstate ERC-20 supply and issuer-published NAV history.

    USTB's ``getChainlinkPrice()`` method returns the Superstate continuous
    price at the queried archive block. It is a NAV/share feed, not a token
    exchange price and not a redemption-liquidity guarantee. The reader marks
    failed or stale oracle values as errors rather than inventing a price.
    """

    def __init__(self, vault: "SuperstateVault", stateful: bool):
        """Create the historical reader.

        :param vault:
            Reviewed Superstate fund adapter.
        :param stateful:
            Whether to retain shared adaptive multicall state.
        """

        super().__init__(vault)
        self.reader_state = SuperstateVaultReaderState(vault) if stateful else None

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Construct the supply and NAV calls for each historical sample.

        :return:
            Calls to USTB ``totalSupply()`` and ``getChainlinkPrice()``.
        """

        yield EncodedCall.from_contract_call(
            self.vault.share_token.contract.functions.totalSupply(),
            extra_data={"function": "totalSupply", "vault": self.vault.address},
            first_block_number=self.first_block,
        )
        yield EncodedCall.from_keccak_signature(
            address=self.vault.address,
            signature=self.vault.chainlink_price_selector,
            function="getChainlinkPrice",
            data=b"",
            extra_data={"function": "getChainlinkPrice", "vault": self.vault.address},
            first_block_number=self.first_block,
        )

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        """Convert archive multicall responses into a historical price row.

        :param block_number:
            Ethereum block containing the readings.
        :param timestamp:
            Naive UTC block timestamp.
        :param call_results:
            Results from :py:meth:`construct_multicalls`.
        :return:
            Supply, NAV/share and NAV-denominated TVL observation.
        """

        total_supply: Decimal | None = None
        share_price: Decimal | None = None
        price_result: EncodedCallResult | None = None
        errors: list[str] = []
        for result in call_results:
            function = result.call.extra_data.get("function")
            if not result.success:
                errors.append(f"Superstate {function} call failed")
                continue
            if function == "totalSupply":
                total_supply = self.vault.share_token.convert_to_decimals(convert_int256_bytes_to_int(result.result))
            elif function == "getChainlinkPrice":
                is_bad_data, _updated_at, raw_price = eth_abi.decode(["bool", "uint256", "uint256"], result.result)
                if is_bad_data:
                    errors.append("Superstate getChainlinkPrice reported stale or invalid oracle data")
                else:
                    share_price = self.vault.convert_oracle_price(raw_price)
                    price_result = result

        total_assets = share_price * total_supply if share_price is not None and total_supply is not None else None
        if self.reader_state is not None and price_result is not None and total_assets is not None:
            self.reader_state.on_called(price_result, total_assets=total_assets, share_price=share_price)

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
