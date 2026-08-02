"""Accountable Capital vault support."""

import datetime
import enum
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from functools import cached_property

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

from eth_defi.abi import get_deployed_contract
from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626HistoricalReader, ERC4626Vault
from eth_defi.erc_4626.vault_protocol.accountable.deposit_redeem import (
    ACCOUNTABLE_ANVIL_SETTLEMENT_UNSUPPORTED_REASON,
    AccountableDepositManager,
)
from eth_defi.erc_4626.vault_protocol.accountable.offchain_metadata import (
    AccountableVaultMetadata,
    fetch_accountable_vault_metadata,
)
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.provider.fallback import ExtraValueError
from eth_defi.types import Percent
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader
from eth_defi.vault.deposit_redeem import VaultDepositManagerCapability
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.handwritten_metadata import get_handwritten_vault_metadata

logger = logging.getLogger(__name__)


class AccountablePermissionLevel(enum.IntEnum):
    """Accountable vault access modes from ``IAccess.PermissionLevel``."""

    #: No account admission check.
    none = 0

    #: Accountable-signed authorisation appended to each call.
    kyc = 1

    #: Persistent membership exposed through ``allowed(address)``.
    whitelist = 2


@dataclass(slots=True, frozen=True)
class AccountableFeeData:
    """Snapshot all Accountable fee-manager terms for one vault.

    Accountable has two deployed fee-manager interfaces. The legacy interface
    supports establishment and performance fees; the current interface also
    supports an annualised management fee and separate manager/protocol splits
    for management and performance charges. All percentages use the manager's
    runtime ``BASIS_POINTS()`` denominator, which is currently one million.

    Establishment and prepayment fees are loan terms paid by the borrower.
    They are included here for a complete native view, but are not ERC-4626 LP
    deposit or withdrawal fees. ``minimum_deposit`` is decimalised using the
    vault denomination token while ``minimum_deposit_raw`` preserves the
    effective maximum of the vault and strategy contract thresholds.

    - Legacy fee manager: https://monadscan.com/address/0x4DE9B4d7b70d1680cD8E3A2C60717cBbe6014991#code
    - Current fee-manager implementation: https://monadscan.com/address/0x13f12a4F960FaEC311dB695C6Bb891ce28d668aE#code
    """

    #: Block tag or number shared by every read in this snapshot.
    block_identifier: BlockIdentifier

    #: ERC-4626 vault whose fee terms were read.
    vault_address: HexAddress

    #: Accountable loan/strategy configured by the vault.
    strategy_address: HexAddress

    #: Fee-manager contract configured by the strategy.
    fee_manager_address: HexAddress

    #: Address receiving the protocol share of collected fees.
    treasury_address: HexAddress

    #: Address receiving the manager share of collected fees.
    manager_fee_recipient_address: HexAddress

    #: Runtime percentage denominator returned by ``BASIS_POINTS()``.
    basis_points: int

    #: Whether the deployed fee manager supports annual management fees.
    supports_management_fee: bool

    #: Borrower establishment fee in Accountable percentage units.
    establishment_fee_raw: int

    #: Annualised management fee in Accountable percentage units.
    #:
    #: Legacy fee managers structurally have no management fee, represented as
    #: zero together with ``supports_management_fee=False``.
    management_fee_raw: int

    #: Performance fee in Accountable percentage units.
    performance_fee_raw: int

    #: Manager share of the performance fee in Accountable percentage units.
    manager_performance_fee_split_raw: int

    #: Protocol share of the performance fee in Accountable percentage units.
    protocol_performance_fee_split_raw: int

    #: Manager share of the management fee, or ``None`` on the legacy ABI.
    manager_management_fee_split_raw: int | None

    #: Protocol share of the management fee, or ``None`` on the legacy ABI.
    protocol_management_fee_split_raw: int | None

    #: Borrower prepayment fee in Accountable percentage units.
    prepayment_fee_raw: int

    #: Vault-level dust threshold returned by ``MIN_AMOUNT_WEI()``, if exposed.
    vault_minimum_deposit_raw: int | None

    #: Strategy-level configured ``loan.minDeposit``, if exposed.
    strategy_minimum_deposit_raw: int | None

    #: Effective ERC-20 base-unit minimum accepted by ``deposit()``.
    #:
    #: This is the maximum of the vault and strategy thresholds when present.
    minimum_deposit_raw: int | None

    #: Human-readable minimum deposit in denomination-token units, if exposed.
    minimum_deposit: Decimal | None

    def _normalise(self, raw_value: int) -> Percent:
        """Convert an Accountable percentage integer to a fractional value.

        :param raw_value:
            Percentage encoded using :attr:`basis_points`.

        :return:
            Fractional percentage, such as ``0.20`` for a 20% fee.

        :raise ValueError:
            If the fee manager reports a non-positive denominator.
        """
        if self.basis_points <= 0:
            raise ValueError(f"Accountable fee denominator must be positive, got {self.basis_points}")
        return raw_value / self.basis_points

    @property
    def establishment_fee(self) -> Percent:
        """Return the borrower establishment fee as a fraction."""
        return self._normalise(self.establishment_fee_raw)

    @property
    def management_fee(self) -> Percent:
        """Return the annualised management fee as a fraction."""
        return self._normalise(self.management_fee_raw)

    @property
    def performance_fee(self) -> Percent:
        """Return the performance fee as a fraction."""
        return self._normalise(self.performance_fee_raw)

    @property
    def manager_performance_fee_split(self) -> Percent:
        """Return the manager's share of performance fees as a fraction."""
        return self._normalise(self.manager_performance_fee_split_raw)

    @property
    def protocol_performance_fee_split(self) -> Percent:
        """Return the protocol's share of performance fees as a fraction."""
        return self._normalise(self.protocol_performance_fee_split_raw)

    @property
    def manager_management_fee_split(self) -> Percent | None:
        """Return the manager's share of management fees when supported."""
        if self.manager_management_fee_split_raw is None:
            return None
        return self._normalise(self.manager_management_fee_split_raw)

    @property
    def protocol_management_fee_split(self) -> Percent | None:
        """Return the protocol's share of management fees when supported."""
        if self.protocol_management_fee_split_raw is None:
            return None
        return self._normalise(self.protocol_management_fee_split_raw)

    @property
    def prepayment_fee(self) -> Percent:
        """Return the borrower prepayment fee as a fraction."""
        return self._normalise(self.prepayment_fee_raw)

    def as_generic_fee_data(self) -> FeeData:
        """Map investor-facing Accountable fees to the shared fee schema.

        Accountable deducts management and performance charges before updating
        the value backing vault shares, so both are internalised skimming fees.
        Establishment and prepayment charges apply to the underlying borrower,
        not an LP entering or leaving the ERC-4626 vault. Generic deposit and
        withdrawal fees are therefore known to be zero.

        :return:
            Shared fee data suitable for vault metadata and comparisons.
        """
        return FeeData(
            fee_mode=VaultFeeMode.internalised_skimming,
            management=self.management_fee,
            performance=self.performance_fee,
            deposit=0.0,
            withdraw=0.0,
        )


