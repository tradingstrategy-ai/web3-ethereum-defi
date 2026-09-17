"""Read-only adapter for Rysk Premium option-writing pools.

Rysk Premium pools issue ERC-20 LP shares but are not ERC-4626 vaults or
legally structured funds. Deposits and withdrawals settle asynchronously by
epoch. Final historical prices are reconstructed from ``EpochPriceSet``,
``EpochPriceDisputed`` and ``epochExecuted`` logs::

    share_price_equivalent = final withdrawal price / 10**collateral decimals

Verified Ethereum implementation:
https://etherscan.io/address/0x6ca8d390c37acc6883e96fa5283246fc39239741#code
"""

# ruff: noqa: ARG002, FBT001, FBT002, PLR0904, PLR0917, PLR6301

from decimal import Decimal
from pathlib import Path

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.rysk.historical import RyskPremiumHistoricalReader
from eth_defi.erc_4626.vault_protocol.rysk.historical_context import get_rysk_historical_context_path
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.types import Percent
from eth_defi.vault.base import TradingUniverse, VaultBase, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.deposit_redeem import VaultDepositManager
from eth_defi.vault.fee import FeeData
from eth_defi.vault.lower_case_dict import LowercaseDict
from eth_defi.vault.price_source import PriceSource

RYSK_PREMIUM_BLOCKED_FLOW_REASON = "Rysk Premium subscriptions and redemptions settle asynchronously by epoch and are not supported by the generic vault transaction adapter"

