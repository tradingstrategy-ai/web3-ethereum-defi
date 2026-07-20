"""Read-only adapter for Sygnum Desygnate FILQ fund shares.

FILQ is a permissioned SygToken ERC-20, not an ERC-4626 vault.  Authoritative
sources: https://www.sygnum.com/filq/ and the exact verified implementation
https://sourcify.dev/server/v2/contract/1/0x7030fe438be6ed196b8886616bbf5a245c267339?fields=all.
"""

# ruff: noqa: ARG002, FBT001, FBT002, PLR0904, PLR0917, PLR6301

import datetime
from decimal import Decimal

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.tokenised_fund.sygnum.historical import SygnumVaultHistoricalReader
from eth_defi.tokenised_fund.vault import TokenisedFundVault
from eth_defi.types import Percent
from eth_defi.vault.base import TradingUniverse, VaultDepositManager, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.fee import BROKEN_FEE_DATA, FeeData
from eth_defi.vault.lower_case_dict import LowercaseDict

SYGNUM_RESTRICTED_FLOW_REASON = "FILQ subscriptions, transfers and redemptions require Sygnum-approved wallets and issuer-controlled settlement"
SYGNUM_NAV_UNAVAILABLE_REASON = "FILQ has a configured Sygnum price-feed contract, but no public historical NAV interface has been verified"


class SygnumVaultInfo(VaultInfo, total=False):
    """FILQ scan metadata."""

    token: HexAddress
    chain_id: int
    synthetic_usd_denomination: bool
    nav_source: str
    nav_available: bool


def export_sygnum_usd_denomination(chain_id: int) -> dict[str, object]:
    """Export non-transferable USD accounting metadata.

    :param chain_id: Chain hosting the FILQ proxy.
    :return: Synthetic denomination metadata.
    """

    return {"address": None, "chain": chain_id, "name": "United States Dollar", "symbol": "USD", "decimals": None, "total_supply": None, "extra_data": {"synthetic": True}}