class AccountableHistoricalReader(ERC4626HistoricalReader):
    """Read Accountable vault core data with corrected NAV and available liquidity.

    Accountable's ``totalAssets()`` only returns idle liquidity in the vault contract,
    excluding capital deployed by the strategy via ``lockAssets()``. This means the
    standard ERC-4626 ``totalAssets()`` severely underreports the true vault NAV.

    This reader:

    - Computes the true NAV as ``share_price * total_supply``
      (derived from ``convertToAssets`` which uses ``sharePrice()``)
    - Exposes the raw ``totalAssets()`` value as ``available_liquidity``
      since it represents the idle capital available for immediate withdrawals
    """

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        yield from self.construct_core_erc_4626_multicall()

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        call_by_name = self.dictify_multicall_results(block_number, call_results)

        # Decode common variables (share_price, total_supply, total_assets from totalAssets(), errors, max_deposit)
        share_price, total_supply, idle_assets, errors, max_deposit = self.process_core_erc_4626_result(call_by_name)

        # idle_assets is the raw totalAssets() value = idle liquidity available for withdrawal
        available_liquidity = idle_assets

        # Override total_assets with the true NAV: share_price * total_supply
        # because totalAssets() only returns idle liquidity, not deployed capital.
        total_assets = idle_assets
        if share_price is not None and total_supply is not None and total_supply > 0:
            total_assets = share_price * total_supply

        # Fix VaultReaderState that was updated with the raw totalAssets() (idle capital only)
        # inside process_core_erc_4626_result(). The state uses TVL for adaptive polling frequency
        # and peaked/faded detection, so it must reflect the true NAV.
        convert_to_assets_result = call_by_name.get("convertToAssets")
        if convert_to_assets_result is not None and convert_to_assets_result.state is not None:
            convert_to_assets_result.state.on_called(
                convert_to_assets_result,
                total_assets=total_assets,
                share_price=share_price,
            )

        # Utilisation = deployed capital / true NAV
        utilisation = None
        if total_assets is not None and available_liquidity is not None and total_assets > 0:
            utilisation = float((total_assets - available_liquidity) / total_assets)

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
            available_liquidity=available_liquidity,
            utilisation=utilisation,
        )