#: Minimal verified LiquidityPool interface used by the read-only adapter.
#: Historical prices and discovery use event topics instead of this ABI.
RYSK_POOL_ABI = [
    {
        "inputs": [],
        "name": "collateralAsset",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "getTVL",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]


class RyskVault(VaultBase):
    """Expose onchain identity and final epoch history for a Rysk pool.

    Generic transaction and investor-flow adapters remain disabled until the
    complete queued lifecycle has guarded transaction coverage.
    """

    def __init__(self, web3: Web3, spec: VaultSpec, token_cache: dict | None = None, features: set[ERC4626Feature] | None = None, default_block_identifier: BlockIdentifier | None = None, require_denomination_token: bool = False) -> None:
        """Create an adapter for an event-discovered Rysk pool.

        The adapter has no mutable application-catalogue dependency. Contract
        identity comes from ``spec`` and collateral metadata comes from the
        verified LiquidityPool interface.

        :param web3:
            Pool-chain Web3 connection.
        :param spec:
            Chain and pool share-token address.
        :param token_cache:
            Optional shared token metadata cache.
        :param features:
            Persisted scanner features.
        :param default_block_identifier:
            Default block for metadata reads.
        :param require_denomination_token:
            Whether collateral-token read failure is fatal.
        :return:
            None.
        """

        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        self.web3 = web3
        self.spec = spec
        self.features = set(features or {ERC4626Feature.rysk_premium_like}) | {ERC4626Feature.share_price_equivalence}
        self.default_block_identifier = default_block_identifier or "latest"
        self.pool_contract: Contract = web3.eth.contract(address=Web3.to_checksum_address(spec.vault_address), abi=RYSK_POOL_ABI)
        self.historical_context_path: Path = get_rysk_historical_context_path()

    @property
    def chain_id(self) -> int:
        """Return the pool deployment chain.

        :return:
            EVM chain identifier from the scanner specification.
        """

        return self.spec.chain_id

    @property
    def address(self) -> HexAddress:
        """Return the pool and LP share-token address.

        :return:
            Checksum EVM address.
        """

        return HexAddress(Web3.to_checksum_address(self.spec.vault_address))

    @property
    def vault_address(self) -> HexAddress:
        """Return the scanner-compatible pool identity.

        :return:
            Pool and LP share-token address.
        """

        return self.address

    @property
    def name(self) -> str:
        """Return the onchain ERC-20 share-token name.

        :return:
            Pool name stored by the deployed contract.
        """

        return self.share_token.name

    @property
    def symbol(self) -> str:
        """Return the onchain ERC-20 share-token symbol.

        :return:
            LP token symbol.
        """

        return self.share_token.symbol

    @property
    def short_description(self) -> str:
        """Return a conservative product description.

        :return:
            Description that does not infer a strategy from token naming.
        """

        return "Rysk Premium epoch-settled option-writing pool"

    def fetch_share_token(self) -> TokenDetails:
        """Fetch ERC-20 metadata for the pool's LP shares.

        :return:
            Share-token details from the pool contract.
        """

        return fetch_erc20_details(self.web3, self.address, chain_id=self.chain_id, cache=self.token_cache, raise_on_error=False, cause_diagnostics_message=f"Rysk Premium share token for pool {self.address}")

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Return the pool itself as its share-token address.

        :param block_identifier:
            Ignored because the identity is immutable for the adapter.
        :return:
            Pool address.
        """

        return self.address

    def fetch_denomination_token_address(self) -> HexAddress:
        """Read the pool collateral token from ``collateralAsset()``.

        :return:
            Checksum collateral-token address.
        """

        address = self.pool_contract.functions.collateralAsset().call(block_identifier=self.default_block_identifier)
        return HexAddress(Web3.to_checksum_address(address))

    def fetch_denomination_token(self) -> TokenDetails | None:
        """Fetch collateral-token metadata used to scale epoch prices.

        :return:
            Collateral ERC-20 details, or ``None`` after a recoverable metadata
            failure.
        """

        return fetch_erc20_details(self.web3, self.fetch_denomination_token_address(), chain_id=self.chain_id, cache=self.token_cache, raise_on_error=False, cause_diagnostics_message=f"Rysk Premium collateral for pool {self.address}")

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch the human-readable ERC-20 LP share supply.

        :param block_identifier:
            Requested EVM state block.
        :return:
            Outstanding LP shares.
        """

        raw_supply = self.share_token.contract.functions.totalSupply().call(block_identifier=block_identifier)
        return self.share_token.convert_to_decimals(raw_supply)

    def fetch_reported_tvl(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch Rysk's collateral-only ``getTVL()`` value.

        This value is useful for pool-size reporting but is not marked option-
        book NAV and must not be used as ``total_assets`` or a share price.

        :param block_identifier:
            Requested EVM state block.
        :return:
            Free plus allocated collateral in denomination-token units.
        """

        raw_tvl = self.pool_contract.functions.getTVL().call(block_identifier=block_identifier)
        denomination = self.denomination_token
        if denomination is None:
            raise RuntimeError(f"Cannot scale Rysk Premium TVL without collateral metadata: {self.address}")
        return denomination.convert_to_decimals(raw_tvl)

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Reject a point-in-time price read before epoch finalisation.

        :param block_identifier:
            Requested EVM state block.
        :return:
            Never returns.
        :raise NotImplementedError:
            Always; use the contextual historical reader.
        """

        message = "Rysk Premium share price is final only after epochExecuted"
        raise NotImplementedError(message)

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Reject collateral-only TVL as full marked option-book NAV.

        :param block_identifier:
            Requested EVM state block.
        :return:
            Never returns.
        :raise NotImplementedError:
            Always; the contract's ``getTVL()`` omits option liabilities.
        """

        message = "Rysk Premium getTVL() is collateral-only and is not full marked option-book NAV"
        raise NotImplementedError(message)

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Reject an isolated NAV read outside final epoch accounting.

        :param block_identifier:
            Requested EVM state block.
        :return:
            Never returns.
        """

        return self.fetch_total_assets(block_identifier)

    def fetch_info(self) -> VaultInfo:
        """Return the onchain pool identity and accounting source.

        :return:
            Pool, collateral and final-price source fields.
        """

        return {
            "token": self.address,
            "chain_id": self.chain_id,
            "asset": self.fetch_denomination_token_address(),
            "nav_source": "rysk_premium_finalised_epoch_events",
        }

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Export Rysk-specific valuation and flow caveats.

        :return:
            Private metadata fields for common scanner rows.
        """

        return {
            "_notes": RYSK_PREMIUM_BLOCKED_FLOW_REASON,
            "_nav_source": "rysk_premium_finalised_epoch_events",
            "_nav_estimated": False,
        }

    def fetch_portfolio(self, universe: TradingUniverse, block_identifier: BlockIdentifier | None = None) -> VaultPortfolio:
        """Return no fabricated spot portfolio for the option book.

        Open option liabilities need protocol-specific position accounting;
        collateral balances alone are incomplete.

        :param universe:
            Ignored common trading universe.
        :param block_identifier:
            Ignored EVM state block.
        :return:
            Empty common spot portfolio.
        """

        return VaultPortfolio(spot_erc20=LowercaseDict())

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Create the finalised-epoch contextual reader.

        :param stateful:
            Ignored because the shared event context owns incrementality.
        :return:
            Rysk historical reader.
        """

        return RyskPremiumHistoricalReader(self)

    def get_flow_manager(self) -> VaultFlowManager:
        """Reject incomplete generic investor-flow accounting.

        Rysk does emit pool-local deposit, redemption and withdrawal events,
        but their queued lifecycle needs a protocol-specific flow manager.

        :return:
            Never returns.
        :raise NotImplementedError:
            Always until the full lifecycle is implemented.
        """

        raise NotImplementedError(RYSK_PREMIUM_BLOCKED_FLOW_REASON)

    def get_deposit_manager(self) -> VaultDepositManager:
        """Reject generic transaction construction for queued flows.

        :return:
            Never returns.
        :raise NotImplementedError:
            Always until both subscription and redemption are implemented.
        """

        raise NotImplementedError(RYSK_PREMIUM_BLOCKED_FLOW_REASON)

    def has_block_range_event_support(self) -> bool:
        """Return whether a common investor-flow manager is implemented.

        Rysk event logs are used for discovery and pricing, but generic
        investor-flow replay is not yet implemented.

        :return:
            Always ``False``.
        """

        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        """Return that deposits do not map to generic spot positions.

        :return:
            Always ``False``.
        """

        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return no mapped recurring LP management fee.

        :param block_identifier:
            Ignored.
        :return:
            ``None`` because option-premium fees are a different fee model.
        """

        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return no mapped generic LP performance fee.

        :param block_identifier:
            Ignored.
        :return:
            ``None`` because option-premium fees are a different fee model.
        """

        return None

    def get_fee_data(self) -> FeeData:
        """Return unspecified common LP fee fields.

        :return:
            Common fee payload without misclassified option-sale fees.
        """

        return FeeData(fee_mode=None, management=None, performance=None, deposit=None, withdraw=None)

    def get_share_price_source(self) -> PriceSource:
        """Classify final epoch values as authoritative smart-contract data.

        :return:
            Smart-contract source classification.
        """

        return PriceSource.smart_contract_event

    def get_link(self, referral: str | None = None) -> str:
        """Return the canonical Rysk Premium application.

        Rysk does not publish a stable per-pool URL pattern.

        :param referral:
            Ignored because no generic referral URL exists.
        :return:
            Official Premium application entry point.
        """

        return "https://app.rysk.finance/premium/"
