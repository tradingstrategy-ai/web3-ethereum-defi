"""Read-only VaultBase adapter for YieldBasis unstaked LT shares.

YieldBasis LTs are ERC-20 yb-LP shares with a protocol-specific
``pricePerShare`` oracle rather than standard ERC-4626 conversion methods.
Their common historical price is the fundamental native-asset PPS multiplied
by the market Curve Cryptoswap pool's smoothed crvUSD oracle.
"""

# ruff: noqa: FBT001, FBT002, PLR0904, PLR0917, PLR6301

import datetime
from collections.abc import Iterable
from decimal import Decimal
from functools import cached_property
from pathlib import Path

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import BadFunctionCallOutput, ContractLogicError, Web3Exception

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.types import Percent
from eth_defi.vault.base import TradingUniverse, VaultBase, VaultFlowManager, VaultHistoricalRead, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.deposit_redeem import VaultDepositManager
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.lower_case_dict import LowercaseDict
from eth_defi.vault.price_source import PriceSource
from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags
from eth_defi.yield_basis.addresses import YIELD_BASIS_MARKET_ID_BY_LT, YIELD_BASIS_STABLECOIN
from eth_defi.yield_basis.contracts import fetch_yield_basis_amm, fetch_yield_basis_curve_pool, fetch_yield_basis_factory, fetch_yield_basis_lt
from eth_defi.yield_basis.historical_context import YieldBasisHistoricalContextStore, YieldBasisHistoricalObservation, get_yield_basis_historical_context_path
from eth_defi.yield_basis.metrics import LT_SHARE_SCALE, ORACLE_SCALE, PPS_SCALE
from eth_defi.yield_basis.tags import STRATEGY_TAGS

#: Error shown when a caller requests unsupported generic transaction flows.
YIELD_BASIS_UNSUPPORTED_FLOW_REASON = "YieldBasis deposits and withdrawals use protocol-specific leveraged-pool operations and are not supported by the generic vault transaction adapter"

