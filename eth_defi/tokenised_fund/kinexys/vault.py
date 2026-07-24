"""ODA-FACT tokenised fund vault adapter.

Kinexys Digital Assets Fungible Asset Contract, or ODA-FACT, instruments are
permissioned ERC-20-compatible tokenised fund contracts. They are not ERC-4626
vaults, but the shared vault database and price scanner can track them through
the :class:`eth_defi.vault.base.VaultBase` interface.

The supported production contracts are JPMorgan's OnChain Liquidity-Token
Money Market Fund token ``JLTXX`` and My OnChain Net Yield Fund token
``MONY``. Both use a FACT Diamond dispatcher:

- Ethereum address: ``0x09864f52B035AE22eE739dFa5c748fA080D07bD8``
- Contract architecture: EIP-2535 diamond
- Token decimals: ``2``
- Verified source package reference: ``@odaplatform/da-fact-smartcontracts``

The adapter is scan-only. Active subscription, redemption and portfolio
execution are intentionally unsupported.
"""

# Adapter classes intentionally mirror :class:`VaultBase` method signatures.
# ruff: noqa: ARG002, FBT001, FBT002, PLR0904, PLR0917, PLR6301

import datetime
import logging
from decimal import Decimal

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3

from eth_defi.erc_4626.classification import ODA_FACT_JLTXX_ADDRESS, ODA_FACT_MONY_ADDRESS
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.tokenised_fund.kinexys.historical import OdaFactVaultHistoricalReader
from eth_defi.tokenised_fund.vault import TokenisedFundVault
from eth_defi.types import Percent
from eth_defi.vault.base import TradingUniverse, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.fee import BROKEN_FEE_DATA, FeeData, VaultFeeMode
from eth_defi.vault.lower_case_dict import LowercaseDict

logger = logging.getLogger(__name__)


class OdaFactVaultInfo(VaultInfo, total=False):
    """ODA-FACT token metadata and compatibility settings."""

    #: ODA-FACT token and diamond address.
    token: HexAddress

    #: Chain id.
    chain_id: int

    #: ODA-FACT instruments do not expose an ERC-20 denomination token.
    denomination_token: HexAddress | None

    #: Whether NAV is a USD estimate without an ERC-20 denomination token.
    synthetic_usd_denomination: bool

    #: NAV source label.
    nav_source: str

    #: Whether NAV is estimated.
    nav_estimated: bool


#: Human-readable JLTXX product name.
JLTXX_PRODUCT_NAME = "JPMorgan OnChain Liquidity-Token Money Market Fund"

#: JLTXX fact sheet short product description.
JLTXX_SHORT_DESCRIPTION = "U.S. Treasury bills, bonds and overnight repurchase-agreement strategy"

#: JLTXX issuer/platform display name.
JLTXX_MANAGER_NAME = "J.P. Morgan Kinexys"

#: Human-readable MONY product name.
MONY_PRODUCT_NAME = "My OnChain Net Yield Fund"

#: MONY public product description.
MONY_SHORT_DESCRIPTION = "U.S. Treasury and Treasury-backed repurchase-agreement money-market strategy"

#: MONY issuer/platform display name.
MONY_MANAGER_NAME = "J.P. Morgan Kinexys"

#: Official J.P. Morgan Asset Management announcement for the JLTXX and MONY
#: tokenised money-market funds. Neither fund currently has a public
#: individual landing page, so this is preferred over a generic Kinexys page.
KINEXYS_MONEY_MARKET_FUND_ANNOUNCEMENT_URL = "https://am.jpmorgan.com/us/en/asset-management/per/about-us/media/press-releases/jp-morgan-asset-management-launches-second-tokenized-fund-on-ethereum/"

#: Temporary JLTXX NAV estimate used until an official historical NAV source is
#: wired in. JLTXX is a tokenised money-market fund share, but the ODA-FACT
#: contract surface does not expose NAV on-chain.
JLTXX_ESTIMATED_NAV_PER_SHARE = Decimal("1")

