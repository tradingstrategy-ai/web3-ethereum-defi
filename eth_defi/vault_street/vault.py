"""Vault Street primeUSD vault adapter.

Vault Street's first product, primeUSD, is a permissioned and yield-bearing
USDC-denominated token for institutional participants. It is not an ERC-4626
vault: primeUSD supply is ERC-20 ``totalSupply()``, while NAV/share is published
by a separate ``PriceStorage.getPrice()`` contract.

- Documentation: https://docs.vaultstreet.com/overview/primeusd.md
- Contracts: https://docs.vaultstreet.com/resources/smart-contracts.md
- Token: https://etherscan.io/token/0x7ea76108975ec0998b9bc2db04b4eca986400dd7
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
from eth_defi.types import Percent
from eth_defi.vault.base import TradingUniverse, VaultBase, VaultDepositManager, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.fee import FeeData
from eth_defi.vault.lower_case_dict import LowercaseDict
from eth_defi.vault_street.constants import (
    PRIME_USD_ADDRESS,
    PRIME_USD_DENOMINATION_TOKEN_ADDRESS,
    PRIME_USD_FIRST_SEEN_AT_BLOCK,
    PRIME_USD_PRICE_DECIMALS,
    PRIME_USD_PRICE_ORACLE_ADDRESS,
    PRIME_USD_REQUEST_MANAGER_ADDRESS,
    VAULT_STREET_CHAIN_ID,
    VAULT_STREET_NAV_SOURCE,
)
from eth_defi.vault_street.historical import VaultStreetHistoricalReader

#: Public integration restriction for permissioned primeUSD flows.
VAULT_STREET_PERMISSIONED_FLOW_REASON = "Vault Street primeUSD deposits, transfers and redemptions require an approved KYB/KYC allowlist"

#: Minimal ABI for the Vault Street ``PriceStorage`` oracle.
_PRICE_ORACLE_ABI = [
    {
        "inputs": [],
        "name": "getPrice",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]


class VaultStreetVaultInfo(VaultInfo, total=False):
    """Vault Street primeUSD metadata and compatibility settings."""

    #: ERC-20 primeUSD token address.
    token: HexAddress

    #: EVM chain id.
    chain_id: int

    #: USDC denomination token.
    denomination_token: HexAddress

    #: Contract publishing fixed-point NAV/share.
    price_oracle: HexAddress

    #: Contract receiving permissioned deposit and redemption requests.
    request_manager: HexAddress

    #: NAV source label.
    nav_source: str


class VaultStreetVault(VaultBase):
    """Scan-only adapter for Vault Street's primeUSD token.

    The adapter reads primeUSD ERC-20 supply and combines it with the public
    price oracle to calculate USDC NAV. Public transaction support is excluded
    because Vault Street applies institutional allowlisting to its request flow.
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
        """Create the primeUSD adapter.

        :param web3:
            Web3 connection for Ethereum mainnet.
        :param spec:
            Chain and primeUSD token address.
        :param token_cache:
            Token metadata cache for ERC-20 reads.
        :param features:
            Shared pipeline features, expected to include ``vault_street_like``.
        :param default_block_identifier:
            Accepted for the shared adapter factory. primeUSD token metadata is
            immutable, so the adapter has no block-pinned metadata reads.
        :param require_denomination_token:
            Whether a missing denomination token must raise.
        """

        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        if spec.chain_id != VAULT_STREET_CHAIN_ID or spec.vault_address.lower() != PRIME_USD_ADDRESS:
            raise ValueError(f"Unsupported Vault Street product: chain={spec.chain_id}, token={spec.vault_address}")

        self.web3 = web3
        self.spec = spec
        self.features = features or {ERC4626Feature.vault_street_like}
        _ = default_block_identifier
        self.first_seen_at_block = PRIME_USD_FIRST_SEEN_AT_BLOCK

    @property
    def chain_id(self) -> int:
        """Return Ethereum mainnet chain id.

        :return:
            ``1`` for the primeUSD deployment.
        """

        return self.spec.chain_id

    @property
    def address(self) -> HexAddress:
        """Return the primeUSD ERC-20 token address.

        :return:
            Checksummed primeUSD address.
        """

        return HexAddress(Web3.to_checksum_address(PRIME_USD_ADDRESS))

    @property
    def vault_address(self) -> HexAddress:
        """Return the primary vault identifier expected by shared scanner code.

        :return:
            primeUSD token address.
        """

        return self.address

    @cached_property
    def price_oracle_contract(self) -> Contract:
        """Return the Vault Street NAV/share oracle contract.

        :return:
            Contract exposing ``getPrice()``.
        """

        return self.web3.eth.contract(
            address=Web3.to_checksum_address(PRIME_USD_PRICE_ORACLE_ADDRESS),
            abi=_PRICE_ORACLE_ABI,
        )

    @property
    def name(self) -> str:
        """Return the primeUSD token name.

        :return:
            ERC-20 token name.
        """

        return self.share_token.name

    @property
    def symbol(self) -> str:
        """Return the primeUSD token symbol.

        :return:
            ERC-20 token symbol.
        """

        return self.share_token.symbol

    @property
    def description(self) -> str:
        """Return the product's strategy description.

        :return:
            Human-readable primeUSD strategy summary.
        """

        return "Permissioned USDC-denominated leveraged carry strategy using tokenised investment-grade fixed income collateral"

    @property
    def short_description(self) -> str:
        """Return a concise product summary.

        :return:
            Short primeUSD description.
        """

        return "Permissioned institutional USDC yield product"

    @property
    def manager_name(self) -> str:
        """Return the Vault Street platform name.

        :return:
            Product manager display name.
        """

        return "Vault Street"

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Return the primeUSD ERC-20 address.

        :param block_identifier:
            Accepted for historical scanner compatibility.
        :return:
            primeUSD address.
        """

        return self.address

    def fetch_share_token(self) -> TokenDetails:
        """Fetch primeUSD ERC-20 metadata.

        :return:
            primeUSD token details.
        """

        return fetch_erc20_details(
            self.web3,
            self.address,
            chain_id=self.chain_id,
            raise_on_error=False,
            cache=self.token_cache,
            cause_diagnostics_message=f"Vault Street share token for vault {self.address}",
        )

    def fetch_denomination_token_address(self) -> HexAddress:
        """Return native Ethereum USDC as the primeUSD denomination token.

        :return:
            USDC ERC-20 address.
        """

        return HexAddress(Web3.to_checksum_address(PRIME_USD_DENOMINATION_TOKEN_ADDRESS))

    def fetch_denomination_token(self) -> TokenDetails:
        """Fetch USDC denomination token metadata.

        :return:
            Ethereum USDC token details.
        """

        return fetch_erc20_details(
            self.web3,
            self.fetch_denomination_token_address(),
            chain_id=self.chain_id,
            raise_on_error=False,
            cache=self.token_cache,
            cause_diagnostics_message=f"Vault Street denomination token for vault {self.address}",
        )

    def convert_raw_share_price(self, raw_price: int) -> Decimal:
        """Convert the PriceStorage fixed-point value to USDC per primeUSD.

        :param raw_price:
            Raw ``getPrice()`` value with eight decimal places.
        :return:
            USDC NAV per one human-readable primeUSD token.
        """

        return Decimal(raw_price) / Decimal(10**PRIME_USD_PRICE_DECIMALS)

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch primeUSD NAV per token from the PriceStorage oracle.

        :param block_identifier:
            Historical block number or block tag.
        :return:
            USDC NAV per primeUSD.
        """

        raw_price = self.price_oracle_contract.functions.getPrice().call(block_identifier=block_identifier)
        return self.convert_raw_share_price(raw_price)

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch total outstanding primeUSD supply.

        :param block_identifier:
            Historical block number or block tag.
        :return:
            Human-readable primeUSD supply.
        """

        raw_supply = self.share_token.contract.functions.totalSupply().call(block_identifier=block_identifier)
        return self.share_token.convert_to_decimals(raw_supply)

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch primeUSD TVL from supply multiplied by NAV/share.

        :param block_identifier:
            Historical block number or block tag.
        :return:
            Total USDC NAV.
        """

        return self.fetch_total_supply(block_identifier) * self.fetch_share_price(block_identifier)

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch primeUSD total NAV.

        :param block_identifier:
            Historical block number or block tag.
        :return:
            Total USDC NAV.
        """

        return self.fetch_total_assets(block_identifier)

    def fetch_info(self) -> VaultStreetVaultInfo:
        """Return Vault Street contract metadata.

        :return:
            primeUSD token, oracle and request-manager information.
        """

        return VaultStreetVaultInfo(
            token=self.address,
            chain_id=self.chain_id,
            denomination_token=self.fetch_denomination_token_address(),
            price_oracle=HexAddress(Web3.to_checksum_address(PRIME_USD_PRICE_ORACLE_ADDRESS)),
            request_manager=HexAddress(Web3.to_checksum_address(PRIME_USD_REQUEST_MANAGER_ADDRESS)),
            nav_source=VAULT_STREET_NAV_SOURCE,
        )

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Return primeUSD scan diagnostics.

        :return:
            Price-source and permissioned-flow fields.
        """

        return {
            "_nav_source": VAULT_STREET_NAV_SOURCE,
            "_nav_estimated": False,
            "_vault_street_price_oracle": HexAddress(Web3.to_checksum_address(PRIME_USD_PRICE_ORACLE_ADDRESS)),
            "_vault_street_request_manager": HexAddress(Web3.to_checksum_address(PRIME_USD_REQUEST_MANAGER_ADDRESS)),
        }

    def fetch_portfolio(
        self,
        universe: TradingUniverse,
        block_identifier: BlockIdentifier | None = None,
    ) -> VaultPortfolio:
        """Return no directly observable strategy-token balances.

        primeUSD's collateral and deployed positions are not represented as
        token balances held at the primeUSD ERC-20 address.

        :param universe:
            Ignored because no on-chain portfolio is exposed by this product.
        :param block_identifier:
            Ignored because no on-chain portfolio is exposed by this product.
        :return:
            Empty spot portfolio.
        """

        return VaultPortfolio(spot_erc20=LowercaseDict())

    def has_block_range_event_support(self) -> bool:
        """Return whether standard flow-event accounting is available.

        :return:
            ``False`` because primeUSD uses a separate request manager.
        """

        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        """Return whether deposits are distributed to visible positions.

        :return:
            ``False`` because positions are not exposed by this adapter.
        """

        return False

    def get_flow_manager(self) -> VaultFlowManager:
        """Raise because Vault Street request-flow accounting is unsupported.

        :raises NotImplementedError:
            Always.
        """

        message = "Vault Street request-flow accounting is not implemented"
        raise NotImplementedError(message)

    def get_deposit_manager(self) -> VaultDepositManager:
        """Raise because permissioned Vault Street transaction support is unavailable.

        :raises NotImplementedError:
            Always.
        """

        message = "Vault Street permissioned deposits and redemptions are not implemented"
        raise NotImplementedError(message)

    def fetch_deposit_closed_reason(self) -> str:
        """Explain why generic deposits are unavailable.

        :return:
            Institutional allowlist requirement.
        """

        return VAULT_STREET_PERMISSIONED_FLOW_REASON

    def fetch_redemption_closed_reason(self) -> str:
        """Explain why generic redemptions are unavailable.

        :return:
            Institutional allowlist requirement.
        """

        return VAULT_STREET_PERMISSIONED_FLOW_REASON

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Create a historical primeUSD reader.

        :param stateful:
            Accepted for shared scanner compatibility.
        :return:
            Historical reader using supply and PriceStorage oracle calls.
        """

        return VaultStreetHistoricalReader(self, stateful=stateful)

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return unknown management fee.

        :param block_identifier:
            Accepted for shared fee API compatibility.
        :return:
            ``None`` because no on-chain management-fee accessor is exposed.
        """

        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return unknown performance fee.

        :param block_identifier:
            Accepted for shared fee API compatibility.
        :return:
            ``None`` because no on-chain performance-fee accessor is exposed.
        """

        return None

    def get_deposit_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return unknown deposit fee.

        :param block_identifier:
            Accepted for shared fee API compatibility.
        :return:
            ``None`` because the permissioned request manager has no public fee accessor.
        """

        return None

    def get_withdraw_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return unknown redemption fee.

        :param block_identifier:
            Accepted for shared fee API compatibility.
        :return:
            ``None`` because the permissioned request manager has no public fee accessor.
        """

        return None

    def get_fee_data(self) -> FeeData:
        """Return unavailable fee metadata without making unsupported assumptions.

        :return:
            Fee data with each fee unset.
        """

        return FeeData(
            fee_mode=None,
            management=None,
            performance=None,
            deposit=None,
            withdraw=None,
        )

    def get_link(self, referral: str | None = None) -> str:
        """Return Vault Street's product page.

        :param referral:
            Ignored because the product URL has no referral parameter.
        :return:
            Vault Street primeUSD documentation page.
        """

        return "https://docs.vaultstreet.com/overview/primeusd.md"
