"""Read published Buoy accounting without trading or API valuation.

Verified implementations cache the fulfiller's gross NAV in EVM storage and
subtract locked withdrawal liabilities in ``totalAssets``. Neither that view nor
``pricePerShare`` reads Core precompiles. See ``eth_defi/abi/buoy/README.md`` and
https://docs.buoy.finance/developers/smart-contract-integration.
"""

import datetime
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from functools import cached_property
from typing import Any, Callable

from eth_typing import BlockIdentifier, HexAddress
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.compat import native_datetime_utc_fromtimestamp
from eth_defi.erc_4626.vault import ERC4626HistoricalReader, ERC4626Vault
from eth_defi.erc_4626.vault_protocol.buoy.constants import BUOY_IMPLEMENTATIONS, BUOY_MIN_TVL_USD, BUOY_V3_IMPLEMENTATION
from eth_defi.erc_4626.vault_protocol.buoy.discovery import fetch_buoy_factories, fetch_buoy_implementation
from eth_defi.erc_4626.vault_protocol.buoy.offchain_metadata import fetch_buoy_vault_metadata
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.erc_4626.vault_protocol.buoy.tags import STRATEGY_TAGS
from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags
from eth_defi.types import Percent
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.handwritten_metadata import get_handwritten_vault_metadata
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader

logger = logging.getLogger(__name__)

#: Immutable maximum performance fee in the reviewed implementations.
MAX_PERFORMANCE_FEE_BPS = 5000
#: Immutable maximum referral share of the performance fee in v2/v3.
MAX_PROTOCOL_FEE_SHARE_BPS = 5000
#: Each canonical value is read at the same block, without API fallbacks.
SNAPSHOT_FIELDS = ("pricePerShare", "totalAssets", "totalSupply", "latestNav", "latestNavTimestamp", "totalOwedUsdc", "pendingDepositsUsdc", "performanceFeeBps")


@dataclass(slots=True, frozen=True)
class BuoyAccounting:
    """One block's published accounting, before human-unit conversion."""

    #: Canonical price, scaled by 1e18.
    price: int
    #: Net asset units backing unburnt shares.
    assets: int
    #: Raw share units.
    supply: int
    #: Gross cached NAV units, already excluding pending deposits.
    gross_nav: int
    #: Last genuine NAV publication, Unix seconds; zero means uninitialised.
    nav_timestamp: int
    #: Locked USDC claims, already removed from assets.
    owed: int
    #: Incoming capital not yet converted into shares.
    pending: int
    #: High-water-mark performance fee in basis points.
    fee_bps: int

    @classmethod
    def from_values(cls, values: list[int]) -> "BuoyAccounting":
        """Validate the reviewed net-asset and price identities.

        A zero supply has a contract-defined bootstrap price, which is not a
        published investment return. No price method fallback is performed.

        :param values: Values in ``SNAPSHOT_FIELDS`` order.
        :return: Verified raw accounting snapshot.
        :raises ValueError: A view disagrees with the reviewed accounting.
        """
        snapshot = cls(*values)
        expected_assets = max(snapshot.gross_nav - snapshot.owed, 0)
        expected_price = snapshot.assets * 10**18 // snapshot.supply if snapshot.supply else 10**18
        if snapshot.assets != expected_assets or snapshot.price != expected_price or not 0 <= snapshot.fee_bps <= MAX_PERFORMANCE_FEE_BPS:
            raise ValueError("Inconsistent Buoy published accounting")
        return snapshot


