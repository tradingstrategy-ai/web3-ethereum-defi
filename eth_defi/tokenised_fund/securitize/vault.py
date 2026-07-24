"""Securitize Digital Securities Protocol tokenised-fund adapter."""

# Adapter classes intentionally mirror :class:`VaultBase` method signatures.
# ruff: noqa: ARG002, FBT001, FBT002, PLR0904, PLR0917, PLR6301

import datetime
from decimal import Decimal
from functools import cached_property

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.tokenised_fund.securitize.description import BUIDL_ETHEREUM, SECURITIZE_PRODUCTS
from eth_defi.tokenised_fund.securitize.historical import SecuritizeVaultHistoricalReader
from eth_defi.tokenised_fund.securitize.redstone import REDSTONE_SECURITIZE_FEEDS, RedstoneSecuritizeFeed, fetch_redstone_feed_contract, fetch_redstone_price_at
from eth_defi.tokenised_fund.vault import TokenisedFundVault
from eth_defi.types import Percent
from eth_defi.vault.base import TradingUniverse, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.fee import BROKEN_FEE_DATA, FeeData
from eth_defi.vault.lower_case_dict import LowercaseDict

#: Backwards-compatible aliases for the first registered Securitize product.
BUIDL_ETHEREUM_CHAIN_ID = BUIDL_ETHEREUM.chain_id
BUIDL_ETHEREUM_ADDRESS = BUIDL_ETHEREUM.token
BUIDL_ESTIMATED_NAV_PER_SHARE = BUIDL_ETHEREUM.estimated_nav_per_share
BUIDL_NAV_SOURCE = BUIDL_ETHEREUM.nav_source
BUIDL_PRODUCT_NAME = BUIDL_ETHEREUM.product_name
BUIDL_HOMEPAGE = BUIDL_ETHEREUM.homepage
SECURITIZE_HOMEPAGE = "https://securitize.io/"
SECURITIZE_RESTRICTED_FLOW_REASON = "Securitize DSToken subscriptions, redemptions and transfers require approved investors and compliance checks"
SECURITIZE_NAV_UNAVAILABLE_ERROR_PREFIX = "No on-chain NAV source configured for Securitize DSToken"


class SecuritizeVaultInfo(VaultInfo, total=False):
    """Securitize DSToken metadata exposed to scan consumers."""

    #: DSToken address.
    token: HexAddress

    #: EVM chain id.
    chain_id: int

    #: Whether the USD denomination is an adapter estimate.
    synthetic_usd_denomination: bool

    #: NAV source identifier.
    nav_source: str

    #: Whether NAV is estimated.
    nav_estimated: bool


