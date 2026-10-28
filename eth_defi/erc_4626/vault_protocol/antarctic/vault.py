"""Read-only Antarctic perpetual exchange liquidity pool adapter.

LP shares are ERC-20 tokens, not ERC-4626 vaults. Handler-settled subscriptions
provide sparse onchain execution prices; no isolated state read supplies NAV.
https://docs.antarctic.exchange/technical-framework/hybird-lp-model
"""

# ruff: noqa: ARG002, PLR6301

import datetime
from decimal import Decimal
from pathlib import Path

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3

from eth_defi.abi import get_deployed_contract
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.antarctic.constants import ANTARCTIC_BY_ADDRESS, ANTARCTIC_CHAIN_ID, ANTARCTIC_USDT
from eth_defi.erc_4626.vault_protocol.antarctic.historical import AntarcticHistoricalReader
from eth_defi.erc_4626.vault_protocol.antarctic.historical_context import get_antarctic_historical_context_path
from eth_defi.erc_4626.vault_protocol.antarctic.tags import STRATEGY_TAGS
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.types import Percent
from eth_defi.vault.base import TradingUniverse, VaultBase, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.fee import FeeData
from eth_defi.vault.lower_case_dict import LowercaseDict
from eth_defi.vault.price_source import PriceSource
from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags

ANTARCTIC_NOTES = "Sparse handler-reported subscription execution prices in USDT per share; reported TVL is pre-batch and not independently computed NAV. Redemption ratios and separately paid staking rewards are excluded. Public asynchronous deposit/redemption transaction construction is unsupported."


class AntarcticVault(VaultBase):  # noqa: PLR0904
    """Expose identity and event-only history for the two reviewed LP tokens.

    This adapter deliberately does not certify a generic deposit manager or
    convert current ERC-20 supply into a historical valuation.
    """

    def __init__(self, web3: Web3, spec: VaultSpec, token_cache: dict | None = None, features: set[ERC4626Feature] | None = None, default_block_identifier: BlockIdentifier | None = None, require_denomination_token: bool = False) -> None:  # noqa: FBT001, FBT002, PLR0917
        """Create a chain-scoped adapter for a reviewed Antarctic deployment.

        :param web3: Arbitrum connection.
        :param spec: Reviewed LP token identity.
        :param token_cache: Shared token cache.
        :param features: Persisted feature flags.
        :param default_block_identifier: Metadata read block.
        :param require_denomination_token: Require successful USDT metadata.
        :return: None.
        """
        if spec.chain_id != ANTARCTIC_CHAIN_ID or spec.vault_address.lower() not in ANTARCTIC_BY_ADDRESS:
            raise ValueError(f"Unknown Antarctic deployment: {spec}")
        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        self.web3 = web3
        self.spec = spec
        self.deployment = ANTARCTIC_BY_ADDRESS[spec.vault_address.lower()]
        self.features = set(features or ()) | {ERC4626Feature.antarctic_like, ERC4626Feature.share_price_equivalence}
        self.default_block_identifier = default_block_identifier or "latest"
        self.manager_contract = get_deployed_contract(web3, f"antarctic/{self.deployment.product.upper()}Manager.json", self.deployment.manager)
        self.historical_context_path: Path = get_antarctic_historical_context_path()

    @property
    def chain_id(self) -> int:
        return self.spec.chain_id

    @property
    def address(self) -> HexAddress:
        return HexAddress(Web3.to_checksum_address(self.spec.vault_address))

    @property
    def vault_address(self) -> HexAddress:
        return self.address

    @property
    def name(self) -> str:
        return self.share_token.name

    @property
    def symbol(self) -> str:
        return self.share_token.symbol

    @property
    def short_description(self) -> str:
        role = "market-making" if self.deployment.product == "amlp" else "hedging"
        return f"Antarctic {self.deployment.product.upper()} USDT {role} liquidity pool"

    def fetch_share_token(self) -> TokenDetails:
        return fetch_erc20_details(self.web3, self.address, chain_id=self.chain_id, cache=self.token_cache, raise_on_error=False)

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        return self.address

    def fetch_denomination_token_address(self) -> HexAddress:
        manager = self.manager_contract
        token = getattr(manager.functions, self.deployment.product)().call(block_identifier=self.default_block_identifier)
        denomination = manager.functions.usdt().call(block_identifier=self.default_block_identifier)
        if token.lower() != self.deployment.address or denomination.lower() != ANTARCTIC_USDT:
            raise ValueError(f"Antarctic manager/token identity mismatch: {self.deployment.manager}")
        return HexAddress(Web3.to_checksum_address(denomination))

    def fetch_denomination_token(self) -> TokenDetails | None:
        return fetch_erc20_details(self.web3, self.fetch_denomination_token_address(), chain_id=self.chain_id, cache=self.token_cache, raise_on_error=False)

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        return self.share_token.convert_to_decimals(self.share_token.contract.functions.totalSupply().call(block_identifier=block_identifier))

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        msg = "Antarctic prices are observed only at subscription settlement events"
        raise NotImplementedError(msg)

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        msg = "Antarctic has no independently computed onchain NAV"
        raise NotImplementedError(msg)

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        return self.fetch_total_assets(block_identifier)

    def fetch_info(self) -> VaultInfo:
        return {"token": self.address, "chain_id": self.chain_id, "asset": self.fetch_denomination_token_address()}

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        return {"_notes": ANTARCTIC_NOTES, "_nav_source": "antarctic_subscription_settlement", "_nav_estimated": True}

    def fetch_portfolio(self, universe: TradingUniverse, block_identifier: BlockIdentifier | None = None) -> VaultPortfolio:
        return VaultPortfolio(spot_erc20=LowercaseDict())

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:  # noqa: FBT001
        return AntarcticHistoricalReader(self)

    def get_flow_manager(self) -> VaultFlowManager:
        msg = "Antarctic asynchronous investor flow lifecycle is unsupported"
        raise NotImplementedError(msg)

    def get_deposit_manager(self) -> None:
        return None

    def has_block_range_event_support(self) -> bool:
        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        return None

    def get_fee_data(self) -> FeeData:
        return FeeData(fee_mode=None, management=None, performance=None, deposit=None, withdraw=None)

    def get_share_price_source(self) -> PriceSource:
        return PriceSource.smart_contract_event

    def get_strategy_tags(self) -> set[StrategyTag] | None:
        return lookup_strategy_tags(STRATEGY_TAGS, self.address)

    def get_link(self, referral: str | None = None) -> str:
        return f"https://www.antarctic.exchange/lp/{self.deployment.product}"

    def get_estimated_lock_up(self) -> datetime.timedelta:
        return datetime.timedelta(seconds=self.manager_contract.functions.removeLiquidityCooldown().call(block_identifier=self.default_block_identifier))

    def fetch_minimum_deposit(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        return self.denomination_token.convert_to_decimals(self.manager_contract.functions.minimumLiquidityAmount().call(block_identifier=block_identifier))
