"""Read-only adapter for Superstate permissioned tokenised funds.

The adapter currently supports USTB on Ethereum.  USTB is an allowlisted
ERC-20 fund share, not an ERC-4626 vault.  Its issuer-published continuous
price is read from the token's documented ``getChainlinkPrice()`` method while
outstanding shares come from ``totalSupply()``.  This gives NAV/share and an
estimated fund value, but does not imply that a holder can subscribe, transfer
or redeem without Superstate eligibility and available redemption liquidity.

Authoritative references:

* https://docs.superstate.com/welcome-to-superstate/smart-contracts
* https://docs.superstate.com/superstate-funds/ustb
* https://docs.superstate.com/ustb/redeeming-ustb
* https://etherscan.io/address/0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e#code
"""

# Adapter classes intentionally mirror :class:`VaultBase` method signatures.
# ruff: noqa: ARG002, FBT001, FBT002, PLR0904, PLR0917, PLR6301

import datetime
from decimal import Decimal

import eth_abi
from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.tokenised_fund.superstate.constants import SUPERSTATE_ETHEREUM_CHAIN_ID, USTB_ETHEREUM_ADDRESS, USTB_ETHEREUM_CONTINUOUS_PRICE_ORACLE, USTB_ETHEREUM_FIRST_SEEN_AT_BLOCK, USTB_ETHEREUM_ORACLE_DECIMALS
from eth_defi.tokenised_fund.superstate.historical import SuperstateVaultHistoricalReader
from eth_defi.tokenised_fund.vault import TokenisedFundVault
from eth_defi.types import Percent
from eth_defi.vault.base import TradingUniverse, VaultDepositManager, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.fee import BROKEN_FEE_DATA, FeeData
from eth_defi.vault.lower_case_dict import LowercaseDict

#: Public Superstate platform link.
SUPERSTATE_HOMEPAGE = "https://superstate.com/"

#: USTB product documentation.
USTB_HOMEPAGE = "https://docs.superstate.com/superstate-funds/ustb"

#: Public-action warning for USTB shares.
SUPERSTATE_RESTRICTED_FLOW_REASON = "USTB subscriptions, transfers and redemptions require Superstate eligibility checks and issuer-controlled settlement"

#: ERC-20 token selector for the documented USTB continuous NAV price method.
CHAINLINK_PRICE_SELECTOR = Web3.keccak(text="getChainlinkPrice()")[:4]


class SuperstateVaultInfo(VaultInfo, total=False):
    """Superstate fund metadata exposed to scan consumers."""

    #: Fund-token address.
    token: HexAddress
    #: EVM chain id.
    chain_id: int
    #: The adapter uses an accounting USD denomination, not a transferable ERC-20 asset.
    synthetic_usd_denomination: bool
    #: NAV source label.
    nav_source: str
    #: Whether NAV is an adapter estimate.
    nav_estimated: bool
    #: Reviewed continuous-price oracle.
    nav_oracle: HexAddress | None


