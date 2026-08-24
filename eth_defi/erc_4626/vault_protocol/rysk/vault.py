"""Read-only Rysk Premium DeFi option-vault adapter.

Rysk Premium issues ERC-20 LP shares for curator-managed option-writing pools.
Subscriptions and withdrawals are queued and settle at epoch prices, so this
adapter deliberately blocks generic vault transactions.  Historical equity
uses the final epoch withdrawal price per share::

    share_price_equivalent = withdrawalPps / 10**collateral_token.decimals

``withdrawalPps`` is the final price per share submitted at the epoch boundary.
``depositPps`` remains in source storage for audit only. The dashboard TVL is
not a share-price proxy because it excludes the option book liability.

Rysk Premium is a DeFi protocol pool, not a legally structured fund.

See https://docs.rysk.finance/rysk-premium/rysk-premium-explainer.
"""

# ruff: noqa: ARG002, FBT001, FBT002, PLR0904, PLR0917, PLR6301

from decimal import Decimal
from pathlib import Path

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.rysk.constants import RYSK_PREMIUM_POOLS, RyskPremiumPool
from eth_defi.erc_4626.vault_protocol.rysk.historical import RyskPremiumHistoricalReader
from eth_defi.erc_4626.vault_protocol.rysk.historical_context import get_rysk_historical_context_path
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.types import Percent
from eth_defi.vault.base import TradingUniverse, VaultBase, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.deposit_redeem import VaultDepositManager
from eth_defi.vault.fee import FeeData
from eth_defi.vault.lower_case_dict import LowercaseDict

RYSK_PREMIUM_BLOCKED_FLOW_REASON = "Rysk Premium subscriptions and redemptions settle asynchronously at epoch prices and are not supported by the generic vault transaction adapter"