#: Diagnostic label for the temporary NAV estimate.
JLTXX_ESTIMATED_NAV_SOURCE = "estimated_jltxx_usd_1"

#: MONY's FACT Diamond has no on-chain NAV or share-price view.
MONY_NAV_SOURCE = "unavailable_mony_no_onchain_nav"

#: JLTXX prospectus/fact sheet source URL.
JLTXX_FACT_SHEET_URL = "https://am.jpmorgan.com/content/dam/jpm-am-aem/americas/us/en/literature/fact-sheet/money-market/fs-ocltmm-t.pdf"

#: JLTXX SEC prospectus source URL.
JLTXX_PROSPECTUS_URL = "https://www.sec.gov/Archives/edgar/data/1659326/000119312526217424/d44657d485bpos.htm"

#: Public availability reason for Kinexys ODA-FACT fund flows.
KINEXYS_WHITELISTED_FLOW_REASON = "Onchain deposits and redemptions are whitelisted and not available to the general public"

#: Prospectus management fee, ``0.08%`` expressed as a fraction.
JLTXX_PROSPECTUS_MANAGEMENT_FEE: Percent = 0.0008

#: Prospectus service fee, ``0.10%`` expressed as a fraction.
JLTXX_PROSPECTUS_SERVICE_FEE: Percent = 0.0010

#: Prospectus gross total annual fund operating expenses, ``0.71%`` expressed as a fraction.
JLTXX_PROSPECTUS_GROSS_EXPENSE_RATIO: Percent = 0.0071

#: Prospectus net total annual fund operating expenses after waivers, ``0.16%`` expressed as a fraction.
JLTXX_PROSPECTUS_NET_EXPENSE_RATIO: Percent = 0.0016

#: Prospectus fee waiver expiry date.
JLTXX_PROSPECTUS_FEE_WAIVER_UNTIL = datetime.date(2028, 6, 30)

#: Shared vault fee model for JLTXX.
#:
#: ``FeeData`` has only one annual management-like field. Use the prospectus
#: net total annual fund operating expense after waivers because this is the
#: investor-facing cost currently advertised for Token Class shares. Keep the
#: gross expense ratio and prospectus breakdown in scan diagnostics.
JLTXX_FEE_DATA = FeeData(
    fee_mode=VaultFeeMode.internalised_skimming,
    management=JLTXX_PROSPECTUS_NET_EXPENSE_RATIO,
    performance=0,
    deposit=0,
    withdraw=0,
)

#: Hardcoded ODA-FACT fee lookup by lower-case contract address.
ODA_FACT_FEES_BY_ADDRESS = {
    ODA_FACT_JLTXX_ADDRESS: JLTXX_FEE_DATA,
}


def is_mony(address: HexAddress | str) -> bool:
    """Check whether an address is the supported MONY FACT Diamond.

    :param address:
        Candidate EVM contract address.

    :return:
        ``True`` for the exact Ethereum MONY token address.
    """

    return address.lower() == ODA_FACT_MONY_ADDRESS


