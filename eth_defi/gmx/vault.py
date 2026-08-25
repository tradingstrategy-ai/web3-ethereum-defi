"""Read-only GMX V2 GM and GLV vault adapters.

GM and GLV ERC-20 tokens are liquidity-provider shares, not ERC-4626 vaults.
Their historical USD share-price equivalent comes from GMX value-and-supply
events whose ratio normalises changes in liquidity and share count.
Asynchronous deposit and withdrawal transactions are outside this dataset
integration.

See `GMX liquidity documentation <https://docs.gmx.io/docs/providing-liquidity/>`__.
"""

# The adapter deliberately implements the broad VaultBase read-only surface.
# ruff: noqa: ARG002, FBT001, FBT002, PLR0904, PLR0917, PLR6301

import datetime
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.gmx.historical_context import GMXHistoricalContextStore, get_gmx_historical_context_path
from eth_defi.gmx.historical_oracle import GMXHistoricalSharePriceObservation
from eth_defi.gmx.links import get_gmx_pool_details_link
from eth_defi.gmx.vault_catalog import GMX_CHAIN_NAMES_BY_ID
from eth_defi.token import USDC_NATIVE_TOKEN, TokenDetails, fetch_erc20_details
from eth_defi.types import Percent
from eth_defi.vault.base import TradingUniverse, VaultBase, VaultFlowManager, VaultHistoricalRead, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec, WithdrawalDelayType, WithdrawalPeriod
from eth_defi.vault.deposit_redeem import VaultDepositManager
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.lower_case_dict import LowercaseDict

#: Explanation returned for unsupported generic GMX flow operations.
GMX_UNSUPPORTED_FLOW_REASON: str = "GMX liquidity deposits and withdrawals are asynchronous ExchangeRouter requests and are not supported by the generic vault transaction adapter"

#: Explanation returned for unsupported isolated GMX NAV reads.
GMX_HISTORICAL_READER_REASON: str = "Use the GMX historical event reader through the common vault price scanner"

#: Non-binding operational estimate for a GMX keeper-executed liquidity request.
#:
#: GMX documents the two-phase request lifecycle as taking typically a few
#: seconds, but gives no contractual completion bound. One minute leaves a
#: conservative UI buffer for request inclusion, oracle handling and keeper
#: execution; it is not a claim that a redemption will complete in one minute.
#: See https://docs.gmx.io/docs/api/contracts/architecture/
GMX_REQUEST_SETTLEMENT_ESTIMATE = datetime.timedelta(minutes=1)


