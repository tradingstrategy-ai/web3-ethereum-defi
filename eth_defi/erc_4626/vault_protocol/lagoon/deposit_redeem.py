"""Lagoon-specific extensions to the generic ERC-7540 flow."""

import logging
from decimal import Decimal

from eth_typing import HexAddress, HexStr
from hexbytes import HexBytes

from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault, LagoonVersion
from eth_defi.erc_7540.deposit_redeem import (
    ERC7540DepositManager as GenericERC7540DepositManager,
)
from eth_defi.erc_7540.deposit_redeem import (
    ERC7540DepositRequest as GenericERC7540DepositRequest,
)
from eth_defi.erc_7540.deposit_redeem import (
    ERC7540DepositTicket,
    ERC7540RedemptionRequest,
    ERC7540RedemptionTicket,
)
from eth_defi.provider.anvil import fund_erc20_on_anvil, is_anvil, make_anvil_custom_rpc_request
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.deposit_redeem import (
    AsyncVaultRequestStatus,
    DepositTicket,
    RedemptionTicket,
    UnsupportedVaultSimulation,
    VaultFlowUnavailable,
    VaultForcedSettlementResult,
    WhitelistingRequired,
)

logger = logging.getLogger(__name__)

#: ``NotWhitelisted()`` custom-error selector in Lagoon v0.5 and earlier.
NOT_WHITELISTED_SELECTOR = HexBytes("0x584a7938")

#: ``AddressNotAllowed(address)`` custom-error selector in Lagoon v0.6.
ADDRESS_NOT_ALLOWED_SELECTOR = HexBytes("0x51ee5ed5")

#: ``requestDeposit(uint256,address,address)`` Lagoon entry-point selector.
REQUEST_DEPOSIT_SELECTOR = HexBytes("0x85b77f45")

#: Legacy Lagoon ``Deposit`` event topic accepted during claim analysis.
LEGACY_DEPOSIT_TOPIC = "0xdcbc1c05240f31ff3ad067ef1ee35ce4997762752e3a095284754544f4c709d7"

#: Legacy Lagoon ``Withdraw`` event topic accepted during claim analysis.
LEGACY_WITHDRAW_TOPIC = "0xfbde797d201c681b91056529119e0b02407c7bb96a4a2c75c01fc9667232c8db"