#: Error shown when a caller bypasses the required contextual-history path.
YIELD_BASIS_HISTORICAL_READER_REASON = "Use the YieldBasis historical context reader through the common vault price scanner"


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
        """Convert one source observation to a common scanner row."""

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
    """Expose one reviewed Ethereum YieldBasis unstaked LT market.

    The adapter reports fundamental LT value in crvUSD while preserving native
    BTC or ETH PPS in the context store. It follows the official
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
            Require the verified crvUSD token where the base interface permits
            a missing denomination token.
        :return:
            None.
        """

        if spec.chain_id != 1:
            raise ValueError(f"YieldBasis is currently supported on Ethereum only, got chain {spec.chain_id}")
        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        self.web3 = web3
        self.spec = spec
        self.features = set(features or {ERC4626Feature.yield_basis_lt}) | {
            ERC4626Feature.yield_basis_lt,
            ERC4626Feature.amm_pool_like,
            ERC4626Feature.share_price_equivalence,
        }
        self.default_block_identifier = default_block_identifier or "latest"
        self.historical_context_path: Path = get_yield_basis_historical_context_path()
        self.lt_contract: Contract = fetch_yield_basis_lt(web3, self.address)

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
        return f"YieldBasis {underlying}/crvUSD leveraged liquidity-provider share"

    @property
    def description(self) -> str:
        """Explain the underlying exposure and accounting unit."""

        underlying = self.fetch_underlying_token().symbol
        return f"YieldBasis supplies {underlying} and borrowed crvUSD to a Curve Cryptoswap pool through its LEVAMM mechanism. This unstaked yb-LP share is reported in crvUSD, remains exposed to {underlying}/crvUSD price moves, and can lose value. The chart uses fundamental value; redemption discounts, leverage, oracle and liquidity conditions can reduce an executable exit."

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

    def fetch_denomination_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Read and verify the Factory stable-side crvUSD address."""

        factory_address = HexAddress(Web3.to_checksum_address(fetch_yield_basis_factory(self.web3).functions.STABLECOIN().call(block_identifier=block_identifier)))
        lt_address = HexAddress(Web3.to_checksum_address(self.lt_contract.functions.STABLECOIN().call(block_identifier=block_identifier)))
        if factory_address.lower() != YIELD_BASIS_STABLECOIN.lower() or lt_address.lower() != factory_address.lower():
            raise ValueError(f"YieldBasis LT {self.address} uses unexpected denomination token Factory={factory_address}, LT={lt_address}")
        return factory_address

    def fetch_denomination_token(self) -> TokenDetails:
        """Fetch verified Ethereum crvUSD metadata."""

        return fetch_erc20_details(self.web3, self.fetch_denomination_token_address(self.default_block_identifier), chain_id=self.chain_id, cache=self.token_cache, cause_diagnostics_message="YieldBasis crvUSD denomination")

    def fetch_curve_pool_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Read the market Curve Cryptoswap pool from the LT."""

        return HexAddress(Web3.to_checksum_address(self.lt_contract.functions.CRYPTOPOOL().call(block_identifier=block_identifier)))

    def fetch_curve_pool(self, block_identifier: BlockIdentifier = "latest") -> Contract:
        """Bind the market's Curve Cryptoswap pool."""

        return fetch_yield_basis_curve_pool(self.web3, self.fetch_curve_pool_address(block_identifier))

    @cached_property
    def historical_curve_pool(self) -> Contract:
        """Bind the LT's immutable Curve pool once for historical sampling.

        ``CRYPTOPOOL`` is an immutable in the reviewed LT implementation, so
        resolving it at the adapter's default block avoids repeating the same
        address call for every historical observation.

        :return:
            Curve pool contract shared by all snapshots for this LT.
        """

        return self.fetch_curve_pool(self.default_block_identifier)

    @cached_property
    def historical_asset_address(self) -> HexAddress:
        """Resolve the LT's immutable underlying asset once.

        ``ASSET_TOKEN`` is an immutable in the reviewed LT implementation and
        cannot change between historical observation blocks.

        :return:
            Checksum address stored with every context observation.
        """

        return self.fetch_underlying_token_address(self.default_block_identifier)

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

    def fetch_asset_crvusd_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Return the Curve oracle price of one native asset in crvUSD."""

        raw_asset_price = int(self.fetch_curve_pool(block_identifier).functions.price_oracle().call(block_identifier=block_identifier))
        if raw_asset_price <= 0:
            raise ValueError(f"YieldBasis returned non-positive Curve oracle for {self.address} at {block_identifier}")
        return Decimal(raw_asset_price) / ORACLE_SCALE

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Return fundamental LT PPS converted to crvUSD."""

        return self.fetch_native_asset_price_per_share(block_identifier) * self.fetch_asset_crvusd_price(block_identifier)

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Return effective supply from ``updated_balances``."""

        raw_supply, _raw_staked = self.lt_contract.functions.updated_balances().call(block_identifier=block_identifier)
        return Decimal(int(raw_supply)) / LT_SHARE_SCALE

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Return fundamental crvUSD equity from PPS and effective supply."""

        return self.fetch_share_price(block_identifier) * self.fetch_total_supply(block_identifier)

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Return fundamental crvUSD NAV."""

        return self.fetch_total_assets(block_identifier)

    def fetch_historical_observation(self, block_identifier: int) -> dict[str, object] | None:
        """Read valuation state and an optional redemption diagnostic.

        Required PPS, supply and Curve-oracle calls must all succeed at the
        same block. A reverting ``preview_withdraw`` is kept as nullable
        context because it does not invalidate fundamental value. A deployed
        market with zero effective supply has no investment observation yet,
        so it returns ``None`` instead of aborting a historical backfill.

        :param block_identifier:
            Historical Ethereum block shared by every contract call.
        :return:
            Keyword values used to create a historical context observation,
            or ``None`` before the market has effective supply.
        """

        raw_pps = int(self.lt_contract.functions.pricePerShare().call(block_identifier=block_identifier))
        raw_effective_supply, raw_staked_supply = (int(value) for value in self.lt_contract.functions.updated_balances().call(block_identifier=block_identifier))
        if raw_pps <= 0:
            raise ValueError(f"YieldBasis LT has no usable PPS at block {block_identifier}")
        if raw_effective_supply == 0:
            if raw_staked_supply != 0:
                raise ValueError(f"YieldBasis LT has staked supply without effective supply at block {block_identifier}")
            return None
        if raw_staked_supply < 0 or raw_staked_supply > raw_effective_supply:
            raise ValueError(f"YieldBasis LT returned invalid staked supply at block {block_identifier}")
        raw_asset_crvusd_price = int(self.historical_curve_pool.functions.price_oracle().call(block_identifier=block_identifier))
        if raw_asset_crvusd_price <= 0:
            raise ValueError(f"YieldBasis Curve oracle is zero at block {block_identifier}")
        preview_shares = min(LT_SHARE_SCALE, raw_effective_supply)
        try:
            raw_redemption_assets = int(self.lt_contract.functions.preview_withdraw(preview_shares).call(block_identifier=block_identifier))
            redemption_missing_reason = None
        except (BadFunctionCallOutput, ContractLogicError, Web3Exception, ValueError, TypeError) as error:
            raw_redemption_assets = None
            redemption_missing_reason = f"preview_withdraw: {error.__class__.__name__}"
        return {
            "lt_address": self.address,
            "asset_address": self.historical_asset_address,
            "raw_asset_crvusd_price": raw_asset_crvusd_price,
            "raw_asset_price_per_share": raw_pps,
            "raw_preview_shares": preview_shares,
            "raw_redemption_assets": raw_redemption_assets,
            "redemption_missing_reason": redemption_missing_reason,
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
            "nav_source": "YieldBasis pricePerShare multiplied by Curve price_oracle",
        }

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Export crvUSD denomination and native asset identity."""

        underlying = self.fetch_underlying_token()
        return {
            "Denomination": "crvUSD",
            "_denomination_token": self.fetch_denomination_token().export(),
            "_synthetic_usd_denomination": False,
            "_yield_basis_market_id": self.market_id,
            "_yield_basis_underlying_token": underlying.address,
            "_yield_basis_underlying_symbol": underlying.symbol,
            "_nav_source": "YieldBasis fundamental PPS x Curve Cryptoswap price_oracle",
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

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return no fixed management fee; allocation is internal."""

        del block_identifier
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return no fixed performance fee; allocation is internal."""

        del block_identifier
        return None

    def get_share_price_source(self) -> PriceSource:
        """Classify the fundamental state read source."""

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
