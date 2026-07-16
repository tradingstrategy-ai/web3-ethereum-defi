"""Historical reader for Vault Street primeUSD."""

# Reader classes intentionally mirror :class:`VaultHistoricalReader` signatures.
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
    from eth_defi.vault_street.vault import VaultStreetVault


class VaultStreetHistoricalReader(VaultHistoricalReader):
    """Read historical primeUSD supply and NAV/share.

    primeUSD is not ERC-4626. Its historical TVL is derived from the ERC-20
    ``totalSupply()`` and Vault Street's ``PriceStorage.getPrice()`` oracle.
    """

    def __init__(self, vault: "VaultStreetVault", stateful: bool):
        """Create a Vault Street historical reader.

        :param vault:
            Vault Street primeUSD adapter.
        :param stateful:
            Whether to attach adaptive reader state for the shared historical
            scanner.
        """

        super().__init__(vault)
        self.reader_state = VaultReaderState(vault) if stateful else None

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Construct supply and NAV/share calls for a historical block.

        :return:
            Multicall batch reading ``totalSupply()`` and ``getPrice()``.
        """

        yield EncodedCall.from_contract_call(
            self.vault.share_token.contract.functions.totalSupply(),
            extra_data={"function": "totalSupply", "vault": self.vault.address},
            first_block_number=self.first_block,
        )
        yield EncodedCall.from_contract_call(
            self.vault.price_oracle_contract.functions.getPrice(),
            extra_data={"function": "getPrice", "vault": self.vault.address},
            first_block_number=self.first_block,
        )

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        """Convert primeUSD multicall results to a vault price row.

        :param block_number:
            Historical block number.
        :param timestamp:
            Naive UTC block timestamp.
        :param call_results:
            Results from :py:meth:`construct_multicalls`.
        :return:
            Historical primeUSD supply, NAV/share and TVL.
        """

        total_supply: Decimal | None = None
        share_price: Decimal | None = None
        price_result: EncodedCallResult | None = None
        errors: list[str] = []

        for result in call_results:
            function = result.call.extra_data["function"]
            if not result.success:
                errors.append(f"Vault Street {function} call failed")
                continue

            raw_value = convert_int256_bytes_to_int(result.result)
            if function == "totalSupply":
                total_supply = self.vault.share_token.convert_to_decimals(raw_value)
            elif function == "getPrice":
                share_price = self.vault.convert_raw_share_price(raw_value)
                price_result = result

        total_assets = share_price * total_supply if share_price is not None and total_supply is not None else None
        if self.reader_state is not None and price_result is not None and total_assets is not None:
            self.reader_state.on_called(
                price_result,
                total_assets=total_assets,
                share_price=share_price,
            )

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
