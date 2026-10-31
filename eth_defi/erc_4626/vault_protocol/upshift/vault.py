"""Upshift vault support, with deployment-specific account admission.

Multi-asset deployments may restrict senders independently of their accepted
tokens. Read ``sendersWhitelistAddress()`` before classifying deposits; USPC
Custody Vault is an allow-listed deployment. An asset whitelist, public app
listing or implemented deposit manager does not establish public admission.
The denomination token is the withdrawal payout token returned by ``asset()``;
additional accepted deposit tokens do not change the denomination.
See ``eth_defi/abi/upshift/README.md`` for verified contract sources.
"""

import datetime
import logging
from collections.abc import Iterable
from decimal import Decimal
from functools import cached_property

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

from eth_defi.abi import ZERO_ADDRESS_STR, get_deployed_contract
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault import ERC4626Vault, VaultReaderState
from eth_defi.erc_4626.vault_protocol.upshift.deposit_redeem import UpshiftDepositManager, UpshiftMultiAssetDepositManager
from eth_defi.erc_4626.vault_protocol.upshift.offchain_metadata import UpshiftVaultMetadata, fetch_upshift_vault_metadata
from eth_defi.erc_4626.vault_protocol.upshift.tags import STRATEGY_TAGS
from eth_defi.event_reader.conversion import convert_int256_bytes_to_int
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.provider.broken_provider import get_fork_safe_latest_block
from eth_defi.provider.fallback import ExtraValueError
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader, WithdrawalDelayType, WithdrawalPeriod
from eth_defi.vault.deposit_redeem import VaultDepositManagerCapability
from eth_defi.vault.fee import VaultFeeMode
from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags

logger = logging.getLogger(__name__)

#: Upshift management, withdrawal and instant-redemption fees use basis points.
UPSHIFT_BASIS_POINTS_DENOMINATOR = 10_000

#: Upshift performance fees use parts per million. The verified implementation
#: calculates ``totalAssetsIncrease * performanceFeeRate / 1e6``.
#: https://etherscan.io/address/0xEB5f80aCEa6060764E91c185bE93752Ab40F01c2#code
UPSHIFT_PERFORMANCE_FEE_DENOMINATOR = 1_000_000


