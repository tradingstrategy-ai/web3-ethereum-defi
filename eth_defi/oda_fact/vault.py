"""ODA-FACT tokenised fund vault adapter.

Kinexys Digital Assets Fungible Asset Contract, or ODA-FACT, instruments are
permissioned ERC-20-compatible tokenised fund contracts. They are not ERC-4626
vaults, but the shared vault database and price scanner can track them through
the :class:`eth_defi.vault.base.VaultBase` interface.

The first supported production contract is JPMorgan's OnChain Liquidity-Token
Money Market Fund token ``JLTXX``:

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

from eth_defi.erc_4626.classification import ODA_FACT_JLTXX_ADDRESS, ODA_FACT_JLTXX_CHAIN_ID
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.oda_fact.historical import OdaFactVaultHistoricalReader
from eth_defi.token import USDC_NATIVE_TOKEN, TokenDetails, fetch_erc20_details
from eth_defi.types import Percent
from eth_defi.vault.base import TradingUniverse, VaultBase, VaultDepositManager, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.fee import BROKEN_FEE_DATA, FeeData, VaultFeeMode
from eth_defi.vault.lower_case_dict import LowercaseDict

logger = logging.getLogger(__name__)


class OdaFactVaultInfo(VaultInfo, total=False):
    """ODA-FACT token metadata and compatibility settings."""

    #: ODA-FACT token and diamond address.
    token: HexAddress

    #: Chain id.
    chain_id: int

    #: Read-only denomination token surrogate used by current pipeline.
    denomination_token: HexAddress | None

    #: Whether denomination token is a surrogate for USD fund accounting.
    synthetic_usd_denomination: bool

    #: NAV source label.
    nav_source: str

    #: Whether NAV is estimated.
    nav_estimated: bool


#: Human-readable JLTXX product name.
JLTXX_PRODUCT_NAME = "JPMorgan OnChain Liquidity-Token Money Market Fund"

#: JLTXX fact sheet short product description.
JLTXX_SHORT_DESCRIPTION = "Vaulted strategy investing in U.S. Treasury bills, bonds and overnight repurchase agreements"

#: JLTXX issuer/platform display name.
JLTXX_MANAGER_NAME = "J.P. Morgan Kinexys"

#: Public Kinexys platform URL.
JLTXX_HOMEPAGE = "https://www.jpmorgan.com/kinexys"

#: Temporary JLTXX NAV estimate used until an official historical NAV source is
#: wired in. JLTXX is a tokenised money-market fund share, but the ODA-FACT
#: contract surface does not expose NAV on-chain.
JLTXX_ESTIMATED_NAV_PER_SHARE = Decimal("1")

#: Diagnostic label for the temporary NAV estimate.
JLTXX_ESTIMATED_NAV_SOURCE = "estimated_jltxx_usd_1"

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


class OdaFactVault(VaultBase):
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
        return token_name or JLTXX_PRODUCT_NAME

    @property
    def symbol(self) -> str:
        """Token symbol."""

        return self.share_token.symbol

    @property
    def description(self) -> str | None:
        """Human-readable product description."""

        return JLTXX_PRODUCT_NAME

    @property
    def short_description(self) -> str | None:
        """Short product description."""

        return JLTXX_SHORT_DESCRIPTION

    @property
    def manager_name(self) -> str | None:
        """Issuer or platform display name."""

        return JLTXX_MANAGER_NAME

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
        """Return the read-only denomination token surrogate.

        ODA-FACT fund accounting is USD-denominated but does not expose an
        ERC-4626 ``asset()`` token. The current pipeline expects a concrete
        ERC-20 denomination token for cache warmup, so Ethereum USDC is used as
        a display/aggregation surrogate for the initial implementation.

        :return:
            Ethereum USDC for the supported JLTXX contract.
        """

        if self.chain_id == ODA_FACT_JLTXX_CHAIN_ID and self.address.lower() == ODA_FACT_JLTXX_ADDRESS:
            return HexAddress(Web3.to_checksum_address(USDC_NATIVE_TOKEN[ODA_FACT_JLTXX_CHAIN_ID]))
        return None

    def fetch_denomination_token(self) -> TokenDetails | None:
        """Fetch read-only denomination token metadata.

        :return:
            Token details for the configured denomination surrogate.
        """

        token_address = self.fetch_denomination_token_address()
        if token_address is None:
            return None
        return fetch_erc20_details(
            self.web3,
            token_address,
            chain_id=self.chain_id,
            raise_on_error=False,
            cache=self.token_cache,
            cause_diagnostics_message=f"ODA-FACT denomination surrogate for vault {self.address}",
        )

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch ODA-FACT share price estimate.

        :param block_identifier:
            Historical or latest block identifier. The current implementation
            does not have block-specific NAV data.

        :return:
            Estimated NAV per one human-readable ODA-FACT share.
        """

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

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch ODA-FACT TVL using supply multiplied by NAV per share.

        :param block_identifier:
            Historical or latest block identifier.

        :return:
            Estimated total assets in USD-denomination units.
        """

        return self.fetch_total_supply(block_identifier) * self.fetch_share_price(block_identifier)

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch ODA-FACT NAV.

        :param block_identifier:
            Historical or latest block identifier.

        :return:
            Estimated total assets in USD-denomination units.
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
            nav_source=JLTXX_ESTIMATED_NAV_SOURCE,
            nav_estimated=True,
        )

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Return ODA-FACT-specific scan-row diagnostics.

        :return:
            Private scan-row fields that make the temporary NAV and
            denomination assumptions explicit.
        """

        return {
            "_notes": self.get_notes(),
            "_deposit_closed_reason": self.fetch_deposit_closed_reason(),
            "_redemption_closed_reason": self.fetch_redemption_closed_reason(),
            "_nav_source": JLTXX_ESTIMATED_NAV_SOURCE,
            "_nav_estimated": True,
            "_synthetic_usd_denomination": True,
            "_fee_source": JLTXX_PROSPECTUS_URL,
            "_fee_fact_sheet": JLTXX_FACT_SHEET_URL,
            "_fee_waiver_until": JLTXX_PROSPECTUS_FEE_WAIVER_UNTIL.isoformat(),
            "_gross_expense_ratio": JLTXX_PROSPECTUS_GROSS_EXPENSE_RATIO,
            "_net_expense_ratio": JLTXX_PROSPECTUS_NET_EXPENSE_RATIO,
            "_prospectus_management_fee": JLTXX_PROSPECTUS_MANAGEMENT_FEE,
            "_prospectus_service_fee": JLTXX_PROSPECTUS_SERVICE_FEE,
        }

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

    def get_deposit_manager(self) -> VaultDepositManager:
        """Get deposit manager.

        :raises NotImplementedError:
            Always, because active ODA-FACT subscription and redemption are not
            implemented.
        """

        message = "ODA-FACT active subscription/redemption is not implemented"
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
        """Return product/platform link.

        :param referral:
            Ignored.

        :return:
            Kinexys platform URL.
        """

        if self.address.lower() == ODA_FACT_JLTXX_ADDRESS:
            return JLTXX_HOMEPAGE
        return f"https://eth.blockscout.com/address/{self.address}"
