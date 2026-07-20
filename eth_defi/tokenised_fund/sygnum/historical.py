"""Historical state reader for Sygnum FILQ shares."""

# ruff: noqa: FBT001

import datetime
from collections.abc import Iterable
from decimal import Decimal
from functools import cached_property
from typing import TYPE_CHECKING

from eth_abi import decode
from web3 import Web3

from eth_defi.erc_4626.vault import VaultReaderState
from eth_defi.event_reader.conversion import convert_int256_bytes_to_int
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.tokenised_fund.sygnum.constants import FILQ_BUNDLE_AGGREGATOR_ADDRESS
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader

if TYPE_CHECKING:
    from eth_defi.tokenised_fund.sygnum.vault import SygnumVault


class SygnumVaultReaderState(VaultReaderState):
    """Adaptive reader state for FILQ's synthetic USD denomination."""

    @cached_property
    def exchange_rate(self) -> Decimal:
        """Return one because FILQ NAV and TVL are already denominated in USD."""

        return Decimal(1)


class SygnumVaultHistoricalReader(VaultHistoricalReader):
    """Read FILQ supply and its official Chainlink bundle NAV.

    FILQ's price feed is a Chainlink bundle proxy rather than an AggregatorV3
    feed.  Historical state reads use ``latestBundle()`` at the sampled block;
    the targeted Sygnum backfill separately reads every accepted report event
    through :mod:`eth_defi.chainlink.bundle_aggregator` and Hypersync.
    """

    def __init__(self, vault: "SygnumVault", stateful: bool):
        """Create the FILQ state reader.

        :param vault: FILQ adapter.
        :param stateful: Retained for shared reader compatibility.
        """

        super().__init__(vault)
        self.reader_state = SygnumVaultReaderState(vault) if stateful else None

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Construct FILQ supply and Chainlink bundle calls.

        :return: Supply, bundle, report-timestamp and schema-validation calls.
        """

        yield EncodedCall.from_contract_call(
            self.vault.share_token.contract.functions.totalSupply(),
            extra_data={"function": "totalSupply", "vault": self.vault.address},
            first_block_number=self.first_block,
        )
        yield EncodedCall.from_contract_call(
            self.vault.price_feed_contract.functions.latestBundle(),
            extra_data={"function": "latestBundle", "vault": self.vault.address},
            first_block_number=self.vault.oracle_first_seen_at_block,
        )
        yield EncodedCall.from_contract_call(
            self.vault.price_feed_contract.functions.latestBundleTimestamp(),
            extra_data={"function": "latestBundleTimestamp", "vault": self.vault.address},
            first_block_number=self.vault.oracle_first_seen_at_block,
        )
        yield EncodedCall.from_contract_call(
            self.vault.price_feed_contract.functions.bundleDecimals(),
            extra_data={"function": "bundleDecimals", "vault": self.vault.address},
            first_block_number=self.vault.oracle_first_seen_at_block,
        )
        yield EncodedCall.from_contract_call(
            self.vault.price_feed_contract.functions.aggregator(),
            extra_data={"function": "aggregator", "vault": self.vault.address},
            first_block_number=self.vault.oracle_first_seen_at_block,
        )

    def process_result(self, block_number: int, timestamp: datetime.datetime, call_results: list[EncodedCallResult]) -> VaultHistoricalRead:
        """Convert supply and bundle results to a priced history row.

        :param block_number: Historical block number.
        :param timestamp: Naive UTC block timestamp.
        :param call_results: Multicall results from :meth:`construct_multicalls`.
        :return: FILQ supply, NAV/share and total NAV history row.
        """

        total_supply: Decimal | None = None
        bundle: bytes | None = None
        bundle_updated_at: int | None = None
        bundle_decimals: tuple[int, ...] | None = None
        aggregator_address: str | None = None
        supply_result: EncodedCallResult | None = None
        price_result: EncodedCallResult | None = None
        errors: list[str] = []
        for result in call_results:
            function = result.call.extra_data.get("function")
            if not result.success:
                errors.append(f"Sygnum FILQ {function} call failed")
                continue
            if function == "totalSupply":
                total_supply = self.vault.share_token.convert_to_decimals(convert_int256_bytes_to_int(result.result))
                supply_result = result
            elif function == "latestBundle":
                (bundle,) = decode(["bytes"], bytes(result.result))
                bundle = bytes(bundle)
                price_result = result
            elif function == "latestBundleTimestamp":
                bundle_updated_at = convert_int256_bytes_to_int(result.result)
            elif function == "bundleDecimals":
                (decoded_decimals,) = decode(["uint8[]"], bytes(result.result))
                bundle_decimals = tuple(decoded_decimals)
            elif function == "aggregator":
                (decoded_address,) = decode(["address"], bytes(result.result))
                aggregator_address = Web3.to_checksum_address(decoded_address)

        share_price: Decimal | None = None
        schema_valid = True
        if bundle_decimals is not None and bundle_decimals != self.vault.bundle_decimals:
            errors.append(f"Sygnum FILQ bundle decimals changed to {bundle_decimals}")
            schema_valid = False
        if aggregator_address is not None and aggregator_address.lower() != FILQ_BUNDLE_AGGREGATOR_ADDRESS:
            errors.append(f"Sygnum FILQ bundle aggregator changed to {aggregator_address}")
            schema_valid = False
        if bundle_decimals is None:
            errors.append("Sygnum FILQ bundle decimals unavailable")
            schema_valid = False
        if aggregator_address is None:
            errors.append("Sygnum FILQ bundle aggregator unavailable")
            schema_valid = False
        if bundle is not None and bundle_updated_at is not None and bundle_updated_at > 0 and schema_valid:
            share_price = self.vault.decode_bundle_nav(bundle, bundle_decimals)
            if share_price <= 0:
                errors.append("Sygnum FILQ bundle returned an invalid NAV")
                share_price = None
        elif bundle is not None and (bundle_updated_at is None or bundle_updated_at <= 0):
            errors.append("Sygnum FILQ bundle returned an invalid report timestamp")

        total_assets = share_price * total_supply if share_price is not None and total_supply is not None else None
        if self.reader_state is not None:
            if price_result is not None and total_assets is not None and share_price is not None:
                self.reader_state.on_called(price_result, total_assets=total_assets, share_price=share_price)
            elif supply_result is not None:
                self.reader_state.on_unpriced_call(supply_result)
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