class LagoonDepositManager(GenericERC7540DepositManager):
    """Lagoon ERC-7540 flow with versioned access policy and Safe settlement.

    Lagoon vaults are fully asynchronous `ERC-7540
    <https://eips.ethereum.org/EIPS/eip-7540>`__ vaults: both deposits and
    redemptions are request → settle → claim. The generic ERC-7540 manager
    supplies the standard request, claim, ticket and event handling; this
    subclass adds Lagoon's versioned deposit access policy, legacy claim-event
    topics and an Anvil settlement driver that impersonates the deployed
    valuation manager and Safe.

    **Deposit process.** Asynchronous. Deposits go through the inherited
    ``requestDeposit(assets, controller, owner)`` entry point (selector
    :data:`REQUEST_DEPOSIT_SELECTOR`), preceded by the ERC-20 ``approve`` of the
    denomination token; the assets move into the vault's pending silo at request
    time. The request id is read from the ERC-7540 ``DepositRequest`` event into
    an :class:`ERC7540DepositTicket`. After settlement the shares are claimed with
    ``deposit(assets, receiver, controller)`` (the three-argument ERC-7540 claim
    form). :meth:`get_deposit_event_signatures` additionally accepts the legacy
    Lagoon ``Deposit`` topic (:data:`LEGACY_DEPOSIT_TOPIC`) during claim analysis.

    **Redemption process.** Asynchronous, symmetric. Redemptions use
    ``requestRedeem(shares, controller, owner)``, emitting the ERC-7540
    ``RedeemRequest`` event whose request id is stored in an
    :class:`ERC7540RedemptionTicket`. After settlement the assets are claimed with
    ``redeem(shares, receiver, controller)``; :meth:`get_redemption_event_signatures`
    also accepts the legacy ``Withdraw`` topic (:data:`LEGACY_WITHDRAW_TOPIC`).

    **Queues and settlement.** Requests are batched per settlement round. The
    vault's valuation manager first reports NAV via ``updateNewTotalAssets`` /
    ``updateTotalAssets``, then the Safe settles: ``settleDeposit(newTotalAssets)``
    mints for pending deposits and always calls ``_settleRedeem(msg.sender)``
    afterwards, which pays queued redemptions by pulling the denomination token
    from the Safe (``safeTransferFrom(safe, vault, convertToAssets(pendingShares))``).
    Claimability is read from ``claimableDepositRequest`` /
    ``claimableRedeemRequest``.

    **Lockups and cooldowns.** There is no fixed cooldown; the wait is one
    settlement round, whose cadence is off-vault operational rather than an
    onchain deadline. :meth:`LagoonFlowManager.get_estimated_lock_up` returns
    Lagoon's offchain ``average_settlement`` metadata when available, otherwise
    ``None``.

    **Whitelisting / access control.** Deposit admission is version specific and
    checked before broadcast by :meth:`_assert_deposit_request_available` /
    :meth:`can_create_deposit_request`. Lagoon v0.5 and earlier derive whitelist
    mode from ``isWhitelisted(0x0)`` and admit an account with
    ``isWhitelisted(account)``; Lagoon v0.6 uses the access layer's
    ``isAllowed(0x0)`` / ``isAllowed(account)`` (supporting whitelist mode,
    blacklist mode and an external sanctions oracle). Both flow through
    :meth:`LagoonVault.is_whitelisted_deposit` and
    :meth:`LagoonVault.is_account_whitelisted`. A denial raises
    :class:`~eth_defi.vault.deposit_redeem.WhitelistingRequired` (decoded error
    ``NotWhitelisted`` / :data:`NOT_WHITELISTED_SELECTOR` for v0.5, or
    ``AddressNotAllowed`` / :data:`ADDRESS_NOT_ALLOWED_SELECTOR` for v0.6); an
    undeterminable policy or a paused vault raises
    :class:`~eth_defi.vault.deposit_redeem.VaultFlowUnavailable` (fails closed).

    **Anvil settlement (force_settle).** :meth:`force_settle` requires an Anvil
    provider and a concrete deposit or redemption ticket. It impersonates and
    funds both the valuation manager and the Safe, then runs one settlement round
    via :func:`~eth_defi.erc_4626.vault_protocol.lagoon.testing.force_lagoon_settle`.
    Because ``settleDeposit`` always triggers ``_settleRedeem``,
    :meth:`_provision_safe_for_settlement` first grants the Safe's missing standing
    ``approve(vault, max)``. It defaults to synthetic Safe provisioning because
    every settlement processes the whole shared redemption queue, including the
    simulated redemption when applicable. Call
    ``force_settle(..., ignore_liquidity=False)`` to require real Safe liquidity.
    The injected amount and the
    ``liquidity_constraints_ignored`` result flag disclose that a ``claimable``
    result proves settlement mechanics, not live solvency. Success requires
    the ticket to reach :attr:`AsyncVaultRequestStatus.claimable`; otherwise it raises
    :class:`UnsupportedVaultSimulation` with the concrete before/after status.
    """

    def __init__(self, vault: LagoonVault):
        """Initialise the Lagoon manager.

        The constructor narrows the generic manager to Lagoon vault adapters
        because later operations rely on Lagoon roles and version detection.

        :param vault:
            Lagoon vault adapter.
        """
        assert isinstance(vault, LagoonVault), f"Got {type(vault)}"
        super().__init__(vault)

    def force_settle(
        self,
        ticket: DepositTicket | RedemptionTicket | None,
        *,
        mock: object | None = None,
        ignore_liquidity: bool = True,
    ) -> VaultForcedSettlementResult:
        """Force one Lagoon settlement round on an Anvil fork.

        The simulation impersonates the vault's valuation manager and Safe,
        then checks that the selected request becomes claimable.

        :param ticket:
            Pending deposit or redemption ticket to progress.
        :param mock:
            Optional local protocol mock. Lagoon has no mock settlement driver;
            use the existing real Anvil-fork driver without this argument.
        :param ignore_liquidity:
            On an Anvil fork only, permit the driver to top up a Safe that is
            short of redemption assets. Defaults to ``True`` because settlement
            processes the shared redemption queue. Pass ``False`` to require
            real Safe liquidity.
        :return:
            Before/after status and settlement transaction hashes.
        :raise UnsupportedVaultSimulation:
            If the provider is not Anvil, the ticket is unsupported, or the
            settlement does not make the request claimable.
        """
        if mock is not None:
            raise UnsupportedVaultSimulation(
                f"{self.__class__.__name__} has no local mock settlement driver",
                unsupported_reason="mock_settlement_driver_not_implemented",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
            )

        if not is_anvil(self.web3):
            raise UnsupportedVaultSimulation(
                "Lagoon force_settle() requires an Anvil provider",
                unsupported_reason="anvil_provider_required",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
            )
        if ticket is None:
            raise UnsupportedVaultSimulation(
                "Lagoon force_settle() requires an async request ticket",
                unsupported_reason="anvil_settlement_ticket_required",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
            )

        if isinstance(ticket, ERC7540DepositTicket):
            status_before = self.get_deposit_request_status(ticket)
        elif isinstance(ticket, ERC7540RedemptionTicket):
            status_before = self.get_redemption_request_status(ticket)
        else:
            raise UnsupportedVaultSimulation(
                f"Unsupported Lagoon ticket type: {type(ticket)}",
                unsupported_reason="anvil_settlement_ticket_unsupported",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
            )

        from eth_defi.erc_4626.vault_protocol.lagoon.testing import force_lagoon_settle

        valuation_manager = self.vault.valuation_manager
        safe_address = self.vault.safe_address
        try:
            make_anvil_custom_rpc_request(self.web3, "anvil_impersonateAccount", [valuation_manager])
            make_anvil_custom_rpc_request(self.web3, "anvil_setBalance", [valuation_manager, hex(10**18)])
            make_anvil_custom_rpc_request(self.web3, "anvil_impersonateAccount", [safe_address])
            make_anvil_custom_rpc_request(self.web3, "anvil_setBalance", [safe_address, hex(10**18)])

            # settleDeposit() always settles the shared redemption queue. Prepare
            # its Safe allowance and, by default, its Anvil-only synthetic balance.
            liquidity_tx_hashes, synthetic_injected_raw = self._provision_safe_for_settlement(
                safe_address,
                ignore_liquidity=ignore_liquidity,
            )

            settle_tx_hashes = force_lagoon_settle(
                self.vault,
                valuation_manager,
                settlement_manager=safe_address,
            )
            # Approval/top-up transactions precede the valuation + settlement ones.
            tx_hashes = (*liquidity_tx_hashes, *settle_tx_hashes)

            if isinstance(ticket, ERC7540DepositTicket):
                status_after = self.get_deposit_request_status(ticket)
            else:
                status_after = self.get_redemption_request_status(ticket)

            if status_after is not AsyncVaultRequestStatus.claimable:
                # Fail loudly with the concrete before/after status rather than
                # returning a misleading "pending -> pending" success.
                raise UnsupportedVaultSimulation(
                    f"Lagoon settlement did not make {type(ticket).__name__} claimable: {status_before.value} -> {status_after.value}",
                    unsupported_reason="lagoon_settlement_not_claimable",
                    protocol=self.vault.get_protocol_name(),
                    vault_address=self.vault.address,
                    direction="deposit" if isinstance(ticket, ERC7540DepositTicket) else "redeem",
                )

            return VaultForcedSettlementResult(
                ticket=ticket,
                settlement_required=True,
                status_before=status_before,
                status_after=status_after,
                transaction_hashes=tuple(HexBytes(h) for h in tx_hashes),
                # Honest signalling: non-zero means the fork Safe was topped up with
                # synthetic liquidity, so `claimable` here does NOT prove the live
                # vault is solvent (see VaultForcedSettlementResult docstring).
                synthetic_assets_injected_raw=synthetic_injected_raw,
                liquidity_constraints_ignored=synthetic_injected_raw > 0,
            )
        finally:
            # Snapshot reversion restores contract state but not this Anvil node
            # setting. Always release both accounts, including failure paths.
            make_anvil_custom_rpc_request(self.web3, "anvil_stopImpersonatingAccount", [safe_address])
            make_anvil_custom_rpc_request(self.web3, "anvil_stopImpersonatingAccount", [valuation_manager])

    def _provision_safe_for_settlement(
        self,
        safe_address: HexAddress,
        *,
        ignore_liquidity: bool,
    ) -> tuple[list[HexBytes], int]:
        """Give the impersonated Safe what a Lagoon redemption round needs on a fork.

        **Why this exists.** Lagoon's ``settleDeposit(_newTotalAssets)`` always
        calls ``_settleRedeem(msg.sender)`` afterwards, and ``_settleRedeem``
        pays queued redemptions with
        ``asset.safeTransferFrom(safe, vault, convertToAssets(pendingShares))``.
        That single ``transferFrom`` from the Safe is where third-party Lagoon
        deployments fail in two distinct ways that eth_defi's own deployment
        script (``lagoon/deployment.py``, which grants a standing approval and
        keeps the Safe funded) prevents in production:

        - **(b) allowance revert.** A third-party Safe never issued the standing
          ``approve(vault, max)``, so ``transferFrom`` reverts
          ``"ERC20: transfer amount exceeds allowance"``. Reproduced when there
          are any pending redemption shares at settlement.
        - **(a) silent stall.** ``_settleRedeem`` returns early with no state
          change when
          ``convertToAssets(pendingShares) > denominationToken.balanceOf(safe)``
          (the Safe's liquidity is deployed into strategy positions).
          ``settleDeposit`` still succeeds, the redemption epoch never settles,
          and the ticket is left ``pending -> pending``. Approval alone cannot
          fix this; the Safe balance must be raised.

        **What this does.** On the fork it (b) issues the missing
        ``approve(vault, max)`` from the impersonated Safe. When
        ``ignore_liquidity=True`` it additionally (a) tops the Safe up with
        synthetic denomination tokens when it is short of the liquidity needed
        to pay every queued redemption. The injected amount is returned so
        :attr:`VaultForcedSettlementResult.synthetic_assets_injected_raw` can
        disclose it — a non-zero injection means a ``claimable`` result proves
        the settlement *mechanism*, NOT that the live vault is solvent.

        **Scope of the shortfall estimate.**
        :meth:`LagoonFlowManager.calculate_underlying_needed_for_redemptions`
        returns ``siloShareBalance * currentSharePrice`` — i.e. every pending
        redemption, including the selected redemption ticket when applicable.
        ``force_lagoon_settle`` calls
        ``updateNewTotalAssets(current NAV)`` immediately before
        ``settleDeposit``, so the settled share price equals the current share
        price and this estimate matches ``convertToAssets()`` at settlement. A
        one-token buffer absorbs share-price rounding between the two.

        This is deliberately a testing/simulation driver (Anvil storage write);
        it must never run against a live provider — the caller guards with
        :func:`is_anvil`.

        :param safe_address:
            The vault Safe address, already impersonated by the caller.
        :param ignore_liquidity:
            Whether an Anvil-only synthetic Safe top-up may cover the whole
            redemption queue's observed shortfall. ``False`` raises before
            settlement instead.
        :return:
            A ``(transaction_hashes, synthetic_assets_injected_raw)`` pair. The
            hashes are the approval (and any not-yet-mined provisioning) txs;
            the injected amount is ``0`` when the Safe already held enough
            denomination token to pay all queued redemptions.
        """
        web3 = self.web3
        # Defence in depth: this mints synthetic balance and grants a max
        # approval from an impersonated Safe, so it must never touch a live
        # provider even if a future caller forgets the is_anvil() guard that
        # force_settle() already applies.
        if not is_anvil(web3):
            raise UnsupportedVaultSimulation(
                "Lagoon Safe settlement provisioning is Anvil-only",
                unsupported_reason="anvil_provider_required",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                direction="redeem",
            )

        denomination_token = self.vault.denomination_token
        tx_hashes: list[HexBytes] = []

        # Redemption liquidity needed to pay every queued redemption. When there
        # are no pending redemptions (needed_human == 0, e.g. a deposit-only
        # settlement round), skip provisioning entirely — no approval or top-up
        # is required and adding the +1 token buffer would otherwise trigger a
        # spurious approve/injection. The +1 token buffer (only when something
        # is owed) absorbs share-price rounding vs convertToAssets() at settlement.
        flow_manager = self.vault.get_flow_manager()
        needed_human = flow_manager.calculate_underlying_needed_for_redemptions("latest")
        needed_raw = denomination_token.convert_to_raw(needed_human)
        if needed_raw > 0:
            needed_raw += denomination_token.convert_to_raw(Decimal(1))

        # Strict mode fails before mutating approval state or running a partial
        # settlement round that can settle deposits while leaving redemption pending.
        current_raw = denomination_token.fetch_raw_balance_of(safe_address)
        if needed_raw > current_raw and not ignore_liquidity:
            raise UnsupportedVaultSimulation(
                f"Lagoon Safe {safe_address} lacks redemption liquidity for strict fork settlement: needs {needed_raw} raw {denomination_token.symbol}, has {current_raw}.",
                unsupported_reason="lagoon_settlement_insufficient_liquidity",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                direction="redeem",
            )

        # (b) Issue the standing approval the production deploy script grants, so
        # _settleRedeem's transferFrom(safe -> vault) does not revert on
        # allowance. Only broadcast when the existing allowance is actually
        # short of what the pending redemptions need: skipping the redundant tx
        # keeps this idempotent and, crucially, does NOT advance the chain for
        # already-provisioned vaults (our self-deployed test vaults carry a
        # max approval), so block-number-exact assertions elsewhere still hold.
        # Uses the raw contract to approve the full uint256 range
        # (TokenDetails.approve() would convert a decimal human amount).
        current_allowance = int(denomination_token.contract.functions.allowance(safe_address, self.vault.address).call())
        if current_allowance < needed_raw:
            approve_hash = denomination_token.contract.functions.approve(self.vault.address, 2**256 - 1).transact({"from": safe_address})
            assert_transaction_success_with_explanation(web3, approve_hash)
            tx_hashes.append(HexBytes(approve_hash))

        # (a) Ensure the Safe holds enough denomination token to cover every
        # queued redemption. The top-up is a storage write (fund_erc20_on_anvil),
        # not a transaction, so it does not mine a block.
        synthetic_injected_raw = 0
        if needed_raw > current_raw:
            synthetic_injected_raw = needed_raw - current_raw
            # fund_erc20_on_anvil sets the ABSOLUTE balance via a storage write,
            # so we target `needed_raw`; the increase over the current balance
            # is the synthetic amount we disclose.
            fund_erc20_on_anvil(web3, denomination_token.address, safe_address, needed_raw)
            logger.info(
                "Lagoon fork settlement topped up Safe %s with %d raw %s to cover redemption shortfall (needed %d, had %d)",
                safe_address,
                synthetic_injected_raw,
                denomination_token.symbol,
                needed_raw,
                current_raw,
            )

        return tx_hashes, synthetic_injected_raw

    def can_create_deposit_request(self, owner: HexAddress) -> bool:
        """Return whether Lagoon's current access policy admits an owner.

        Unknown access policy fails closed. Transient provider failures still
        propagate so callers can distinguish an unavailable read from a known
        denial.

        :param owner:
            Request owner and controller.
        :return:
            ``True`` only when the vault is open and its access view admits the
            owner.
        """
        if self._is_vault_paused():
            return False
        try:
            self.vault.is_whitelisted_deposit()
            return self.vault.is_account_whitelisted(owner)
        except NotImplementedError:
            return False

    def _assert_deposit_request_available(self, owner: HexAddress) -> None:
        """Reject a Lagoon access-policy denial before request broadcast.

        Lagoon v0.5 derives whitelist mode from ``isWhitelisted(0x0)``.
        Lagoon v0.6 uses ``isAllowed(0x0)`` because its access layer supports
        whitelist mode, blacklist mode and an external sanctions oracle.

        :param owner:
            Request owner and controller.
        :raise VaultFlowUnavailable:
            If the vault is paused, its policy is unknown, or the owner is
            denied.
        """
        if self._is_vault_paused():
            raise VaultFlowUnavailable(
                "Lagoon deposit requests are paused",
                protocol="Lagoon",
                vault_address=self.vault.address,
                caller=owner,
                direction="deposit",
                phase="preflight",
            )

        try:
            self.vault.is_whitelisted_deposit()
        except NotImplementedError as e:
            raise VaultFlowUnavailable(
                "Lagoon deposit access policy cannot be determined",
                protocol="Lagoon",
                vault_address=self.vault.address,
                caller=owner,
                direction="deposit",
                phase="preflight",
            ) from e

        try:
            account_allowed = self.vault.is_account_whitelisted(owner)
        except NotImplementedError as e:
            raise VaultFlowUnavailable(
                "Lagoon deposit account admission cannot be determined",
                protocol="Lagoon",
                vault_address=self.vault.address,
                caller=owner,
                direction="deposit",
                phase="preflight",
            ) from e

        if account_allowed:
            return

        is_v06 = self.vault.version == LagoonVersion.v_0_6_0
        # Self-describing message for diagnostics: chain id, vault and depositor
        # embedded in the text (see WhitelistingRequired docstring).
        raise WhitelistingRequired(
            f"Lagoon deposit account {owner} is not allowed for vault {self.vault.address} on chain {self.vault.chain_id}",
            protocol="Lagoon",
            vault_address=self.vault.address,
            caller=owner,
            direction="deposit",
            phase="preflight",
            decoded_error="AddressNotAllowed" if is_v06 else "NotWhitelisted",
            function_selector=REQUEST_DEPOSIT_SELECTOR,
            error_selector=ADDRESS_NOT_ALLOWED_SELECTOR if is_v06 else NOT_WHITELISTED_SELECTOR,
        )

    def get_deposit_event_signatures(self) -> set[HexStr]:
        """Return Lagoon claim-deposit event topics.

        Legacy deployments use a non-standard topic while current releases
        emit the ERC-4626 ``Deposit`` event accepted by the generic manager.

        :return:
            Standard and legacy Lagoon deposit topics.
        """
        return super().get_deposit_event_signatures() | {LEGACY_DEPOSIT_TOPIC}

    def get_redemption_event_signatures(self) -> set[HexStr]:
        """Return Lagoon claim-redemption event topics.

        Legacy deployments use a non-standard topic while current releases
        emit the ERC-4626 ``Withdraw`` event accepted by the generic manager.

        :return:
            Standard and legacy Lagoon withdrawal topics.
        """
        return super().get_redemption_event_signatures() | {LEGACY_WITHDRAW_TOPIC}


# Backwards-compatible names retained for callers that imported the generic
# class names from the Lagoon module before the protocol-neutral split.
LagoonDepositRequest = GenericERC7540DepositRequest
ERC7540DepositManager = LagoonDepositManager
ERC7540DepositRequest = LagoonDepositRequest

__all__ = [
    "ADDRESS_NOT_ALLOWED_SELECTOR",
    "NOT_WHITELISTED_SELECTOR",
    "REQUEST_DEPOSIT_SELECTOR",
    "ERC7540DepositManager",
    "ERC7540DepositRequest",
    "ERC7540DepositTicket",
    "ERC7540RedemptionRequest",
    "ERC7540RedemptionTicket",
    "LagoonDepositManager",
    "LagoonDepositRequest",
]
