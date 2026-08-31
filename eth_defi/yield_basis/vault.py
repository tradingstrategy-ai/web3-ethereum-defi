"""Read-only VaultBase adapter for YieldBasis LT shares.

YieldBasis LTs are ERC-20 yb-LP shares with a protocol-specific
``pricePerShare`` oracle rather than standard ERC-4626 conversion methods.
Their common historical price is the marginal native asset returned by
``preview_withdraw`` multiplied by the market Curve Cryptoswap pool's smoothed
stable-side oracle, treated as a USD proxy. Fundamental PPS remains available
as a protocol diagnostic. The adapter exposes a fixed, token-addressed
generic USD-stablecoin conversion estimate as both entry and exit cost. These
endpoint costs are not part of the historical equity curve.
"""

# ruff: noqa: FBT001, FBT002, PLR0904, PLR0917, PLR6301

import datetime
import logging
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import ContractLogicError

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.types import Percent
from eth_defi.vault.base import INSTANT_WITHDRAWAL_PERIOD, TradingUniverse, VaultBase, VaultFlowManager, VaultHistoricalRead, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec, WithdrawalPeriod
from eth_defi.vault.deposit_redeem import VaultDepositManager
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.lower_case_dict import LowercaseDict
from eth_defi.vault.price_source import PriceSource
from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags
from eth_defi.yield_basis.addresses import YIELD_BASIS_ACTIVE_MARKETS, YIELD_BASIS_MARKET_ID_BY_LT, YieldBasisMarketReview
from eth_defi.yield_basis.contracts import fetch_yield_basis_amm, fetch_yield_basis_curve_pool, fetch_yield_basis_lt
from eth_defi.yield_basis.historical_context import YieldBasisHistoricalContextStore, YieldBasisHistoricalObservation, get_yield_basis_historical_context_path
from eth_defi.yield_basis.metrics import LT_SHARE_SCALE, ORACLE_SCALE, PPS_SCALE, estimate_usd_stablecoin_swap_cost, redemption_asset_per_share
from eth_defi.yield_basis.tags import STRATEGY_TAGS

logger = logging.getLogger(__name__)

#: Error shown when a caller requests unsupported generic transaction flows.
YIELD_BASIS_UNSUPPORTED_FLOW_REASON = "YieldBasis deposits and withdrawals use protocol-specific leveraged-pool operations and are not supported by the generic vault transaction adapter"

#: Error shown when a caller bypasses the required contextual-history path.
YIELD_BASIS_HISTORICAL_READER_REASON = "Use the YieldBasis historical context reader through the common vault price scanner"

#: Features carried by every reviewed YieldBasis LT row and adapter.
YIELD_BASIS_VAULT_FEATURES: frozenset[ERC4626Feature] = frozenset(
    {
        ERC4626Feature.yield_basis_lt,
        ERC4626Feature.amm_pool_like,
        ERC4626Feature.share_price_equivalence,
    }
)


def export_yield_basis_usd_denomination(chain_id: int) -> dict[str, object]:
    """Export the synthetic USD accounting denomination.

    YieldBasis protocol contracts use crvUSD internally, but the integration's
    investor comparison is against a generic USD stablecoin rather than one
    specific ERC-20. Token-like metadata keeps this distinction explicit in
    the common vault export.

    :param chain_id:
        EVM chain id of the YieldBasis LT.
    :return:
        Synthetic USD metadata without an ERC-20 address.
    """

    return {
        "address": None,
        "chain": chain_id,
        "name": "United States Dollar",
        "symbol": "USD",
        "decimals": None,
        "total_supply": None,
        "extra_data": {"synthetic": True},
    }