class BuoyHistoricalReader(ERC4626HistoricalReader):
    """Read cached EVM state and its valuation provenance in shared multicalls.

    Reuse the standard reader's state/scheduling ownership, but replace its
    ERC-4626 capacity probes and quoting method with verified Buoy views.
    """

    include_historical_end_sample = True

    def __init__(self, vault: "BuoyVault", stateful: bool) -> None:
        """Keep small funded Buoy vaults in the ordinary polling schedule.

        The protocol's indexing threshold applies to traction and tiny-vault
        cadence too, so a funded $500 vault is not retired by generic limits.

        :param vault: Read-only Buoy adapter.
        :param stateful: Whether adaptive polling state is required.
        :return: None after applying the protocol's polling thresholds.
        """
        super().__init__(vault, stateful)
        if self.reader_state is not None:
            self.reader_state.min_tvl_threshold = BUOY_MIN_TVL_USD
            self.reader_state.tiny_tvl_threshold_rare_read = BUOY_MIN_TVL_USD

    def get_warmup_calls(self) -> Iterable[tuple[str, Callable[[], Any], object]]:
        """Warm only the reviewed storage views.

        Buoy has no ERC-4626 maxDeposit/maxRedeem execution surface.

        :return: Individual view calls for shared warmup diagnostics.
        """
        for name in SNAPSHOT_FIELDS:
            call = getattr(self.vault.vault_contract.functions, name)()
            yield name, call.call, call

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Build block-aligned published-accounting calls.

        Calls retain vault identity for the shared multicall scheduler.

        :return: Encoded storage reads, without metadata API requests.
        """
        for name in SNAPSHOT_FIELDS:
            yield EncodedCall.from_contract_call(getattr(self.vault.vault_contract.functions, name)(), extra_data={"function": name, "vault": self.vault.address}, first_block_number=self.first_block)

    def process_result(self, block_number: int, timestamp: datetime.datetime, call_results: list[EncodedCallResult]) -> VaultHistoricalRead:
        """Decode an atomic accounting observation and preserve NAV age.

        Failed required calls cannot produce a partial price or a false zero.
        Invalid observations carry errors without advancing reader state. The
        shared writer preserves saved source rows for this adapter.

        :param block_number: Actual read block.
        :param timestamp: Actual read time in naive UTC.
        :param call_results: Shared multicall responses.
        :return: Historical price and accounting with separate NAV time.
        """
        try:
            results = self.dictify_multicall_results(block_number, call_results)
            if any(name not in results or not results[name].success or len(results[name].result) != 32 for name in SNAPSHOT_FIELDS):
                raise ValueError(f"Incomplete Buoy accounting at block {block_number}")
            if any(result.block_identifier != block_number for result in results.values()):
                raise ValueError("Mixed Buoy accounting blocks")
            snapshot = BuoyAccounting.from_values([int.from_bytes(results[name].result, "big") for name in SNAPSHOT_FIELDS])
            nav_at = native_datetime_utc_fromtimestamp(snapshot.nav_timestamp) if snapshot.nav_timestamp else None
            if nav_at is not None and nav_at > timestamp:
                raise ValueError("Buoy NAV publication is ahead of its read block")
        except (ValueError, OverflowError) as error:
            logger.warning("Deferred Buoy observation: vault=%s block=%d reason=%s", self.vault.address, block_number, error)
            return VaultHistoricalRead(vault=self.vault, block_number=block_number, timestamp=timestamp, share_price=None, total_assets=None, total_supply=None, performance_fee=None, management_fee=None, errors=[str(error)])
        price = Decimal(snapshot.price) / Decimal(10**18) if snapshot.supply and nav_at else None
        assets = self.vault.denomination_token.convert_to_decimals(snapshot.assets)
        if self.reader_state is not None:
            anchor = results["pricePerShare"]
            self.reader_state.on_called(anchor, total_assets=assets, share_price=price if price is not None else Decimal(0))
        return VaultHistoricalRead(vault=self.vault, block_number=block_number, timestamp=timestamp, share_price=price, total_assets=assets, total_supply=self.vault.share_token.convert_to_decimals(snapshot.supply), performance_fee=snapshot.fee_bps / 10_000, management_fee=0.0, errors=None, nav_timestamp=nav_at)


class BuoyVault(ERC4626Vault):
    """Read-only adapter reusing only ERC-20/valuation machinery.

    The reviewed contract has the inherited ``asset``, ``totalAssets`` and
    share views, but requests are bespoke. No deposit manager is certified.
    """

    preserve_unavailable_price_history = True

    @cached_property
    def implementation(self) -> HexAddress:
        """Verify the immutable clone and its official registry membership.

        Factory identity is checked separately from the clone's bytecode so
        a third-party clone cannot impersonate a Buoy deployment.

        :return: Reviewed implementation address.
        :raises ValueError: Clone or registry identity is not supported.
        """
        block = self._get_block_identifier()
        implementation = fetch_buoy_implementation(self.web3, self.address, block)
        for address in fetch_buoy_factories(self.web3, block):
            factory = get_deployed_contract(self.web3, "buoy/VaultFactory.json", address)
            if factory.functions.isVault(self.address).call(block_identifier=block):
                return implementation
        raise ValueError(f"Unregistered Buoy vault {self.address}")

    @cached_property
    def vault_contract(self) -> Contract:
        """Bind the verified implementation interface to its clone.

        All reviewed generations expose the same storage valuation methods.

        :return: Read contract with the generation-specific canonical ABI.
        """
        return get_deployed_contract(self.web3, "buoy/" + BUOY_IMPLEMENTATIONS[self.implementation], self.address)

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Construct the published-value reader.

        The existing multicall pipeline owns scheduling and persistence.

        :param stateful: Enable adaptive reader state.
        :return: Protocol reader with no transaction support.
        """
        return BuoyHistoricalReader(self, stateful)

    def fetch_accounting(self, block_identifier: BlockIdentifier | None = None) -> BuoyAccounting:
        """Read exact block-aligned published accounting.

        Resolve moving tags once before reading multiple storage views.

        :param block_identifier: Observation block, or the configured metadata block.
        :return: Validated raw accounting snapshot.
        """
        block = block_identifier if block_identifier is not None else self._get_block_identifier()
        if not isinstance(block, int):
            block = self.web3.eth.get_block(block)["number"]
        return BuoyAccounting.from_values([getattr(self.vault_contract.functions, name)().call(block_identifier=block) for name in SNAPSHOT_FIELDS])

    def fetch_share_price(self, block_identifier: BlockIdentifier) -> Decimal | None:
        """Read the canonical published price, without an API substitution.

        The timestamp is separately exported; this value may be old.

        :param block_identifier: Actual observation block.
        :return: Denomination units per share.
        """
        snapshot = self.fetch_accounting(block_identifier)
        if not snapshot.supply or not snapshot.nav_timestamp:
            return None
        return Decimal(snapshot.price) / Decimal(10**18)

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent:
        """Read the current high-water-mark fee.

        Fee-share dilution is already internalised in the published price.

        :param block_identifier: Observation block.
        :return: Fractional performance fee.
        """
        return self.vault_contract.functions.performanceFeeBps().call(block_identifier=block_identifier) / 10_000

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent:
        """Return the verified absence of a management fee.

        Native request and trading fees are separate from this fee mode.

        :param block_identifier: Retained for the common fee interface.
        :return: Zero annual management charge.
        """
        return 0.0

    def is_whitelisted_deposit(self) -> bool:
        """Read identity restrictions separately from caps and pause state.

        Source-reviewed pre-v3 vaults have no depositor whitelist. A v3
        policy read failure propagates to the scanner's unknown classification.

        :return: Whether deposits require identity approval.
        """
        if self.implementation != BUOY_V3_IMPLEMENTATION:
            return False
        block = self._get_block_identifier()
        address = self.vault_contract.functions.policy().call(block_identifier=block)
        if int(address, 16) == 0:
            return False
        policy = get_deployed_contract(self.web3, "buoy/VaultPolicy.json", address)
        return bool(policy.functions.configOf(self.address).call(block_identifier=block)[1])

    def fetch_scan_record_extra_data(self) -> dict:
        """Expose published-accounting provenance in metadata diagnostics.

        These are raw denomination units, not independent live market equity.

        :return: Private scan fields consumed by export and freshness checks.
        """
        snapshot = self.fetch_accounting()
        metadata_available = get_handwritten_vault_metadata(self.chain_id, self.address) is not None or fetch_buoy_vault_metadata(self.web3, self.address) is not None
        return {"_buoy_metadata_available": metadata_available, "_buoy_leader": self.vault_contract.functions.leader().call(block_identifier=self._get_block_identifier()), "_nav_timestamp": native_datetime_utc_fromtimestamp(snapshot.nav_timestamp) if snapshot.nav_timestamp else None, "_buoy_gross_nav_raw": snapshot.gross_nav, "_buoy_pending_deposits_raw": snapshot.pending, "_buoy_owed_raw": snapshot.owed, "_flow_accounting_supported": False}

    @property
    def description(self) -> str | None:
        """Optional normalised strategy text from the cached metadata API."""
        handwritten = get_handwritten_vault_metadata(self.chain_id, self.address)
        if handwritten is not None:
            return handwritten.description
        metadata = fetch_buoy_vault_metadata(self.web3, self.address)
        return metadata["description"] if metadata else None

    @property
    def short_description(self) -> str | None:
        """Optional single-sentence plain-text strategy summary."""
        handwritten = get_handwritten_vault_metadata(self.chain_id, self.address)
        if handwritten is not None:
            return handwritten.short_description
        metadata = fetch_buoy_vault_metadata(self.web3, self.address)
        return metadata["short_description"] if metadata else None

    def get_link(self, referral: str | None = None) -> str:
        """Return this vault's official app page.

        No referral attribution is performed by read-only integration.

        :param referral: Unused common interface argument.
        :return: Address-scoped app URL.
        """
        return f"https://buoy.finance/vaults/{self.address}"

    def get_deposit_manager(self) -> None:
        """Keep all transaction execution explicitly unsupported.

        Inheriting ERC-20 reads does not certify the generic ERC-4626 manager.

        :return: No deposit manager for this read-only integration.
        """
        return None

    @property
    def leader(self) -> HexAddress:
        """Read the leader identity from the vault, independent of API labels.

        Administrators can replace the leader, so this is not an immutable
        deployment identity or a name inferred from strategy descriptions.

        :return: Onchain leader address at the metadata observation block.
        """
        return HexAddress(self.vault_contract.functions.leader().call(block_identifier=self._get_block_identifier()))

    def get_strategy_tags(self) -> set[StrategyTag] | None:
        """Return reviewed address-specific strategy classifications.

        Missing information remains None; spot-only vaults do not acquire
        a perpetual-futures tag from the protocol they use.

        :return: A fresh tag set, or missing-information result.
        """
        return lookup_strategy_tags(STRATEGY_TAGS, self.address)

    def get_fee_mode(self) -> VaultFeeMode:
        """Classify reviewed fee-share dilution independently of discovery flags.

        Direct adapter users may not supply scanner feature flags. The four
        reviewed implementations all mint fee shares, so their published share
        prices already reflect the charged performance fee.

        :return: Internalised fee-share minting mode.
        """
        return VaultFeeMode.internalised_minting

    def get_fee_data(self, block_identifier: BlockIdentifier | None = None) -> FeeData:
        """Keep fee reads aligned with the configured metadata observation.

        The shared scanner invokes this without a block argument. Resolve that
        case to this adapter's fixed block rather than an unrelated latest head.

        The protocol component is the referral slice already included in the
        performance rate, expressed on the same new-profit basis. Reviewed
        pre-v2 deployments have no split. Fixed HYPE request charges are paid
        separately by transacting wallets; this read-only integration submits
        no requests and excludes them from percentage fee and return fields.
        See https://docs.buoy.finance/for-depositors/fees.

        :param block_identifier: Explicit fee block, or the metadata block.
        :return: Complete standard fee fractions, without double-counting the split.
        """
        block = block_identifier if block_identifier is not None else self._get_block_identifier()
        if not isinstance(block, int):
            block = self.web3.eth.get_block(block)["number"]
        fees = super().get_fee_data(block)
        fees.protocol = 0.0
        if BUOY_IMPLEMENTATIONS[self.implementation] in ("BuoyVaultV2.json", "BuoyVaultV3.json"):
            share_bps = self.vault_contract.functions.protocolFeeShareBps().call(block_identifier=block)
            if not 0 <= share_bps <= MAX_PROTOCOL_FEE_SHARE_BPS:
                raise ValueError("Invalid Buoy protocol fee share")
            if share_bps:
                recipient = self.vault_contract.functions.protocolFeeRecipient().call(block_identifier=block)
                if int(recipient, 16) != 0:
                    fees.protocol = fees.performance * share_bps / 10_000
        return fees

    def has_custom_fees(self) -> bool:
        """Report the fixed native-token request charge outside percentage fees.

        Performance-share dilution is represented by the ordinary fee model.
        The HYPE charge affects transacting wallets, but no requests are sent
        by our read-only indexing, rankings or share-price return calculations.

        :return: True because request fees cannot be expressed as a fixed rate.
        """
        return True
