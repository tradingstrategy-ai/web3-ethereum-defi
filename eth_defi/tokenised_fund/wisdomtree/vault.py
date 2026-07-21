"""Read-only adapter for WisdomTree permissioned tokenised-fund shares.

WTGXX's verified token implementation is a revocable compliance ERC-20, not
an ERC-4626 contract. Official NAV is available through WisdomTree DataSpan:
https://docs.wisdomtreeconnect.com/dataspan/nav
"""

# Adapter methods deliberately mirror VaultBase's broad shared interface.
# ruff: noqa: ARG002, FBT001, FBT002, PLR0904, PLR0917, PLR6301

import datetime
from bisect import bisect_right
from decimal import Decimal

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.tokenised_fund.vault import TokenisedFundVault
from eth_defi.tokenised_fund.wisdomtree.constants import WISDOMTREE_PRODUCTS, WisdomTreeProduct
from eth_defi.tokenised_fund.wisdomtree.historical import WisdomTreeVaultHistoricalReader
from eth_defi.tokenised_fund.wisdomtree.nav import WisdomTreeNAVPoint, fetch_wisdomtree_nav_history
from eth_defi.types import Percent
from eth_defi.vault.base import TradingUniverse, VaultDepositManager, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.lower_case_dict import LowercaseDict

WISDOMTREE_RESTRICTED_FLOW_REASON = "WisdomTree subscriptions and redemptions require an approved, permissioned wallet and issuer settlement"
WISDOMTREE_NAV_SOURCE = "wisdomtree_dataspan_nav"


class WisdomTreeVaultInfo(VaultInfo, total=False):
    """WisdomTree-specific scan metadata."""

    token: HexAddress
    chain_id: int
    nav_source: str


