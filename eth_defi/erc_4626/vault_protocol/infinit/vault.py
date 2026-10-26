# Overrides retain the shared VaultBase signatures and constant protocol answers.
# ruff: noqa: ARG002, FBT001, FBT002, PLR0904, PLR0917, PLR6301
"""Read Infinit's separate vault, receipt token and managed trading account.

This custom DeFi product is not ERC-4626. Deposits route through a manager to
a custody Safe; redemption requires capital to be returned to the actual vault. Published
unit prices are not the vault contract's idle USDC balance.

Sources:

* https://app.infinit.tech/vaults/apex-multistrategy
* https://docs.infinit.tech/infinit-ai-managed-trading-strategy-marketplace/vault-terms
* https://arbiscan.io/address/0x0bb4f37f9bd8fe3a72c8acb53509ae95ca5343ab#code
"""

import datetime
import logging
from bisect import bisect_right
from decimal import Decimal
from functools import cached_property

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3

from eth_defi.abi import get_deployed_contract
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.infinit.api import InfinitHistory, fetch_infinit_history, fetch_infinit_trading_equity
from eth_defi.erc_4626.vault_protocol.infinit.constants import INFINIT_APP, INFINIT_CHAIN_ID, INFINIT_MANAGEMENT_FEE, INFINIT_MAX_PRICE_AGE, INFINIT_PERFORMANCE_FEE, INFINIT_PRODUCTS, INFINIT_USDC, is_quarantined
from eth_defi.erc_4626.vault_protocol.infinit.historical import InfinitHistoricalReader
from eth_defi.erc_4626.vault_protocol.infinit.historical_context import get_infinit_historical_context_path
from eth_defi.erc_4626.vault_protocol.infinit.tags import get_strategy_tags
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.types import Percent
from eth_defi.vault.base import TradingUniverse, VaultBase, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.price_source import PriceSource
from eth_defi.vault.strategy_tag import StrategyTag

logger = logging.getLogger(__name__)