class UpshiftMultiAssetHistoricalReader(VaultHistoricalReader):
    """Read Upshift multi-asset vault accounting history.

    Upshift multi-asset vaults are not plain ERC-4626 share-token contracts.
    The vault proxy exposes accounting methods like ``getSharePrice()`` and
    ``getTotalAssets()``, while ``lpTokenAddress()`` points to the ERC-20 share
    token used for name, symbol, decimals and total supply.

    Relevant verified implementation:
    `Upshift multiAssetVault <https://etherscan.io/address/0xEB5f80aCEa6060764E91c185bE93752Ab40F01c2#code>`__.
    """

    def __init__(self, vault: "UpshiftVault", stateful: bool):  # noqa: FBT001
        """Create a historical reader for Upshift multi-asset vaults.

        :param vault:
            Upshift vault adapter.

        :param stateful:
            Whether to attach adaptive reader state used by the shared
            historical multicaller.
        """

        super().__init__(vault)
        if stateful:
            self.reader_state = VaultReaderState(vault)
        else:
            self.reader_state = None

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Construct historical multicalls for Upshift multi-asset vaults.

        :return:
            Calls for vault share price, NAV, LP token supply, pause flags and
            configured deposit limits.
        """

        upshift = self.vault.upshift_contract.functions
        calls = {
            "getSharePrice": upshift.getSharePrice(),
            "getTotalAssets": upshift.getTotalAssets(),
            "lpTokenTotalSupply": self.vault.share_token.contract.functions.totalSupply(),
            "depositsPaused": upshift.depositsPaused(),
            "withdrawalsPaused": upshift.withdrawalsPaused(),
            "maxDepositAmount": upshift.maxDepositAmount(),
            "depositCap": upshift.depositCap(),
        }

        for function_name, contract_call in calls.items():
            yield EncodedCall.from_contract_call(
                contract_call,
                extra_data={
                    "function": function_name,
                    "vault": self.vault.address,
                },
                first_block_number=self.first_block,
            )

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        """Convert Upshift multi-asset multicalls to a vault price row.

        :param block_number:
            Historical block number.

        :param timestamp:
            Naive UTC block timestamp.

        :param call_results:
            Multicall results created by :py:meth:`construct_multicalls`.

        :return:
            :py:class:`VaultHistoricalRead` with Upshift share price, NAV and
            LP token supply.
        """

        call_by_name = {r.call.extra_data["function"]: r for r in call_results}
        denomination_token = self.vault.denomination_token
        share_token = self.vault.share_token

        share_price = None
        total_assets = None
        total_supply = None
        deposits_open = None
        redemption_open = None
        max_deposit = None
        # A request cap does not establish payout liquidity. Historical
        # liquidity stays unknown until a token balance is actually read.
        available_liquidity = None
        errors = []

        share_price_result = call_by_name.get("getSharePrice")
        if denomination_token is not None and share_price_result is not None and share_price_result.success:
            share_price = denomination_token.convert_to_decimals(convert_int256_bytes_to_int(share_price_result.result))
        else:
            errors.append("getSharePrice call failed")

        raw_total_assets = None
        total_assets_result = call_by_name.get("getTotalAssets")
        if denomination_token is not None and total_assets_result is not None and total_assets_result.success:
            raw_total_assets = convert_int256_bytes_to_int(total_assets_result.result)
            total_assets = denomination_token.convert_to_decimals(raw_total_assets)
        else:
            errors.append("getTotalAssets call failed")

        total_supply_result = call_by_name.get("lpTokenTotalSupply")
        if total_supply_result is not None and total_supply_result.success:
            total_supply = share_token.convert_to_decimals(convert_int256_bytes_to_int(total_supply_result.result))
        else:
            errors.append("lpTokenTotalSupply call failed")

        if total_assets == 0:
            errors.append(f"getTotalAssets returned zero: {total_assets_result}")

        if total_supply == 0:
            errors.append(f"lpTokenTotalSupply returned zero: {total_supply_result}")

        deposits_paused_result = call_by_name.get("depositsPaused")
        if deposits_paused_result is not None and deposits_paused_result.success:
            deposits_open = not bool(convert_int256_bytes_to_int(deposits_paused_result.result))

        withdrawals_paused_result = call_by_name.get("withdrawalsPaused")
        if withdrawals_paused_result is not None and withdrawals_paused_result.success:
            redemption_open = not bool(convert_int256_bytes_to_int(withdrawals_paused_result.result))

        max_deposit_result = call_by_name.get("maxDepositAmount")
        deposit_cap_result = call_by_name.get("depositCap")
        if denomination_token is not None and raw_total_assets is not None and max_deposit_result is not None and max_deposit_result.success and deposit_cap_result is not None and deposit_cap_result.success:
            raw_per_deposit_limit = convert_int256_bytes_to_int(max_deposit_result.result)
            raw_total_limit = convert_int256_bytes_to_int(deposit_cap_result.result)
            raw_remaining_capacity = max(raw_total_limit - raw_total_assets, 0)
            max_deposit = denomination_token.convert_to_decimals(min(raw_per_deposit_limit, raw_remaining_capacity))
            if deposits_open:
                deposits_open = max_deposit > 0

        if share_price_result is not None and share_price_result.state is not None and share_price is not None:
            share_price_result.state.on_called(
                share_price_result,
                total_assets=total_assets,
                share_price=share_price,
            )

        return VaultHistoricalRead(
            vault=self.vault,
            block_number=block_number,
            timestamp=timestamp,
            share_price=share_price,
            total_assets=total_assets,
            total_supply=total_supply,
            performance_fee=None,
            management_fee=None,
            errors=errors or None,
            max_deposit=max_deposit,
            deposits_open=deposits_open,
            redemption_open=redemption_open,
            available_liquidity=available_liquidity,
        )


class UpshiftVault(ERC4626Vault):
    """Upshift protocol vaults.

    Upshift hosts independently managed vaults built on August infrastructure.
    Account admission is read per deployment through the shared permission
    hooks; a permissionless strategy must exclude allow-listed vaults such as
    USPC even when their transaction manager is supported.

    The adapter supports two observed contract families:

    - TokenizedAccount ERC-4626 vaults, such as Upshift AZT.
    - Upshift ``multiAssetVault`` proxies, such as RockawayX's Tori Ecosystem
      Vault and Earn ctUSD vault. These expose accounting on the vault proxy
      and share-token metadata through ``lpTokenAddress()``.

    Links:

    - `Homepage <https://www.upshift.finance/>`__
    - `Documentation <https://docs.upshift.finance/>`__
    - `Example vault on Etherscan <https://etherscan.io/address/0x69fc3f84fd837217377d9dae0212068ceb65818e>`__
    - `Implementation contract on Etherscan <https://etherscan.io/address/0x83AF2736AD2f59BA60F2da1493DE95730Bc0649d#code>`__
    - `Multi-asset implementation on Etherscan <https://etherscan.io/address/0xEB5f80aCEa6060764E91c185bE93752Ab40F01c2#code>`__
    - `Tori Ecosystem Vault <https://etherscan.io/address/0xcd69123b3FBBfC666E1f6a501da27B564C00De54>`__
    - `Earn ctUSD <https://etherscan.io/address/0xc87DBBB8C67e4F19fCD2E297c05937567b2572Ce>`__
    - `Twitter <https://x.com/upshift_fi>`__

    Fee mechanism:

    Upshift vaults have multiple fee types that are configured per-vault and managed by the vault operator.
    The fee functions in the smart contract include:

    - ``withdrawalFee()``: Fee charged on standard withdrawals
    - ``instantRedemptionFee()``: Higher fee for immediate redemptions bypassing the claim queue
    - Management fees are charged periodically via ``chargeManagementFee()``

    See the `TokenizedAccount implementation <https://etherscan.io/address/0x83AF2736AD2f59BA60F2da1493DE95730Bc0649d#code>`__
    for the fee collection logic.
    """

    def get_strategy_tags(self) -> set[StrategyTag] | None:
        """Return the maintained strategy tags for this Upshift vault.

        The protocol hosts independently managed vaults, so classifications
        are maintained per vault address rather than applied to every Upshift
        deployment.

        :return:
            Copy of the tag set, or ``None`` when this vault has not yet been
            classified.
        """
        return lookup_strategy_tags(STRATEGY_TAGS, self.vault_address)

    @cached_property
    def upshift_metadata(self) -> UpshiftVaultMetadata | None:
        """Fetch cached public Upshift vault metadata.

        Upshift supplies descriptions, named strategists and named operator
        wallets through its `vault API <https://docs.upshift.finance/developer-docs/api-reference/vaults>`__.
        The API does not expose a curator field, so strategist identities must
        not be treated as verified curator records without an external mapping.

        :return:
            Parsed public metadata, or ``None`` when no API record is
            accessible for this vault.
        """

        return fetch_upshift_vault_metadata(self.web3, self.vault_address)

    def is_whitelisted_deposit(self) -> bool:
        """Report the actual account admission policy, independently of assets.

        A non-zero ``sendersWhitelistAddress()`` on a multi-asset deployment
        requires prior account approval and maps to ``whitelisted`` in scanner
        metadata. A zero address means no positive admission list. Supported
        legacy deployments use a blacklist, checked separately per account.
        Accepted tokens, pauses, capacity and adapter capability do not change
        this classification. Read failures propagate so the scanner reports
        ``unknown`` rather than inventing permissionless access.
        An explicit metadata block is honoured; unpinned execution checks use
        fresh state rather than the delayed, cached metadata head.

        Contract source: https://etherscan.io/address/0xEB5f80aCEa6060764E91c185bE93752Ab40F01c2#code

        :return: Whether the deployment requires positive account admission.
        """
        if not self.multi_asset_like:
            return False
        block = self.default_block_identifier if self.default_block_identifier is not None else get_fork_safe_latest_block(self.web3)
        return self.upshift_contract.functions.sendersWhitelistAddress().call(block_identifier=block).lower() != ZERO_ADDRESS_STR

    def is_account_whitelisted(self, address: HexAddress) -> bool:
        """Read sender membership or the legacy blacklist.

        Admission is checked for both funder and receiver by the manager.
        An unreadable policy propagates rather than assuming permission.

        :param address: Custody account to check.
        :return: Whether the account passes the configured policy.
        """
        block = self.default_block_identifier if self.default_block_identifier is not None else get_fork_safe_latest_block(self.web3)
        if not self.multi_asset_like:
            return not self.upshift_contract.functions.isBlacklisted(address).call(block_identifier=block)
        whitelist = self.upshift_contract.functions.sendersWhitelistAddress().call(block_identifier=block)
        if whitelist.lower() == ZERO_ADDRESS_STR:
            return True
        contract = get_deployed_contract(self.web3, "upshift/ISendersAllocationWhitelist.json", whitelist)
        return bool(contract.functions.isWhitelisted(address).call(block_identifier=block))

    @property
    def description(self) -> str | None:
        """Return the full Upshift-supplied vault strategy description.

        :return:
            Public Upshift description, or ``None`` when it is not available.
        """

        if self.upshift_metadata is None:
            return None
        return self.upshift_metadata["description"]

    @property
    def short_description(self) -> str | None:
        """Derive a one-sentence vault summary from Upshift's API description.

        Upshift does not expose a dedicated short-description field, so this
        follows the IPOR metadata convention and uses the first sentence of
        its long-form strategy description.

        :return:
            First sentence of the public Upshift description, or ``None`` when
            the API does not supply one.
        """
        description = self.description
        if not description:
            return None

        sentence_end = description.find(". ")
        if sentence_end >= 0:
            return description[: sentence_end + 1]

        return description.rstrip(".") + "."

    @property
    def manager_name(self) -> str | None:
        """Expose Upshift strategist brands through the generic manager field.

        :return:
            Comma-separated strategist display names, or ``None`` when the API
            does not publish any strategist names.
        """

        return self.fetch_strategist()

    def fetch_strategist(self) -> str | None:
        """Fetch the Upshift strategist identity from public metadata.

        Upshift calls these records ``hardcoded_strategists``. This is the
        closest available offchain manager identity, but it is not an explicit
        curator field. Named EOA operators are intentionally not used because
        they identify operational wallets rather than the strategy brand.

        :return:
            Comma-separated strategist display names, or ``None`` when the API
            does not publish any strategist names.
        """

        metadata = self.upshift_metadata
        if metadata is None:
            return None

        strategist_names = metadata["strategist_names"]
        return ", ".join(strategist_names) or None

    @cached_property
    def multi_asset_like(self) -> bool:
        """Is this an Upshift multi-asset vault.

        :return:
            True when the feature detector saw ``assetsWhitelistAddress()`` on
            the vault proxy.
        """

        return bool(self.features and ERC4626Feature.upshift_multi_asset_like in self.features)

    @cached_property
    def vault_contract(self) -> Contract:
        """Get the vault deployment.

        Multi-asset vaults need a dedicated ABI because their accounting
        methods are not part of the generic ERC-4626 ABI.

        :return:
            Web3 contract proxy for the vault address.
        """

        if self.multi_asset_like:
            return get_deployed_contract(
                self.web3,
                "upshift/MultiAssetVault.json",
                self.spec.vault_address,
            )

        return get_deployed_contract(
            self.web3,
            "upshift/TokenizedAccount.json",
            self.spec.vault_address,
        )

    @property
    def upshift_contract(self) -> Contract:
        """Alias for the Upshift implementation-specific vault contract."""

        return self.vault_contract

    @cached_property
    def assets_whitelist_contract(self) -> Contract:
        """Get the multi-asset accepted-deposit-token whitelist contract.

        This list describes deposit inputs, independently of the single
        withdrawal payout and accounting denomination returned by ``asset()``.

        :return:
            The configured whitelist contract.
        :raise ValueError:
            If this is not an Upshift multi-asset vault.
        """
        if not self.multi_asset_like:
            reason = "Only Upshift multi-asset vaults have an assets whitelist"
            raise ValueError(reason)
        address = self.upshift_contract.functions.assetsWhitelistAddress().call()
        return get_deployed_contract(
            self.web3,
            "upshift/EnableOnlyAssetsWhitelist.json",
            address,
        )

    def fetch_all_denomination_tokens(self) -> tuple[TokenDetails, ...]:
        """Fetch accepted deposit tokens, with the withdrawal payout token first.

        The inherited method name is retained for compatibility. Its tuple is
        a deposit-input list, not a choice of accounting denominations. It
        starts with the reference/payout asset, preserves extra whitelist
        ordering and removes duplicate addresses. Only
        :meth:`fetch_denomination_token` defines the denomination.

        :return:
            Accepted deposit tokens in protocol order. Single-asset Upshift
            vaults return only their denomination/payout token.
        :raise ValueError:
            If a configured token cannot be read as an ERC-20.
        """
        if not self.multi_asset_like:
            token = super().fetch_denomination_token()
            return (token,) if token is not None else ()

        reference = self.upshift_contract.functions.asset().call()
        extras = self.assets_whitelist_contract.functions.getWhitelistedAssets().call()
        addresses = tuple(dict.fromkeys(address.lower() for address in (reference, *extras)))
        tokens = []
        for address in addresses:
            token = fetch_erc20_details(
                self.web3,
                Web3.to_checksum_address(address),
                chain_id=self.spec.chain_id,
                raise_on_error=False,
                cause_diagnostics_message=f"Upshift vault {self.address} denomination token lookup",
                cache=self.token_cache,
            )
            if token is None:
                reason = f"Could not fetch Upshift denomination token {address} for vault {self.address}"
                raise ValueError(reason)
            tokens.append(token)
        return tuple(tokens)

    def fetch_denomination_token(self) -> TokenDetails | None:
        """Fetch the withdrawal payout token as Upshift's denomination.

        In both supported contract families, ``asset()`` identifies the token
        used for withdrawal payouts and share-price accounting. Multi-asset
        deposits can accept other tokens, but they never replace this
        denomination. Axis is denominated in USDx, Tori Ecosystem in trUSD and
        earnAUSD in AUSD, even when they accept USDC deposits. Use this token's
        address and decimals for payout analysis and valuation; an input-token
        choice does not implement conversion back to a strategy's reserve.

        Accepted inputs are exposed through :meth:`fetch_all_denomination_tokens`.
        Verified payout implementation: https://etherscan.io/address/0xEB5f80aCEa6060764E91c185bE93752Ab40F01c2#code

        :return:
            Withdrawal payout/accounting token configured by ``asset()``, or
            ``None`` when ``asset()`` or its token metadata cannot be read.
        """
        return super().fetch_denomination_token()

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Get the share token address.

        Upshift multi-asset vault proxies keep ERC-20 share metadata on a
        separate LP token returned by ``lpTokenAddress()``. TokenizedAccount
        vaults remain standard ERC-4626 share-token contracts.

        :param block_identifier:
            Block number or ``"latest"``.

        :return:
            Share token address.
        """

        if self.multi_asset_like:
            return self.upshift_contract.functions.lpTokenAddress().call(block_identifier=block_identifier)

        return super().fetch_share_token_address(block_identifier)

    def fetch_total_supply(self, block_identifier: BlockIdentifier) -> Decimal:
        """Fetch current outstanding share supply.

        For Upshift multi-asset vaults, :py:meth:`fetch_share_token_address`
        remaps the share token to the LP token returned by ``lpTokenAddress()``.
        Keeping this method explicit documents that all inherited callers of
        ``fetch_total_supply()`` use LP token supply, not the vault proxy address.

        :param block_identifier:
            Block number or ``"latest"``.

        :return:
            LP token supply in share token units.
        """

        return super().fetch_total_supply(block_identifier)

    def has_custom_fees(self) -> bool:
        """Upshift has withdrawal and instant redemption fees."""
        return True

    def get_fee_mode(self) -> VaultFeeMode | None:
        """Return the fee-accounting model when the vault exposes it.

        Multi-asset vaults accrue management and performance fees against vault
        assets, making the share price net of those fees.  TokenizedAccount
        vaults do not expose those configuration values, so their overall fee
        mode remains unknown.

        :return:
            ``internalised_skimming`` for multi-asset vaults, otherwise
            ``None``.
        """
        if self.multi_asset_like:
            return VaultFeeMode.internalised_skimming
        return None

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get management fee.

        Multi-asset vaults expose ``managementFeePercent()`` in basis points.
        TokenizedAccount contracts do not expose an equivalent fee getter, so
        their management fee remains unknown rather than being treated as zero.

        :param block_identifier:
            Block number or ``"latest"``.

        :return:
            Decimal management-fee fraction, or ``None`` when unavailable.
        """
        if not self.multi_asset_like:
            return None

        raw_bps = self.upshift_contract.functions.managementFeePercent().call(block_identifier=block_identifier)
        return raw_bps / UPSHIFT_BASIS_POINTS_DENOMINATOR

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get performance fee.

        Multi-asset vaults expose ``performanceFeeRate()`` in parts per
        million.
        TokenizedAccount contracts do not expose an equivalent fee getter, so
        their performance fee remains unknown rather than being treated as zero.

        :param block_identifier:
            Block number or ``"latest"``.

        :return:
            Decimal performance-fee fraction, or ``None`` when unavailable.
        """
        if not self.multi_asset_like:
            return None

        raw_ppm = self.upshift_contract.functions.performanceFeeRate().call(block_identifier=block_identifier)
        return raw_ppm / UPSHIFT_PERFORMANCE_FEE_DENOMINATOR

    def get_withdraw_fee(self, block_identifier: BlockIdentifier) -> float:
        """Read the standard queued-redemption fee.

        ``withdrawalFee()`` excludes the separate fee for the optional instant
        redemption path.  That additional fee remains represented by
        :py:meth:`has_custom_fees`.

        :param block_identifier:
            Block number or ``"latest"`` at which to read the configuration.

        :return:
            Decimal withdrawal-fee fraction, e.g. ``0.002`` for 20 bps.
        """
        raw_bps = self.upshift_contract.functions.withdrawalFee().call(block_identifier=block_identifier)
        return raw_bps / UPSHIFT_BASIS_POINTS_DENOMINATOR

    def fetch_instant_redemption_fee(self, block_identifier: BlockIdentifier) -> float:
        """Read the extra instant-redemption fee outside the shared fee schema.

        Upshift supports a queued redemption flow and an optional immediate
        redemption.  The latter fee cannot be substituted for the standard
        withdrawal fee because it applies only when a user chooses that path.

        :param block_identifier:
            Block number or ``"latest"`` at which to read the configuration.

        :return:
            Decimal instant-redemption fee fraction, e.g. ``0.002`` for 20 bps.
        """
        raw_bps = self.upshift_contract.functions.instantRedemptionFee().call(block_identifier=block_identifier)
        return raw_bps / UPSHIFT_BASIS_POINTS_DENOMINATOR

    def fetch_total_assets(self, block_identifier: BlockIdentifier) -> Decimal | None:
        """Fetch vault NAV in denomination token units.

        Multi-asset vaults use ``getTotalAssets()`` instead of the ERC-4626
        ``totalAssets()`` function.

        :param block_identifier:
            Block number or ``"latest"``.

        :return:
            NAV in denomination token units, or ``None`` if the denomination
            token is unavailable.
        """

        if self.multi_asset_like:
            raw_amount = self.upshift_contract.functions.getTotalAssets().call(block_identifier=block_identifier)
            if self.underlying_token is not None:
                return self.underlying_token.convert_to_decimals(raw_amount)
            return None

        return super().fetch_total_assets(block_identifier)

    def fetch_share_price(self, block_identifier: BlockIdentifier) -> Decimal:
        """Fetch the current share price.

        Multi-asset vaults expose the canonical price through
        ``getSharePrice()``.

        :param block_identifier:
            Block number or ``"latest"``.

        :return:
            Share price in denomination token units.
        """

        if self.multi_asset_like:
            token = self.denomination_token
            raw_amount = self.upshift_contract.functions.getSharePrice().call(block_identifier=block_identifier)
            return token.convert_to_decimals(raw_amount)

        return super().fetch_share_price(block_identifier)

    def fetch_nav(self, block_identifier=None) -> Decimal | None:
        """Fetch the most recent onchain NAV value.

        :param block_identifier:
            Block number or ``"latest"``.

        :return:
            NAV in denomination token units.
        """

        if self.multi_asset_like:
            return self.fetch_total_assets(block_identifier)

        return super().fetch_nav(block_identifier)

    def get_deposit_manager(self) -> UpshiftMultiAssetDepositManager:
        """Get deposit/redeem manager.

        The generic ERC-4626 deposit manager calls ERC-4626 deposit/redeem
        functions on the vault address. Upshift multi-asset vaults are proxy
        accounting contracts and use protocol-specific deposit flows through the
        Upshift app, so exposing the generic manager would be misleading.
        """

        if self.multi_asset_like:
            return UpshiftMultiAssetDepositManager(self)

        return UpshiftDepositManager(self)

    def get_deposit_manager_capability(self) -> VaultDepositManagerCapability:
        """Declare Upshift's fork-proven directional lifecycle support.

        Multi-asset accounting vaults support synchronous deposits through
        Upshift's protocol-specific ``deposit(asset, amount, receiver)`` entry
        point. Redemptions use the verified queued
        ``requestRedeem/processAllClaimsByDate/claim`` lifecycle; the manager
        also exposes Upshift's optional atomic ``instantRedeem`` call.

        .. note::

            Trade-executor must inspect the matching directional flag and use
            ``deposit_assets`` to require an explicit accepted-asset selection.

        :return:
            Directional capability and accepted assets for the vault shape.
        """
        asynchronous = not self.get_deposit_manager().has_synchronous_redemption()
        return VaultDepositManagerCapability(
            can_deposit=True,
            can_redeem=True,
            deposit_flow="synchronous",
            redemption_flow="asynchronous" if asynchronous else "synchronous",
            supports_anvil_settlement=True if asynchronous else None,
            deposit_assets=tuple(token.address for token in self.fetch_all_denomination_tokens()) if self.multi_asset_like else (),
            publish_partial=self.multi_asset_like,
        )

    def can_check_deposit(self) -> bool:
        """Can the generic ERC-4626 ``maxDeposit(address(0))`` probe be used."""

        if self.multi_asset_like:
            return False

        return super().can_check_deposit()

    def fetch_deposit_cap(self, block_identifier: BlockIdentifier = "latest") -> int | None:
        """Read the global cap, recognising the one verified uncapped implementation.

        Wildcat's older verified TokenizedVault has only a per-deposit limit.
        A failed getter on any other implementation remains a hard error; it
        must never turn an unreadable limit into unlimited capacity.

        :param block_identifier: Block used for both getter and implementation.
        :return: Raw reference-asset global cap, or verified absence of a cap.
        """
        try:
            return int(self.upshift_contract.functions.depositCap().call(block_identifier=block_identifier))
        except (ContractLogicError, BadFunctionCallOutput, ExtraValueError):
            slot = int.from_bytes(Web3.keccak(text="eip1967.proxy.implementation"), "big") - 1
            value = self.web3.eth.get_storage_at(self.address, slot, block_identifier=block_identifier)
            implementation = "0x" + bytes(value[-20:]).hex()
            if implementation != "0xaacc687cea8cd706bd7a56be0f7ad85d420767dc":
                raise
            return None

    def fetch_deposit_closed_reason(self) -> str | None:
        """Fetch live deposit closure reason.

        Upshift multi-asset vaults expose deposit availability with
        ``depositsPaused()`` and ``maxDepositAmount()`` instead of ERC-4626
        ``maxDeposit(address)``.
        """

        if not self.multi_asset_like:
            return super().fetch_deposit_closed_reason()

        if self.upshift_contract.functions.depositsPaused().call():
            return "Upshift depositsPaused() is true"

        functions = self.upshift_contract.functions
        raw_max_deposit = functions.maxDepositAmount().call()
        if raw_max_deposit == 0:
            return "Upshift maxDepositAmount() is zero"

        raw_deposit_cap = self.fetch_deposit_cap()
        raw_total_assets = functions.getTotalAssets().call()
        if raw_deposit_cap is not None and raw_total_assets >= raw_deposit_cap:
            return "Upshift depositCap() has been reached"

        return None

    def fetch_redemption_closed_reason(self) -> str | None:
        """Fetch live redemption closure reason.

        Upshift multi-asset vaults expose redemption availability with
        ``withdrawalsPaused()`` and ``maxWithdrawalAmount()`` instead of
        ERC-4626 ``maxRedeem(address)``.
        """

        if self.upshift_contract.functions.withdrawalsPaused().call():
            return "Upshift withdrawalsPaused() is true"

        raw_max_withdrawal = self.upshift_contract.functions.maxWithdrawalAmount().call()
        if raw_max_withdrawal == 0:
            return "Upshift maxWithdrawalAmount() is zero"

        return None

    def fetch_available_liquidity(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Fetch immediately available withdrawal liquidity.

        Only a zero-lag deployment without queued obligations has known
        immediate liquidity. Queued defaults report unknown instead of a gross
        token balance or configured cap. Managers check a particular mature
        ticket against the actual token balance separately.

        :param block_identifier:
            Block number or ``"latest"``.

        :return:
            Available withdrawal liquidity in denomination token units.
        """

        token = self.denomination_token
        if token is None:
            return None
        functions = self.upshift_contract.functions
        if int(functions.lagDuration().call(block_identifier=block_identifier)) > 0 or int(functions.globalLiabilityShares().call(block_identifier=block_identifier)) > 0:
            # A token balance is not immediately redeemable liquidity while
            # withdrawals are queued or competing obligations remain.
            return None
        return token.fetch_balance_of(self.address, block_identifier=block_identifier)

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:  # noqa: FBT001
        """Get the historical reader for this Upshift vault.

        :param stateful:
            Whether to use adaptive reader state.

        :return:
            Upshift multi-asset reader for multi-asset vaults, otherwise the
            generic ERC-4626 reader.
        """

        if self.multi_asset_like:
            return UpshiftMultiAssetHistoricalReader(self, stateful=stateful)

        return super().get_historical_reader(stateful)

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Return Upshift's configured request-to-claim delay."""
        withdrawal_period = self.get_withdrawal_period()
        return withdrawal_period.min_period if withdrawal_period else None

    def get_withdrawal_period(self) -> WithdrawalPeriod | None:
        """Read Upshift's configured redemption delay from ``lagDuration()``.

        ``lagDuration`` is the onchain request-to-claim timelock in seconds;
        it is configurable per vault and must not be assumed to be one day.
        The optional instant-redemption path has a separate fee and therefore
        does not alter the standard request-and-claim timing exported here.

        :return:
            Fixed configured delay for standard Upshift withdrawals, or
            ``None`` for an older deployment that lacks ``lagDuration()``.
        """
        try:
            lag_duration = self.upshift_contract.functions.lagDuration().call()
        except (BadFunctionCallOutput, ContractLogicError):
            logger.debug("Upshift vault %s does not expose lagDuration()", self.address)
            return None
        period = datetime.timedelta(seconds=lag_duration)
        return WithdrawalPeriod(
            min_period=period,
            max_period=period,
            delay_type=WithdrawalDelayType.delay,
        )

    def get_link(self, referral: str | None = None) -> str:
        """Get link to the vault on Upshift app.

        URL format: https://app.upshift.finance/pools/{chain_id}/{checksummed_address}
        """
        del referral
        chain_id = self.chain_id
        checksummed_address = Web3.to_checksum_address(self.vault_address)
        return f"https://app.upshift.finance/pools/{chain_id}/{checksummed_address}"