class WisdomTreeVault(TokenisedFundVault):
    """Read WTGXX supply and issuer-published NAV without public flow support."""

    def __init__(self, web3: Web3, spec: VaultSpec, token_cache: dict | None = None, features: set[ERC4626Feature] | None = None, default_block_identifier: BlockIdentifier | None = None, require_denomination_token: bool = False):
        """Create a product adapter.

        :param web3: Ethereum Web3 connection.
        :param spec: Chain and WTGXX token address.
        :param token_cache: Optional ERC-20 metadata cache.
        :param features: Routing features.
        :param default_block_identifier: Retained shared-adapter setting.
        :param require_denomination_token: Retained shared-adapter setting.
        """

        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        self.web3 = web3
        self.spec = spec
        self.features = features or {ERC4626Feature.wisdomtree_like}
        self.default_block_identifier = default_block_identifier
        try:
            self.product: WisdomTreeProduct = WISDOMTREE_PRODUCTS[spec.chain_id, HexAddress(spec.vault_address.lower())]
        except KeyError as error:
            raise RuntimeError(f"Unsupported WisdomTree product: chain={spec.chain_id}, token={spec.vault_address}") from error
        self._nav_history: tuple[WisdomTreeNAVPoint, ...] | None = None

    @property
    def chain_id(self) -> int:
        """Return the deployment chain id."""

        return self.spec.chain_id

    @property
    def address(self) -> HexAddress:
        """Return the checksum share-token address."""

        return HexAddress(Web3.to_checksum_address(self.product.token))

    @property
    def vault_address(self) -> HexAddress:
        """Return the scanner-compatible vault address."""

        return self.address

    @property
    def name(self) -> str:
        """Return the token name with product fallback."""

        return self.share_token.name or self.product.product_name

    @property
    def symbol(self) -> str:
        """Return the token symbol."""

        return self.share_token.symbol or self.product.symbol

    @property
    def description(self) -> str | None:
        """Return product description."""

        return "Tokenised shares in WisdomTree's Treasury money market fund"

    @property
    def short_description(self) -> str | None:
        """Return concise product description."""

        return "U.S. Treasury money-market strategy targeting income, capital preservation and liquidity"

    @property
    def manager_name(self) -> str | None:
        """Return the issuer and asset manager."""

        return "WisdomTree"

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Return the ERC-20 share token address.

        :param block_identifier: Ignored compatibility parameter.
        :return: WTGXX token address.
        """

        return self.address

    def fetch_share_token(self) -> TokenDetails:
        """Fetch WTGXX ERC-20 metadata.

        :return: Token details for WTGXX.
        """

        return fetch_erc20_details(self.web3, self.address, chain_id=self.chain_id, raise_on_error=False, cache=self.token_cache, cause_diagnostics_message=f"WisdomTree share token for vault {self.address}")

    def fetch_denomination_token_address(self) -> HexAddress | None:
        """Return no ERC-20 denomination token.

        :return: ``None`` because subscriptions settle through WisdomTree.
        """

        return None

    def fetch_denomination_token(self) -> TokenDetails | None:
        """Return no denomination ERC-20 metadata.

        :return: Always ``None``.
        """

        return None

    def _fetch_nav_history(self) -> tuple[WisdomTreeNAVPoint, ...]:
        """Lazily load issuer-published NAV observations."""

        if self._nav_history is None:
            self._nav_history = tuple(fetch_wisdomtree_nav_history(self.product.symbol))
        return self._nav_history

    def fetch_share_price_at(self, timestamp: datetime.datetime) -> Decimal:
        """Return the latest official NAV on or before a timestamp.

        :param timestamp: Naive UTC target time.
        :return: Official USD NAV per WTGXX share.
        :raise RuntimeError: If no issuer NAV exists at the requested time.
        """

        history = self._fetch_nav_history()
        timestamps = tuple(point.timestamp for point in history)
        index = bisect_right(timestamps, timestamp) - 1
        if index < 0:
            raise RuntimeError(f"No WisdomTree NAV observation on or before {timestamp.isoformat()}")
        return history[index].nav

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch the issuer-published NAV matching an on-chain block.

        The WisdomTree API is date-based rather than block-based. Historical
        block reads therefore resolve the block timestamp before selecting the
        latest official NAV published on or before that time.

        :param block_identifier: Block whose timestamp selects the NAV observation.
        :return: Official NAV per share matching the requested block.
        """

        if block_identifier == "latest":
            return self._fetch_nav_history()[-1].nav

        block = self.web3.eth.get_block(block_identifier)
        timestamp = datetime.datetime.fromtimestamp(block["timestamp"], tz=datetime.UTC).replace(tzinfo=None)
        return self.fetch_share_price_at(timestamp)

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch total ERC-20 supply.

        :param block_identifier: Block for the token state read.
        :return: Human-readable WTGXX supply.
        """

        return self.share_token.convert_to_decimals(self.share_token.contract.functions.totalSupply().call(block_identifier=block_identifier))

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Calculate NAV from official share NAV and on-chain supply.

        :param block_identifier: Token supply block.
        :return: USD NAV represented by the scanned token supply.
        """

        return self.fetch_total_supply(block_identifier) * self.fetch_share_price(block_identifier)

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Return calculated tokenised-share NAV.

        :param block_identifier: Token supply block.
        :return: USD total assets.
        """

        return self.fetch_total_assets(block_identifier)

    def fetch_info(self) -> WisdomTreeVaultInfo:
        """Return adapter diagnostics.

        :return: Token and NAV-source metadata.
        """

        return WisdomTreeVaultInfo(token=self.address, chain_id=self.chain_id, nav_source=WISDOMTREE_NAV_SOURCE)

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Return explicit read-only and NAV provenance diagnostics.

        :return: Scan row additions.
        """

        return {"Denomination": "USD", "_notes": self.get_notes(), "_deposit_closed_reason": self.fetch_deposit_closed_reason(), "_redemption_closed_reason": self.fetch_redemption_closed_reason(), "_nav_source": WISDOMTREE_NAV_SOURCE, "_synthetic_usd_denomination": True}

    def fetch_portfolio(self, universe: TradingUniverse, block_identifier: BlockIdentifier | None = None) -> VaultPortfolio:
        """Return no on-chain portfolio holdings.

        :param universe: Ignored.
        :param block_identifier: Ignored.
        :return: Empty spot portfolio.
        """

        return VaultPortfolio(spot_erc20=LowercaseDict())

    def has_block_range_event_support(self) -> bool:
        """Return whether flow-event accounting is implemented."""

        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        """Return whether deposits are distributed on-chain."""

        return False

    def get_flow_manager(self) -> VaultFlowManager:
        """Reject incomplete public flow support.

        :raise NotImplementedError: Always.
        """

        message = "WisdomTree flow accounting is not implemented"
        raise NotImplementedError(message)

    def get_deposit_manager(self) -> VaultDepositManager:
        """Reject incomplete public subscription/redemption support.

        :raise NotImplementedError: Always.
        """

        message = "WisdomTree public subscription/redemption is not implemented"
        raise NotImplementedError(message)

    def fetch_deposit_closed_reason(self) -> str | None:
        """Explain why public deposits are unavailable.

        :return: Permissioning and issuer-settlement explanation.
        """

        return WISDOMTREE_RESTRICTED_FLOW_REASON

    def fetch_redemption_closed_reason(self) -> str | None:
        """Explain why public redemptions are unavailable.

        :return: Permissioning and issuer-settlement explanation.
        """

        return WISDOMTREE_RESTRICTED_FLOW_REASON

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Get the official-NAV historical reader.

        :param stateful: Accepted shared-adapter setting.
        :return: Historical reader.
        """

        return WisdomTreeVaultHistoricalReader(self, stateful=stateful)

    def get_fee_data(self) -> FeeData:
        """Return the published fund expense ratio.

        :return: Internalised annual expense representation.
        """

        return FeeData(fee_mode=VaultFeeMode.internalised_skimming, management=self.product.expense_ratio, performance=0, deposit=0, withdraw=0)

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return the published annual expense ratio.

        :param block_identifier: Ignored.
        :return: WTGXX expense ratio.
        """

        return self.get_fee_data().management

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return the separate performance fee.

        :param block_identifier: Ignored.
        :return: Zero; no separate performance fee is published.
        """

        return self.get_fee_data().performance

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Return unknown lock-up duration.

        :return: ``None``.
        """

        return None

    def get_link(self, referral: str | None = None) -> str:
        """Return the official WTGXX page.

        :param referral: Ignored.
        :return: Official product URL.
        """

        return self.product.homepage