class SygnumVault(TokenisedFundVault):
    """Scan-only adapter for reviewed Sygnum FILQ share classes.

    The public token surface provides ERC-20 supply and price-feed metadata,
    but not a publicly readable, verified historical NAV.  Public dealing
    flows are deliberately unavailable because every transfer and settlement
    is subject to the Sygnum permission manager.
    """

    nav_unavailable_reason = SYGNUM_NAV_UNAVAILABLE_REASON

    def __init__(self, web3: Web3, spec: VaultSpec, token_cache: dict | None = None, features: set[ERC4626Feature] | None = None, default_block_identifier: BlockIdentifier | None = None, require_denomination_token: bool = False):
        """Create a FILQ adapter.

        :param web3: Web3 connection to Ethereum.
        :param spec: Chain and FILQ share-token address.
        :param token_cache: Shared token metadata cache.
        :param features: Classification flags.
        :param default_block_identifier: Default metadata block.
        :param require_denomination_token: Retained for :class:`VaultBase` compatibility.
        """

        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        self.web3, self.spec, self.features = web3, spec, features or {ERC4626Feature.sygnum_like}
        self.default_block_identifier = default_block_identifier

    @property
    def chain_id(self) -> int:
        """Return the configured EVM chain id."""
        return self.spec.chain_id

    @property
    def address(self) -> HexAddress:
        """Return the checksum FILQ share-token address."""
        return HexAddress(Web3.to_checksum_address(self.spec.vault_address))

    @property
    def vault_address(self) -> HexAddress:
        """Return the scanner-compatible share-token address."""
        return self.address

    @property
    def name(self) -> str:
        """Return the on-chain FILQ class name."""
        return self.share_token.name or "Fidelity USD Digital Liquidity Fund"

    @property
    def symbol(self) -> str:
        """Return the on-chain FILQ class symbol."""
        return self.share_token.symbol

    @property
    def description(self) -> str | None:
        """Return a plain-language product description."""
        return "Fidelity International USD Digital Liquidity Fund share token issued through Sygnum Desygnate."

    @property
    def short_description(self) -> str | None:
        """Return the compact listing description."""
        return "Permissioned Fidelity International USD liquidity-fund share"

    @property
    def manager_name(self) -> str | None:
        """Return the tokenisation platform and settlement operator."""
        return "Sygnum"

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Return the FILQ ERC-20 address.

        :param block_identifier: Ignored compatibility parameter.
        :return: Share-token address.
        """
        return self.address

    def fetch_share_token(self) -> TokenDetails:
        """Fetch FILQ ERC-20 metadata.

        :return: Token details for the share class.
        """
        return fetch_erc20_details(self.web3, self.address, chain_id=self.chain_id, raise_on_error=False, cache=self.token_cache, cause_diagnostics_message=f"Sygnum FILQ share token {self.address}")

    def fetch_denomination_token_address(self) -> HexAddress | None:
        """Return no ERC-20 asset because FILQ is not ERC-4626.

        :return: Always ``None``.
        """
        return None

    def fetch_denomination_token(self) -> TokenDetails | None:
        """Return no transferable denomination token.

        :return: Always ``None``.
        """
        return None

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Read outstanding FILQ shares.

        :param block_identifier: Historical block identifier.
        :return: Decimal-scaled total supply.
        """
        return self.share_token.convert_to_decimals(self.share_token.contract.functions.totalSupply().call(block_identifier=block_identifier))

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Reject unavailable NAV reads rather than returning a synthetic price.

            :param block_identifier: Requested block identifier.
        :raise NotImplementedError: Always, until a public, verified NAV route exists.
        """
        raise NotImplementedError(self.nav_unavailable_reason)

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Reject TVL calculation without a verified NAV.

            :param block_identifier: Requested block identifier.
        :raise NotImplementedError: Always, because NAV is unavailable.
        """
        raise NotImplementedError(self.nav_unavailable_reason)

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Reject NAV calculation without a verified price source.

            :param block_identifier: Requested block identifier.
        :raise NotImplementedError: Always, because NAV is unavailable.
        """
        raise NotImplementedError(self.nav_unavailable_reason)

    def fetch_info(self) -> SygnumVaultInfo:
        """Return conservative FILQ scan metadata.

        :return: Token, chain and NAV-availability metadata.
        """
        return SygnumVaultInfo(token=self.address, chain_id=self.chain_id, denomination_token=None, synthetic_usd_denomination=True, nav_source="sygnum_price_feed_unavailable", nav_available=False)

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Export explicit restrictions and price-data diagnostics.

        :return: Private scan columns.
        """
        return {"Denomination": "USD", "_denomination_token": export_sygnum_usd_denomination(self.chain_id), "_deposit_closed_reason": SYGNUM_RESTRICTED_FLOW_REASON, "_redemption_closed_reason": SYGNUM_RESTRICTED_FLOW_REASON, "_nav_source": "sygnum_price_feed_unavailable", "_nav_available": False}

    def fetch_portfolio(self, universe: TradingUniverse, block_identifier: BlockIdentifier | None = None) -> VaultPortfolio:
        """Return no on-chain asset portfolio for the off-chain fund.

        :param universe: Ignored trading universe.
        :param block_identifier: Ignored block identifier.
        :return: Empty spot portfolio.
        """
        return VaultPortfolio(spot_erc20=LowercaseDict())

    def has_block_range_event_support(self) -> bool:
        """Return whether public flow accounting is supported."""
        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        """Return whether ERC-4626 position distribution is supported."""
        return False

    def get_flow_manager(self) -> VaultFlowManager:
        """Reject unsupported public flow accounting.

        :raise NotImplementedError: Always.
        """
        raise NotImplementedError(SYGNUM_RESTRICTED_FLOW_REASON)

    def get_deposit_manager(self) -> VaultDepositManager:
        """Reject unsupported public subscription/redemption flows.

        :raise NotImplementedError: Always.
        """
        raise NotImplementedError(SYGNUM_RESTRICTED_FLOW_REASON)

    def fetch_deposit_closed_reason(self) -> str | None:
        """Return why public subscription is unavailable."""
        return SYGNUM_RESTRICTED_FLOW_REASON

    def fetch_redemption_closed_reason(self) -> str | None:
        """Return why public redemption is unavailable."""
        return SYGNUM_RESTRICTED_FLOW_REASON

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Create a supply-only historical reader.

        :param stateful: Retained for reader compatibility.
        :return: FILQ supply-only reader.
        """
        return SygnumVaultHistoricalReader(self, stateful)

    def get_fee_data(self) -> FeeData:
        """Return unknown fee data because the token has no fee surface.

        :return: Broken fee-data sentinel.
        """
        return BROKEN_FEE_DATA

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return unknown product management fee.

        :param block_identifier: Ignored block identifier.
        :return: ``None``.
        """
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return unknown product performance fee.

        :param block_identifier: Ignored block identifier.
        :return: ``None``.
        """
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Return unknown dealing lock-up.

        :return: ``None``.
        """
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Return the official FILQ product page.

        :param referral: Ignored referral.
        :return: Official FILQ URL.
        """
        return "https://www.sygnum.com/filq/"