def export_superstate_usd_denomination(chain_id: int) -> dict[str, object]:
    """Export non-transferable USD accounting metadata.

    :param chain_id:
        EVM chain id for the fund record.
    :return:
        Token-like USD metadata with no ERC-20 address.
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


class SuperstateVault(TokenisedFundVault):
    """Scan-only adapter for reviewed Superstate tokenised fund shares.

    The adapter deliberately has no deposit, redemption or flow manager.  The
    USTB contract includes issuer-specific subscription and redemption paths,
    but certifying public use would require a complete eligibility-aware,
    funded lifecycle test against the issuer systems.
    """

    chainlink_price_selector = CHAINLINK_PRICE_SELECTOR

    def __init__(
        self,
        web3: Web3,
        spec: VaultSpec,
        token_cache: dict | None = None,
        features: set[ERC4626Feature] | None = None,
        default_block_identifier: BlockIdentifier | None = None,
        require_denomination_token: bool = False,
    ):
        """Create a Superstate fund adapter.

        :param web3:
            Web3 connection for the product chain.
        :param spec:
            Chain and reviewed fund-token address.
        :param token_cache:
            Shared ERC-20 metadata cache.
        :param features:
            Shared classification flags.
        :param default_block_identifier:
            Default block for direct metadata reads.
        :param require_denomination_token:
            Retained for :class:`VaultBase` compatibility.
        """

        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        self.web3 = web3
        self.spec = spec
        self.features = features or {ERC4626Feature.superstate_like}
        self.default_block_identifier = default_block_identifier
        self.first_seen_at_block = USTB_ETHEREUM_FIRST_SEEN_AT_BLOCK

    @property
    def chain_id(self) -> int:
        """Return the product chain id."""

        return self.spec.chain_id

    @property
    def address(self) -> HexAddress:
        """Return the checksum fund-token address."""

        return HexAddress(Web3.to_checksum_address(self.spec.vault_address))

    @property
    def vault_address(self) -> HexAddress:
        """Return scanner-compatible alias for the token address."""

        return self.address

    @property
    def name(self) -> str:
        """Return the issuer's ERC-20 name."""

        return self.share_token.name or "Superstate tokenised fund"

    @property
    def symbol(self) -> str:
        """Return the issuer's ERC-20 symbol."""

        return self.share_token.symbol

    @property
    def description(self) -> str | None:
        """Return a general product description."""

        return "Tokenised shares in the Invesco Short Duration US Government Securities Fund."

    @property
    def short_description(self) -> str | None:
        """Return a compact product description."""

        return "Short-duration U.S. Treasury-bill strategy targeting income and principal stability"

    @property
    def manager_name(self) -> str | None:
        """Return the issuer platform used for curator attribution."""

        return "Superstate"

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Return the ERC-20 fund-token address.

        :param block_identifier:
            Accepted for shared scanner compatibility.
        :return:
            Fund-token address.
        """

        return self.address

    def fetch_share_token(self) -> TokenDetails:
        """Fetch USTB ERC-20 metadata.

        :return:
            Cached token details.
        """

        return fetch_erc20_details(
            self.web3,
            self.address,
            chain_id=self.chain_id,
            raise_on_error=False,
            cache=self.token_cache,
            cause_diagnostics_message=f"Superstate fund token for vault {self.address}",
        )

    def fetch_denomination_token_address(self) -> HexAddress | None:
        """Return no ERC-20 denomination token.

        USTB has a USD NAV and may accept configured stablecoins for approved
        subscriptions, but it does not expose a single ERC-4626-style asset.
        Reporting USDC as a denomination would incorrectly advertise a public
        transferable or redeemable asset relationship.

        :return:
            Always ``None``.
        """

        return None

    def fetch_denomination_token(self) -> TokenDetails | None:
        """Return no ERC-20 denomination metadata.

        :return:
            Always ``None``.
        """

        return None

    def _call_chainlink_price(self, block_identifier: BlockIdentifier) -> tuple[bool, int, int]:
        """Read USTB's documented continuous-price endpoint.

        :param block_identifier:
            Archive block or ``latest``.
        :return:
            ``(is_bad_data, updated_at, raw_price)`` from the token.
        """

        result = self.web3.eth.call({"to": self.address, "data": self.chainlink_price_selector}, block_identifier=block_identifier)
        return eth_abi.decode(["bool", "uint256", "uint256"], result)

    def convert_oracle_price(self, raw_price: int) -> Decimal:
        """Convert reviewed USTB oracle units to USD/share.

        :param raw_price:
            Raw value returned by ``getChainlinkPrice()``.
        :return:
            Human-readable USD NAV/share.
        """

        return Decimal(raw_price) / Decimal(10**USTB_ETHEREUM_ORACLE_DECIMALS)

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch the issuer-published USTB continuous NAV/share.

        :param block_identifier:
            Archive block or ``latest``.
        :raises RuntimeError:
            If Superstate marks its oracle value stale or invalid.
        :return:
            USD NAV/share.
        """

        is_bad_data, _updated_at, raw_price = self._call_chainlink_price(block_identifier)
        if is_bad_data:
            message = "Superstate getChainlinkPrice() reported stale or invalid oracle data"
            raise RuntimeError(message)
        return self.convert_oracle_price(raw_price)

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch outstanding human-readable fund shares.

        :param block_identifier:
            Archive block or ``latest``.
        :return:
            Total USTB supply.
        """

        raw_supply = self.share_token.contract.functions.totalSupply().call(block_identifier=block_identifier)
        return self.share_token.convert_to_decimals(raw_supply)

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Estimate fund value from outstanding shares and published NAV.

        :param block_identifier:
            Archive block or ``latest``.
        :return:
            USD-denominated supply times NAV/share.
        """

        return self.fetch_total_supply(block_identifier) * self.fetch_share_price(block_identifier)

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Return the estimated USD NAV represented by outstanding shares.

        :param block_identifier:
            Archive block or ``latest``.
        :return:
            USD-denominated supply times NAV/share.
        """

        return self.fetch_total_assets(block_identifier)

    def fetch_info(self) -> SuperstateVaultInfo:
        """Return Superstate scan metadata.

        :return:
            Token and NAV-source metadata.
        """

        oracle = USTB_ETHEREUM_CONTINUOUS_PRICE_ORACLE if self.chain_id == SUPERSTATE_ETHEREUM_CHAIN_ID and self.address.lower() == USTB_ETHEREUM_ADDRESS else None
        return SuperstateVaultInfo(
            token=self.address,
            chain_id=self.chain_id,
            synthetic_usd_denomination=True,
            nav_source="superstate_getChainlinkPrice",
            nav_estimated=False,
            nav_oracle=oracle,
        )

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Return Superstate-specific scanner diagnostics.

        :return:
            NAV source, compliance and denomination details.
        """

        return {
            "Denomination": "USD",
            "_denomination_token": export_superstate_usd_denomination(self.chain_id),
            "_notes": self.get_notes(),
            "_deposit_closed_reason": self.fetch_deposit_closed_reason(),
            "_redemption_closed_reason": self.fetch_redemption_closed_reason(),
            "_nav_source": "superstate_getChainlinkPrice",
            "_nav_estimated": False,
            "_nav_oracle": USTB_ETHEREUM_CONTINUOUS_PRICE_ORACLE if self.address.lower() == USTB_ETHEREUM_ADDRESS else None,
            "_synthetic_usd_denomination": True,
        }

    def fetch_portfolio(self, universe: TradingUniverse, block_identifier: BlockIdentifier | None = None) -> VaultPortfolio:
        """Return no on-chain asset composition.

        Fund holdings are administered off-chain and are not ERC-20 balances
        held by the token proxy.

        :param universe:
            Ignored.
        :param block_identifier:
            Ignored.
        :return:
            Empty spot portfolio.
        """

        return VaultPortfolio(spot_erc20=LowercaseDict())

    def has_block_range_event_support(self) -> bool:
        """Return whether lifecycle event accounting is implemented.

        :return:
            Always ``False``.
        """

        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        """Return whether subscriptions distribute on-chain positions.

        :return:
            Always ``False``.
        """

        return False

    def get_flow_manager(self) -> VaultFlowManager:
        """Reject unsupported public flow accounting.

        :raises NotImplementedError:
            Always.
        """

        message = "Superstate flow accounting is not implemented"
        raise NotImplementedError(message)

    def get_deposit_manager(self) -> VaultDepositManager:
        """Reject unsupported public subscription/redemption execution.

        :raises NotImplementedError:
            Always, until a fully eligibility-aware lifecycle is implemented
            and tested against the issuer's settlement systems.
        """

        message = "Superstate public subscription/redemption is not implemented"
        raise NotImplementedError(message)

    def fetch_deposit_closed_reason(self) -> str | None:
        """Return the reason public USTB subscription is unavailable.

        :return:
            Compliance and issuer-settlement warning.
        """

        return SUPERSTATE_RESTRICTED_FLOW_REASON

    def fetch_redemption_closed_reason(self) -> str | None:
        """Return the reason public USTB redemption is unavailable.

        :return:
            Compliance and issuer-settlement warning.
        """

        return SUPERSTATE_RESTRICTED_FLOW_REASON

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Return the supply and continuous-price reader.

        :param stateful:
            Whether to retain reader progress.
        :return:
            Superstate historical reader.
        """

        return SuperstateVaultHistoricalReader(self, stateful=stateful)

    def get_fee_data(self) -> FeeData:
        """Return unknown product-level fees.

        :return:
            Broken fee sentinel because USTB's token does not expose fund fee
            terms and the protocol matrix intentionally has no generic fee mode.
        """

        return BROKEN_FEE_DATA

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return no on-chain management-fee value.

        :param block_identifier:
            Ignored.
        :return:
            ``None``.
        """

        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return no on-chain performance-fee value.

        :param block_identifier:
            Ignored.
        :return:
            ``None``.
        """

        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Return unknown redemption timing.

        :return:
            ``None`` because issuer eligibility and redemption liquidity apply.
        """

        return None

    def get_link(self, referral: str | None = None) -> str:
        """Return the USTB product documentation link.

        :param referral:
            Ignored.
        :return:
            USTB product page.
        """

        return USTB_HOMEPAGE if self.address.lower() == USTB_ETHEREUM_ADDRESS else SUPERSTATE_HOMEPAGE