class GMXHistoricalReader(VaultHistoricalReader):
    """Read cached GMX value-and-supply observations."""

    @property
    def uses_contextual_history(self) -> bool:
        """Select the protocol-sourced history branch in the common scanner."""

        return True

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Return no static calls because GMX history is event sourced."""

        return ()

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        """Reject the static multicall path for GMX.

        :raises RuntimeError:
            Always; :meth:`fetch_contextual_historical_reads` owns GMX reads.
        """

        message = "GMX historical prices come from cached value-and-supply events"
        raise RuntimeError(message)

    def _create_share_price_read(self, observation: GMXHistoricalSharePriceObservation) -> VaultHistoricalRead:
        """Create a common historical row from one GMX value event."""

        timestamp = datetime.datetime.fromtimestamp(observation.block_timestamp, tz=datetime.UTC).replace(tzinfo=None)
        return VaultHistoricalRead(
            vault=self.vault,
            block_number=observation.block_number,
            timestamp=timestamp,
            share_price=observation.share_price,
            total_assets=observation.total_assets,
            total_supply=observation.total_supply,
            performance_fee=None,
            management_fee=None,
            errors=None,
            deposits_open=None,
            redemption_open=None,
        )

    def fetch_contextual_historical_reads(
        self,
        start_block: int,
        end_block: int,
        step: int,
    ) -> Iterable[VaultHistoricalRead]:
        """Yield GMX observations downsampled to common block buckets.

        :param start_block:
            Inclusive archive range boundary.
        :param end_block:
            Exclusive archive range boundary.
        :param step:
            Common scan's positive block bucket width.
        :return:
            Supply-normalised GM or GLV historical rows.
        """

        with GMXHistoricalContextStore(self.vault.historical_context_path) as store:
            observations = store.iter_share_prices(
                chain_id=self.vault.chain_id,
                product_address=self.vault.address,
                start_block=start_block,
                end_block=end_block,
                step=step,
            )
            yield from (self._create_share_price_read(observation) for observation in observations)


class GMXVaultBase(VaultBase):
    """Common read-only adapter surface for GM and GLV ERC-20 shares."""

    #: Protocol feature selecting the concrete GM or GLV adapter.
    feature: ERC4626Feature

    #: Lowercase GMX product kind used in exported metadata.
    product_type: str

    def __init__(
        self,
        web3: Web3,
        spec: VaultSpec,
        token_cache: dict | None = None,
        features: set[ERC4626Feature] | None = None,
        default_block_identifier: BlockIdentifier | None = None,
        require_denomination_token: bool = False,
    ) -> None:
        """Create a GMX share-token adapter.

        :param web3:
            Arbitrum One or Avalanche connection.
        :param spec:
            Chain and GM/GLV token address.
        :param token_cache:
            Optional shared token metadata cache.
        :param features:
            Persisted GMX product features.
        :param default_block_identifier:
            Retained shared-adapter compatibility option.
        :param require_denomination_token:
            Retained shared-adapter compatibility option.
        """

        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        if spec.chain_id not in GMX_CHAIN_NAMES_BY_ID:
            raise ValueError(f"Unsupported GMX V2 chain: {spec.chain_id}")
        self.web3 = web3
        self.spec = spec
        self.features = set(features or {self.feature}) | {ERC4626Feature.share_price_equivalence}
        self.default_block_identifier = default_block_identifier or "latest"
        self.historical_context_path: Path = get_gmx_historical_context_path()

    @property
    def chain_id(self) -> int:
        """Return the EVM deployment chain."""

        return self.spec.chain_id

    @property
    def address(self) -> HexAddress:
        """Return the checksum GM or GLV share-token address."""

        return HexAddress(Web3.to_checksum_address(self.spec.vault_address))

    @property
    def vault_address(self) -> HexAddress:
        """Return the scanner-compatible share-token address."""

        return self.address

    @property
    def name(self) -> str:
        """Return the onchain share-token name."""

        return self.share_token.name

    @property
    def symbol(self) -> str:
        """Return the onchain share-token symbol."""

        return self.share_token.symbol

    @property
    def short_description(self) -> None:
        return None

    def fetch_share_token(self) -> TokenDetails:
        """Fetch GM or GLV ERC-20 metadata."""

        return fetch_erc20_details(self.web3, self.address, chain_id=self.chain_id, cache=self.token_cache, cause_diagnostics_message=f"GMX V2 {self.product_type} share {self.address}")

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Return the GM or GLV ERC-20 token address."""

        return self.address

    def fetch_denomination_token_address(self) -> HexAddress:
        """Return native USDC as the catalogue display denomination."""

        return HexAddress(Web3.to_checksum_address(USDC_NATIVE_TOKEN[self.chain_id]))

    def fetch_denomination_token(self) -> TokenDetails:
        """Fetch native USDC metadata for the catalogue display denomination."""

        return fetch_erc20_details(
            self.web3,
            self.fetch_denomination_token_address(),
            chain_id=self.chain_id,
            cache=self.token_cache,
            cause_diagnostics_message="GMX USDC display denomination",
        )

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Read decimal-scaled GM or GLV outstanding shares."""

        raw_supply = self.share_token.contract.functions.totalSupply().call(block_identifier=block_identifier)
        return self.share_token.convert_to_decimals(raw_supply)

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Reject an isolated live NAV read outside the historical pipeline."""

        raise NotImplementedError(GMX_HISTORICAL_READER_REASON)

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Reject an isolated live TVL read outside the historical pipeline."""

        raise NotImplementedError(GMX_HISTORICAL_READER_REASON)

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Reject an isolated live share-price read outside the historical pipeline."""

        raise NotImplementedError(GMX_HISTORICAL_READER_REASON)

    def fetch_info(self) -> VaultInfo:
        """Return scanner-compatible GMX product metadata."""

        return {"token": self.address, "chain_id": self.chain_id, "product_type": self.product_type, "denomination": "USDC"}

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Export USDC display denomination and GMX product metadata."""

        return {
            "Denomination": "USDC",
            "_denomination_token": self.fetch_denomination_token().export(),
            "_synthetic_usd_denomination": False,
            "_gmx_product_type": self.product_type,
            "_nav_source": "GMX value-and-supply events",
            "_nav_available": False,
            "_historical_nav_available": True,
        }

    def fetch_portfolio(self, universe: TradingUniverse, block_identifier: BlockIdentifier | None = None) -> VaultPortfolio:
        """Return no generic spot portfolio for protocol-managed liquidity."""

        return VaultPortfolio(spot_erc20=LowercaseDict())

    def has_block_range_event_support(self) -> bool:
        """Return whether generic vault flow events are supported."""

        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        """Return whether generic automatic position distribution applies."""

        return False

    def get_flags(self) -> set[VaultFlag]:
        """Return GMX market-making and liquidity-provision flags.

        GM and GLV holders provide the inventory against which GMX trading is
        settled. Their pool claim receives part of protocol fees and trader
        losses, and bears trader profits.

        :return:
            Manual flags plus the two strategy classifications.
        """

        return set(super().get_flags()) | {VaultFlag.market_making, VaultFlag.liquidity_provision}

    def get_flow_manager(self) -> VaultFlowManager:
        """Reject unsupported generic flow accounting."""

        raise NotImplementedError(GMX_UNSUPPORTED_FLOW_REASON)

    def get_deposit_manager(self) -> VaultDepositManager:
        """Reject unsupported asynchronous transaction construction."""

        raise NotImplementedError(GMX_UNSUPPORTED_FLOW_REASON)

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Return the sparse GMX event reader."""

        return GMXHistoricalReader(self)

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent:
        """Return the zero depositor-facing management fee."""

        # GMX execution, swap and price-impact costs are transaction costs, not
        # manager fees exposed by VaultBase's management/performance interface.
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent:
        """Return the zero depositor-facing performance fee."""

        return 0.0

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Return no fixed lock-up period."""

        return None

    def get_withdrawal_period(self) -> WithdrawalPeriod:
        """Describe GMX's asynchronous, keeper-executed redemption lifecycle.

        GMX liquidity withdrawals are submitted as requests and execute after
        a keeper supplies the required oracle prices. The official architecture
        documentation describes this as typically taking a few seconds, but
        the protocol does not provide a binding completion deadline: liquidity,
        oracle availability, gas prices and keeper execution can extend the
        wait. We therefore expose a one-minute non-binding estimate for the
        website while leaving the contractual minimum and maximum unset.

        :return:
            Asynchronous withdrawal type with a one-minute settlement estimate.

        .. seealso::

            `GMX architecture <https://docs.gmx.io/docs/api/contracts/architecture/>`__.
        """

        return WithdrawalPeriod(
            min_period=None,
            max_period=None,
            delay_type=WithdrawalDelayType.delay,
            estimated_settlement=GMX_REQUEST_SETTLEMENT_ESTIMATE,
        )

    def fetch_deposit_open(self) -> bool | None:
        """Return unknown for enabled markets whose dynamic caps can still reject a deposit.

        The catalogue synchroniser exports ``False`` when GMX disables a
        product. For enabled markets, a successful deposit can still depend on
        dynamic pool caps and the deposit PnL-factor limit, so the adapter must
        not report an unconditional ``True``.

        :return:
            ``None`` until a per-market executable deposit quote is available.
        """

        return None

    def get_link(self, referral: str | None = None) -> str:
        """Return the direct GMX pool-details page for this share token.

        Both GM and GLV products use GMX's ``market`` URL query parameter. The
        link includes the deployment chain so the GMX interface selects the
        correct network before resolving the product address.

        :param referral:
            Ignored because GMX's pool-details route does not accept the
            common vault referral parameter.
        :return:
            Direct GMX pool-details URL with the deposit view selected.

        .. seealso::

            `GMX interface pool-details route <https://github.com/gmx-io/gmx-interface/blob/release/src/pages/PoolsDetails/PoolsDetails.tsx>`__.
        """

        return get_gmx_pool_details_link(self.chain_id, self.address)

    def is_whitelisted_deposit(self) -> bool:
        """Return whether GMX LP access is allowlisted."""

        return False


class GMXMarketVault(GMXVaultBase):
    """One GMX V2 GM market-token liquidity-provider vault."""

    #: Select GM market-token handling.
    feature = ERC4626Feature.gmx_gm

    #: Protocol-native product kind.
    product_type = "gm"


class GMXLiquidityVault(GMXVaultBase):
    """One GMX V2 GLV multi-market liquidity-provider vault."""

    #: Select GLV multi-market handling.
    feature = ERC4626Feature.gmx_glv

    #: Protocol-native product kind.
    product_type = "glv"
