"""Read-only adapter for Wildcat Finance version 2 lending markets.

A `Wildcat <https://app.wildcat.finance/lender>`__ market lends one asset to
one borrower, mostly without collateral. It is not an ERC-4626 vault: the
market contract is itself the lender's rebasing ERC-20 token, like an Aave
aToken::

    balanceOf = scaledBalanceOf * scaleFactor / 1e27
    share price = scaleFactor / 1e27

``scaleFactor`` grows with base interest and, while a borrower stays
delinquent beyond the grace period, with penalty interest. The market's view
functions accrue interest to ``block.timestamp``, so historical ``eth_call``
reads return the accrued state. ``totalAssets()`` is the market's idle cash,
not lender value; lender value is ``totalSupply()``.

The protocol fee is a share of interest paid by the borrower on top of lender
interest, so lenders pay no fees. Withdrawals are queued into batches. When
a batch expires, lenders can claim what the market's cash covers; the rest
stays queued until the borrower repays.

See the `scale factor documentation <https://github.com/wildcat-finance/v2-protocol/blob/main/docs/Scale%20Factor.md>`__
and `core behaviour <https://github.com/wildcat-finance/wildcat-docs/blob/main/technical-overview/security-developer-dives/core-behaviour.md>`__.
"""

# Adapter methods follow the VaultBase interface.
# ruff: noqa: ARG002, FBT001, FBT002, PLR0904, PLR0917, PLR6301

import datetime
from decimal import Decimal
from functools import cached_property

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.compat import native_datetime_utc_fromtimestamp, native_datetime_utc_now
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.wildcat.constants import WILDCAT_FIXED_TERM_HOOKS_VERSION, WILDCAT_OPEN_TERM_HOOKS_VERSION, WILDCAT_TEST_BORROWERS
from eth_defi.erc_4626.vault_protocol.wildcat.historical import WildcatHistoricalReader
from eth_defi.erc_4626.vault_protocol.wildcat.offchain_metadata import WildcatMarketMetadata, extract_summary_line, fetch_wildcat_market_metadata
from eth_defi.erc_4626.vault_protocol.wildcat.state import WildcatMarketState
from eth_defi.erc_4626.vault_protocol.wildcat.tags import get_strategy_tags
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.types import Percent
from eth_defi.vault.base import DEPOSIT_CLOSED_CAP_REACHED, TradingUniverse, VaultBase, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec, WithdrawalDelayType, WithdrawalPeriod, normalise_vault_description, normalise_vault_short_description
from eth_defi.vault.deposit_redeem import VaultDepositManager
from eth_defi.vault.flag import TEST_VAULT, VaultFlag
from eth_defi.vault.lower_case_dict import LowercaseDict
from eth_defi.vault.price_source import PriceSource
from eth_defi.vault.strategy_tag import StrategyTag

#: Reason for disabled generic transaction and flow adapters.
WILDCAT_BLOCKED_FLOW_REASON = "Wildcat markets use queued withdrawal batches and per-lender access control, which the generic vault transaction adapter does not support"

#: Deposit closure reason for a market closed by its borrower.
DEPOSIT_CLOSED_MARKET_CLOSED = "Market closed by borrower"

#: Redemption closure reason before a fixed-term market matures.
REDEMPTION_CLOSED_FIXED_TERM = "Withdrawals open when the fixed term ends"

#: Note shown for a market flagged ``delinquent_markets``.
WILDCAT_DEFAULT_NOTE = "The borrower has kept less than the required reserve in the market for longer than its grace period, so a [penalty interest rate](https://docs.wildcat.finance/using-wildcat/delinquency) applies. The share price includes interest the borrower may never repay."


