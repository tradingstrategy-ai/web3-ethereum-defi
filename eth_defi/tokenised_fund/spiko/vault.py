"""Spiko USTBL tokenised fund adapter.

USTBL is a permissioned ERC-20 share in Spiko's U.S. Treasury-bill money
market fund. It is not ERC-4626. The verified issuer Oracle publishes NAV per
share using the Chainlink AggregatorV3 interface, allowing safe read-only
tracking through :class:`eth_defi.vault.base.VaultBase`.

See https://tech.spiko.io/posts/spiko-smart-contracts/ and
https://github.com/spiko-tech/contracts/blob/main/contracts/oracle/Oracle.sol.
"""

# Adapter methods intentionally mirror :class:`VaultBase` signatures.
# ruff: noqa: FBT001, FBT002, PLR0904, PLR0917, PLR6301

from decimal import Decimal
from functools import cached_property

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.tokenised_fund.spiko.constants import SPIKO_CHAIN_ID, USTBL_FIRST_SEEN_AT_BLOCK, USTBL_NAV_SOURCE, USTBL_ORACLE_FIRST_SEEN_AT_BLOCK, USTBL_PRICE_ORACLE_ADDRESS, USTBL_TOKEN_ADDRESS
from eth_defi.tokenised_fund.spiko.historical import SpikoHistoricalReader
from eth_defi.tokenised_fund.vault import TokenisedFundVault
from eth_defi.types import Percent
from eth_defi.vault.base import TradingUniverse, VaultDepositManager, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.lower_case_dict import LowercaseDict

#: Public flows must not be advertised for Spiko's permissioned lifecycle.
SPIKO_PERMISSIONED_FLOW_REASON = "Spiko USTBL subscriptions, transfers and redemptions require eligibility checks and issuer-operated daily servicing"

#: USTBL's published annual management fee. The reported NAV is net of this fee.
#: https://www.spiko.io/use-cases/web3
USTBL_MANAGEMENT_FEE: Percent = 0.0025

