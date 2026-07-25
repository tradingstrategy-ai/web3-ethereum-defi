"""Plutus hedge token vault support."""

import datetime
import logging
from decimal import Decimal
from functools import cached_property
from typing import Iterable

from eth_typing import BlockIdentifier, HexAddress
from web3.contract import Contract

from eth_defi.abi import ZERO_ADDRESS_STR, get_deployed_contract
from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager
from eth_defi.erc_4626.vault import ERC4626HistoricalReader, ERC4626Vault
from eth_defi.erc_4626.vault_protocol.plutus.deposit_redeem import PlutusAsyncDepositManager
from eth_defi.event_reader.conversion import convert_int256_bytes_to_int
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.vault.base import (
    DEPOSIT_CLOSED_BY_ADMIN,
    REDEMPTION_CLOSED_BY_ADMIN,
    VaultHistoricalRead,
    VaultHistoricalReader,
)
from eth_defi.vault.deposit_redeem import VaultDepositManager, VaultDepositManagerCapability

logger = logging.getLogger(__name__)

#: Plutus deployments upgraded to the ERC-7540-style asynchronous redemption
#: contract (``HedgeVaultV2``): synchronous deposits, but ``redeem`` is disabled
#: (reverts ``UseRequestRedeem``) and redemptions go through
#: ``requestRedeem`` / ``fulfillRedeem`` / ``redeem(requestId, receiver)``.
PLUTUS_ASYNC_VAULT_ADDRESSES = frozenset(
    {
        "0x58bfc95a864e18e8f3041d2fcd3418f48393fe6a",  # Plutus Hedge Token (plvHedge), Arbitrum
    }
)


class PlutusDepositManager(ERC4626DepositManager):
    """Plutus synchronous ERC-4626 lifecycle that estimates via ``convertToShares``.

    This manager serves the standard (non-async) Plutus Hedge Token
    deployments, where both deposit and redemption are direct synchronous
    ERC-4626 calls. The upgraded ``HedgeVaultV2`` deployment, whose ``redeem``
    is disabled in favour of ``requestRedeem`` / ``fulfillRedeem``, uses the
    separate :class:`PlutusAsyncDepositManager` instead (selected by
    :meth:`PlutusVault.get_deposit_manager`). Plutus vaults are manually
    opened and closed by an admin, so a window may simply be shut.

    **Deposit process**

    Synchronous. After ``approve()``, :meth:`create_deposit_request` builds the
    inherited single ERC-4626 ``deposit`` call. :meth:`estimate_deposit` is
    overridden: instead of ``previewDeposit`` (unreliable/reverting on these
    deployments) it prices shares through the non-reverting
    ``convertToShares(raw_amount)`` and raises :class:`ValueError` when the
    result is zero, which indicates the deposit window is closed or pricing is
    unavailable rather than a free deposit.

    **Redemption process**

    Synchronous, fully inherited. :meth:`create_redemption_request` builds a
    single ERC-4626 ``redeem`` call returning denomination tokens to the owner
    in the same transaction.

    **Queues and settlement**

    No per-owner request queue: each ``deposit`` / ``redeem`` settles in its own
    transaction. The only gating is the admin-controlled open/closed state,
    read at the vault level from ``maxDeposit(address(0))`` and
    ``maxRedeem(address(0))`` (zero means closed). Queued generations, delegated
    operators, cancellation and reclaim are not modelled.

    **Lockups and cooldowns**

    This manager enforces no onchain cooldown. For modelling purposes
    :meth:`PlutusVault.get_estimated_lock_up` returns a fixed 30-day estimate
    (from Plutus Discord guidance, since windows are manually scheduled rather
    than time-locked in the contract).

    **Whitelisting / access control**

    Permissionless. No Plutus-specific whitelist is applied beyond the inherited
    :meth:`check_deposit_whitelist` preflight; access is gated only by whether
    the admin currently has the deposit/redemption window open.

    **Anvil settlement (force_settle)**

    The direct ``deposit`` and ``redeem`` calls complete in their originating
    transaction, so the inherited :meth:`force_settle` accepts ``None`` and
    performs the Anvil-validated shared no-op.
    """

    def estimate_deposit(
        self,
        owner: HexAddress | None,
        amount: Decimal,
        block_identifier: BlockIdentifier = "latest",
    ) -> Decimal:
        """Estimate shares through the non-reverting conversion function.

        :param owner:
            Unused because the conversion is not owner-specific.
        :param amount:
            Decimal denomination amount.
        :param block_identifier:
            Block number or ``"latest"``.
        :return:
            Estimated decimal shares.
        :raise ValueError:
            If the selected deposit window produces a zero estimate.
        """
        del owner
        raw_amount = self.vault.denomination_token.convert_to_raw(amount)
        raw_shares = self.vault.vault_contract.functions.convertToShares(raw_amount).call(block_identifier=block_identifier)
        if raw_shares <= 0:
            raise ValueError(f"Plutus deposit estimate is zero for {amount} {self.vault.denomination_token.symbol}")
        return self.vault.share_token.convert_to_decimals(raw_shares)


