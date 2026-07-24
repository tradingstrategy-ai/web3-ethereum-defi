"""Lagoon-specific extensions to the generic ERC-7540 flow."""

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
from eth_defi.provider.anvil import is_anvil, make_anvil_custom_rpc_request
from eth_defi.vault.deposit_redeem import (
    AsyncVaultRequestStatus,
    DepositTicket,
    RedemptionTicket,
    UnsupportedVaultSimulation,
    VaultFlowUnavailable,
    VaultForcedSettlementResult,
)

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
    """Lagoon ERC-7540 flow with access-policy and settlement support.

    The generic ERC-7540 manager supplies standard request, claim, ticket and
    event handling. Lagoon adds versioned access checks, legacy claim-event
    topics and an Anvil settlement driver that impersonates the deployed
    valuation manager and Safe.
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
    ) -> VaultForcedSettlementResult:
        """Force one Lagoon settlement round on an Anvil fork.

        The simulation impersonates the vault's valuation manager and Safe,
        then checks that the selected request becomes claimable.

        :param ticket:
            Pending deposit or redemption ticket to progress.
        :return:
            Before/after status and settlement transaction hashes.
        :raise UnsupportedVaultSimulation:
            If the provider is not Anvil, the ticket is unsupported, or the
            settlement does not make the request claimable.
        """
        if not is_anvil(self.web3):
            raise UnsupportedVaultSimulation("Lagoon force_settle() requires an Anvil provider")
        if ticket is None:
            raise UnsupportedVaultSimulation("Lagoon force_settle() requires an async request ticket")

        if isinstance(ticket, ERC7540DepositTicket):
            status_before = self.get_deposit_request_status(ticket)
        elif isinstance(ticket, ERC7540RedemptionTicket):
            status_before = self.get_redemption_request_status(ticket)
        else:
            raise UnsupportedVaultSimulation(f"Unsupported Lagoon ticket type: {type(ticket)}")

        from eth_defi.erc_4626.vault_protocol.lagoon.testing import force_lagoon_settle

        valuation_manager = self.vault.valuation_manager
        safe_address = self.vault.safe_address
        make_anvil_custom_rpc_request(self.web3, "anvil_impersonateAccount", [valuation_manager])
        make_anvil_custom_rpc_request(self.web3, "anvil_setBalance", [valuation_manager, hex(10**18)])
        make_anvil_custom_rpc_request(self.web3, "anvil_impersonateAccount", [safe_address])
        make_anvil_custom_rpc_request(self.web3, "anvil_setBalance", [safe_address, hex(10**18)])
        tx_hashes = force_lagoon_settle(
            self.vault,
            valuation_manager,
            settlement_manager=safe_address,
        )

        if isinstance(ticket, ERC7540DepositTicket):
            status_after = self.get_deposit_request_status(ticket)
        else:
            status_after = self.get_redemption_request_status(ticket)

        if status_after is not AsyncVaultRequestStatus.claimable:
            raise UnsupportedVaultSimulation(f"Lagoon settlement did not make {type(ticket).__name__} claimable: {status_before.value} -> {status_after.value}")

        return VaultForcedSettlementResult(
            ticket=ticket,
            settlement_required=True,
            status_before=status_before,
            status_after=status_after,
            transaction_hashes=tx_hashes,
        )

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
        raise VaultFlowUnavailable(
            "Lagoon deposit account is not allowed",
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