_ORACLE_ABI = [
    {"inputs": [], "name": "latestRoundData", "outputs": [{"type": "uint80"}, {"type": "int256"}, {"type": "uint256"}, {"type": "uint256"}, {"type": "uint80"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "decimals", "outputs": [{"type": "uint8"}], "stateMutability": "view", "type": "function"},
]


class SpikoVaultInfo(VaultInfo, total=False):
    """USTBL metadata exported to vault scan consumers."""

    token: HexAddress
    chain_id: int
    price_oracle: HexAddress
    nav_source: str
    synthetic_usd_denomination: bool


def export_spiko_usd_denomination(chain_id: int) -> dict[str, object]:
    """Export USTBL's non-transferable USD accounting denomination.

    :param chain_id:
        EVM chain id of the USTBL deployment.
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


class SpikoVault(TokenisedFundVault):
    """Read-only Ethereum adapter for Spiko USTBL.

    The adapter calculates NAV from ERC-20 supply and the official issuer
    Oracle. It cannot perform investor dealing: transfers and servicing are
    controlled by Spiko's permission manager and redemption workflow.
    """

    def __init__(self, web3: Web3, spec: VaultSpec, token_cache: dict | None = None, features: set[ERC4626Feature] | None = None, default_block_identifier: BlockIdentifier | None = None, require_denomination_token: bool = False):
        """Create a verified USTBL adapter.

        :param web3: Ethereum Web3 connection.
        :param spec: Ethereum chain and USTBL token identifier.
        :param token_cache: Optional ERC-20 metadata cache.
        :param features: Classification flags supplied by the shared factory.
        :param default_block_identifier: Accepted for factory compatibility.
        :param require_denomination_token: Accepted for :class:`VaultBase` compatibility.
        :raise ValueError: If the requested product is not Ethereum USTBL.
        """
        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        if spec.chain_id != SPIKO_CHAIN_ID or spec.vault_address.lower() != USTBL_TOKEN_ADDRESS:
            raise ValueError(f"Unsupported Spiko product: chain={spec.chain_id}, token={spec.vault_address}")
        self.web3 = web3
        self.spec = spec
        self.features = features or {ERC4626Feature.spiko_like}
        self.first_seen_at_block = USTBL_FIRST_SEEN_AT_BLOCK
        self.oracle_first_seen_at_block = USTBL_ORACLE_FIRST_SEEN_AT_BLOCK
        _ = default_block_identifier

    @property
    def chain_id(self) -> int:
        """Return the USTBL deployment chain id.

        :return: Ethereum mainnet chain id.
        """
        return self.spec.chain_id

    @property
    def address(self) -> HexAddress:
        """Return the USTBL token address.

        :return: Checksummed Ethereum address.
        """
        return HexAddress(Web3.to_checksum_address(USTBL_TOKEN_ADDRESS))

    @property
    def vault_address(self) -> HexAddress:
        """Return the shared scanner vault identifier.

        :return: USTBL token address.
        """
        return self.address

    @cached_property
    def price_oracle_contract(self) -> Contract:
        """Return Spiko's verified Chainlink-compatible NAV oracle.

        :return: Oracle contract instance.
        """
        return self.web3.eth.contract(address=Web3.to_checksum_address(USTBL_PRICE_ORACLE_ADDRESS), abi=_ORACLE_ABI)

    @cached_property
    def oracle_decimals(self) -> int:
        """Read the NAV oracle decimal scale.

        :return: Number of oracle decimal places.
        """
        return self.price_oracle_contract.functions.decimals().call()

    @property
    def name(self) -> str:
        """Return the on-chain share token name.

        :return: ERC-20 token name.
        """
        return self.share_token.name

    @property
    def symbol(self) -> str:
        """Return the on-chain share token symbol.

        :return: ERC-20 token symbol.
        """
        return self.share_token.symbol

    @property
    def description(self) -> str:
        """Return Spiko's public fund description.

        :return: Concise fund strategy description.
        """
        return "Tokenised share in Spiko's U.S. Treasury-bill money market fund"

    @property
    def short_description(self) -> str:
        """Return a concise public listing description.

        :return: USTBL eligibility-aware product summary.
        """
        return "U.S. Treasury-bill money-market strategy"

    @property
    def manager_name(self) -> str:
        """Return the protocol-operated curator identity.

        :return: Spiko.
        """
        return "Spiko"

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Return USTBL's share token address.

        :param block_identifier: Accepted for shared scanner compatibility.
        :return: USTBL token address.
        """
        _ = block_identifier
        return self.address

    def fetch_share_token(self) -> TokenDetails:
        """Fetch USTBL ERC-20 details.

        :return: USTBL token metadata and conversion methods.
        """
        return fetch_erc20_details(self.web3, self.address, chain_id=self.chain_id, raise_on_error=False, cache=self.token_cache, cause_diagnostics_message=f"Spiko USTBL share token for vault {self.address}")

    def fetch_denomination_token_address(self) -> HexAddress | None:
        """Return no surrogate ERC-20 denomination token.

        Spiko servicing supports eligibility-checked fiat and stablecoin
        settlement choices, but USTBL does not expose an ERC-4626 ``asset``
        token. A surrogate would wrongly advertise a public dealing route.

        :return: Always ``None``.
        """
        return None

    def fetch_denomination_token(self) -> TokenDetails | None:
        """Return no on-chain denomination-token metadata.

        :return: Always ``None`` because no single public ERC-20 asset exists.
        """
        return None

    def convert_raw_share_price(self, raw_price: int) -> Decimal:
        """Convert oracle units to USD NAV per USTBL token.

        :param raw_price: Raw Chainlink-compatible oracle answer.
        :return: Human-readable USD NAV/share.
        """
        return Decimal(raw_price) / Decimal(10**self.oracle_decimals)

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Read the official USTBL NAV/share.

        :param block_identifier: Ethereum block tag or historical block number.
        :return: USD NAV per USTBL.
        :raise ValueError: If the oracle returns no valid NAV observation.
        """
        _round, answer, _started, updated_at, _answered = self.price_oracle_contract.functions.latestRoundData().call(block_identifier=block_identifier)
        if answer <= 0 or updated_at <= 0:
            message = "Spiko USTBL oracle returned an invalid NAV observation"
            raise ValueError(message)
        return self.convert_raw_share_price(answer)

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Read the outstanding USTBL supply.

        :param block_identifier: Ethereum block tag or historical block number.
        :return: Human-readable USTBL supply.
        """
        return self.share_token.convert_to_decimals(self.share_token.contract.functions.totalSupply().call(block_identifier=block_identifier))

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Calculate estimated fund NAV from supply and issuer NAV/share.

        :param block_identifier: Ethereum block tag or historical block number.
        :return: USD estimated fund net asset value.
        """
        return self.fetch_total_supply(block_identifier) * self.fetch_share_price(block_identifier)

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Read USTBL total NAV.

        :param block_identifier: Ethereum block tag or historical block number.
        :return: USD estimated fund net asset value.
        """
        return self.fetch_total_assets(block_identifier)

    def fetch_info(self) -> SpikoVaultInfo:
        """Export verified on-chain USTBL integration metadata.

        :return: Token, oracle and price-source identifiers.
        """
        return SpikoVaultInfo(token=self.address, chain_id=self.chain_id, price_oracle=HexAddress(Web3.to_checksum_address(USTBL_PRICE_ORACLE_ADDRESS)), nav_source=USTBL_NAV_SOURCE, synthetic_usd_denomination=True)

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Expose valuation and restricted-flow diagnostics.

        :return: Data recorded with USTBL scan results.
        """
        return {
            "Denomination": "USD",
            "_denomination_token": export_spiko_usd_denomination(self.chain_id),
            "_nav_source": USTBL_NAV_SOURCE,
            "_nav_estimated": False,
            "_spiko_price_oracle": HexAddress(Web3.to_checksum_address(USTBL_PRICE_ORACLE_ADDRESS)),
            "_synthetic_usd_denomination": True,
        }

    def fetch_portfolio(self, universe: TradingUniverse, block_identifier: BlockIdentifier | None = None) -> VaultPortfolio:
        """Return no directly observable underlying portfolio.

        :param universe: Ignored because USTBL assets are off-chain.
        :param block_identifier: Ignored because holdings are not on-chain.
        :return: Empty spot portfolio.
        """
        _ = universe, block_identifier
        return VaultPortfolio(spot_erc20=LowercaseDict())

    def has_block_range_event_support(self) -> bool:
        """Report unsupported generic flow-event accounting.

        :return: ``False`` because servicing is issuer-operated.
        """
        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        """Report unavailable on-chain portfolio distribution.

        :return: ``False``.
        """
        return False

    def get_flow_manager(self) -> VaultFlowManager:
        """Reject unsupported generic flow accounting.

        :raise NotImplementedError: Always, as Spiko servicing is bespoke.
        """
        message = "Spiko USTBL subscription and redemption flow accounting is not implemented"
        raise NotImplementedError(message)

    def get_deposit_manager(self) -> VaultDepositManager:
        """Reject public generic transaction support.

        :raise NotImplementedError: Always, due to eligibility and daily servicing.
        """
        message = "Spiko USTBL permissioned deposits and redemptions are not implemented"
        raise NotImplementedError(message)

    def fetch_deposit_closed_reason(self) -> str:
        """Explain unavailable generic subscriptions.

        :return: Eligibility and issuer-servicing explanation.
        """
        return SPIKO_PERMISSIONED_FLOW_REASON

    def fetch_redemption_closed_reason(self) -> str:
        """Explain unavailable generic redemptions.

        :return: Eligibility and issuer-servicing explanation.
        """
        return SPIKO_PERMISSIONED_FLOW_REASON

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Construct the supply and NAV historical reader.

        :param stateful: Whether to retain shared reader state.
        :return: USTBL historical reader.
        """
        return SpikoHistoricalReader(self, stateful=stateful)

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return USTBL's published annual management fee.

        :param block_identifier: Accepted for shared fee API compatibility.
        :return: Annual management fee as a fraction.
        """
        _ = block_identifier
        return USTBL_MANAGEMENT_FEE

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return no separately published USTBL performance fee.

        :param block_identifier: Accepted for shared fee API compatibility.
        :return: ``None``.
        """
        _ = block_identifier
        return None

    def get_fee_data(self) -> FeeData:
        """Return published USTBL fee metadata.

        :return: Management fee with no inferred dealing fees.
        """
        return FeeData(fee_mode=VaultFeeMode.internalised_skimming, management=USTBL_MANAGEMENT_FEE, performance=None, deposit=None, withdraw=None)

    def get_link(self, referral: str | None = None) -> str:
        """Return Spiko's USTBL product page.

        :param referral: Ignored because Spiko does not provide referral URLs.
        :return: Official Spiko product page.
        """
        _ = referral
        return "https://www.spiko.io/use-cases/web3"