class RyskVault(VaultBase):
    """Read-only adapter for epoch-settled Rysk Premium LP shares, not a fund.

    The adapter exposes pool identity and token metadata while delegating
    valuation to final epoch snapshots and rejecting unsupported transaction flows.
    """

    def __init__(self, web3: Web3, spec: VaultSpec, token_cache: dict | None = None, features: set[ERC4626Feature] | None = None, default_block_identifier: BlockIdentifier | None = None, require_denomination_token: bool = False) -> None:
        """Create an adapter for a catalogue-registered Rysk Premium pool.

        Construction resolves the chain-aware runtime catalogue entry used by
        all subsequent contract and application metadata reads.

        :param web3:
            Pool-chain Web3 connection.
        :param spec:
            Chain and pool share-token address.
        :param token_cache:
            Optional shared token details cache.
        :param features:
            Persisted features, expected to include ``rysk_premium_like``.
        :param default_block_identifier:
            Default metadata read block.
        :param require_denomination_token:
            Whether collateral metadata failure is fatal.
        :return:
            None.
        """

        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        self.web3 = web3
        self.spec = spec
        self.features = set(features or {ERC4626Feature.rysk_premium_like}) | {ERC4626Feature.share_price_equivalence}
        self.default_block_identifier = default_block_identifier or "latest"
        try:
            self.pool: RyskPremiumPool = RYSK_PREMIUM_POOLS[spec.chain_id, HexAddress(spec.vault_address.lower())]
        except KeyError as error:
            raise RuntimeError(f"Unsupported Rysk Premium pool: chain={spec.chain_id}, pool={spec.vault_address}") from error
        self.historical_context_path: Path = get_rysk_historical_context_path()

    @property
    def chain_id(self) -> int:
        """Return the pool deployment chain.

        Rysk catalogue identities are chain-aware because equal EVM addresses
        on different networks must not be treated as the same product.

        :return:
            EVM chain id.
        """

        return self.spec.chain_id

    @property
    def address(self) -> HexAddress:
        """Return the pool's ERC-20 LP share address.

        The checksum form is suitable for Web3 contract calls while catalogue
        storage remains normalised to lower case.

        :return:
            Checksum-addressed LP share token.
        """

        return HexAddress(Web3.to_checksum_address(self.pool.address))

    @property
    def vault_address(self) -> HexAddress:
        """Return the scanner-compatible LP share token address.

        For Rysk Premium the canonical vault identity and issued share token
        are the same contract address.

        :return:
            Pool share token address.
        """

        return self.address

    @property
    def name(self) -> str:
        """Return the catalogue pool name.

        Rysk and its curators publish the display name through the application
        catalogue rather than a generic ERC-4626 metadata interface.

        :return:
            Rysk Premium pool display name.
        """

        return self.pool.name

    @property
    def symbol(self) -> str:
        """Return the onchain LP share symbol.

        Symbol metadata is read from the pool's ERC-20 share contract at the
        adapter's configured block identifier.

        :return:
            ERC-20 symbol.
        """

        return self.share_token.symbol

    @property
    def short_description(self) -> str:
        """Return a product-specific option-writing description.

        The text reflects only the advertised call or put strategy and avoids
        assigning an unsupported generic strategy taxonomy tag.

        :return:
            Concise Rysk Premium strategy text.
        """

        return f"Rysk Premium curator-managed {self.pool.option_type}-writing pool"

    def fetch_share_token(self) -> TokenDetails:
        """Fetch ERC-20 LP share token metadata.

        Metadata comes from the pool contract identified by Rysk's `public
        catalogue <https://premium.rysk.finance/api/pools>`__.

        :return:
            Pool share token details.
        """

        return fetch_erc20_details(self.web3, self.address, chain_id=self.chain_id, cache=self.token_cache, raise_on_error=False, cause_diagnostics_message=f"Rysk Premium share token for pool {self.address}")

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Return the LP share token itself.

        The Premium pool contract is also the ERC-20 share token, so no
        block-dependent lookup is necessary.

        :param block_identifier:
            Ignored, retained for adapter compatibility.
        :return:
            Pool share token address.
        """

        return self.address

    def fetch_denomination_token_address(self) -> HexAddress:
        """Return the pool collateral token.

        Rysk publishes this contract as the pool ``asset`` in its application
        catalogue and uses it for subscription and withdrawal accounting.

        :return:
            Subscription and withdrawal asset.
        """

        return HexAddress(Web3.to_checksum_address(self.pool.asset))

    def fetch_denomination_token(self) -> TokenDetails | None:
        """Fetch the pool collateral token metadata.

        The token's native decimals determine how raw final withdrawal PPS is
        scaled into the common share-price-equivalent curve.

        :return:
            Collateral ERC-20 details.
        """

        return fetch_erc20_details(self.web3, self.fetch_denomination_token_address(), chain_id=self.chain_id, cache=self.token_cache, raise_on_error=False, cause_diagnostics_message=f"Rysk Premium collateral for pool {self.address}")

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch current LP share supply.

        The ERC-20 raw supply is converted with share-token metadata rather than
        a hardcoded decimal multiplier.

        :param block_identifier:
            Requested EVM state block.
        :return:
            Human-readable outstanding share supply.
        """

        raw_supply = self.share_token.contract.functions.totalSupply().call(block_identifier=block_identifier)
        return self.share_token.convert_to_decimals(raw_supply)

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Reject a point-in-time RPC price read without final epoch context.

        Rysk's official `Premium explainer
        <https://docs.rysk.finance/rysk-premium/rysk-premium-explainer>`__ defines
        share pricing at epoch boundaries after option-book valuation.

        :param block_identifier:
            Requested EVM block.
        :return:
            Never returns.
        :raise NotImplementedError:
            Rysk price is an epoch finalisation output, not pool balance/supply.
        """

        message = "Rysk Premium share price must be read from final epoch withdrawal PPS context"
        raise NotImplementedError(message)

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Reject simplified collateral as a full Rysk option-book NAV.

        Dashboard collateral excludes the marked open-option liability, so it
        cannot safely populate the common ``total_assets`` field.

        :param block_identifier:
            Requested EVM block.
        :return:
            Never returns.
        :raise NotImplementedError:
            Rysk final NAV requires its epoch valuation process.
        """

        message = "Rysk Premium dashboard assets do not represent final marked option-book NAV"
        raise NotImplementedError(message)

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Reject isolated NAV reads outside finalised epoch context.

        This method shares the full-NAV restriction enforced by
        :meth:`fetch_total_assets` and does not manufacture a balance-based value.

        :param block_identifier:
            Requested EVM block.
        :return:
            Never returns.
        """

        return self.fetch_total_assets(block_identifier)

    def fetch_info(self) -> VaultInfo:
        """Return Rysk pool identity and accounting metadata.

        The payload records which contracts and source feed define the product
        without presenting gross collateral TVL as marked NAV.

        :return:
            Pool registry, collateral and option strategy details.
        """

        return {
            "token": self.address,
            "chain_id": self.chain_id,
            "registry": Web3.to_checksum_address(self.pool.registry),
            "option_handler": Web3.to_checksum_address(self.pool.option_handler),
            "asset": self.fetch_denomination_token_address(),
            "option_type": self.pool.option_type,
            "nav_source": "rysk_premium_final_epoch_withdrawal_pps",
        }

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Export Rysk-specific metadata without treating gross TVL as NAV.

        Scanner fields preserve catalogue identities, async flow restrictions
        and the final-epoch valuation source for downstream consumers.

        :return:
            Common scanner enrichment fields.
        """

        denomination = self.denomination_token
        return {
            "Denomination": denomination.symbol if denomination else None,
            "_denomination_token": denomination.export() if denomination else None,
            "_notes": self.get_notes(),
            "_deposit_closed_reason": self.fetch_deposit_closed_reason(),
            "_redemption_closed_reason": self.fetch_redemption_closed_reason(),
            "_nav_source": "rysk_premium_final_epoch_withdrawal_pps",
            "_nav_estimated": False,
            "_rysk_registry": Web3.to_checksum_address(self.pool.registry),
            "_rysk_option_handler": Web3.to_checksum_address(self.pool.option_handler),
            "_rysk_option_type": self.pool.option_type,
            "_rysk_option_sale_fee_bps": self.pool.option_sale_fee_bps,
            "_rysk_authority": Web3.to_checksum_address(self.pool.authority) if self.pool.authority else None,
        }

    def fetch_portfolio(self, universe: TradingUniverse, block_identifier: BlockIdentifier | None = None) -> VaultPortfolio:
        """Return no generic spot portfolio for a managed option book.

        Open option liabilities need Rysk-specific position accounting and
        cannot be reconstructed from collateral-token balances alone.

        :param universe:
            Ignored portfolio universe.
        :param block_identifier:
            Ignored EVM block.
        :return:
            Empty portfolio because option positions require protocol accounting.
        """

        return VaultPortfolio(spot_erc20=LowercaseDict())

    def fetch_deposit_closed_reason(self) -> str:
        """Describe the unsupported asynchronous subscription lifecycle.

        Deposits are queued and priced during epoch execution, so the generic
        synchronous vault transaction path remains disabled.

        :return:
            Public generic-adapter block reason.
        """

        return RYSK_PREMIUM_BLOCKED_FLOW_REASON

    def fetch_redemption_closed_reason(self) -> str:
        """Describe the unsupported asynchronous redemption lifecycle.

        Withdrawals require request and completion steps across an epoch, which
        the generic redemption adapter does not implement.

        :return:
            Public generic-adapter block reason.
        """

        return RYSK_PREMIUM_BLOCKED_FLOW_REASON

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Create the final-PPS contextual historical reader.

        The reader consumes locally persisted application snapshots rather than
        issuing a static multicall at every sampled block.

        :param stateful:
            Ignored because Rysk context owns its own incrementality.
        :return:
            Rysk Premium contextual reader.
        """

        return RyskPremiumHistoricalReader(self)

    def get_flow_manager(self) -> VaultFlowManager:
        """Reject unsupported generic flow accounting.

        Rysk uses queued protocol-specific events, so a standard ERC-4626 flow
        manager would expose incomplete and misleading investor activity.

        :return:
            Never returns.
        :raise NotImplementedError:
            Rysk requires an epoch-aware lifecycle implementation.
        """

        raise NotImplementedError(RYSK_PREMIUM_BLOCKED_FLOW_REASON)

    def get_deposit_manager(self) -> VaultDepositManager:
        """Reject generic transaction construction for epoch-settled LP shares.

        Its queued subscription lifecycle needs a protocol-specific adapter.

        :return:
            Never returns.
        :raise NotImplementedError:
            Generic deposit and redemption construction is unsupported.
        """

        raise NotImplementedError(RYSK_PREMIUM_BLOCKED_FLOW_REASON)

    def has_block_range_event_support(self) -> bool:
        """Return that generic ERC-4626 flow events are unavailable.

        Historical valuation is sourced from the Premium snapshot API instead
        of canonical ``Deposit`` and ``Withdraw`` event discovery.

        :return:
            Always ``False``.
        """

        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        """Return that queued subscriptions are not generic position flows.

        Capital allocation occurs through the curator's option strategy and
        cannot be represented as a uniform spot-position distribution.

        :return:
            Always ``False``.
        """

        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return no universal published manager fee.

        Option-premium deductions are not equivalent to a recurring LP
        management fee and therefore are not mapped into this field.

        :param block_identifier:
            Ignored.
        :return:
            ``None`` because pool-level curator fees need a separate source.
        """

        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return no universal published performance fee.

        The advertised option-sale fee applies to option premium rather than
        positive LP performance measured by the generic vault model.

        :param block_identifier:
            Ignored.
        :return:
            ``None`` because option-sale fees are not LP performance fees.
        """

        return None

    def get_fee_data(self) -> FeeData:
        """Return unspecified LP fees rather than misclassifying option fees.

        Unknown fields remain explicit ``None`` values so downstream reporting
        does not convert a protocol fee into an investor management charge.

        :return:
            Common fee payload with unknown values.
        """

        return FeeData(fee_mode=None, management=None, performance=None, deposit=None, withdraw=None)

    def get_link(self, referral: str | None = None) -> str:
        """Return the official Rysk Premium application page.

        Rysk does not publish a stable per-pool URL pattern, so every catalogue
        product links to the canonical Premium application entry point.

        :param referral:
            Ignored; Rysk Premium has no generic referral URL.
        :return:
            Official Premium app.
        """

        return "https://app.rysk.finance/premium/"