class YieldBasisHistoricalReader(VaultHistoricalReader):
    """Read cached YieldBasis observations on a common block grid.

    Historical archive calls are prefetched into the protocol context table;
    this reader only converts exact source rows to the common vault format.
    """

    @property
    def uses_contextual_history(self) -> bool:
        """Select the protocol-owned context path."""

        return True

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Return no static calls; observations are prefetched."""

        return ()

    def process_result(self, block_number: int, timestamp: datetime.datetime, call_results: list[EncodedCallResult]) -> VaultHistoricalRead:
        """Reject the ordinary static-call path."""

        del block_number, timestamp, call_results
        raise RuntimeError(YIELD_BASIS_HISTORICAL_READER_REASON)

    @staticmethod
    def _to_read(vault: "YieldBasisVault", observation: YieldBasisHistoricalObservation) -> VaultHistoricalRead:
        """Convert one source observation to a common scanner row.

        The common curve has one accounting basis: marginal redemption value.
        Context prefill requires every input, so no fundamental-value fallback
        or protocol-specific error branch is needed here.

        :param vault:
            Reviewed LT adapter attached to the common row.
        :param observation:
            Exact same-block raw context used for valuation.
        :return:
            USD redemption-value-equivalent historical read.
        """

        timestamp = datetime.datetime.fromtimestamp(observation.block_timestamp, tz=datetime.UTC).replace(tzinfo=None)
        return VaultHistoricalRead(
            vault=vault,
            block_number=observation.block_number,
            timestamp=timestamp,
            share_price=observation.share_price,
            total_assets=observation.total_assets,
            total_supply=observation.effective_supply,
            performance_fee=None,
            management_fee=None,
            errors=None,
            deposits_open=None,
            redemption_open=None,
        )

    def fetch_contextual_historical_reads(self, start_block: int, end_block: int, step: int) -> Iterable[VaultHistoricalRead]:
        """Yield the latest context observation inside each block bucket."""

        with YieldBasisHistoricalContextStore(self.vault.historical_context_path) as store:
            observations = store.iter_observations(
                chain_id=self.vault.chain_id,
                lt_address=self.vault.address,
                start_block=start_block,
                end_block=end_block,
                step=step,
            )
            yield from (self._to_read(self.vault, observation) for observation in observations)


class YieldBasisVault(VaultBase):
    """Expose one reviewed Ethereum YieldBasis LT market.

    The adapter reports marginal redemption value in USD while preserving
    fundamental native BTC or ETH PPS in the context store. It follows the official
    `YieldBasis mechanism description <https://docs.yieldbasis.com/user/overview/how-yieldbasis-works>`__
    and deliberately does not emulate ERC-4626 transaction methods.
    """

    #: Runtime deposit availability still depends on protocol-specific quotes.
    whitelist_notes = "YieldBasis markets are treated as permissionless by design review; this does not prove that every account currently satisfies pool limits or quote requirements."

    def __init__(
        self,
        web3: Web3,
        spec: VaultSpec,
        token_cache: dict | None = None,
        features: set[ERC4626Feature] | None = None,
        default_block_identifier: BlockIdentifier | None = None,
        require_denomination_token: bool = False,
    ) -> None:
        """Create a read-only adapter for an LT share token.

        The adapter always adds the reviewed YieldBasis and AMM feature flags;
        callers may supply further persisted classification features.

        :param web3:
            Ethereum connection used for current and historical state reads.
        :param spec:
            Ethereum chain and reviewed LT share-token address.
        :param token_cache:
            Optional shared ERC-20 metadata cache.
        :param features:
            Persisted scanner features to retain on the adapter.
        :param default_block_identifier:
            Default fixed block, or ``latest`` when omitted.
        :param require_denomination_token:
            Retained for shared adapter compatibility. The accounting
            denomination is synthetic USD and has no ERC-20 address.
        :return:
            None.
        """

        if spec.chain_id != 1:
            raise ValueError(f"YieldBasis is currently supported on Ethereum only, got chain {spec.chain_id}")
        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        self.web3 = web3
        self.spec = spec
        self.features = set(features or ()) | YIELD_BASIS_VAULT_FEATURES
        self.default_block_identifier = default_block_identifier or "latest"
        self.historical_context_path: Path = get_yield_basis_historical_context_path()
        self.lt_contract: Contract = fetch_yield_basis_lt(web3, self.address)
        self._historical_curve_pool: Contract | None = None
        self._historical_asset_address: HexAddress | None = None

    @property
    def chain_id(self) -> int:
        """Return Ethereum chain ID."""

        return self.spec.chain_id

    @property
    def address(self) -> HexAddress:
        """Return the checksum LT address."""

        return HexAddress(Web3.to_checksum_address(self.spec.vault_address))

    @property
    def vault_address(self) -> HexAddress:
        """Return the scanner-compatible LT address."""

        return self.address

    @property
    def market_id(self) -> int | None:
        """Return the reviewed Factory market ID, if known."""

        return YIELD_BASIS_MARKET_ID_BY_LT.get(self.address.lower())

    @property
    def market_review(self) -> YieldBasisMarketReview | None:
        """Return the immutable review record for this LT address.

        The production scanner routes only reviewed addresses. Keeping this
        lookup explicit gives historical valuation a canonical token precision
        without performing a mutable ERC-20 metadata read at every block.

        :return:
            Reviewed market metadata, or ``None`` for a manually constructed
            unknown adapter.
        """

        market_id = self.market_id
        return None if market_id is None else YIELD_BASIS_ACTIVE_MARKETS[market_id]

    @property
    def name(self) -> str:
        """Return the LT token name."""

        return self.share_token.name

    @property
    def symbol(self) -> str:
        """Return the LT token symbol."""

        return self.share_token.symbol

    @property
    def short_description(self) -> str:
        """Return a plain-language product summary."""

        underlying = self.fetch_underlying_token().symbol
        return f"YieldBasis {underlying} leveraged liquidity-provider share"

    @property
    def description(self) -> str:
        """Explain the underlying exposure and accounting unit."""

        underlying = self.fetch_underlying_token().symbol
        return f"YieldBasis supplies {underlying} and borrowed crvUSD to a Curve Cryptoswap pool through its LEVAMM mechanism. The yb-LP share remains exposed to {underlying} price moves and can lose value. An immediate redemption can be below fundamental value because of the Temporary Redemption Discount."

    def fetch_share_token(self) -> TokenDetails:
        """Fetch LT ERC-20 metadata."""

        return fetch_erc20_details(self.web3, self.address, chain_id=self.chain_id, cache=self.token_cache, cause_diagnostics_message=f"YieldBasis LT share {self.address}")

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Return the LT share-token address."""

        del block_identifier
        return self.address

    def fetch_underlying_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Read the volatile asset token configured by the LT."""

        return HexAddress(Web3.to_checksum_address(self.lt_contract.functions.ASSET_TOKEN().call(block_identifier=block_identifier)))

    def fetch_underlying_token(self, block_identifier: BlockIdentifier = "latest") -> TokenDetails:
        """Fetch metadata for the volatile asset used by the market."""

        return fetch_erc20_details(self.web3, self.fetch_underlying_token_address(block_identifier), chain_id=self.chain_id, cache=self.token_cache, cause_diagnostics_message=f"YieldBasis underlying asset for {self.address}")

    def fetch_denomination_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress | None:
        """Return no single ERC-20 for the generic USD denomination.

        crvUSD remains the stable-side token inside the reviewed YieldBasis
        contracts, but it is not selected as the investor's required starting
        or ending asset. The separate catalogue pre-scan still validates the
        protocol's crvUSD wiring.

        :param block_identifier:
            Ignored because synthetic USD has no contract state.
        :return:
            Always ``None``.
        """

        del block_identifier
        return None

    def fetch_denomination_token(self) -> TokenDetails | None:
        """Return no ERC-20 token for the synthetic USD denomination.

        :return:
            Always ``None``; scanner exports contain token-like synthetic USD
            metadata instead.
        """

        return None

    def fetch_curve_pool_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Read the market Curve Cryptoswap pool from the LT."""

        return HexAddress(Web3.to_checksum_address(self.lt_contract.functions.CRYPTOPOOL().call(block_identifier=block_identifier)))

    def fetch_curve_pool(self, block_identifier: BlockIdentifier = "latest") -> Contract:
        """Bind the market's Curve Cryptoswap pool."""

        return fetch_yield_basis_curve_pool(self.web3, self.fetch_curve_pool_address(block_identifier))

    def fetch_historical_curve_pool(self) -> Contract:
        """Fetch and cache the LT's immutable Curve pool.

        ``CRYPTOPOOL`` is an immutable in the reviewed LT implementation, so
        resolving it at the adapter's default block avoids repeating the same
        address call for every historical observation.

        :return:
            Curve pool contract shared by all snapshots for this LT.
        """

        if self._historical_curve_pool is None:
            self._historical_curve_pool = self.fetch_curve_pool(self.default_block_identifier)
        return self._historical_curve_pool

    def fetch_historical_asset_address(self) -> HexAddress:
        """Fetch and cache the LT's immutable underlying asset.

        ``ASSET_TOKEN`` is an immutable in the reviewed LT implementation and
        cannot change between historical observation blocks.

        :return:
            Checksum address stored with every context observation.
        """

        if self._historical_asset_address is None:
            self._historical_asset_address = self.fetch_underlying_token_address(self.default_block_identifier)
        return self._historical_asset_address

    def fetch_amm_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Read the market LEVAMM component."""

        return HexAddress(Web3.to_checksum_address(self.lt_contract.functions.amm().call(block_identifier=block_identifier)))

    def fetch_amm(self, block_identifier: BlockIdentifier = "latest") -> Contract:
        """Bind the market LEVAMM component."""

        return fetch_yield_basis_amm(self.web3, self.fetch_amm_address(block_identifier))

    def fetch_native_asset_price_per_share(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Return fundamental LT PPS in native BTC or ETH units."""

        raw_pps = int(self.lt_contract.functions.pricePerShare().call(block_identifier=block_identifier))
        if raw_pps <= 0:
            raise ValueError(f"YieldBasis returned non-positive native PPS for {self.address} at {block_identifier}")
        return Decimal(raw_pps) / PPS_SCALE

    def fetch_asset_usd_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Return the Curve stable-side oracle as an assumed USD asset price.

        YieldBasis's pool oracle is quoted against its internal crvUSD side.
        The integration treats this as a USD proxy while presenting a generic
        stablecoin denomination to investors.

        :param block_identifier:
            Ethereum state block for the Curve oracle read.
        :return:
            Assumed USD value of one underlying token.
        """

        raw_asset_price = int(self.fetch_curve_pool(block_identifier).functions.price_oracle().call(block_identifier=block_identifier))
        if raw_asset_price <= 0:
            raise ValueError(f"YieldBasis returned non-positive Curve oracle for {self.address} at {block_identifier}")
        return Decimal(raw_asset_price) / ORACLE_SCALE

    def fetch_redemption_asset_price_per_share(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Return marginal ``preview_withdraw`` value in the native asset.

        At most one whole LT share is previewed. This gives a comparable
        share-price equivalent and captures the current TRD, but deliberately
        does not claim that the entire market can exit at that price. The
        reviewed token precision is part of the formula because BTC wrappers
        in the supported set use both 8 and 18 decimal places.

        See the official `YieldBasis value and TRD documentation
        <https://docs.yieldbasis.com/user/protocol/fundamental-value-redemption-value-and-trd>`__.

        :param block_identifier:
            Ethereum state block shared by supply and preview calls.
        :return:
            Native BTC or ETH units redeemable per whole LT share.
        """

        review = self.market_review
        if review is None:
            raise ValueError(f"YieldBasis LT {self.address} has no reviewed asset precision")
        raw_effective_supply, _raw_staked_supply = self.lt_contract.functions.updated_balances().call(block_identifier=block_identifier)
        raw_effective_supply = int(raw_effective_supply)
        if raw_effective_supply <= 0:
            raise ValueError(f"YieldBasis LT {self.address} has no effective supply at {block_identifier}")
        raw_preview_shares = min(LT_SHARE_SCALE, raw_effective_supply)
        raw_redemption_assets = int(self.lt_contract.functions.preview_withdraw(raw_preview_shares).call(block_identifier=block_identifier))
        return redemption_asset_per_share(
            raw_preview_shares,
            raw_redemption_assets,
            asset_decimals=review.asset_decimals,
        )

    def fetch_fundamental_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Return fundamental LT PPS converted to USD.

        This YieldBasis-specific diagnostic excludes TRD. Use
        :meth:`fetch_share_price` for the primary common-vault value.

        :param block_identifier:
            Ethereum state block shared by PPS and Curve-oracle calls.
        :return:
            Fundamental USD value per whole LT share.
        """

        return self.fetch_native_asset_price_per_share(block_identifier) * self.fetch_asset_usd_price(block_identifier)

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Return gross marginal redemption value converted to USD.

        The formula is ``previewed native assets / LT shares * Curve
        asset/stable-side oracle``. The first term includes TRD and the second term
        includes BTC or ETH price volatility, so TRD must not be deducted again
        downstream. Investor-specific entry and exit swaps remain separate
        standard fee fields; this avoids charging the exit fee both in the
        equity curve and again in fee-adjusted performance.

        :param block_identifier:
            Ethereum state block shared by the preview and oracle.
        :return:
            Primary gross USD share-price equivalent.
        """

        return self.fetch_redemption_asset_price_per_share(block_identifier) * self.fetch_asset_usd_price(block_identifier)

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Return effective supply from ``updated_balances``."""

        raw_supply, _raw_staked = self.lt_contract.functions.updated_balances().call(block_identifier=block_identifier)
        return Decimal(int(raw_supply)) / LT_SHARE_SCALE

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Return redemption-value-equivalent USD equity.

        This applies the marginal share price to effective supply for comparable
        TVL reporting. It is not a whole-market liquidation quote.

        :param block_identifier:
            Ethereum state block used for all valuation calls.
        :return:
            Marginal redemption-value equivalent in USD.
        """

        return self.fetch_share_price(block_identifier) * self.fetch_total_supply(block_identifier)

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Return redemption-value-equivalent USD NAV."""

        return self.fetch_total_assets(block_identifier)

    def fetch_historical_observation(self, block_identifier: int) -> dict[str, object] | None:
        """Read fundamental and marginal-redemption valuation inputs.

        PPS, effective supply, Curve oracle and redemption preview must all
        succeed at the same block. A deployed market with zero effective supply
        has no investment observation yet. A deterministic preview revert also
        leaves a logged gap instead of substituting fundamental value or
        blocking unrelated vault scans.

        :param block_identifier:
            Historical Ethereum block shared by every contract call.
        :return:
            Keyword values used to create a historical context observation,
            or ``None`` before the market has effective supply or when its
            redemption preview reverts.
        """

        review = self.market_review
        if review is None:
            raise ValueError(f"YieldBasis LT {self.address} has no reviewed asset precision")

        # Preserve the fundamental native-asset benchmark for native CAGR and
        # TRD, even though redemption value drives the primary curve.
        raw_pps = int(self.lt_contract.functions.pricePerShare().call(block_identifier=block_identifier))

        # ``updated_balances`` includes LT represented by the staker; plain
        # ERC-20 ``totalSupply`` does not provide this effective supply view.
        raw_effective_supply, raw_staked_supply = (int(value) for value in self.lt_contract.functions.updated_balances().call(block_identifier=block_identifier))
        if raw_pps <= 0:
            raise ValueError(f"YieldBasis LT has no usable PPS at block {block_identifier}")
        if raw_effective_supply == 0:
            if raw_staked_supply != 0:
                raise ValueError(f"YieldBasis LT has staked supply without effective supply at block {block_identifier}")
            return None
        if raw_staked_supply < 0 or raw_staked_supply > raw_effective_supply:
            raise ValueError(f"YieldBasis LT returned invalid staked supply at block {block_identifier}")
        asset_address = self.fetch_historical_asset_address()
        if asset_address.lower() != review.asset_address.lower():
            raise ValueError(f"YieldBasis LT {self.address} has unexpected asset {asset_address}; reviewed asset is {review.asset_address}")
        # Use the same-block Curve oracle so BTC/ETH volatility is part of the
        # USD comparison curve without introducing timing drift. The raw field
        # keeps ``crvusd`` in its name because that is the actual onchain quote
        # source, even though the derived accounting denomination is USD.
        raw_asset_crvusd_price = int(self.fetch_historical_curve_pool().functions.price_oracle().call(block_identifier=block_identifier))
        if raw_asset_crvusd_price <= 0:
            raise ValueError(f"YieldBasis Curve oracle is zero at block {block_identifier}")
        # A fixed one-share maximum measures marginal redemption value rather
        # than a path-dependent whole-market liquidation.
        preview_shares = min(LT_SHARE_SCALE, raw_effective_supply)
        try:
            raw_redemption_assets = int(self.lt_contract.functions.preview_withdraw(preview_shares).call(block_identifier=block_identifier))
        except ContractLogicError as error:
            logger.warning("Skipping YieldBasis LT %s at block %d because preview_withdraw reverted: %s", self.address, block_identifier, error)
            return None
        if raw_redemption_assets <= 0:
            # A very small effective supply can round below one indivisible
            # underlying-token unit. Retrying the same historical block cannot
            # change that deterministic result, so leave a visible gap instead
            # of aborting the complete bounded backfill.
            logger.warning(
                "Skipping YieldBasis LT %s at block %d because preview_withdraw returned zero assets for %d shares",
                self.address,
                block_identifier,
                preview_shares,
            )
            return None
        return {
            "lt_address": self.address,
            "asset_address": asset_address,
            # Raw ERC-20 amounts carry no precision, so store the reviewed
            # decimals needed to reproduce the valuation independently.
            "asset_decimals": review.asset_decimals,
            "raw_asset_crvusd_price": raw_asset_crvusd_price,
            "raw_asset_price_per_share": raw_pps,
            "raw_preview_shares": preview_shares,
            "raw_redemption_assets": raw_redemption_assets,
            "raw_effective_supply": raw_effective_supply,
            "raw_staked_supply": raw_staked_supply,
        }

    def fetch_info(self) -> VaultInfo:
        """Return component addresses and valuation source metadata."""

        return {
            "token": self.address,
            "chain_id": self.chain_id,
            "asset": self.fetch_underlying_token_address(),
            "denomination": self.fetch_denomination_token_address(),
            "market_id": self.market_id,
            "nav_source": "Gross YieldBasis preview_withdraw marginal redemption value multiplied by Curve price_oracle; assumed USD-stablecoin endpoint costs are separate",
        }

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Export synthetic USD denomination and native asset identity."""

        underlying = self.fetch_underlying_token()
        return {
            "Denomination": "USD",
            "_denomination_token": export_yield_basis_usd_denomination(self.chain_id),
            "_synthetic_usd_denomination": True,
            "_yield_basis_market_id": self.market_id,
            "_yield_basis_underlying_token": underlying.address,
            "_yield_basis_underlying_symbol": underlying.symbol,
            "_nav_source": "Gross YieldBasis preview_withdraw marginal redemption value x Curve Cryptoswap price_oracle; assumed USD-stablecoin endpoint costs are separate",
            "_fundamental_nav_source": "YieldBasis pricePerShare x Curve Cryptoswap price_oracle",
            "_nav_available": True,
            "_historical_nav_available": True,
        }

    def fetch_portfolio(self, universe: TradingUniverse, block_identifier: BlockIdentifier | None = None) -> VaultPortfolio:
        """Return no fabricated spot portfolio for protocol-managed LP shares."""

        del universe, block_identifier
        return VaultPortfolio(spot_erc20=LowercaseDict())

    def has_block_range_event_support(self) -> bool:
        """Return whether generic investor-flow replay is supported."""

        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        """Return whether generic spot-position distribution applies."""

        return False

    def get_flags(self) -> set[VaultFlag]:
        """Return market-making and liquidity-provision flags."""

        return set(super().get_flags()) | {VaultFlag.market_making, VaultFlag.liquidity_provision}

    def get_strategy_tags(self) -> set[StrategyTag] | None:
        """Return reviewed address-level strategy tags."""

        return lookup_strategy_tags(STRATEGY_TAGS, self.address)

    def get_flow_manager(self) -> VaultFlowManager:
        """Reject unsupported generic flow accounting."""

        raise NotImplementedError(YIELD_BASIS_UNSUPPORTED_FLOW_REASON)

    def get_deposit_manager(self) -> VaultDepositManager:
        """Reject unsupported generic transaction construction."""

        raise NotImplementedError(YIELD_BASIS_UNSUPPORTED_FLOW_REASON)

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Return the contextual YieldBasis reader."""

        del stateful
        return YieldBasisHistoricalReader(self)

    def get_withdrawal_period(self) -> WithdrawalPeriod:
        """Declare direct YieldBasis LT withdrawals as immediate.

        The LT withdrawal path has no request queue, epoch or contract-enforced
        cooldown. This timing declaration does not guarantee an executable
        quote: live pool state, slippage and the AMM kill switch still decide
        whether normal or emergency withdrawal is appropriate.

        :return:
            Shared zero-delay withdrawal-period metadata.

        .. seealso::

            `YieldBasis withdrawal flow <https://docs.yieldbasis.com/dev/action-flows/withdraw>`__.
        """

        return INSTANT_WITHDRAWAL_PERIOD

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return no fixed management fee; allocation is internal."""

        del block_identifier
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return no fixed performance fee; allocation is internal."""

        del block_identifier
        return None

    def _estimate_usd_stablecoin_conversion_cost(self) -> Percent:
        """Return the fixed conversion estimate for this market's token.

        The reviewed asset address is immutable, so fee metadata needs no RPC
        read and remains independent of the requested historical block.

        :return:
            One-way generic USD-stablecoin conversion cost.
        """

        review = self.market_review
        if review is None:
            message = f"YieldBasis LT {self.address} has no reviewed underlying token"
            raise ValueError(message)
        return estimate_usd_stablecoin_swap_cost(review.asset_address)

    def get_deposit_fee(self, block_identifier: BlockIdentifier) -> Percent:
        """Return the assumed USD-stablecoin-to-underlying entry cost.

        YieldBasis LTs receive their volatile underlying asset. For comparable
        USD returns, this accessor assumes a generic stablecoin is converted to
        that token for a fixed 10 bps. The estimate excludes price impact, gas
        and MEV and is applied only at the investment endpoint.

        :param block_identifier:
            Ignored because the baseline is constant rather than historical.
        :return:
            Entry cost fraction, currently ``0.001`` or 10 bps.
        """

        del block_identifier
        return self._estimate_usd_stablecoin_conversion_cost()

    def get_withdraw_fee(self, block_identifier: BlockIdentifier) -> Percent:
        """Return the assumed underlying-to-USD-stablecoin exit cost.

        ``preview_withdraw`` returns WBTC, cbBTC, tBTC or WETH. The primary
        historical share price already includes TRD but remains gross of the
        fixed 10-bps conversion estimate exposed here.

        :param block_identifier:
            Ignored because the baseline is constant rather than historical.
        :return:
            Exit cost fraction, currently ``0.001`` or 10 bps.
        """

        del block_identifier
        return self._estimate_usd_stablecoin_conversion_cost()

    def get_share_price_source(self) -> PriceSource:
        """Classify the redemption preview and oracle state-read source."""

        return PriceSource.smart_contract_state

    def fetch_deposit_open(self) -> bool | None:
        """Return closed only when the AMM kill switch is active.

        An enabled market can still reject a protocol-specific deposit quote,
        minimum amount or account operation, so the adapter deliberately
        reports ``None`` rather than promising that a generic deposit is open.
        """

        is_killed = bool(self.fetch_amm(self.default_block_identifier).functions.is_killed().call(block_identifier=self.default_block_identifier))
        return False if is_killed else None

    def fetch_deposit_closed_reason(self) -> str | None:
        """Explain when the market's AMM kill switch closes deposits."""

        if self.fetch_deposit_open() is False:
            return "YieldBasis AMM market is killed; existing LT history remains valued"
        return None

    def is_whitelisted_deposit(self) -> bool:
        """Return the permissionless market assumption."""

        return False

    def get_link(self, referral: str | None = None) -> str:
        """Return the official YieldBasis Earn page."""

        del referral
        return "https://yieldbasis.com/earn"