class AccountableVault(ERC4626Vault):  # noqa: PLR0904
    """Accountable Capital vault support.

    Accountable Capital develops blockchain-based financial verification technology
    that enables organisations and investors to demonstrate solvency, liquidity,
    and compliance through transparent, verifiable attestations. The platform
    combines cryptographic proofs with auditable financial data to enhance trust
    across Web3 and traditional finance.

    Accountable vaults implement ERC-7540 async redemption pattern with a queue
    system for processing withdrawal requests.

    NAV calculation
    ~~~~~~~~~~~~~~~

    Accountable's ``totalAssets()`` only returns the **idle liquidity** held by the vault
    contract. When the strategy deploys capital via ``lockAssets()``, those assets are
    subtracted from ``totalAssets()``. This means ``totalAssets()`` severely underreports
    the true vault value.

    The true NAV is computed as ``convertToAssets(totalSupply())``, which uses
    ``sharePrice()`` — the oracle/strategy-set price that reflects all capital
    including deployed positions. Both :py:meth:`fetch_total_assets` and
    :py:class:`AccountableHistoricalReader` use this corrected calculation.

    The raw ``totalAssets()`` value is exposed via :py:meth:`fetch_idle_capital`
    and :py:meth:`fetch_available_liquidity` as it represents capital available
    for immediate withdrawals.

    Key contract functions for NAV:

    - ``sharePrice()`` — current price per share (reflects deployed capital)
    - ``totalSupply()`` — total shares outstanding
    - ``convertToAssets(shares)`` — converts shares to assets using share price
    - ``totalAssets()`` — idle liquidity only (excludes deployed capital)
    - ``lockAssets(assets, sender)`` — strategy deploys capital (reduces ``totalAssets``)
    - ``releaseAssets(assets, receiver)`` — strategy returns capital (increases ``totalAssets``)
    - ``reservedLiquidity()`` — assets reserved for pending redemptions

    - Homepage: https://www.accountable.capital/
    - Twitter: https://x.com/AccountableData
    - No public GitHub repository available for smart contracts
    - Example contract: https://monadscan.com/address/0x58ba69b289De313E66A13B7D1F822Fc98b970554
    """

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment with Accountable-specific ABI."""
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="accountable/AccountableAsyncRedeemVault.json",
        )

    def get_historical_reader(self, stateful) -> VaultHistoricalReader:
        return AccountableHistoricalReader(self, stateful=stateful)

    def fetch_idle_capital(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Fetch idle capital held by the vault contract.

        This is the raw ``totalAssets()`` value — assets sitting in the vault
        that have not been deployed by the strategy via ``lockAssets()``.
        This is the capital available for immediate withdrawals.

        :param block_identifier:
            Block number to read.

        :return:
            Idle capital in underlying token, or None if denomination token is unavailable.
        """
        if self.underlying_token is None:
            return None

        raw_amount = self.vault_contract.functions.totalAssets().call(block_identifier=block_identifier)
        return self.underlying_token.convert_to_decimals(raw_amount)

    def fetch_total_assets(self, block_identifier: BlockIdentifier) -> Decimal | None:
        """Fetch the true vault NAV including deployed capital.

        Accountable's ``totalAssets()`` only returns idle liquidity.
        We compute the true NAV as ``convertToAssets(totalSupply())``,
        which uses the strategy-set ``sharePrice()`` to account for
        all capital including deployed positions.

        :param block_identifier:
            Block number to read.

        :return:
            The vault NAV in underlying token, or None if denomination token is unavailable.
        """
        if self.underlying_token is None:
            return None

        raw_total_supply = self.vault_contract.functions.totalSupply().call(block_identifier=block_identifier)
        if raw_total_supply == 0:
            return Decimal(0)

        raw_nav = self.vault_contract.functions.convertToAssets(raw_total_supply).call(block_identifier=block_identifier)
        return self.underlying_token.convert_to_decimals(raw_nav)

    def fetch_nav(self, block_identifier=None) -> Decimal:
        """Fetch the most recent onchain NAV value.

        Uses ``convertToAssets(totalSupply())`` instead of ``totalAssets()``
        because Accountable's ``totalAssets()`` excludes deployed capital.

        :return:
            Vault NAV, denominated in :py:meth:`denomination_token`
        """
        token = self.denomination_token
        raw_total_supply = self.vault_contract.functions.totalSupply().call(block_identifier=block_identifier)
        if raw_total_supply == 0:
            return Decimal(0)
        raw_nav = self.vault_contract.functions.convertToAssets(raw_total_supply).call(block_identifier=block_identifier)
        return token.convert_to_decimals(raw_nav)

    def fetch_available_liquidity(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Get the amount of denomination token available for immediate withdrawal.

        For Accountable vaults, this is ``totalAssets()`` which returns only idle
        capital not deployed by the strategy.

        :param block_identifier:
            Block to query. Defaults to "latest".

        :return:
            Amount in denomination token units (human-readable Decimal).
        """
        return self.fetch_idle_capital(block_identifier)

    def fetch_utilisation_percent(self, block_identifier: BlockIdentifier = "latest") -> Percent | None:
        """Get the percentage of assets currently deployed by the strategy.

        Utilisation = (true NAV - idle capital) / true NAV

        :param block_identifier:
            Block to query. Defaults to "latest".

        :return:
            Utilisation as float between 0.0 and 1.0 (0% to 100%).
        """
        nav = self.fetch_total_assets(block_identifier)
        idle = self.fetch_idle_capital(block_identifier)
        if nav is None or idle is None or nav == 0:
            return None
        return float((nav - idle) / nav)

    def get_deposit_manager(self) -> AccountableDepositManager:
        """Create Accountable's synchronous-deposit async-redeem manager.

        :return:
            Protocol-specific request and claim manager.
        """
        return AccountableDepositManager(self)

    def fetch_permission_level(self, block_identifier: BlockIdentifier | None = None) -> AccountablePermissionLevel:
        """Read Accountable's explicit vault-wide admission mode.

        Verified Accountable source defines ``None = 0``, ``KYC = 1`` and
        ``Whitelist = 2``. The vault's ``onlyAuth`` modifier permits every
        account in mode zero, requires a signed per-call authorisation in KYC
        mode, and reads ``allowed(account)`` in whitelist mode.

        :param block_identifier:
            Block at which to inspect the configured permission mode.
        :return:
            Typed Accountable permission level.
        :raise NotImplementedError:
            If a future deployment returns an unknown enum value.
        """
        if block_identifier is None:
            block_identifier = self._get_block_identifier()
        raw_level = int(self.vault_contract.functions.permissionLevel().call(block_identifier=block_identifier))
        try:
            return AccountablePermissionLevel(raw_level)
        except ValueError as error:
            raise NotImplementedError(f"Unknown Accountable permission level {raw_level}") from error

    def is_whitelisted_deposit(self) -> bool:
        """Report whether Accountable requires identity-based admission.

        Minimum deposits, strategy capacity, loan state, and redemption queues
        are independent lifecycle conditions and do not affect this result.

        :return:
            ``False`` for Accountable's ``None`` mode and ``True`` for KYC or
            explicit whitelist modes.
        """
        return self.fetch_permission_level() is not AccountablePermissionLevel.none

    def is_account_whitelisted(
        self,
        address: HexAddress,
        permission_level: AccountablePermissionLevel | None = None,
    ) -> bool:
        """Check whether a bare account can use the configured admission mode.

        Whitelist mode has persistent membership through ``allowed(address)``.
        KYC mode instead requires Accountable-signed authorisation appended to
        each call; the standard ERC-4626 transaction builder supplies no such
        payload, so an address-only request is not admitted.

        :param address:
            Deposit controller or receiver to inspect.
        :param permission_level:
            Previously read permission mode. Supplying it avoids a duplicate
            onchain read when several accounts are checked for one call.
        :return:
            Whether the standard adapter call is admitted for this account.
        """
        if permission_level is None:
            permission_level = self.fetch_permission_level()
        if permission_level is AccountablePermissionLevel.none:
            return True
        if permission_level is AccountablePermissionLevel.whitelist:
            return bool(
                self.vault_contract.functions.allowed(address).call(
                    block_identifier=self._get_block_identifier(),
                )
            )
        return False

    def get_deposit_manager_capability(self) -> VaultDepositManagerCapability:  # noqa: PLR6301
        """Declare Accountable's public request lifecycle.

        :return:
            Synchronous deposit and asynchronous redemption capability.
        """
        return VaultDepositManagerCapability(
            can_deposit=True,
            can_redeem=True,
            deposit_flow="synchronous",
            redemption_flow="asynchronous",
            supports_anvil_settlement=False,
            anvil_settlement_unsupported_reason=ACCOUNTABLE_ANVIL_SETTLEMENT_UNSUPPORTED_REASON,
        )

    @cached_property
    def accountable_metadata(self) -> AccountableVaultMetadata | None:
        """Offchain metadata from Accountable's yield app API.

        Fetched from ``yield.accountable.capital/api/loan``.
        Cached on disk and in-process to avoid repeated API calls.
        """
        return fetch_accountable_vault_metadata(self.web3, self.spec.vault_address)

    @property
    def description(self) -> str | None:
        """Full vault strategy description from Accountable's offchain metadata."""

        metadata = get_handwritten_vault_metadata(self.chain_id, self.address)
        if metadata:
            return metadata.description

        if self.accountable_metadata:
            return self.accountable_metadata.get("description")
        return None

    @property
    def short_description(self) -> str | None:
        """First sentence of the vault strategy from Accountable's offchain metadata."""

        metadata = get_handwritten_vault_metadata(self.chain_id, self.address)
        if metadata:
            return metadata.short_description

        if self.accountable_metadata:
            return self.accountable_metadata.get("short_description")
        return None

    @property
    def manager_name(self) -> str | None:
        """Curator company name from Accountable's public vault API.

        Accountable separates the ERC-4626 share-token name from the strategy
        manager.  Its ``company_name`` metadata therefore provides the
        canonical curator identity for the generic vault scan and export.

        :return:
            Accountable's manager display name, or ``None`` when the vault is
            not present in the public metadata API.
        """
        metadata = self.accountable_metadata
        if metadata:
            return metadata.get("company_name")
        return None

    def _fetch_vault_minimum_raw(self, block_identifier: BlockIdentifier) -> int | None:
        """Fetch Accountable's context-sensitive vault-level dust threshold.

        The verified vault compares ``MIN_AMOUNT_WEI()`` directly with raw
        denomination assets in its deposit path and directly with raw shares in
        its ``requestRedeem`` path. The same raw scalar is therefore exposed
        through the shared API in the context of the respective caller; it is
        never converted between assets and shares.

        :param block_identifier:
            Block tag or number at which to read the minimum.

        :return:
            Raw threshold, or ``None`` when the getter is unsupported.
        """
        try:
            return int(self.vault_contract.functions.MIN_AMOUNT_WEI().call(block_identifier=block_identifier))
        except (BadFunctionCallOutput, ContractLogicError, ExtraValueError):
            return None

    def _fetch_strategy_contract(self, block_identifier: BlockIdentifier) -> tuple[HexAddress, Contract]:
        """Fetch the configured strategy address and bind its read ABI.

        :param block_identifier:
            Block tag or number at which to resolve the strategy.

        :return:
            Checksum strategy address and its Accountable contract proxy.
        """
        strategy_address = Web3.to_checksum_address(self.vault_contract.functions.strategy().call(block_identifier=block_identifier))
        strategy_contract = get_deployed_contract(
            self.web3,
            "accountable/AccountableStrategy.json",
            strategy_address,
            register_for_tracing=False,
        )
        return strategy_address, strategy_contract

    @staticmethod
    def _fetch_strategy_minimum_raw_deposit(strategy_contract: Contract, block_identifier: BlockIdentifier) -> int | None:
        """Fetch ``loan.minDeposit`` from an Accountable strategy.

        :param strategy_contract:
            Accountable strategy bound to the read ABI.
        :param block_identifier:
            Block tag or number at which to read the minimum.

        :return:
            Strategy-configured base units, or ``None`` when unsupported.
        """
        try:
            loan = strategy_contract.functions.loan().call(block_identifier=block_identifier)
            return int(loan[0])
        except (BadFunctionCallOutput, ContractLogicError, ExtraValueError):
            return None

    @staticmethod
    def _effective_minimum_raw_deposit(vault_minimum: int | None, strategy_minimum: int | None) -> int | None:
        """Combine vault and strategy deposit thresholds.

        Both checks can reject the same ERC-4626 deposit, so the effective
        minimum is the greatest available value.

        :param vault_minimum:
            Vault-level dust threshold.
        :param strategy_minimum:
            Strategy loan-term threshold.

        :return:
            Greatest configured threshold, or ``None`` when neither exists.
        """
        available_minimums = tuple(value for value in (vault_minimum, strategy_minimum) if value is not None)
        return max(available_minimums) if available_minimums else None

    def fetch_minimum_raw_deposit(self, block_identifier: BlockIdentifier = "latest") -> int | None:
        """Fetch the effective minimum deposit in ERC-20 base units.

        Accountable checks both the vault's ``MIN_AMOUNT_WEI()`` threshold and
        the strategy's configured ``loan.minDeposit``. The larger value is the
        amount an LP must satisfy.

        :param block_identifier:
            Block tag or number shared by both reads.

        :return:
            Effective denomination-token base-unit minimum, or ``None`` when
            neither getter is supported.
        """
        _, strategy_contract = self._fetch_strategy_contract(block_identifier)
        vault_minimum = self._fetch_vault_minimum_raw(block_identifier)
        strategy_minimum = self._fetch_strategy_minimum_raw_deposit(strategy_contract, block_identifier)
        return self._effective_minimum_raw_deposit(vault_minimum, strategy_minimum)

    def fetch_minimum_deposit(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Fetch the contract-enforced minimum deposit in token units.

        The effective maximum of ``MIN_AMOUNT_WEI()`` and
        ``loan.minDeposit`` is decimalised with the vault's denomination
        token. Use :meth:`fetch_minimum_raw_deposit` when an exact input for a
        transaction is required.

        :param block_identifier:
            Block tag or number at which to read the minimum.

        :return:
            Human-readable denomination-token amount, or ``None`` when the
            getter or denomination token is unavailable.
        """
        minimum_raw = self.fetch_minimum_raw_deposit(block_identifier)
        if minimum_raw is None or self.denomination_token is None:
            return None
        return self.denomination_token.convert_to_decimals(minimum_raw)

    def fetch_minimum_redemption(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Fetch Accountable's request-redemption dust threshold in shares.

        ``loan().minRedeem`` intentionally remains excluded until its unit is
        source-proven for the selected strategy deployment.

        :param block_identifier:
            Block at which to read the threshold.
        :return:
            Decimal share minimum, or ``None`` when unavailable.
        """
        minimum_raw = self._fetch_vault_minimum_raw(block_identifier)
        if minimum_raw is None or self.share_token is None:
            return None
        return self.share_token.convert_to_decimals(minimum_raw)

    def fetch_accountable_fees(self, block_identifier: BlockIdentifier = "latest") -> AccountableFeeData:  # noqa: PLR0914
        """Fetch every Accountable fee-manager term and the minimum deposit.

        The vault points to its strategy, and the strategy points to the fee
        manager. The reader first tries the current ABI's ``managementFee()``
        getter. A missing selector identifies the legacy ABI, where management
        fees are structurally unsupported and therefore known to be zero.

        :param block_identifier:
            Block tag or number shared by all onchain reads.

        :return:
            Complete Accountable-native fee snapshot.
        """
        strategy_address, strategy_contract = self._fetch_strategy_contract(block_identifier)
        fee_manager_address = Web3.to_checksum_address(strategy_contract.functions.feeManager().call(block_identifier=block_identifier))
        current_fee_manager = get_deployed_contract(
            self.web3,
            "accountable/AccountableFeeManagerV2.json",
            fee_manager_address,
            register_for_tracing=False,
        )

        try:
            management_fee_raw = int(current_fee_manager.functions.managementFee(strategy_address).call(block_identifier=block_identifier))
        except (BadFunctionCallOutput, ContractLogicError, ExtraValueError):
            supports_management_fee = False
            management_fee_raw = 0
            manager_fee_recipient_address = Web3.to_checksum_address(strategy_contract.functions.investmentManager().call(block_identifier=block_identifier))
            fee_manager = get_deployed_contract(
                self.web3,
                "accountable/AccountableFeeManagerV1.json",
                fee_manager_address,
                register_for_tracing=False,
            )
            manager_performance_fee_split_raw = int(fee_manager.functions.managerSplit(strategy_address).call(block_identifier=block_identifier))
            protocol_performance_fee_split_raw = int(fee_manager.functions.protocolSplit(strategy_address).call(block_identifier=block_identifier))
            manager_management_fee_split_raw = None
            protocol_management_fee_split_raw = None
        else:
            supports_management_fee = True
            manager_fee_recipient_address = Web3.to_checksum_address(strategy_contract.functions.managerFeeRecipient().call(block_identifier=block_identifier))
            fee_manager = current_fee_manager
            manager_performance_fee_split_raw = int(fee_manager.functions.managerSplit(strategy_address, True).call(block_identifier=block_identifier))
            protocol_performance_fee_split_raw = int(fee_manager.functions.protocolSplit(strategy_address, True).call(block_identifier=block_identifier))
            manager_management_fee_split_raw = int(fee_manager.functions.managerSplit(strategy_address, False).call(block_identifier=block_identifier))
            protocol_management_fee_split_raw = int(fee_manager.functions.protocolSplit(strategy_address, False).call(block_identifier=block_identifier))

        vault_minimum_deposit_raw = self._fetch_vault_minimum_raw(block_identifier)
        strategy_minimum_deposit_raw = self._fetch_strategy_minimum_raw_deposit(strategy_contract, block_identifier)
        minimum_deposit_raw = self._effective_minimum_raw_deposit(vault_minimum_deposit_raw, strategy_minimum_deposit_raw)
        minimum_deposit = None
        if minimum_deposit_raw is not None and self.underlying_token is not None:
            minimum_deposit = self.underlying_token.convert_to_decimals(minimum_deposit_raw)

        return AccountableFeeData(
            block_identifier=block_identifier,
            vault_address=Web3.to_checksum_address(self.vault_address),
            strategy_address=strategy_address,
            fee_manager_address=fee_manager_address,
            treasury_address=Web3.to_checksum_address(fee_manager.functions.treasury().call(block_identifier=block_identifier)),
            manager_fee_recipient_address=manager_fee_recipient_address,
            basis_points=int(fee_manager.functions.BASIS_POINTS().call(block_identifier=block_identifier)),
            supports_management_fee=supports_management_fee,
            establishment_fee_raw=int(fee_manager.functions.establishmentFee(strategy_address).call(block_identifier=block_identifier)),
            management_fee_raw=management_fee_raw,
            performance_fee_raw=int(fee_manager.functions.performanceFee(strategy_address).call(block_identifier=block_identifier)),
            manager_performance_fee_split_raw=manager_performance_fee_split_raw,
            protocol_performance_fee_split_raw=protocol_performance_fee_split_raw,
            manager_management_fee_split_raw=manager_management_fee_split_raw,
            protocol_management_fee_split_raw=protocol_management_fee_split_raw,
            prepayment_fee_raw=int(fee_manager.functions.prepaymentFee(strategy_address).call(block_identifier=block_identifier)),
            vault_minimum_deposit_raw=vault_minimum_deposit_raw,
            strategy_minimum_deposit_raw=strategy_minimum_deposit_raw,
            minimum_deposit_raw=minimum_deposit_raw,
            minimum_deposit=minimum_deposit,
        )

    def get_fee_data(self) -> FeeData:
        """Fetch Accountable fees using the shared fee-data representation.

        :return:
            Investor-facing management and performance fees with zero LP
            deposit and withdrawal charges.
        """
        return self.fetch_accountable_fees().as_generic_fee_data()

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Fetch the annualised onchain management fee.

        Legacy fee managers cannot charge a management fee, so they return a
        known zero instead of the previous unknown ``None``.

        :param block_identifier:
            Block tag or number at which to read the fee.

        :return:
            Fractional management fee, such as ``0.01`` for 1%.
        """
        return self.fetch_accountable_fees(block_identifier).management_fee

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float:
        """Fetch the onchain performance fee.

        This replaces the previous offchain API value with the fee manager as
        the authoritative source.

        :param block_identifier:
            Block tag or number at which to read the fee.

        :return:
            Fractional performance fee, such as ``0.20`` for 20%.
        """
        return self.fetch_accountable_fees(block_identifier).performance_fee

    def get_deposit_fee(self, block_identifier: BlockIdentifier) -> float:  # noqa: PLR6301
        """Return zero because Accountable has no ERC-4626 LP deposit fee.

        ``establishmentFee`` is collected from the underlying borrower during
        loan repayment and must not be presented as an investor entry fee.

        :param block_identifier:
            Unused because the LP deposit fee is structurally zero.

        :return:
            Always ``0.0``.
        """
        del block_identifier
        return 0.0

    def get_withdraw_fee(self, block_identifier: BlockIdentifier) -> float:  # noqa: PLR6301
        """Return zero because Accountable has no ERC-4626 LP withdrawal fee.

        ``prepaymentFee`` applies when an underlying borrower repays early and
        must not be presented as an investor redemption fee.

        :param block_identifier:
            Unused because the LP withdrawal fee is structurally zero.

        :return:
            Always ``0.0``.
        """
        del block_identifier
        return 0.0

    def has_custom_fees(self) -> bool:  # noqa: PLR6301
        """Report native borrower fee terms outside the shared fee schema.

        Accountable establishment, prepayment and recipient-split fields are
        preserved in :class:`AccountableFeeData`, but the shared fee schema
        cannot represent them without mislabelling borrower charges as LP
        entry or exit charges.

        :return:
            Always ``True`` for Accountable vaults.
        """
        return True

    def get_estimated_lock_up(self) -> datetime.timedelta | None:  # noqa: PLR6301
        """Accountable vaults use async redemption queue.

        Lock-up period depends on the vault strategy and available liquidity.
        """
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Return the yield app link.

        Accountable's yield app URLs use the loan/strategy contract address,
        not the ERC-4626 vault (share token) address.
        Falls back to the vault address if metadata is unavailable.
        """
        del referral
        metadata = get_handwritten_vault_metadata(self.chain_id, self.address)
        if metadata:
            return metadata.link

        meta = self.accountable_metadata
        if meta and meta.get("loan_address"):
            return f"https://yield.accountable.capital/vaults/{meta['loan_address']}"
        return f"https://yield.accountable.capital/vaults/{self.vault_address}"
