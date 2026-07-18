"""Circle USYC tokenised-fund adapter.

USYC is a permissioned token representing a share in Hashnote International
Short Duration Yield Fund Ltd. It is not an ERC-4626 vault: the ERC-20 token
provides supply, while the official USYC Oracle publishes NAV/share.

See https://usyc.docs.hashnote.com/overview/token-price and
https://usyc.docs.hashnote.com/overview/smart-contracts.
"""

# Adapter classes intentionally mirror :class:`VaultBase` method signatures.
# ruff: noqa: ARG002, FBT001, FBT002, PLR0904, PLR0917, PLR6301

from decimal import Decimal
from functools import cached_property

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.tokenised_fund.usyc.constants import USYC_CHAIN_ID, USYC_DENOMINATION_TOKEN_ADDRESS, USYC_FIRST_SEEN_AT_BLOCK, USYC_NAV_SOURCE, USYC_ORACLE_FIRST_SEEN_AT_BLOCK, USYC_PRICE_ORACLE_ADDRESS, USYC_TELLER_ADDRESS, USYC_TOKEN_ADDRESS
from eth_defi.tokenised_fund.usyc.historical import USYCHistoricalReader
from eth_defi.types import Percent
from eth_defi.vault.base import TradingUniverse, VaultBase, VaultDepositManager, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.lower_case_dict import LowercaseDict

#: Public integration restriction for USYC's entitlement-gated Teller flows.
USYC_PERMISSIONED_FLOW_REASON = "Circle USYC subscriptions, transfers and redemptions require approved non-U.S. institutional investors and Teller entitlement checks"

#: Official USYC performance fee: 10% of yield.
#:
#: https://www.circle.com/usyc
USYC_PERFORMANCE_FEE: Percent = 0.10

#: Official standard subscription fee, waived for eligible daily volume up to $1m.
USYC_DEPOSIT_FEE: Percent = 0.0004

#: Official standard redemption fee, waived for eligible daily volume up to $1m.
USYC_WITHDRAW_FEE: Percent = 0.0003