def export_securitize_usd_denomination(chain_id: int) -> dict[str, object]:
    """Export synthetic USD denomination metadata.

    :param chain_id:
        EVM chain id of the product.
    :return:
        Token-like USD metadata without an ERC-20 address.
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


class SecuritizeVault(TokenisedFundVault):
    """Scan-only adapter for Securitize DS Protocol tokenised securities.

    The adapter reads share supply from the ERC-20-compatible token. BUIDL has
    an explicit one-USD NAV estimate; recognised variable-NAV funds read a
    reviewed RedStone on-chain push feed at the same archive block.
    """

    def __init__(
        self,
        web3: Web3,
        spec: VaultSpec,
        token_cache: dict | None = None,
        features: set[ERC4626Feature] | None = None,
        default_block_identifier: BlockIdentifier | None = None,
        require_denomination_token: bool = False,
    ):
        """Create a Securitize DSToken adapter.

        :param web3:
            Web3 connection.
        :param spec:
            Chain and DSToken address.
        :param token_cache:
            ERC-20 token metadata cache.
        :param features:
            Shared pipeline feature flags.
        :param default_block_identifier:
            Default metadata block, retained for adapter compatibility.
        :param require_denomination_token:
            Whether a missing ERC-20 denomination is an error.
        """

        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        self.web3 = web3
        self.spec = spec
        self.features = features or {ERC4626Feature.securitize_like}
        self.default_block_identifier = default_block_identifier
        self.product = SECURITIZE_PRODUCTS.get((spec.chain_id, HexAddress(spec.vault_address.lower())))

    @property
    def chain_id(self) -> int:
        """Return the EVM chain id.

        :return:
            Chain id from the vault specification.
        """

        return self.spec.chain_id

    @property
    def address(self) -> HexAddress:
        """Return the DSToken contract address.

        :return:
            Checksum DSToken address.
        """

        return HexAddress(Web3.to_checksum_address(self.spec.vault_address))

    @property
    def vault_address(self) -> HexAddress:
        """Return a scanner-compatible alias for the DSToken address.

        :return:
            DSToken address.
        """

        return self.address

    @property
    def is_buidl(self) -> bool:
        """Check whether this token is a supported BUIDL share class.

        :return:
            ``True`` for a reviewed BUIDL deployment.
        """

        return self.product is not None and self.product.nav_source == BUIDL_ETHEREUM.nav_source

    @property
    def redstone_feed(self) -> RedstoneSecuritizeFeed | None:
        """Return the reviewed RedStone feed for this product.

        :return:
            Feed configuration, or ``None`` for fixed-price and unpriced
            products.
        """

        return REDSTONE_SECURITIZE_FEEDS.get((self.chain_id, HexAddress(self.address.lower())))

    @cached_property
    def redstone_feed_contract(self) -> Contract | None:
        """Create the product's RedStone push-feed contract.

        :return:
            Chainlink-compatible feed contract, or ``None`` when no reviewed
            feed is configured.
        """

        return fetch_redstone_feed_contract(self.web3, self.redstone_feed) if self.redstone_feed is not None else None

    @property
    def name(self) -> str:
        """Return the token name.

        :return:
            On-chain token name, registered product name or a DSToken fallback.
        """

        return self.share_token.name or (self.product.product_name if self.product else "Securitize DSToken")

    @property
    def symbol(self) -> str:
        """Return the DSToken symbol.

        :return:
            On-chain token symbol.
        """

        return self.share_token.symbol

    @property
    def description(self) -> str | None:
        """Return a registered product description when available.

        :return:
            Product description or a DS Protocol fallback.
        """

        return self.product.description if self.product else "Permissioned ERC-20 security token issued with Securitize's Digital Securities Protocol."

    @property
    def short_description(self) -> str | None:
        """Return a compact product description.

        :return:
            Short product description.
        """

        return self.product.short_description if self.product else "Unclassified Securitize fund strategy"

    @property
    def manager_name(self) -> str | None:
        """Return the known product issuer or protocol operator.

        :return:
            Registered fund manager, otherwise Securitize.
        """

        return self.product.manager_name if self.product else "Securitize"

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Return the DSToken address.

        :param block_identifier:
            Accepted for historical-reader compatibility.
        :return:
            DSToken address.
        """

        return self.address

    def fetch_share_token(self) -> TokenDetails:
        """Fetch DSToken ERC-20 metadata.

        :return:
            Token details for the DSToken share token.
        """

        return fetch_erc20_details(
            self.web3,
            self.address,
            chain_id=self.chain_id,
            raise_on_error=False,
            cache=self.token_cache,
            cause_diagnostics_message=f"Securitize DSToken share token for vault {self.address}",
        )

    def fetch_denomination_token_address(self) -> HexAddress | None:
        """Return no ERC-20 denomination token.

        :return:
            ``None`` because DSToken product accounting is not ERC-4626.
        """

        return None

    def fetch_denomination_token(self) -> TokenDetails | None:
        """Return no ERC-20 denomination token.

        :return:
            ``None`` because product subscription assets are off-adapter.
        """

        return None

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch the product NAV/share estimate.

        :param block_identifier:
            Historical or latest block identifier.
        :return:
            Registered product NAV/share estimate.
        :raises NotImplementedError:
            If the DSToken has no on-chain or static adapter NAV source.
        """

        if self.product and self.product.estimated_nav_per_share is not None:
            return self.product.estimated_nav_per_share
        if self.redstone_feed is not None:
            return fetch_redstone_price_at(self.web3, self.redstone_feed, block_identifier).share_price
        raise NotImplementedError(f"{SECURITIZE_NAV_UNAVAILABLE_ERROR_PREFIX} {self.address}")

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch outstanding DSToken supply.

        :param block_identifier:
            Historical or latest block identifier.
        :return:
            Human-readable token supply.
        """

        raw_supply = self.share_token.contract.functions.totalSupply().call(block_identifier=block_identifier)
        return self.share_token.convert_to_decimals(raw_supply)

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Estimate TVL from DSToken supply and NAV/share.

        :param block_identifier:
            Historical or latest block identifier.
        :return:
            USD-denominated total assets.
        """

        return self.fetch_total_supply(block_identifier) * self.fetch_share_price(block_identifier)

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Return the estimated fund NAV.

        :param block_identifier:
            Historical or latest block identifier.
        :return:
            USD-denominated total assets.
        """

        return self.fetch_total_assets(block_identifier)

    def fetch_info(self) -> SecuritizeVaultInfo:
        """Return Securitize product metadata.

        :return:
            Token, chain and NAV source metadata.
        """

        product = self.product
        return SecuritizeVaultInfo(
            token=self.address,
            chain_id=self.chain_id,
            synthetic_usd_denomination=product is not None and product.estimated_nav_per_share is not None,
            nav_source=product.nav_source if product else "unconfigured",
            nav_estimated=product is not None and product.estimated_nav_per_share is not None,
        )

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Return scanner diagnostics for DSToken product assumptions.

        :return:
            Private scanner fields for registered product assumptions.
        """

        product = self.product
        has_synthetic_usd_denomination = product is not None and product.estimated_nav_per_share is not None
        return {
            "Denomination": product.denomination if product else None,
            "_denomination_token": export_securitize_usd_denomination(self.chain_id) if has_synthetic_usd_denomination else None,
            "_notes": self.get_notes(),
            "_deposit_closed_reason": self.fetch_deposit_closed_reason(),
            "_redemption_closed_reason": self.fetch_redemption_closed_reason(),
            "_nav_source": product.nav_source if product else "unconfigured",
            "_nav_estimated": product is not None and product.estimated_nav_per_share is not None,
            "_synthetic_usd_denomination": has_synthetic_usd_denomination,
        }

    def fetch_portfolio(self, universe: TradingUniverse, block_identifier: BlockIdentifier | None = None) -> VaultPortfolio:
        """Return an empty portfolio for a tokenised fund.

        :param universe:
            Ignored because fund assets are off-chain.
        :param block_identifier:
            Ignored because fund assets are off-chain.
        :return:
            Empty spot portfolio.
        """

        return VaultPortfolio(spot_erc20=LowercaseDict())

    def has_block_range_event_support(self) -> bool:
        """Return whether public flow accounting is supported.

        :return:
            ``False`` because DSToken issuance is permissioned.
        """

        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        """Return whether deposits distribute to on-chain positions.

        :return:
            ``False`` because DSTokens do not expose a vault portfolio.
        """

        return False

    def get_flow_manager(self) -> VaultFlowManager:
        """Reject unsupported public flow management.

        :raises NotImplementedError:
            Always, because DSToken flows are permissioned.
        """

        message = "Securitize DSToken flow accounting is not implemented"
        raise NotImplementedError(message)

    def fetch_deposit_closed_reason(self) -> str | None:
        """Return why public subscriptions are unavailable.

        :return:
            Permissioning explanation.
        """

        return SECURITIZE_RESTRICTED_FLOW_REASON

    def fetch_redemption_closed_reason(self) -> str | None:
        """Return why public redemptions are unavailable.

        :return:
            Permissioning explanation.
        """

        return SECURITIZE_RESTRICTED_FLOW_REASON

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Create the DSToken historical reader.

        :param stateful:
            Whether to attach adaptive reader state.
        :return:
            DSToken historical reader.
        """

        return SecuritizeVaultHistoricalReader(self, stateful=stateful)

    def get_fee_data(self) -> FeeData:
        """Return unknown product fee data.

        :return:
            Unknown fee data because DSToken does not contain fund fees.
        """

        return BROKEN_FEE_DATA

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return unknown management fee.

        :param block_identifier:
            Ignored because no on-chain fee accessor exists.
        :return:
            ``None``.
        """

        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return unknown performance fee.

        :param block_identifier:
            Ignored because no on-chain fee accessor exists.
        :return:
            ``None``.
        """

        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Return unknown product lock-up.

        :return:
            ``None`` because redemption terms are product-specific.
        """

        return None

    def get_link(self, referral: str | None = None) -> str:
        """Return the appropriate product or protocol page.

        :param referral:
            Ignored.
        :return:
            Registered product page or Securitize homepage.
        """

        return self.product.homepage if self.product else SECURITIZE_HOMEPAGE