def export_oda_fact_usd_denomination(chain_id: int) -> dict[str, object]:
    """Export synthetic USD accounting denomination metadata.

    ODA-FACT instruments do not expose an ERC-20 denomination token. The
    ``address`` and ``decimals`` fields stay ``None`` so downstream consumers
    do not attempt raw token amount conversions or on-chain transfers.

    :param chain_id:
        Chain id for the scan row.

    :return:
        Fresh token-like metadata dictionary for export.
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


class OdaFactVault(TokenisedFundVault):
    """Scan-only adapter for ODA-FACT tokenised fund contracts.

    The adapter reads ERC-20 supply from the ODA-FACT token itself. For the
    initial JLTXX integration, share price is an explicitly labelled ``1.00``
    USD estimate until an official NAV feed is available.
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
        """Create an ODA-FACT vault adapter.

        :param web3:
            Web3 connection.

        :param spec:
            Chain and ODA-FACT token address.

        :param token_cache:
            Token metadata cache used by :py:func:`fetch_erc20_details`.

        :param features:
            Shared pipeline feature flags. Expected to contain
            :py:data:`ERC4626Feature.oda_fact_like`.

        :param default_block_identifier:
            Default block for metadata reads.

        :param require_denomination_token:
            Whether missing denomination token should raise through
            :py:attr:`VaultBase.denomination_token`.
        """

        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        self.web3 = web3
        self.spec = spec
        self.features = features or {ERC4626Feature.oda_fact_like}
        self.default_block_identifier = default_block_identifier

    def _get_block_identifier(self) -> BlockIdentifier:
        """Resolve the block identifier used for metadata reads.

        :return:
            Configured default block or ``latest``.
        """

        return self.default_block_identifier or "latest"

    @property
    def chain_id(self) -> int:
        """EVM chain id for this ODA-FACT contract."""

        return self.spec.chain_id

    @property
    def address(self) -> HexAddress:
        """ODA-FACT token and diamond address."""

        return HexAddress(Web3.to_checksum_address(self.spec.vault_address))

    @property
    def vault_address(self) -> HexAddress:
        """Compatibility alias for scanner code that expects ``vault_address``."""

        return self.address

    @property
    def name(self) -> str:
        """Token name, falling back to static product metadata."""

        token_name = self.share_token.name
        return token_name or self._get_product_name()

    @property
    def symbol(self) -> str:
        """Token symbol."""

        return self.share_token.symbol

    @property
    def description(self) -> str | None:
        """Human-readable product description."""

        return self._get_product_name()

    @property
    def short_description(self) -> str | None:
        """Short product description."""

        return MONY_SHORT_DESCRIPTION if is_mony(self.address) else JLTXX_SHORT_DESCRIPTION

    @property
    def manager_name(self) -> str | None:
        """Issuer or platform display name."""

        return MONY_MANAGER_NAME if is_mony(self.address) else JLTXX_MANAGER_NAME

    def _get_product_name(self) -> str:
        """Return the static name for a supported FACT product.

        :return:
            MONY or JLTXX product name, depending on the exact token address.
        """

        return MONY_PRODUCT_NAME if is_mony(self.address) else JLTXX_PRODUCT_NAME

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Return the ODA-FACT token address.

        :param block_identifier:
            Accepted for compatibility with the shared historical scanner.

        :return:
            ODA-FACT token address.
        """

        return self.address

    def fetch_share_token(self) -> TokenDetails:
        """Fetch ERC-20 metadata for the ODA-FACT token.

        :return:
            Token details for the ODA-FACT share token.
        """

        return fetch_erc20_details(
            self.web3,
            self.address,
            chain_id=self.chain_id,
            raise_on_error=False,
            cache=self.token_cache,
            cause_diagnostics_message=f"ODA-FACT share token for vault {self.address}",
        )

    def fetch_denomination_token_address(self) -> HexAddress | None:
        """Return the ERC-20 denomination token address.

        ODA-FACT fund accounting is USD-denominated but does not expose an
        ERC-4626 ``asset()`` token or any other ERC-20 denomination token.
        Exporting a surrogate token would make the vault appear to accept or
        redeem that asset, so this adapter reports no denomination token.

        :return:
            Always ``None``.
        """

        return None

    def fetch_denomination_token(self) -> TokenDetails | None:
        """Fetch ERC-20 denomination token metadata.

        :return:
            Always ``None`` because ODA-FACT instruments do not expose a
            denomination token.
        """

        return None

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Fetch ODA-FACT share price when an authorised source is available.

        :param block_identifier:
            Historical or latest block identifier. The current implementation
            does not have block-specific NAV data.

        :return:
            Estimated JLTXX NAV per one human-readable share, or ``None`` for
            MONY because its FACT Diamond has no on-chain price or NAV view.
        """

        if is_mony(self.address):
            return None
        return JLTXX_ESTIMATED_NAV_PER_SHARE

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch total outstanding ODA-FACT token supply.

        :param block_identifier:
            Historical or latest block identifier.

        :return:
            Human-readable token supply.
        """

        raw_supply = self.share_token.contract.functions.totalSupply().call(block_identifier=block_identifier)
        return self.share_token.convert_to_decimals(raw_supply)

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Fetch ODA-FACT TVL when a share NAV is available.

        :param block_identifier:
            Historical or latest block identifier.

        :return:
            Estimated total assets in USD-denomination units, or ``None`` when
            the product provides no verified NAV.
        """

        share_price = self.fetch_share_price(block_identifier)
        if share_price is None:
            return None
        return self.fetch_total_supply(block_identifier) * share_price

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Fetch ODA-FACT NAV.

        :param block_identifier:
            Historical or latest block identifier.

        :return:
            Estimated total assets in USD-denomination units, or ``None`` when
            the product provides no verified NAV.
        """

        return self.fetch_total_assets(block_identifier)

    def fetch_info(self) -> OdaFactVaultInfo:
        """Return ODA-FACT metadata for this scan-only adapter.

        :return:
            Token and NAV-source metadata.
        """

        return OdaFactVaultInfo(
            token=self.address,
            chain_id=self.chain_id,
            denomination_token=self.fetch_denomination_token_address(),
            synthetic_usd_denomination=True,
            nav_source=MONY_NAV_SOURCE if is_mony(self.address) else JLTXX_ESTIMATED_NAV_SOURCE,
            nav_estimated=not is_mony(self.address),
        )

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Return ODA-FACT-specific scan-row diagnostics.

        :return:
            Private scan-row fields that make the temporary NAV and
            denomination assumptions explicit.
        """

        nav_source = MONY_NAV_SOURCE if is_mony(self.address) else JLTXX_ESTIMATED_NAV_SOURCE
        nav_estimated = not is_mony(self.address)
        extra_data: dict[str, object] = {
            "Denomination": "USD",
            "_denomination_token": export_oda_fact_usd_denomination(self.chain_id),
            "_notes": self.get_notes(),
            "_deposit_closed_reason": self.fetch_deposit_closed_reason(),
            "_redemption_closed_reason": self.fetch_redemption_closed_reason(),
            "_nav_source": nav_source,
            "_nav_estimated": nav_estimated,
            "_synthetic_usd_denomination": True,
        }
        if not is_mony(self.address):
            extra_data.update(
                {
                    "_fee_source": JLTXX_PROSPECTUS_URL,
                    "_fee_fact_sheet": JLTXX_FACT_SHEET_URL,
                    "_fee_waiver_until": JLTXX_PROSPECTUS_FEE_WAIVER_UNTIL.isoformat(),
                    "_gross_expense_ratio": JLTXX_PROSPECTUS_GROSS_EXPENSE_RATIO,
                    "_net_expense_ratio": JLTXX_PROSPECTUS_NET_EXPENSE_RATIO,
                    "_prospectus_management_fee": JLTXX_PROSPECTUS_MANAGEMENT_FEE,
                    "_prospectus_service_fee": JLTXX_PROSPECTUS_SERVICE_FEE,
                }
            )
        return extra_data

    def fetch_portfolio(
        self,
        universe: TradingUniverse,
        block_identifier: BlockIdentifier | None = None,
    ) -> VaultPortfolio:
        """Return an empty portfolio for the scan-only adapter.

        ODA-FACT fund assets are not represented as on-chain ERC-20 holdings of
        the token contract. Portfolio composition requires an off-chain fund
        accounting feed and is intentionally out of scope.

        :param universe:
            Ignored.

        :param block_identifier:
            Ignored.

        :return:
            Empty spot portfolio.
        """

        return VaultPortfolio(spot_erc20=LowercaseDict())

    def has_block_range_event_support(self) -> bool:
        """Whether event-based deposit/redemption flow accounting is implemented."""

        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        """Whether deposits are automatically distributed to positions."""

        return False

    def get_flow_manager(self) -> VaultFlowManager:
        """Get flow manager.

        :raises NotImplementedError:
            Always, because ODA-FACT flow accounting is not implemented yet.
        """

        message = "ODA-FACT flow accounting is not implemented"
        raise NotImplementedError(message)

    def fetch_deposit_closed_reason(self) -> str | None:
        """Return the Kinexys public deposit availability status.

        Kinexys ODA-FACT contracts are permissioned tokenised fund contracts.
        On-chain subscriptions are available only to whitelisted parties and
        are not exposed through a public ERC-4626-style deposit flow.

        :return:
            Human-readable reason why public deposits are closed.
        """

        return KINEXYS_WHITELISTED_FLOW_REASON

    def fetch_redemption_closed_reason(self) -> str | None:
        """Return the Kinexys public redemption availability status.

        Kinexys ODA-FACT contracts are permissioned tokenised fund contracts.
        On-chain redemptions are available only to whitelisted parties and are
        not exposed through a public ERC-4626-style redemption flow.

        :return:
            Human-readable reason why public redemptions are closed.
        """

        return KINEXYS_WHITELISTED_FLOW_REASON

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Get ODA-FACT historical reader.

        :param stateful:
            Whether to attach adaptive reader state.

        :return:
            Historical reader.
        """

        return OdaFactVaultHistoricalReader(self, stateful=stateful)

    def get_fee_data(self) -> FeeData:
        """Return hardcoded JLTXX prospectus fee data.

        ODA-FACT product fees are not available from the on-chain token
        surface. J.P. Morgan's JLTXX prospectus advertises ``0.71%`` gross
        total annual fund operating expenses and ``0.16%`` net total annual
        fund operating expenses after waivers through
        :py:data:`JLTXX_PROSPECTUS_FEE_WAIVER_UNTIL`. The shared vault fee
        model has only one annual management-like field, so the current net
        expense ratio is exposed as ``management``. ``performance``,
        ``deposit`` and ``withdraw`` are reported as zero because they are not
        advertised as separate Token Class fees.

        :return:
            Hardcoded fee data for the known ODA-FACT contract, or broken fee
            data for unknown ODA-FACT addresses.
        """

        return ODA_FACT_FEES_BY_ADDRESS.get(self.address.lower(), BROKEN_FEE_DATA)

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return JLTXX net annual expense ratio.

        The fee is hardcoded from the JLTXX prospectus, because it is not
        readable from the ODA-FACT token contract.

        :param block_identifier:
            Ignored. Prospectus fee disclosure is not block-specific.

        :return:
            ``0.0016`` for JLTXX, representing ``0.16%`` net annual fund
            operating expenses after waivers.
        """

        return self.get_fee_data().management

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return JLTXX performance fee.

        :param block_identifier:
            Ignored. Prospectus fee disclosure is not block-specific.

        :return:
            ``0`` because the JLTXX Token Class prospectus does not advertise a
            separate performance fee.
        """

        return self.get_fee_data().performance

    def get_notes(self) -> str | None:
        """Return markdown notes for the known ODA-FACT vault.

        The notes are stored in the shared vault notes matrix so both direct
        adapter users and downstream vault metric exports see the same
        vault-specific description.

        :return:
            Markdown note for JLTXX, or ``None`` for unknown ODA-FACT addresses.
        """

        return super().get_notes()

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Return unknown lock-up.

        :return:
            ``None`` because subscription/redemption terms are off-chain.
        """

        return None

    def get_link(self, referral: str | None = None) -> str:
        """Return the official fund-announcement link.

        :param referral:
            Ignored.

        :return:
            J.P. Morgan Asset Management announcement covering the product.
        """

        return KINEXYS_MONEY_MARKET_FUND_ANNOUNCEMENT_URL