class PlutusHistoricalReader(ERC4626HistoricalReader):
    """Read Plutus vault core data + deposit/redemption state via maxDeposit/maxRedeem.

    - Plutus vaults are manually opened/closed
    - Uses ERC-4626 ``maxDeposit(address(0))`` and ``maxRedeem(address(0))``
      to determine if deposits/redemptions are open
    - Trading state is not tracked (always ``None``)
    """

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        yield from self.construct_core_erc_4626_multicall()
        yield from self.construct_max_redeem_call()

    def construct_max_redeem_call(self) -> Iterable[EncodedCall]:
        """Plutus uses maxRedeem(address(0)) to signal whether redemptions are open."""
        max_redeem = EncodedCall.from_contract_call(
            self.vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR),
            extra_data={
                "function": "maxRedeem",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )
        yield max_redeem

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        call_by_name = self.dictify_multicall_results(block_number, call_results)

        # Decode common variables
        share_price, total_supply, total_assets, errors, max_deposit = self.process_core_erc_4626_result(call_by_name)

        # Derive deposits_open/redemption_open from maxDeposit/maxRedeem
        deposits_open = None
        if max_deposit is not None:
            deposits_open = max_deposit > 0

        max_redeem = None
        redemption_open = None
        max_redeem_result = call_by_name.get("maxRedeem")
        if max_redeem_result and max_redeem_result.success and self.vault.share_token is not None:
            raw_max_redeem = convert_int256_bytes_to_int(max_redeem_result.result)
            max_redeem = self.vault.share_token.convert_to_decimals(raw_max_redeem)
            redemption_open = max_redeem > 0

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
            max_redeem=max_redeem,
            deposits_open=deposits_open,
            redemption_open=redemption_open,
        )


class PlutusVault(ERC4626Vault):
    """Plutus vaults.

    - Hedge token vaults: https://plutus.fi/Vaults
    - Docs: https://docs.plutusdao.io/plutus-docs
    - About plHEDGE vault: https://medium.com/@plutus.fi/introducing-plvhedge-an-automated-funding-arbitrage-vault-f2f222fa8c56
    """

    def is_async_redemption_deployment(self) -> bool:
        """Return whether this deployment uses the ERC-7540-style async redemption.

        :return:
            ``True`` for the upgraded Hedge (``HedgeVaultV2``) deployment.
        """
        return self.address.lower() in PLUTUS_ASYNC_VAULT_ADDRESSES

    @cached_property
    def vault_contract(self) -> Contract:
        """Bind the deployment-appropriate ABI.

        The async Hedge deployment needs the ``HedgeVaultV2`` interface for its
        ``requestRedeem`` / ``fulfillRedeem`` / ``pendingRedeemRequest`` surface;
        other Plutus vaults use the standard ERC-4626 interface.

        :return:
            Web3 contract bound to the vault address.
        """
        if self.is_async_redemption_deployment():
            return get_deployed_contract(self.web3, "plutus/HedgeVaultV2.json", self.vault_address)
        return get_deployed_erc_4626_contract(self.web3, self.vault_address)

    def get_historical_reader(self, stateful) -> VaultHistoricalReader:
        return PlutusHistoricalReader(self, stateful)

    def get_deposit_manager(self) -> VaultDepositManager:
        """Create the Plutus lifecycle manager for this deployment.

        :return:
            Asynchronous-redemption manager for the upgraded Hedge deployment,
            otherwise the synchronous manager that avoids Plutus' reverting
            ``previewDeposit`` path.
        """
        if self.is_async_redemption_deployment():
            return PlutusAsyncDepositManager(self)
        return PlutusDepositManager(self)

    def get_deposit_manager_capability(self) -> VaultDepositManagerCapability:
        """Declare the deployment-specific deposit/redemption capability.

        :return:
            Synchronous-deposit / asynchronous-redemption capability for the
            upgraded Hedge deployment, otherwise the fully synchronous
            capability.
        """
        if self.is_async_redemption_deployment():
            return VaultDepositManagerCapability(
                can_deposit=True,
                can_redeem=True,
                deposit_flow="synchronous",
                redemption_flow="asynchronous",
                supports_anvil_settlement=False,
                anvil_settlement_unsupported_reason="plutus_redeem_fulfilment_is_access_control_role_gated",
            )
        return self.get_synchronous_deposit_manager_capability()

    def has_custom_fees(self) -> bool:
        """Deposit/withdrawal fees."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Hardcoded PLutus fees.

        - Fees are internalized in the share price, no explicit performance fee as per discussion in Plutus Discord
        - https://docs.plutusdao.io/plutus-docs/protocol-fees
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Hardcoded PLutus fees.

        - Fees are internalized in the share price, no explicit performance fee as per discussion in Plutus Discord
        - https://docs.plutusdao.io/plutus-docs/protocol-fees
        """
        return 0.12

    def get_estimated_lock_up(self) -> datetime.timedelta:
        """Currently Plutus vaults are manually opened/closed.

        We estimate one month lock-up for modelling purposes based on the discussion with Plutus in Discord.
        """
        return datetime.timedelta(days=30)

    def get_link(self, referral: str | None = None) -> str:
        # No vault pages
        return f"https://plutus.fi/Vaults"

    def fetch_deposit_closed_reason(self) -> str | None:
        """Check maxDeposit to determine if deposits are closed.

        Plutus vaults are manually opened/closed by admin.
        """
        try:
            max_deposit = self.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
            if max_deposit == 0:
                return f"{DEPOSIT_CLOSED_BY_ADMIN} (maxDeposit=0)"
        except Exception:
            pass
        return None

    def fetch_redemption_closed_reason(self) -> str | None:
        """Check maxRedeem to determine if redemptions are closed.

        Plutus vaults are manually opened/closed by admin.
        """
        try:
            max_redeem = self.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
            if max_redeem == 0:
                return f"{REDEMPTION_CLOSED_BY_ADMIN} (maxRedeem=0)"
        except Exception:
            pass
        return None

    def fetch_deposit_next_open(self) -> datetime.datetime | None:
        """Deposit timing is unpredictable - manually controlled."""
        return None

    def fetch_redemption_next_open(self) -> datetime.datetime | None:
        """Withdrawal timing is unpredictable - manually controlled."""
        return None

    def can_check_redeem(self) -> bool:
        """Plutus supports address(0) checks for redemption availability.

        - maxRedeem(address(0)) returns 0 when admin has closed redemptions
        """
        return True