_USYC_ORACLE_ABI = [
    {"inputs": [], "name": "latestRoundData", "outputs": [{"type": "uint80"}, {"type": "int256"}, {"type": "uint256"}, {"type": "uint256"}, {"type": "uint80"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "decimals", "outputs": [{"type": "uint8"}], "stateMutability": "view", "type": "function"},
]


class USYCVaultInfo(VaultInfo, total=False):
    """USYC product metadata exposed to scan consumers."""

    token: HexAddress
    chain_id: int
    denomination_token: HexAddress
    price_oracle: HexAddress
    teller: HexAddress
    nav_source: str


class USYCVault(VaultBase):
    """Read-only adapter for Circle USYC on Ethereum.

    The adapter derives USYC TVL from ERC-20 supply and the official,
    Chainlink-compatible NAV oracle. It intentionally does not expose public
    transaction support because subscriptions and redemptions are performed
    through the entitlement-gated Teller contract.
    """

    def __init__(self, web3: Web3, spec: VaultSpec, token_cache: dict | None = None, features: set[ERC4626Feature] | None = None, default_block_identifier: BlockIdentifier | None = None, require_denomination_token: bool = False):
        """Create the USYC adapter.

        :param web3: Ethereum Web3 connection.
        :param spec: Chain and USYC token address.
        :param token_cache: ERC-20 metadata cache.
        :param features: Shared classification flags.
        :param default_block_identifier: Accepted for factory compatibility.
        :param require_denomination_token: Whether a failed USDC lookup is fatal.
        """
        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        if spec.chain_id != USYC_CHAIN_ID or spec.vault_address.lower() != USYC_TOKEN_ADDRESS:
            raise ValueError(f"Unsupported USYC product: chain={spec.chain_id}, token={spec.vault_address}")
        self.web3 = web3
        self.spec = spec
        self.features = features or {ERC4626Feature.usyc_like}
        self.first_seen_at_block = USYC_FIRST_SEEN_AT_BLOCK
        self.oracle_first_seen_at_block = USYC_ORACLE_FIRST_SEEN_AT_BLOCK
        _ = default_block_identifier

    @property
    def chain_id(self) -> int:
        """Return the Ethereum mainnet chain id."""
        return self.spec.chain_id

    @property
    def address(self) -> HexAddress:
        """Return the checksummed USYC ERC-20 address."""
        return HexAddress(Web3.to_checksum_address(USYC_TOKEN_ADDRESS))

    @property
    def vault_address(self) -> HexAddress:
        """Return the primary shared scanner identifier."""
        return self.address

    @cached_property
    def price_oracle_contract(self) -> Contract:
        """Return the official Chainlink-compatible USYC Oracle contract."""
        return self.web3.eth.contract(address=Web3.to_checksum_address(USYC_PRICE_ORACLE_ADDRESS), abi=_USYC_ORACLE_ABI)

    @cached_property
    def oracle_decimals(self) -> int:
        """Fetch the official oracle answer decimal scale."""
        return self.price_oracle_contract.functions.decimals().call()

    @property
    def name(self) -> str:
        """Return the USYC ERC-20 name."""
        return self.share_token.name

    @property
    def symbol(self) -> str:
        """Return the USYC ERC-20 symbol."""
        return self.share_token.symbol

    @property
    def description(self) -> str:
        """Return a strategy description based on Circle's product page."""
        return "Tokenised money market fund investing in short-term U.S. government securities and reverse repurchase agreements"

    @property
    def short_description(self) -> str:
        """Return a concise USYC product description."""
        return "Permissioned tokenised U.S. Treasury money market fund"

    @property
    def manager_name(self) -> str:
        """Return the product administrator and platform name."""
        return "Circle"

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Return the USYC ERC-20 token address.

        :param block_identifier: Accepted for historical scanner compatibility.
        :return: USYC token address.
        """
        return self.address

    def fetch_share_token(self) -> TokenDetails:
        """Fetch USYC ERC-20 metadata.

        :return: USYC token details.
        """
        return fetch_erc20_details(self.web3, self.address, chain_id=self.chain_id, raise_on_error=False, cache=self.token_cache, cause_diagnostics_message=f"USYC share token for vault {self.address}")

    def fetch_denomination_token_address(self) -> HexAddress:
        """Return USYC's Ethereum USDC subscription denomination."""
        return HexAddress(Web3.to_checksum_address(USYC_DENOMINATION_TOKEN_ADDRESS))

    def fetch_denomination_token(self) -> TokenDetails:
        """Fetch the USDC denomination token.

        :return: Ethereum USDC token details.
        """
        return fetch_erc20_details(self.web3, self.fetch_denomination_token_address(), chain_id=self.chain_id, raise_on_error=False, cache=self.token_cache, cause_diagnostics_message=f"USYC denomination token for vault {self.address}")

    def convert_raw_share_price(self, raw_price: int) -> Decimal:
        """Convert an oracle answer to USDC NAV per USYC.

        :param raw_price: Raw Chainlink-compatible oracle answer.
        :return: USDC NAV per human-readable USYC token.
        """
        return Decimal(raw_price) / Decimal(10**self.oracle_decimals)

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch USYC NAV per token from the official oracle.

        :param block_identifier: Historical block number or block tag.
        :return: USDC NAV per USYC.
        """
        _round_id, answer, _started_at, updated_at, _answered_in_round = self.price_oracle_contract.functions.latestRoundData().call(block_identifier=block_identifier)
        if answer <= 0 or updated_at <= 0:
            message = "USYC Oracle returned an invalid price observation"
            raise ValueError(message)
        return self.convert_raw_share_price(answer)

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch total outstanding USYC supply.

        :param block_identifier: Historical block number or block tag.
        :return: Human-readable USYC supply.
        """
        return self.share_token.convert_to_decimals(self.share_token.contract.functions.totalSupply().call(block_identifier=block_identifier))

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch USYC TVL as supply times official NAV/share.

        :param block_identifier: Historical block number or block tag.
        :return: Total USDC NAV.
        """
        return self.fetch_total_supply(block_identifier) * self.fetch_share_price(block_identifier)

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch total USYC NAV.

        :param block_identifier: Historical block number or block tag.
        :return: Total USDC NAV.
        """
        return self.fetch_total_assets(block_identifier)

    def fetch_info(self) -> USYCVaultInfo:
        """Export official USYC contract metadata.

        :return: Token, oracle, Teller and denomination details.
        """
        return USYCVaultInfo(token=self.address, chain_id=self.chain_id, denomination_token=self.fetch_denomination_token_address(), price_oracle=HexAddress(Web3.to_checksum_address(USYC_PRICE_ORACLE_ADDRESS)), teller=HexAddress(Web3.to_checksum_address(USYC_TELLER_ADDRESS)), nav_source=USYC_NAV_SOURCE)

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Return USYC NAV-source and permissioned-flow diagnostics."""
        return {"_nav_source": USYC_NAV_SOURCE, "_nav_estimated": False, "_usyc_price_oracle": HexAddress(Web3.to_checksum_address(USYC_PRICE_ORACLE_ADDRESS)), "_usyc_teller": HexAddress(Web3.to_checksum_address(USYC_TELLER_ADDRESS))}

    def fetch_portfolio(self, universe: TradingUniverse, block_identifier: BlockIdentifier | None = None) -> VaultPortfolio:
        """Return no directly observable fund portfolio.

        :param universe: Ignored because underlying holdings are off-chain.
        :param block_identifier: Ignored because holdings are not on-chain.
        :return: Empty spot portfolio.
        """
        return VaultPortfolio(spot_erc20=LowercaseDict())

    def has_block_range_event_support(self) -> bool:
        """Return whether standard flow-event accounting is available."""
        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        """Return whether deposits enter visible on-chain positions."""
        return False

    def get_flow_manager(self) -> VaultFlowManager:
        """Raise because Teller flow accounting is not implemented."""
        message = "USYC Teller flow accounting is not implemented"
        raise NotImplementedError(message)

    def get_deposit_manager(self) -> VaultDepositManager:
        """Raise because public USYC transactions are not supported."""
        message = "USYC permissioned deposits and redemptions are not implemented"
        raise NotImplementedError(message)

    def fetch_deposit_closed_reason(self) -> str:
        """Explain why generic deposits are unavailable."""
        return USYC_PERMISSIONED_FLOW_REASON

    def fetch_redemption_closed_reason(self) -> str:
        """Explain why generic redemptions are unavailable."""
        return USYC_PERMISSIONED_FLOW_REASON

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Create a USYC historical reader.

        :param stateful: Whether to retain shared scanner reader state.
        :return: Supply and oracle historical reader.
        """
        return USYCHistoricalReader(self, stateful=stateful)

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return unknown management fee because the product discloses a yield fee only.

        :param block_identifier: Accepted for shared fee API compatibility.
        :return: ``None``.
        """
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return Circle's disclosed yield fee.

        :param block_identifier: Accepted for shared fee API compatibility.
        :return: Ten percent of yield.
        """
        return USYC_PERFORMANCE_FEE

    def get_deposit_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return the published standard subscription fee.

        :param block_identifier: Accepted for shared fee API compatibility.
        :return: Standard fee before eligible volume waivers.
        """
        return USYC_DEPOSIT_FEE

    def get_withdraw_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return the published standard redemption fee.

        :param block_identifier: Accepted for shared fee API compatibility.
        :return: Standard fee before eligible volume waivers.
        """
        return USYC_WITHDRAW_FEE

    def get_fee_data(self) -> FeeData:
        """Return USYC's published fee metadata.

        :return: Externalised Teller entry and redemption fees with a yield fee.
        """
        return FeeData(fee_mode=VaultFeeMode.externalised, management=None, performance=USYC_PERFORMANCE_FEE, deposit=USYC_DEPOSIT_FEE, withdraw=USYC_WITHDRAW_FEE)

    def get_link(self, referral: str | None = None) -> str:
        """Return Circle's USYC product page.

        :param referral: Ignored because USYC has no referral URL parameter.
        :return: USYC product page.
        """
        return "https://www.circle.com/usyc"
