"""Shared base class for tokenised fund vault adapters."""

# ruff: noqa: ARG002, FBT001, FBT002, PLR0917, PLR6301, RUF013

import datetime
from decimal import Decimal
from typing import NoReturn

from eth_typing import BlockIdentifier, HexAddress
from hexbytes import HexBytes
from web3.contract.contract import ContractFunction

from eth_defi.vault.base import VaultBase
from eth_defi.vault.deposit_redeem import (
    DepositRedeemEventAnalysis,
    DepositRedeemEventFailure,
    DepositRequest,
    DepositTicket,
    RedemptionRequest,
    RedemptionTicket,
    VaultDepositManager,
    VaultDepositManagerCapability,
    VaultFlowUnavailable,
    VaultForcedSettlementResult,
)
from eth_defi.vault.flag import VaultFlag

TOKENISED_FUND_FLOW_UNAVAILABLE = "Tokenised fund subscriptions and redemptions require issuer-specific permission and are not publicly executable"


class TokenisedFundDepositManager(VaultDepositManager):
    """Non-operational manager for permissioned tokenised funds.

    The manager exposes the common vault-flow interface so scanner metadata can
    distinguish a deliberately unsupported permissioned product from an
    adapter whose capability is unknown. It never constructs, settles, or
    analyses a transaction because tokenised-fund dealing requires
    issuer-specific eligibility and servicing.
    """

    def _reject(self, direction: str | None = None, phase: str = "preflight") -> NoReturn:
        """Raise the shared typed error for an unsupported fund operation.

        :param direction:
            Requested flow direction, when known.
        :param phase:
            Lifecycle phase that attempted the operation.
        :raise VaultFlowUnavailable:
            Always, because no public tokenised-fund flow is implemented.
        """
        raise VaultFlowUnavailable(
            TOKENISED_FUND_FLOW_UNAVAILABLE,
            protocol="tokenised_fund",
            direction=direction,
            phase=phase,
        )

    def force_settle(self, ticket: DepositTicket | RedemptionTicket | None) -> VaultForcedSettlementResult:
        """Reject settlement of an unsupported tokenised-fund flow."""
        self._reject(phase="settlement")

    def reclaim_deposit(self, ticket: DepositTicket) -> ContractFunction | None:
        """Reject recovery because this manager cannot create deposit tickets."""
        self._reject(direction="deposit", phase="reclaim")

    def reclaim_withdrawal(self, ticket: RedemptionTicket) -> ContractFunction | None:
        """Reject recovery because this manager cannot create redemption tickets."""
        self._reject(direction="redeem", phase="reclaim")

    def has_synchronous_deposit(self) -> bool:
        """Return false because no public deposit flow is supported."""
        return False

    def has_synchronous_redemption(self) -> bool:
        """Return false because no public redemption flow is supported."""
        return False

    def estimate_deposit(self, owner: HexAddress | None, amount: Decimal, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Reject estimation of an unsupported deposit."""
        self._reject(direction="deposit")

    def estimate_redeem(self, owner: HexAddress | None, shares: Decimal, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Reject estimation of an unsupported redemption."""
        self._reject(direction="redeem")

    def create_deposit_request(
        self,
        owner: HexAddress,
        to: HexAddress = None,
        amount: Decimal = None,
        raw_amount: int = None,
        check_max_deposit: bool = True,
        check_enough_token: bool = True,
    ) -> DepositRequest:
        """Reject creation of a public tokenised-fund deposit."""
        self._reject(direction="deposit")

    def create_redemption_request(
        self,
        owner: HexAddress,
        to: HexAddress = None,
        shares: Decimal = None,
        raw_shares: int = None,
        check_max_deposit: bool = True,
        check_enough_token: bool = True,
    ) -> RedemptionRequest:
        """Reject creation of a public tokenised-fund redemption."""
        self._reject(direction="redeem")

    def is_redemption_in_progress(self, owner: HexAddress) -> bool:
        """Return false because this manager cannot create redemptions."""
        return False

    def is_deposit_in_progress(self, owner: HexAddress) -> bool:
        """Return false because this manager cannot create deposits."""
        return False

    def can_create_deposit_request(self, owner: HexAddress) -> bool:
        """Return false because public tokenised-fund deposits are unsupported."""
        return False

    def get_max_deposit(self, owner: HexAddress) -> Decimal:
        """Return zero because this manager refuses public deposits."""
        return Decimal(0)

    def can_create_redemption_request(self, owner: HexAddress) -> bool:
        """Return false because public tokenised-fund redemptions are unsupported."""
        return False

    def can_finish_redeem(self, redemption_ticket: RedemptionTicket) -> bool:
        """Return false because this manager cannot create redemptions."""
        return False

    def can_finish_deposit(self, deposit_ticket: DepositTicket) -> bool:
        """Return false because this manager cannot create deposits."""
        return False

    def finish_deposit(self, deposit_ticket: DepositTicket) -> ContractFunction:
        """Reject completion of an unsupported deposit."""
        self._reject(direction="deposit", phase="claim")

    def finish_redemption(self, redemption_ticket: RedemptionTicket) -> ContractFunction | None:
        """Reject completion of an unsupported redemption."""
        self._reject(direction="redeem", phase="claim")

    def estimate_redemption_delay(self) -> datetime.timedelta:
        """Reject delay estimation for an unsupported redemption."""
        self._reject(direction="redeem")

    def get_redemption_delay_over(self, address: HexAddress | str) -> datetime.datetime | None:
        """Return no deadline because no redemption can be created."""
        return None

    def analyse_deposit(
        self,
        claim_tx_hash: HexBytes | str,
        deposit_ticket: DepositTicket | None,
    ) -> DepositRedeemEventAnalysis | DepositRedeemEventFailure:
        """Reject analysis because this manager cannot produce deposit transactions."""
        self._reject(direction="deposit", phase="transaction")

    def analyse_redemption(
        self,
        claim_tx_hash: HexBytes | str,
        redemption_ticket: RedemptionTicket | None,
    ) -> DepositRedeemEventAnalysis | DepositRedeemEventFailure:
        """Reject analysis because this manager cannot produce redemption transactions."""
        self._reject(direction="redeem", phase="transaction")


class TokenisedFundVault(VaultBase):
    """Base class for every tokenised fund protocol adapter.

    Tokenised fund classification belongs to the adapter type instead of a
    manually maintained address list. This also covers products discovered
    dynamically from issuer registries, such as Asseto funds.
    """

    def is_whitelisted_deposit(self) -> bool:
        """Classify tokenised-fund subscriptions as permissioned.

        Tokenised-fund adapters model issuer-operated products whose
        subscriptions require investor eligibility, issuer approval, or both.
        This is a vault-wide classification: individual adapters may expose
        different KYC, allow-list, transfer-agent, or offchain settlement
        mechanisms, so :meth:`is_account_whitelisted` remains
        protocol-specific.

        :return:
            Always ``True`` because tokenised-fund deposits are permissioned.
        """
        return True

    def get_deposit_manager(self) -> TokenisedFundDepositManager:
        """Return a manager that explicitly refuses public fund operations.

        The manager gives runtime callers a typed refusal, while
        :meth:`get_deposit_manager_capability` provides the corresponding
        report metadata. Concrete issuer integrations must replace both only
        after implementing their complete permission-aware dealing lifecycle.

        :return:
            Non-operational tokenised-fund deposit manager.
        """
        return TokenisedFundDepositManager(self)

    def get_deposit_manager_capability(self) -> VaultDepositManagerCapability:
        """Report explicit lack of public deposit and redemption support.

        :return:
            A two-direction capability with both operations disabled.
        """
        return VaultDepositManagerCapability(
            can_deposit=False,
            can_redeem=False,
        )

    def get_flags(self) -> set[VaultFlag]:
        """Return vault flags including the tokenised fund classification.

        Preserve address- and protocol-specific flags supplied by the generic
        vault implementation, then add the descriptive flag used by tokenised
        fund listings.

        :return:
            A new set containing all generic flags and
            :py:data:`VaultFlag.tokenised_fund`.
        """

        return super().get_flags() | {VaultFlag.tokenised_fund}

    def get_link(self, referral: str | None = None) -> str:
        """Return the issuer's most useful public fund link.

        Tokenised-fund adapters must provide a product landing page where one
        exists, then fall back to an official announcement, curator page or
        protocol page.  A block-explorer address is technical contract
        metadata, not an investor-facing product link, and is never a valid
        fallback for these products.

        :param referral:
            Optional referral code. Tokenised-fund products currently do not
            use it.
        :return:
            An official issuer, curator or protocol URL.
        :raise NotImplementedError:
            Always. Concrete adapters must select the appropriate official
            link rather than inheriting :class:`VaultBase`'s explorer URL.
        """

        _ = self, referral
        message = "Tokenised fund adapters must define an official product, announcement, curator or protocol link"
        raise NotImplementedError(message)
