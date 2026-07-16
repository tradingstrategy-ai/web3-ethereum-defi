"""Asseto tokenised fund vault adapter.

Asseto AoABT is a KYC-gated tokenised fund share, issued and redeemed through
a separate request/claim manager using an administrator-published NAV.  It is
not ERC-4626 or ERC-7540, but can be read through :class:`VaultBase`.
"""

#: Adapter classes intentionally mirror :class:`VaultBase` method signatures.
# ruff: noqa: ARG002, FBT001, FBT002, PLR0904, PLR0917, PLR6301

import logging
from collections.abc import Iterator
from decimal import Decimal

import requests
from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.asseto.constants import ASSETO_PRODUCTS, AssetoProduct
from eth_defi.asseto.historical import AssetoVaultHistoricalReader
from eth_defi.asseto.offchain_api import AssetoAPIError, AssetoRoleInfo, fetch_asseto_product_roles
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.types import Percent
from eth_defi.vault.base import TradingUniverse, VaultBase, VaultDepositManager, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.lower_case_dict import LowercaseDict

logger = logging.getLogger(__name__)

#: Asseto ``Pricer`` view ABI.  Source: verified HashKey Chain contract at
#: https://hsk.blockscout.com/address/0xD72529F8b54fcB59010F2141FC328aDa5Aa72abb
ASSETO_PRICER_ABI = [
    {
        "inputs": [],
        "name": "getLatestPrice",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]

#: Asseto ``AoABTManager`` fee view ABI. Source: verified HashKey Chain
#: contract at https://hsk.blockscout.com/address/0x6dB7eA55c94fb0F4b22D6b384C18CdAa3B33d746
ASSETO_MANAGER_FEE_ABI = [
    {
        "inputs": [],
        "name": "BPS_DENOMINATOR",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "mintFee",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "redemptionFee",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]

#: Generic manager reason used to keep Asseto outside public transaction flows.
ASSETO_BLOCKED_FLOW_REASON = "Asseto deposit manager is blocked: KYC-gated request/claim subscriptions and redemptions are not supported"

#: NAV/share source diagnostic exported with scan rows.
ASSETO_NAV_SOURCE = "asseto_pricer_getLatestPrice"


def convert_asseto_basis_points_to_percent(raw_fee: int, basis_point_denominator: int) -> Percent:
    """Convert Asseto manager fee units to a fractional percent.

    ``AoABTManager`` specifies ``mintFee`` and ``redemptionFee`` in basis
    points and defines ``BPS_DENOMINATOR`` as 10,000. The same source applies
    ``amount * fee / BPS_DENOMINATOR`` when processing subscriptions and
    redemption claims.

    :param raw_fee:
        Fee value returned by the Asseto manager contract.
    :param basis_point_denominator:
        ``BPS_DENOMINATOR`` returned by the same contract.
    :return:
        Fee as a fractional :class:`~eth_defi.types.Percent`.
    :raise ValueError:
        If the manager reports an invalid denominator.
    """

    if basis_point_denominator <= 0:
        message = "Asseto manager BPS_DENOMINATOR must be positive"
        raise ValueError(message)
    return raw_fee / basis_point_denominator


class AssetoVaultInfo(VaultInfo, total=False):
    """Asseto product metadata exported by :class:`AssetoVault`."""

    #: ERC-20 AoABT token address.
    token: HexAddress

    #: EVM chain id.
    chain_id: int

    #: Asseto request/claim manager contract.
    manager: HexAddress

    #: Asseto NAV/share price contract.
    pricer: HexAddress

    #: Subscription and redemption collateral token.
    collateral: HexAddress

    #: NAV source label.
    nav_source: str


class AssetoVault(VaultBase):
    """Read-only adapter for Asseto AoABT tokenised fund products.

    The adapter reads NAV/share from Asseto's verified ``Pricer`` contract and
    calculates TVL from that NAV and the AoABT ERC-20 supply.  It intentionally
    blocks the deposit manager because the on-chain request flow requires
    off-chain KYC, fund dealing-cycle processing and administrator actions.
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
        """Create an Asseto product adapter.

        :param web3:
            Connection to the Asseto product chain.
        :param spec:
            Chain and AoABT token address.
        :param token_cache:
            Token metadata cache used by :func:`fetch_erc20_details`.
        :param features:
            Shared classification features, expected to include
            :py:data:`ERC4626Feature.asseto_like`.
        :param default_block_identifier:
            Optional default block for metadata reads.
        :param require_denomination_token:
            Whether a failed collateral token lookup is a hard error.
        """

        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        self.web3 = web3
        self.spec = spec
        self.features = features or {ERC4626Feature.asseto_like}
        self.default_block_identifier = default_block_identifier

        key = (spec.chain_id, HexAddress(spec.vault_address.lower()))
        try:
            self.product: AssetoProduct = ASSETO_PRODUCTS[key]
        except KeyError as error:
            raise RuntimeError(f"Unsupported Asseto product: chain={spec.chain_id}, token={spec.vault_address}") from error

    @property
    def chain_id(self) -> int:
        """Return the product's EVM chain id."""

        return self.spec.chain_id

    @property
    def address(self) -> HexAddress:
        """Return the AoABT share-token address used as the vault id."""

        return HexAddress(Web3.to_checksum_address(self.product.token))

    @property
    def vault_address(self) -> HexAddress:
        """Return the compatibility alias used by shared vault scanner code."""

        return self.address

    @property
    def pricer_contract(self) -> Contract:
        """Return the Asseto NAV/share pricer contract."""

        return self.web3.eth.contract(address=Web3.to_checksum_address(self.product.pricer), abi=ASSETO_PRICER_ABI)

    @property
    def manager_contract(self) -> Contract:
        """Return the Asseto request/claim manager fee contract."""

        return self.web3.eth.contract(address=Web3.to_checksum_address(self.product.manager), abi=ASSETO_MANAGER_FEE_ABI)

    @property
    def name(self) -> str:
        """Return the AoABT token name with product metadata fallback."""

        return self.share_token.name or self.product.product_name

    @property
    def symbol(self) -> str:
        """Return the AoABT share-token symbol with product metadata fallback."""

        return self.share_token.symbol or self.product.symbol

    @property
    def description(self) -> str | None:
        """Return a short Asseto product description."""

        return "Tokenised fund share backed by Asseto Orient Arbitrage strategy exposure"

    @property
    def short_description(self) -> str | None:
        """Return the concise product description used in vault listings."""

        return "KYC-gated tokenised fund share with on-chain NAV pricing"

    @property
    def manager_name(self) -> str | None:
        """Return the Asseto product's investment manager or advisor.

        Asseto's public application lists partners by role. An investment
        manager takes precedence over an investment advisor, as it is the
        closer match for the shared vault-curator concept. Unknown partner logos
        and optional API failures yield ``None`` rather than attributing the
        Asseto technology provider as the strategy curator.
        """

        try:
            return self.fetch_curator_name()
        except (AssetoAPIError, requests.RequestException) as error:
            logger.warning("Could not read Asseto product roles for %s: %s", self.product.symbol, error)
            return None

    def fetch_roles(self) -> Iterator[AssetoRoleInfo]:
        """Fetch public Asseto partner roles for this vault product.

        Asseto exposes its product partners through an undocumented public
        application API. The result includes role labels, Asseto logo URLs and
        an organisation name only where the logo is a recognised official asset.
        See https://asseto.finance/product for the source application.

        :return:
            Iterator of :class:`AssetoRoleInfo` values in Asseto API order.
        :raise AssetoAPIError:
            If Asseto returns an invalid application response.
        :raise requests.RequestException:
            If the public application request fails.
        """

        yield from fetch_asseto_product_roles(self.product.symbol)

    def fetch_curator_name(self) -> str | None:
        """Resolve the strategy curator from Asseto's priority partner roles.

        Investment managers have priority over investment advisors. Generic
        advisory, custody, legal and administration roles intentionally do not
        produce a curator attribution.

        :return:
            Resolved investment manager or advisor organisation name, if known.
        """

        investment_advisor: str | None = None
        for role in self.fetch_roles():
            if not role.organisation_name:
                continue
            if role.role.casefold() == "investment manager":
                return role.organisation_name
            if role.role.casefold() == "investment advisor" and investment_advisor is None:
                investment_advisor = role.organisation_name
        return investment_advisor

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Return the AoABT share-token address.

        :param block_identifier:
            Accepted for scanner compatibility.
        :return:
            AoABT proxy address.
        """

        return self.address

    def fetch_share_token(self) -> TokenDetails:
        """Fetch AoABT ERC-20 token metadata.

        :return:
            AoABT token details.
        """

        return fetch_erc20_details(
            self.web3,
            self.address,
            chain_id=self.chain_id,
            raise_on_error=False,
            cache=self.token_cache,
            cause_diagnostics_message=f"Asseto share token for vault {self.address}",
        )

    def fetch_denomination_token_address(self) -> HexAddress:
        """Return the manager's collateral token address.

        :return:
            USDT collateral address for the registered Asseto product.
        """

        return HexAddress(Web3.to_checksum_address(self.product.collateral))

    def fetch_denomination_token(self) -> TokenDetails:
        """Fetch Asseto's collateral token metadata.

        :return:
            Product collateral token details.
        """

        return fetch_erc20_details(
            self.web3,
            self.fetch_denomination_token_address(),
            chain_id=self.chain_id,
            raise_on_error=False,
            cache=self.token_cache,
            cause_diagnostics_message=f"Asseto collateral token for vault {self.address}",
        )

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch the latest Asseto NAV/share in collateral denomination.

        :param block_identifier:
            Historical or latest block identifier.
        :return:
            NAV for one human-readable AoABT token.
        """

        raw_price = self.pricer_contract.functions.getLatestPrice().call(block_identifier=block_identifier)
        return Decimal(raw_price) / Decimal(10**18)

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch the outstanding AoABT share supply.

        :param block_identifier:
            Historical or latest block identifier.
        :return:
            Human-readable AoABT supply.
        """

        raw_supply = self.share_token.contract.functions.totalSupply().call(block_identifier=block_identifier)
        return self.share_token.convert_to_decimals(raw_supply)

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Calculate TVL from AoABT supply and the administrator-published NAV.

        :param block_identifier:
            Historical or latest block identifier.
        :return:
            Total assets in collateral denomination.
        """

        return self.fetch_total_supply(block_identifier) * self.fetch_share_price(block_identifier)

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch Asseto product NAV.

        :param block_identifier:
            Historical or latest block identifier.
        :return:
            Total assets in the collateral denomination.
        """

        return self.fetch_total_assets(block_identifier)

    def fetch_info(self) -> AssetoVaultInfo:
        """Return Asseto product contract metadata.

        :return:
            Token, manager, pricer and collateral addresses.
        """

        return AssetoVaultInfo(
            token=self.address,
            chain_id=self.chain_id,
            manager=Web3.to_checksum_address(self.product.manager),
            pricer=Web3.to_checksum_address(self.product.pricer),
            collateral=self.fetch_denomination_token_address(),
            nav_source=ASSETO_NAV_SOURCE,
        )

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Return Asseto-specific scan diagnostics.

        :return:
            Product contract addresses, NAV source and blocked-flow status.
        """

        return {
            "Denomination": self.denomination_token.symbol,
            "_notes": self.get_notes(),
            "_deposit_closed_reason": self.fetch_deposit_closed_reason(),
            "_redemption_closed_reason": self.fetch_redemption_closed_reason(),
            "_nav_source": ASSETO_NAV_SOURCE,
            "_nav_estimated": False,
            "_asseto_manager": Web3.to_checksum_address(self.product.manager),
            "_asseto_pricer": Web3.to_checksum_address(self.product.pricer),
            "_asseto_collateral": self.fetch_denomination_token_address(),
        }

    def fetch_portfolio(
        self,
        universe: TradingUniverse,
        block_identifier: BlockIdentifier | None = None,
    ) -> VaultPortfolio:
        """Return no on-chain portfolio holdings.

        The underlying fund and its custodian operate off-chain; token balances
        held by the share token or manager do not represent its portfolio.

        :param universe:
            Ignored.
        :param block_identifier:
            Ignored.
        :return:
            Empty spot portfolio.
        """

        return VaultPortfolio(spot_erc20=LowercaseDict())

    def has_block_range_event_support(self) -> bool:
        """Return whether generic flow accounting is supported.

        :return:
            ``False`` because Asseto request/claim flow accounting is not yet
            implemented in this adapter.
        """

        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        """Return whether deposits are automatically distributed on-chain.

        :return:
            Always ``False`` for this tokenised fund adapter.
        """

        return False

    def get_flow_manager(self) -> VaultFlowManager:
        """Reject generic flow-manager use.

        :raise NotImplementedError:
            Always, because request/claim event accounting is not implemented.
        """

        message = "Asseto request/claim flow accounting is not implemented"
        raise NotImplementedError(message)

    def get_deposit_manager(self) -> VaultDepositManager:
        """Block the public transaction manager.

        Asseto subscriptions and redemptions require KYC eligibility, fund
        dealing-cycle settlement and privileged NAV/price-ID assignment.

        :raise NotImplementedError:
            Always, by deliberate product policy.
        """

        raise NotImplementedError(ASSETO_BLOCKED_FLOW_REASON)

    def fetch_deposit_closed_reason(self) -> str:
        """Return why the deposit manager is blocked.

        :return:
            Permanent public-integration block reason.
        """

        return ASSETO_BLOCKED_FLOW_REASON

    def fetch_redemption_closed_reason(self) -> str:
        """Return why the redemption manager is blocked.

        :return:
            Permanent public-integration block reason.
        """

        return ASSETO_BLOCKED_FLOW_REASON

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Create the Asseto supply and NAV historical reader.

        :param stateful:
            Whether to attach adaptive read state.
        :return:
            Asseto historical reader.
        """

        return AssetoVaultHistoricalReader(self, stateful=stateful)

    def get_fee_data(self) -> FeeData:
        """Return Asseto fee data with current manager request fees.

        The underlying-fund management and performance fees are reflected in
        NAV/share. The manager's ``mintFee`` maps to the shared entry/deposit
        fee and its ``redemptionFee`` maps to the shared exit/withdraw fee.
        Both settings are read at the requested default block because they can
        be updated by the Asseto limitation administrator.

        :return:
            Asseto fund and manager fees in the shared fee data model.
        """

        block_identifier = self.default_block_identifier or "latest"
        return FeeData(
            fee_mode=VaultFeeMode.internalised_skimming,
            management=self.get_management_fee(block_identifier),
            performance=self.get_performance_fee(block_identifier),
            deposit=self.get_deposit_fee(block_identifier),
            withdraw=self.get_withdraw_fee(block_identifier),
        )

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return the documented annual underlying-fund management fee.

        :param block_identifier:
            Ignored because this fee is fund documentation metadata, not a
            token-contract value.
        :return:
            Annual management fee when the Asseto product documents one.
        """

        return self.product.management_fee

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return the documented underlying-fund performance fee.

        :param block_identifier:
            Ignored because this fee is fund documentation metadata, not a
            token-contract value.
        :return:
            Performance fee when the Asseto product documents one.
        """

        return self.product.performance_fee

    def get_deposit_fee(self, block_identifier: BlockIdentifier) -> Percent:
        """Read the current entry fee from the manager's ``mintFee``.

        ``AoABTManager`` deducts this fee from subscribed collateral before the
        request is assigned an NAV and claimed.

        :param block_identifier:
            Historical or latest block identifier.
        :return:
            Entry/deposit fee as a fraction.
        """

        manager = self.manager_contract.functions
        raw_fee = manager.mintFee().call(block_identifier=block_identifier)
        denominator = manager.BPS_DENOMINATOR().call(block_identifier=block_identifier)
        return convert_asseto_basis_points_to_percent(raw_fee, denominator)

    def get_withdraw_fee(self, block_identifier: BlockIdentifier) -> Percent:
        """Read the current exit fee from the manager's ``redemptionFee``.

        ``AoABTManager`` deducts this fee from collateral after calculating the
        redemption's NAV value and before transferring it to the investor.

        :param block_identifier:
            Historical or latest block identifier.
        :return:
            Exit/withdraw fee as a fraction.
        """

        manager = self.manager_contract.functions
        raw_fee = manager.redemptionFee().call(block_identifier=block_identifier)
        denominator = manager.BPS_DENOMINATOR().call(block_identifier=block_identifier)
        return convert_asseto_basis_points_to_percent(raw_fee, denominator)

    def has_custom_fees(self) -> bool:
        """Report fund fee terms that cannot fit the shared fee model.

        AoABT's documented performance fee has a 6% hurdle and the underlying
        fund's redemption fee depends on the holder's lock-up period. Those
        conditions cannot be represented by scalar fee fields.

        :return:
            ``True`` for Asseto products with conditional fund fee terms.
        """

        return self.product.has_custom_fees