class InfinitVault(VaultBase):
    """Data-only reader for the reviewed Apex strategy.

    Static classification is chain-scoped and manager relationships are
    validated onchain before any metadata or valuation is returned.
    """

    def __init__(self, web3: Web3, spec: VaultSpec, token_cache: dict | None = None, features: set[ERC4626Feature] | None = None, default_block_identifier: BlockIdentifier | None = None, require_denomination_token: bool = False) -> None:
        """Create a reviewed Infinit adapter.

        :param web3: Configured Arbitrum connection.
        :param spec: Actual vault identity, not its share token.
        :param token_cache: Shared token metadata cache.
        :param features: Persisted protocol classification.
        :param default_block_identifier: Fixed metadata block or latest.
        :param require_denomination_token: Fail on unavailable USDC metadata.
        """
        if spec.chain_id != INFINIT_CHAIN_ID or spec.vault_address.lower() not in INFINIT_PRODUCTS:
            raise ValueError(f"Unsupported Infinit vault: {spec}")
        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        self.web3 = web3
        self.spec = spec
        self.product = INFINIT_PRODUCTS[spec.vault_address.lower()]
        self.features = {ERC4626Feature.infinit_like}
        self.default_block_identifier = default_block_identifier or "latest"
        self.first_seen_at_block = self.product.deployment_block
        self.historical_context_path = get_infinit_historical_context_path()
        self.manager_contract = get_deployed_contract(web3, "infinit/VaultManager.json", self.product.manager)
        self.vault_contract = get_deployed_contract(web3, "infinit/Vault.json", self.address)

    @property
    def chain_id(self) -> int:
        return self.spec.chain_id

    @property
    def address(self) -> HexAddress:
        return HexAddress(Web3.to_checksum_address(self.product.address))

    @property
    def vault_address(self) -> HexAddress:
        return self.address

    @cached_property
    def manager_record(self) -> tuple:
        """Read and validate the manager's exact vault/share/collateral tuple."""
        row = self.manager_contract.functions.vaults(self.product.vault_id).call(block_identifier=self.default_block_identifier)
        if tuple(a.lower() for a in row[:3]) != (self.product.share_token, INFINIT_USDC, self.product.address):
            msg = "Infinit onchain manager mapping disagrees with reviewed identities"
            raise ValueError(msg)
        return tuple(row)

    @cached_property
    def source_history(self) -> InfinitHistory:
        """Fetch a validated application snapshot once per adapter instance."""
        return fetch_infinit_history(self.product)

    @property
    def name(self) -> str:
        return self.manager_record[3]

    @property
    def symbol(self) -> str:
        return self.share_token.symbol

    @property
    def manager_name(self) -> str:
        return "APEX Quants"

    @property
    def short_description(self) -> str:
        return "Dollar-neutral relative-strength long/short perpetual-futures strategy managed by APEX Quants."

    @property
    def description(self) -> str:
        return "APEX Quants runs a deterministic relative-strength strategy across liquid Hyperliquid perpetual markets. Matched long and short books seek returns from relative performance. Deposits pass through a custody Safe and an Arbitrum funding EOA to a separate Hyperliquid trading account. Receipt tokens are locked until maturity; trading, custody, settlement and valuation-source risks remain."

    def fetch_share_token(self) -> TokenDetails:
        return fetch_erc20_details(self.web3, self.manager_record[0], cache=self.token_cache, chain_id=self.chain_id)

    def fetch_share_token_address(self) -> HexAddress:
        """Return the separate receipt identity for the historical token cache.

        The common writer warms token metadata before contextual reads.

        :return: Reviewed inUSDC contract, validated against the manager.
        """
        return HexAddress(self.manager_record[0])

    def fetch_denomination_token_address(self) -> HexAddress:
        return HexAddress(self.manager_record[1])

    def fetch_denomination_token(self) -> TokenDetails:
        return fetch_erc20_details(self.web3, self.fetch_denomination_token_address(), cache=self.token_cache, chain_id=self.chain_id)

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Read receipt supply at the same block used for valuation.

        :param block_identifier: EVM supply observation block.
        :return: Human-readable inUSDC units.
        """
        raw = self.share_token.contract.functions.totalSupply().call(block_identifier=block_identifier)
        return self.share_token.convert_to_decimals(raw)

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Read the latest source observation no later than the EVM block.

        Prices have a thirty-minute freshness limit, allowing one interval of
        publication lag. Missing, stale and reviewed corrupt periods stay missing.

        :param block_identifier: EVM source horizon.
        :return: Published USDC per share, or unavailable.
        """
        stamp = self.web3.eth.get_block(block_identifier)["timestamp"]
        timestamp = datetime.datetime.fromtimestamp(stamp, datetime.UTC).replace(tzinfo=None)
        if is_quarantined(timestamp):
            return None
        points = self.source_history.points
        index = bisect_right(tuple(p.timestamp for p in points), timestamp) - 1
        if index < 0:
            return None
        point = points[index]
        if timestamp - point.timestamp > INFINIT_MAX_PRICE_AGE or is_quarantined(point.timestamp):
            return None
        return point.price

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Calculate assets using source price and block-matched receipt supply.

        :param block_identifier: EVM observation block.
        :return: Published valuation in USDC, or unavailable.
        """
        block = self.web3.eth.block_number if block_identifier == "latest" else block_identifier
        price = self.fetch_share_price(block)
        return price * self.fetch_total_supply(block) if price is not None else None

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        return self.fetch_total_assets(block_identifier)

    def fetch_info(self) -> VaultInfo:
        return {"address": self.address, "share_token": self.product.share_token, "manager": self.product.manager, "custody_safe": self.product.custody_safe, "contract_vault_id": self.product.vault_id, "trading_account": self.product.trading_account}

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        dates = tuple(datetime.datetime.fromtimestamp(self.manager_record[i], datetime.UTC).replace(tzinfo=None) for i in (4, 5, 6))
        return {"_infinit_manager": self.product.manager, "_infinit_custody_safe": self.product.custody_safe, "_infinit_contract_vault_id": self.product.vault_id, "_infinit_trading_account": self.product.trading_account, "_infinit_application_id": self.product.application_id, "_infinit_application_slug": self.product.slug, "_infinit_deposit_opens_at": dates[0], "_infinit_deposit_closes_at": dates[1], "_infinit_matures_at": dates[2], "_notes": self.get_notes()}

    def fetch_portfolio(self, universe: TradingUniverse, block_identifier: BlockIdentifier | None = None) -> VaultPortfolio:
        msg = "Infinit data integration does not expose an ERC-20 spot portfolio"
        raise NotImplementedError(msg)

    def get_flow_manager(self) -> VaultFlowManager:
        msg = "Infinit flow management is not implemented"
        raise VaultFlowUnavailable(msg, protocol="infinit", vault_address=self.address)

    def get_deposit_manager(self) -> None:
        msg = "Infinit transaction support is unavailable in this data-only integration"
        raise VaultFlowUnavailable(msg, protocol="infinit", vault_address=self.address)

    def get_deposit_manager_capability(self) -> None:
        return None

    def has_block_range_event_support(self) -> bool:
        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        return False

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        return InfinitHistoricalReader(self)

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent:
        return INFINIT_MANAGEMENT_FEE

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent:
        return INFINIT_PERFORMANCE_FEE

    def get_deposit_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        return None

    def get_withdraw_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        return None

    def get_share_price_source(self) -> PriceSource:
        return PriceSource.api

    def get_strategy_tags(self) -> set[StrategyTag] | None:
        return get_strategy_tags(self.address)

    def get_estimated_lock_up(self) -> datetime.timedelta:
        return datetime.timedelta(seconds=self.manager_record[6] - self.manager_record[5])

    def get_link(self, referral: str | None = None) -> str:
        return f"{INFINIT_APP}/vaults/{self.product.slug}"

    def get_flags(self) -> set[VaultFlag]:
        return {VaultFlag.perp_dex_trading_vault}

    def is_whitelisted_deposit(self) -> bool:
        return False

    def get_whitelist_notes(self) -> str:
        return "The reviewed contract has no investor allow-list. The application excludes restricted jurisdictions; this is separate from its deposit window and transaction-adapter support."

    def _fetch_timestamp(self) -> int:
        return int(self.web3.eth.get_block(self.default_block_identifier)["timestamp"])

    def fetch_deposit_closed_reason(self) -> str | None:
        timestamp = self._fetch_timestamp()
        if self.manager_contract.functions.paused().call(block_identifier=self.default_block_identifier) or self.vault_contract.functions.paused().call(block_identifier=self.default_block_identifier):
            return "Vault paused"
        if timestamp < self.manager_record[4]:
            return "Deposit window has not opened"
        if timestamp > self.manager_record[5]:
            return "Deposit window closed"
        return None

    def fetch_deposit_open(self) -> bool | None:
        # TokenVault has no public cap accessor. An open window alone does not
        # establish that a positive deposit can still be accepted.
        return False if self.fetch_deposit_closed_reason() else None

    def fetch_redemption_closed_reason(self) -> str | None:
        if self._fetch_timestamp() < self.manager_record[6]:
            return "Vault has not matured"
        if self.manager_contract.functions.paused().call(block_identifier=self.default_block_identifier) or self.vault_contract.functions.paused().call(block_identifier=self.default_block_identifier):
            return "Vault paused"
        balance = self.denomination_token.contract.functions.balanceOf(self.address).call(block_identifier=self.default_block_identifier)
        return "Custody settlement has not funded the redemption contract" if balance == 0 else None

    def fetch_deposit_next_open(self) -> datetime.datetime | None:
        return datetime.datetime.fromtimestamp(self.manager_record[4], datetime.UTC).replace(tzinfo=None) if self._fetch_timestamp() < self.manager_record[4] else None

    def fetch_redemption_next_open(self) -> datetime.datetime | None:
        return datetime.datetime.fromtimestamp(self.manager_record[6], datetime.UTC).replace(tzinfo=None) if self._fetch_timestamp() < self.manager_record[6] else None

    def get_notes(self) -> str:
        return (
            "**Fee disclosure disparity (reviewed 26 October 2026):** The "
            "[published vault terms](https://docs.infinit.tech/infinit-ai-managed-trading-strategy-marketplace/vault-terms) "
            "specify a 2% annual management fee accruing daily and a 20% performance fee on realised profit above the high-water mark. "
            "The reviewed [manager implementation](https://arbiscan.io/address/0x0bb4f37f9bd8fe3a72c8acb53509ae95ca5343ab#code) "
            "and [vault implementation](https://arbiscan.io/address/0x3bdabf4bb61069b7ec117241c437f32b269306cf#code) "
            "contain no management-fee accrual, performance-fee calculation or high-water-mark accounting. "
            "Any collection of these fees requires operator-side accounting or settlement outside those contract paths. "
            "Whether the published API prices reserve these fees is unverified; investor net returns remain unavailable.\n\n"
            "**Funding EOA (reviewed 26 October 2026):** The Arbitrum funding wallet "
            "[0xf2460f28c9bacb35ac6fef048b661d33ad070aaf](https://arbiscan.io/address/0xf2460f28c9bacb35ac6fef048b661d33ad070aaf) "
            "is an externally owned account with no contract code at the reviewed block. On 3 August 2026, the custody Safe "
            "[transferred 506,127.385398 USDC to this EOA](https://arbiscan.io/tx/0x0611b2f9dc3335a922be14eb8775e6b726c9894505b038b29058ea45735b0d94), "
            "which then [sent 506,127.39 USDC to the Hyperliquid bridge](https://arbiscan.io/tx/0x0719cf323b9415a354187383600e52e024c07a0f60135894ff1230c0def54a05). "
            "The Hyperliquid ledger credits that transaction to the same-address trading account. "
            "These Arbitrum transfers are signed by the EOA; the custody Safe's signature threshold does not govern outgoing transfers from it. "
            "Hyperliquid account permissions are separate from Arbitrum signing authority; the EOA's key-management arrangements are unverified. "
            "The [transaction evidence and flow chart](https://web3-ethereum-defi.readthedocs.io/vaults/infinit/index.html#observed-fund-movements) "
            "establish funding into Hyperliquid, but do not establish a completed bridge withdrawal back to Arbitrum or a return to the redemption vault. "
            "Through Arbitrum block 518462154, no USDC transfers involving the redemption vault, receipt burns or manager withdrawals were observed.\n\n"
            "**Connection with the legacy vault (reviewed 26 October 2026):** The "
            "[legacy Infinit Multi-Strategy vault](https://tradingstrategy.ai/vaults/infinit-multi-strategy) "
            "is a separate native Hyperliquid vault at 0x8220ed9e3d84da5e9d5f6d115a28a0b28b967388. "
            "The recorded Hyperliquid ledger shows that this product's trading account, "
            "0xf2460f28c9bacb35ac6fef048b661d33ad070aaf, deposited 5 USDC as a test and then "
            "506,137.39 USDC into the legacy vault on 3 August 2026. On 6 August it withdrew "
            "505,859.98 USDC net back into the trading account on Hyperliquid. "
            "This establishes a temporary investment in the legacy vault, not a return of funds to Arbitrum or investor redemption. "
            "The two vaults retain separate identities and performance histories; the legacy vault's returns and fees "
            "must not be treated as this product's investor returns or fee terms.\n\n"
            "Reviewed corrupt source periods are excluded from performance calculations. "
            "Public transaction construction is not implemented."
        )

    def fetch_valuation_validation(self) -> dict[str, Decimal | None]:
        """Compare the published value with both reviewed perp markets.

        Market movement and differing source timestamps can create a small
        difference. Validation never replaces the canonical API price.

        :return: Published assets, observed trading equity and relative difference.
        """
        assets = self.fetch_total_assets()
        equity = fetch_infinit_trading_equity(self.product)
        difference = abs(assets - equity) / abs(equity) if assets is not None and equity else None
        if difference is not None and difference > Decimal("0.02"):
            logger.warning("Infinit published assets %s differ from native+xyz equity %s", assets, equity)
        return {"published_assets": assets, "trading_equity": equity, "relative_difference": difference}