class WildcatMarket(VaultBase):
    """Expose a Wildcat version 2 market as a read-only vault.

    Generic transaction and investor-flow adapters are disabled.
    """

    def __init__(self, web3: Web3, spec: VaultSpec, token_cache: dict | None = None, features: set[ERC4626Feature] | None = None, default_block_identifier: BlockIdentifier | None = None, require_denomination_token: bool = False) -> None:
        """Create an adapter for a registry-discovered Wildcat market.

        :param web3:
            Ethereum Web3 connection.
        :param spec:
            Chain and market address.
        :param token_cache:
            Optional shared token metadata cache.
        :param features:
            Persisted scanner features.
        :param default_block_identifier:
            Default block for metadata reads.
        :param require_denomination_token:
            Whether denomination-token read failure is fatal.
        """
        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        self.web3 = web3
        self.spec = spec
        self.features = set(features or ()) | {ERC4626Feature.wildcat_like}
        self.default_block_identifier = default_block_identifier or "latest"
        self.market_contract: Contract = get_deployed_contract(web3, "wildcat/WildcatMarket.json", Web3.to_checksum_address(spec.vault_address))

    @property
    def chain_id(self) -> int:
        """Return the market chain."""
        return self.spec.chain_id

    @property
    def address(self) -> HexAddress:
        """Return the market and share-token address."""
        return HexAddress(Web3.to_checksum_address(self.spec.vault_address))

    @property
    def vault_address(self) -> HexAddress:
        """Return the scanner-compatible market identity."""
        return self.address

    @property
    def name(self) -> str:
        """Return the onchain market token name."""
        return self.share_token.name

    @property
    def symbol(self) -> str:
        """Return the onchain market token symbol."""
        return self.share_token.symbol

    @cached_property
    def borrower(self) -> HexAddress:
        """Return the market's immutable borrower address."""
        return HexAddress(self.market_contract.functions.borrower().call(block_identifier=self.default_block_identifier))

    @cached_property
    def delinquency_grace_period(self) -> int:
        """Return the market's immutable delinquency grace period in seconds."""
        return self.market_contract.functions.delinquencyGracePeriod().call(block_identifier=self.default_block_identifier)

    @cached_property
    def wildcat_metadata(self) -> WildcatMarketMetadata:
        """Offchain market description and borrower profile from the Wildcat app API.

        Fetched and cached on first access, see
        :py:mod:`eth_defi.erc_4626.vault_protocol.wildcat.offchain_metadata`.
        """
        return fetch_wildcat_market_metadata(self.web3, self.address, self.borrower)

    @property
    def description(self) -> str | None:
        """Return the market description, or the borrower's profile description.

        Both texts are supplied by the borrower and not verified by Wildcat.
        """
        metadata = self.wildcat_metadata
        return normalise_vault_description(metadata["market_description"] or metadata["borrower_description"])

    @property
    def short_description(self) -> str | None:
        """Return the first sentence of :py:attr:`description`.

        Starts from the first line that ends a sentence, skipping heading,
        date and link lines, see :py:func:`extract_summary_line`.
        """
        metadata = self.wildcat_metadata
        return normalise_vault_short_description(extract_summary_line(metadata["market_description"] or metadata["borrower_description"]))

    @property
    def manager_name(self) -> str | None:
        """Return the borrower's KYB-verified legal name.

        The unverified borrower alias and the borrower address are never used.
        """
        return self.wildcat_metadata["borrower_name"]

    def fetch_share_token(self) -> TokenDetails:
        """Fetch the market token, which is the lender's share."""
        return fetch_erc20_details(self.web3, self.address, chain_id=self.chain_id, cache=self.token_cache, raise_on_error=False, cause_diagnostics_message=f"Wildcat market token {self.address}")

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Return the market itself, which is the lender's share token.

        The historical price scanner reads this to prepare share-token metadata.

        :param block_identifier:
            Ignored, because the identity is immutable.
        :return:
            Market address.
        """
        return self.address

    def fetch_denomination_token_address(self) -> HexAddress:
        """Read the lent asset from ``asset()``."""
        return HexAddress(Web3.to_checksum_address(self.market_contract.functions.asset().call(block_identifier=self.default_block_identifier)))

    def fetch_denomination_token(self) -> TokenDetails | None:
        """Fetch the lent asset."""
        return fetch_erc20_details(self.web3, self.fetch_denomination_token_address(), chain_id=self.chain_id, cache=self.token_cache, raise_on_error=False, cause_diagnostics_message=f"Wildcat asset for market {self.address}")

    def fetch_market_state(self, block_identifier: BlockIdentifier = "latest") -> WildcatMarketState:
        """Read the market state accrued to the requested block.

        :param block_identifier:
            Requested EVM state block.
        :return:
            Decoded ``currentState()``.
        """
        return WildcatMarketState(*self.market_contract.functions.currentState().call(block_identifier=block_identifier))

    @cached_property
    def current_state(self) -> WildcatMarketState:
        """Market state at the adapter's default block, read once per adapter."""
        return self.fetch_market_state(self.default_block_identifier)

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Return ``scaleFactor / 1e27`` in units of the lent asset.

        :param block_identifier:
            Requested EVM state block.
        :return:
            Asset value of one scaled market token.
        """
        return self.fetch_market_state(block_identifier).share_price

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Return lender value, the market's ``totalSupply()``.

        The market's own ``totalAssets()`` is idle cash and is never used as TVL.

        :param block_identifier:
            Requested EVM state block.
        :return:
            Amount owed to lenders in asset units.
        """
        raw = self.market_contract.functions.totalSupply().call(block_identifier=block_identifier)
        return self.share_token.convert_to_decimals(raw)

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Return lender value, see :py:meth:`fetch_total_assets`.

        :param block_identifier:
            Requested EVM state block.
        :return:
            Amount owed to lenders in asset units.
        """
        return self.fetch_total_assets(block_identifier)

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Return the scaled market token supply.

        :param block_identifier:
            Requested EVM state block.
        :return:
            Scaled supply, so that supply times share price equals lender value.
        """
        raw = self.market_contract.functions.scaledTotalSupply().call(block_identifier=block_identifier)
        return self.share_token.convert_to_decimals(raw)

    def fetch_lending_metrics(self, block_identifier: BlockIdentifier = "latest") -> tuple[Decimal, Percent | None]:
        """Read available liquidity and utilisation from one state snapshot.

        See :py:meth:`WildcatMarketState.calculate_lending_metrics`.

        :param block_identifier:
            Requested EVM state block.
        :return:
            ``(available liquidity in asset units, utilisation)``.
        """
        state = self.fetch_market_state(block_identifier)
        cash = self.market_contract.functions.totalAssets().call(block_identifier=block_identifier)
        available, utilisation = state.calculate_lending_metrics(cash)
        return self.share_token.convert_to_decimals(available), utilisation

    def fetch_available_liquidity(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Return lender cash not reserved for queued withdrawals.

        :param block_identifier:
            Requested EVM state block.
        :return:
            Available liquidity in asset units.
        """
        return self.fetch_lending_metrics(block_identifier)[0]

    def fetch_utilisation_percent(self, block_identifier: BlockIdentifier = "latest") -> Percent | None:
        """Return the share of lender supply lent out to the borrower.

        :param block_identifier:
            Requested EVM state block.
        :return:
            Utilisation from 0 to 1, or ``None`` when the market has no supply.
        """
        return self.fetch_lending_metrics(block_identifier)[1]

    def fetch_deposit_open(self) -> bool:
        """Return whether the market accepts deposits up to its cap.

        Account admission is separate, see :py:meth:`is_whitelisted_deposit`.
        """
        return self.current_state.max_deposit > 0

    def fetch_deposit_closed_reason(self) -> str | None:
        """Explain a closed or full market."""
        state = self.current_state
        if state.is_closed:
            return DEPOSIT_CLOSED_MARKET_CLOSED
        if state.max_deposit == 0:
            return DEPOSIT_CLOSED_CAP_REACHED
        return None

    @cached_property
    def hooks_address(self) -> HexAddress:
        """Return the market's hooks instance.

        ``hooks()`` returns a packed ``HooksConfig`` whose top 160 bits are
        the hooks address.
        """
        config = self.market_contract.functions.hooks().call(block_identifier=self.default_block_identifier)
        return HexAddress(Web3.to_checksum_address(f"0x{config >> 96:040x}"))

    @cached_property
    def hooks_version(self) -> str:
        """Return the hooks template name, ``OpenTermHooks`` or ``FixedTermHooks``."""
        hooks = get_deployed_contract(self.web3, "wildcat/OpenTermHooks.json", self.hooks_address)
        return hooks.functions.version().call(block_identifier=self.default_block_identifier)

    def fetch_hooked_market(self) -> tuple:
        """Read the market's access configuration from its hooks instance.

        The OpenTermHooks and FixedTermHooks ``HookedMarket`` structs differ,
        so the template's own ABI decodes the result.

        :return:
            Decoded ``HookedMarket`` tuple.
        :raise NotImplementedError:
            For an unrecognised hooks template.
        """
        abi_by_version = {
            WILDCAT_OPEN_TERM_HOOKS_VERSION: "wildcat/OpenTermHooks.json",
            WILDCAT_FIXED_TERM_HOOKS_VERSION: "wildcat/FixedTermHooks.json",
        }
        abi = abi_by_version.get(self.hooks_version)
        if abi is None:
            raise NotImplementedError(f"Unsupported Wildcat hooks template {self.hooks_version!r} for market {self.address}")
        hooks = get_deployed_contract(self.web3, abi, self.hooks_address)
        return hooks.functions.getHookedMarket(self.address).call(block_identifier=self.default_block_identifier)

    def is_whitelisted_deposit(self) -> bool:
        """Return whether deposits require a lender credential.

        Reads ``HookedMarket.depositRequiresAccess``. A market whose role
        provider grants credentials to anyone is still reported as
        whitelisted. The ``useOnDeposit`` hook bit is not an admission rule.

        :return:
            ``True`` when deposits require a credential.
        :raise NotImplementedError:
            For an unrecognised hooks template, recorded as unknown permission.
        """
        return bool(self.fetch_hooked_market()[2])

    def fetch_fixed_term_end(self) -> datetime.datetime | None:
        """Return the fixed-term end for a fixed-term market.

        :return:
            Naive UTC term end, or ``None`` for an open-term market.
        """
        if self.hooks_version != WILDCAT_FIXED_TERM_HOOKS_VERSION:
            return None
        return native_datetime_utc_fromtimestamp(self.fetch_hooked_market()[5])

    def fetch_redemption_closed_reason(self) -> str | None:
        """Explain withdrawals blocked until a fixed term ends."""
        term_end = self.fetch_fixed_term_end()
        if term_end is not None and term_end > native_datetime_utc_now():
            return REDEMPTION_CLOSED_FIXED_TERM
        return None

    def fetch_redemption_next_open(self) -> datetime.datetime | None:
        """Return when a fixed-term market's withdrawals open."""
        term_end = self.fetch_fixed_term_end()
        if term_end is not None and term_end > native_datetime_utc_now():
            return term_end
        return None

    def get_withdrawal_period(self) -> WithdrawalPeriod:
        """Return the withdrawal batch duration.

        A withdrawal joins the current batch and becomes claimable when the
        batch expires, so the contract-enforced wait is at most one batch
        duration. Like every :py:class:`WithdrawalPeriod`, this excludes
        liquidity shortages: an unfunded part of a batch stays queued until
        the borrower repays. Closed markets use zero-length batches. A fixed
        term is reported through :py:meth:`fetch_redemption_next_open` instead.

        :return:
            Withdrawal timing bounds.
        """
        if self.current_state.is_closed:
            duration = 0
        else:
            duration = self.market_contract.functions.withdrawalBatchDuration().call(block_identifier=self.default_block_identifier)
        return WithdrawalPeriod(
            min_period=datetime.timedelta(0),
            max_period=datetime.timedelta(seconds=duration),
            delay_type=WithdrawalDelayType.delay,
        )

    def is_defaulted(self) -> bool:
        """Return whether the borrower is delinquent beyond the grace period.

        Wildcat applies the penalty rate while the accumulated delinquent time
        exceeds the grace period, including for a while after a cure.

        :return:
            ``True`` when the penalty rate applies at the default block.
        """
        return self.current_state.time_delinquent > self.delinquency_grace_period

    def is_test_market(self) -> bool:
        """Return whether the market belongs to a reviewed Wildcat test borrower.

        See :py:data:`~eth_defi.erc_4626.vault_protocol.wildcat.constants.WILDCAT_TEST_BORROWERS`.

        :return:
            ``True`` for a Wildcat test market.
        """
        return self.borrower.lower() in WILDCAT_TEST_BORROWERS

    def get_flags(self) -> set[VaultFlag]:
        """Add Wildcat state flags to the manual flags.

        A test market is ``unofficial``, which hides it, and a defaulted
        market is ``delinquent_markets``.
        """
        flags = set(super().get_flags())
        if self.is_test_market():
            flags.add(VaultFlag.unofficial)
        if self.is_defaulted():
            flags.add(VaultFlag.delinquent_markets)
        return flags

    def get_notes(self) -> str | None:
        """Prepend test-market and default warnings to the manual notes."""
        warnings = [note for note, applies in ((TEST_VAULT, self.is_test_market()), (WILDCAT_DEFAULT_NOTE, self.is_defaulted())) if applies]
        notes = super().get_notes()
        return " ".join([*warnings, notes] if notes else warnings) or None

    def get_strategy_tags(self) -> set[StrategyTag]:
        """Return the lending and unsecured credit tags and reviewed address-specific tags."""
        return get_strategy_tags(self.address)

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent:
        """Return zero: the borrower pays the protocol fee on top of lender interest.

        :param block_identifier:
            Ignored; the fee model is fixed.
        :return:
            Zero.
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent:
        """Return zero: the borrower pays the protocol fee on top of lender interest.

        :param block_identifier:
            Ignored; the fee model is fixed.
        :return:
            Zero.
        """
        return 0.0

    def get_share_price_source(self) -> PriceSource:
        """Classify ``scaleFactor`` as smart-contract state."""
        return PriceSource.smart_contract_state

    def fetch_info(self) -> VaultInfo:
        """Return the market identity and lent asset."""
        return {
            "token": self.address,
            "chain_id": self.chain_id,
            "asset": self.fetch_denomination_token_address(),
            "borrower": self.borrower,
        }

    def fetch_portfolio(self, universe: TradingUniverse, block_identifier: BlockIdentifier | None = None) -> VaultPortfolio:
        """Return no spot portfolio; lender value is a claim on the borrower.

        :param universe:
            Ignored trading universe.
        :param block_identifier:
            Ignored EVM state block.
        :return:
            Empty portfolio.
        """
        return VaultPortfolio(spot_erc20=LowercaseDict())

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Create the static Multicall historical reader.

        :param stateful:
            Whether to keep adaptive reader state, read at most daily.
        :return:
            Wildcat historical reader.
        """
        return WildcatHistoricalReader(self, stateful)

    def get_flow_manager(self) -> VaultFlowManager:
        """Reject generic investor-flow accounting."""
        raise NotImplementedError(WILDCAT_BLOCKED_FLOW_REASON)

    def get_deposit_manager(self) -> VaultDepositManager:
        """Reject generic transaction construction."""
        raise NotImplementedError(WILDCAT_BLOCKED_FLOW_REASON)

    def has_block_range_event_support(self) -> bool:
        """Return that generic investor-flow replay is not implemented."""
        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        """Return that deposits do not map to spot positions."""
        return False

    def get_link(self, referral: str | None = None) -> str:
        """Return the market page in the Wildcat app.

        :param referral:
            Ignored; the app has no referral links.
        :return:
            Market page URL.
        """
        return f"https://app.wildcat.finance/lender/market/{self.address.lower()}"
