"""ERC-4626 deposit and redeem requests."""

import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from eth_typing import BlockIdentifier, HexAddress
from hexbytes import HexBytes
from web3.contract.contract import ContractFunction
from web3.exceptions import ABIFunctionNotFound, BadFunctionCallOutput, ContractLogicError

from eth_defi.erc_4626.analysis import analyse_4626_flow_transaction
from eth_defi.erc_4626.estimate import estimate_4626_deposit, estimate_4626_redeem
from eth_defi.erc_4626.flow import deposit_4626, redeem_4626
from eth_defi.timestamp import get_block_timestamp
from eth_defi.trade import TradeFail, TradeSuccess
from eth_defi.vault.deposit_redeem import DepositRedeemEventAnalysis, DepositRedeemEventFailure, DepositRequest, DepositTicket, RedemptionRequest, RedemptionTicket, VaultDepositManager, VaultFlowUnavailable

if TYPE_CHECKING:
    from eth_defi.erc_4626.vault import ERC4626Vault


class ERC4626DepositTicket(DepositRequest):
    """Synchronous deposit request for ERC-4626 vaults.

    - No-op as requests are synchronous
    """


class ERC4626DepositRequest(DepositRequest):
    """Synchronous deposit request for ERC-4626 vaults."""


class ERC4626RedemptionTicket(RedemptionTicket):
    """Synchronous deposit request for ERC-4626 vaults.

    - No-op as requests are synchronous
    """


class ERC4626RedemptionRequest(RedemptionRequest):
    """Synchronous deposit request for ERC-4626 vaults."""


class ERC4626DepositManager(VaultDepositManager):
    """Standard synchronous `ERC-4626 <https://eips.ethereum.org/EIPS/eip-4626>`__ deposit and redemption flow.

    Subclasses that change the emitted call surface must follow
    :file:`eth_defi/erc_4626/README-vault-protocol-support.md`, including
    GuardV0 support and guarded Anvil-fork coverage for every lifecycle call.

    Generic manager for plain ERC-4626 vaults where both deposit and redemption
    complete atomically in a single transaction. It is the default manager for
    vaults exposing the standard entry points; queued, delegated, multi-asset
    or otherwise protocol-specific vaults require a specialised subclass.

    **Deposit process**

    Synchronous. The owner first ``approve()``s the ERC-20 denomination token to
    the vault (the default :meth:`get_deposit_approval_target`), then
    :meth:`create_deposit_request` builds a single ERC-4626 ``deposit`` call via
    :func:`eth_defi.erc_4626.flow.deposit_4626`. That one transaction pulls the
    assets and mints shares to the receiver in the same call, so there is no
    request/settle/claim split — :meth:`can_finish_deposit` is always ``True``
    and :meth:`finish_deposit` raises (nothing to settle). The receiver is
    always ``owner`` (a separate ``to`` is not supported here). Capacity is
    preflighted through :meth:`fetch_depositable_raw_assets`, which reads the
    standard ``maxDeposit()`` (a zero result is treated as "no limit exposed").

    **Redemption process**

    Synchronous. :meth:`create_redemption_request` builds a single ERC-4626
    ``redeem`` call via :func:`eth_defi.erc_4626.flow.redeem_4626`, burning the
    shares and returning denomination tokens to ``owner`` in the same
    transaction. There is no ticket to track and no separate claim step:
    :meth:`can_finish_redeem` is always ``True`` and :meth:`finish_redemption`
    raises. A separate ``to`` receiver is not supported.

    **Queues and settlement**

    None (synchronous). There is no pending-request queue; each request
    transaction is its own settlement. :meth:`is_deposit_in_progress` and
    :meth:`is_redemption_in_progress` always return ``False``.

    **Lockups and cooldowns**

    None. :meth:`estimate_redemption_delay` returns zero and
    :meth:`get_redemption_delay_over` returns the Unix epoch sentinel;
    :meth:`can_create_deposit_request` and :meth:`can_create_redemption_request`
    always return ``True``.

    **Whitelisting / access control**

    :meth:`create_deposit_request` calls the shared
    :meth:`~eth_defi.vault.deposit_redeem.VaultDepositManager.check_deposit_whitelist`
    preflight, so a vault that exposes a queryable whitelist policy raises
    :class:`~eth_defi.vault.deposit_redeem.WhitelistingRequired` for an
    unpermitted owner. Vaults without a determinable policy are treated as
    permissionless and any real denial surfaces as an onchain revert.

    **Anvil settlement (force_settle)**

    The standard ``deposit`` and ``redeem`` calls complete in their originating
    transaction, so the inherited :meth:`force_settle` accepts ``None`` and
    performs the Anvil-validated shared no-op.
    """

    def __init__(self, vault: "ERC4626Vault"):
        from eth_defi.erc_4626.vault import ERC4626Vault

        assert isinstance(vault, ERC4626Vault), f"Got {type(vault)}"
        self.vault = vault

    def fetch_depositable_raw_assets(self, owner: HexAddress) -> int | None:
        """Read the vault's current raw deposit limit for an owner.

        Overridable deposit-limit hook. The base implementation reads the
        standard ERC-4626 ``maxDeposit()``. Multi-asset or non-standard vaults
        that do not implement ``maxDeposit`` (for example Upshift's multi-asset
        vault) override this to answer from their own limit reader, so the
        deposit preflight does not depend on the ERC-4626 method being present.

        :param owner:
            Account the deposit limit is queried for.
        :return:
            Raw deposit limit, or ``None`` when the vault exposes no limit. A
            zero ``maxDeposit`` is treated as "no limit exposed" (EIP-4626 is
            not universally honoured), consistent with
            :func:`eth_defi.erc_4626.flow.deposit_4626`.
        :raise VaultFlowUnavailable:
            When the vault does not expose a readable ERC-4626 ``maxDeposit``
            and no protocol-specific override is provided, instead of leaking a
            raw web3 ABI/read error.
        """
        try:
            max_deposit = self.vault.vault_contract.functions.maxDeposit(owner).call()
        except (ABIFunctionNotFound, BadFunctionCallOutput, ContractLogicError, ValueError) as e:
            raise VaultFlowUnavailable(
                f"Vault {self.vault.address} on chain {self.vault.chain_id} does not expose a readable ERC-4626 maxDeposit(); a protocol-specific deposit manager is required",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                caller=owner,
                direction="deposit",
                phase="preflight",
            ) from e

        return None if max_deposit == 0 else max_deposit

    def create_deposit_request(
        self,
        owner: HexAddress,
        to: HexAddress = None,
        amount: Decimal = None,
        raw_amount: int = None,
        check_max_deposit=True,
        check_enough_token=True,
    ) -> ERC4626DepositRequest:
        # Reject a whitelisted vault before broadcast when the owner is not
        # permitted; a no-op for permissionless vaults and adapters whose
        # whitelist policy cannot be determined.
        self.check_deposit_whitelist(owner)

        if not raw_amount:
            assert self.vault.denomination_token is not None, "Vault denomination token data missing: likely flaky RPC"
            raw_amount = self.vault.denomination_token.convert_to_raw(amount)

        if check_max_deposit:
            # Preflight deposit capacity through the overridable hook so
            # non-standard vaults can answer without ERC-4626 maxDeposit.
            # deposit_4626() is then called with check_max_deposit=False to
            # avoid re-reading the limit.
            limit = self.fetch_depositable_raw_assets(owner)
            if limit is not None and raw_amount > limit:
                # "deposit limit" (not "maxDeposit") because an overridden hook
                # may source the limit from a protocol reader rather than the
                # ERC-4626 maxDeposit method.
                raise VaultFlowUnavailable(
                    f"Deposit capacity exceeded for vault {self.vault.address} on chain {self.vault.chain_id}: requested {raw_amount}, deposit limit {limit}",
                    protocol=self.vault.get_protocol_name(),
                    vault_address=self.vault.address,
                    caller=owner,
                    direction="deposit",
                    phase="preflight",
                    requested_raw_amount=raw_amount,
                    available_raw_amount=limit,
                )

        func = deposit_4626(
            self.vault,
            owner,
            raw_amount=raw_amount,
            check_max_deposit=False,
            check_enough_token=check_enough_token,
        )
        return ERC4626DepositRequest(
            vault=self.vault,
            owner=owner,
            to=owner,
            funcs=[func],
            amount=amount,
            raw_amount=raw_amount,
        )

    def create_redemption_request(
        self,
        owner: HexAddress,
        to: HexAddress = None,
        shares: Decimal = None,
        raw_shares: int = None,
        check_max_deposit=True,
        check_enough_token=True,
        check_max_redeem=True,
    ) -> ERC4626RedemptionRequest:
        assert raw_shares or shares, "Either raw_shares or shares must be supplied"
        assert not to, f"Unsupported to={to}"

        if not raw_shares:
            raw_shares = self.vault.share_token.convert_to_raw(shares)

        func = redeem_4626(
            self.vault,
            owner,
            raw_amount=raw_shares,
            check_enough_token=check_enough_token,
            check_max_redeem=check_max_redeem,
        )
        return ERC4626RedemptionRequest(
            vault=self.vault,
            owner=owner,
            to=owner,
            funcs=[func],
            shares=shares,
            raw_shares=raw_shares,
        )

    def can_finish_deposit(
        self,
        deposit_ticket: ERC4626DepositTicket,
    ):
        """Synchronous deposits can be finished immediately."""
        return True

    def can_finish_redeem(
        self,
        redemption_ticket: ERC4626RedemptionTicket,
    ):
        """Synchronous redemptions can be finished immediately."""
        return True

    def can_create_deposit_request(self, owner: HexAddress) -> bool:
        return True

    def can_create_redemption_request(self, owner: HexAddress) -> bool:
        return True

    def has_synchronous_deposit(self) -> bool:
        """Does this vault support synchronous deposits?

        - E.g. ERC-4626 vaults
        """
        return True

    def has_synchronous_redemption(self) -> bool:
        """Does this vault support synchronous deposits?

        - E.g. ERC-4626 vaults
        """
        return True

    def estimate_redemption_delay(self) -> datetime.timedelta:
        return datetime.timedelta(seconds=0)

    def get_redemption_delay_over(self, address: HexAddress | str) -> datetime.datetime:
        return datetime.datetime(1970, 1, 1)

    def is_redemption_in_progress(self, owner: HexAddress) -> bool:
        return False

    def is_deposit_in_progress(self, owner: HexAddress) -> bool:
        return False

    def finish_redemption(
        self,
        redemption_ticket: RedemptionTicket,
    ) -> ContractFunction:
        raise NotImplementedError("Redemptions are synchronous, nothing to settle")

    def finish_deposit(
        self,
        deposit_ticket: DepositTicket,
    ) -> ContractFunction:
        raise NotImplementedError("Deposits are synchronous, nothing to settle")

    def estimate_deposit(self, owner: HexAddress, amount: Decimal, block_identifier: BlockIdentifier = "latest") -> Decimal:
        return estimate_4626_deposit(self.vault, amount, block_identifier=block_identifier)

    def estimate_redeem(self, owner: HexAddress, shares: Decimal, block_identifier: BlockIdentifier = "latest") -> Decimal:
        return estimate_4626_redeem(self.vault, owner, shares, block_identifier=block_identifier)

    def analyse_deposit(
        self,
        claim_tx_hash: HexBytes | str,
        deposit_ticket: DepositTicket | None,
    ) -> DepositRedeemEventAnalysis | DepositRedeemEventFailure:
        """Analyse a mined ERC-4626 deposit or guarded SimpleVault wrapper.

        A ticket identifies an expected SimpleVault wrapper by address. The
        event analyser still filters events by the underlying vault address.

        :param claim_tx_hash:
            Mined deposit transaction hash.
        :param deposit_ticket:
            Optional ticket whose owner identifies a guarded wrapper.
        :return:
            Decoded executed deposit quantities or a revert description.
        """
        vault = self.vault
        tx = vault.web3.eth.get_transaction(claim_tx_hash)
        receipt = vault.web3.eth.get_transaction_receipt(claim_tx_hash)
        guarded_call = deposit_ticket is not None and tx["to"].lower() == deposit_ticket.owner.lower()
        analysis = analyse_4626_flow_transaction(
            vault=vault,
            tx_hash=claim_tx_hash,
            tx_receipt=receipt,
            direction="deposit",
            hot_wallet=not guarded_call,
        )

        match analysis:
            case TradeSuccess():
                return DepositRedeemEventAnalysis(
                    from_=None,  # TODO
                    to=None,  # TODO
                    tx_hash=HexBytes(claim_tx_hash),
                    block_number=tx["blockNumber"],
                    block_timestamp=get_block_timestamp(vault.web3, tx["blockNumber"]),
                    share_count=vault.share_token.convert_to_decimals(analysis.amount_out),
                    denomination_amount=vault.denomination_token.convert_to_decimals(analysis.amount_in),
                )
            case TradeFail():
                return DepositRedeemEventFailure(
                    tx_hash=HexBytes(claim_tx_hash),
                    revert_reason=analysis.revert_reason,
                    protocol=vault.get_protocol_name(),
                    vault_address=vault.address,
                    direction="deposit",
                    phase="transaction",
                    receipt_status=int(receipt["status"]),
                )
            case _:
                raise NotImplementedError(f"Unknown {type(analysis)}")

    def analyse_redemption(
        self,
        claim_tx_hash: HexBytes | str,
        redemption_ticket: RedemptionTicket | None,
    ) -> DepositRedeemEventAnalysis | DepositRedeemEventFailure:
        """Analyse a mined ERC-4626 redemption or guarded SimpleVault wrapper.

        A ticket identifies the wrapper only for the transaction-target check;
        the decoded ``Withdraw`` event must still originate from this vault.

        :param claim_tx_hash:
            Mined redemption transaction hash.
        :param redemption_ticket:
            Optional ticket whose owner identifies a guarded wrapper.
        :return:
            Decoded executed redemption quantities or a revert description.
        """
        vault = self.vault
        tx = vault.web3.eth.get_transaction(claim_tx_hash)
        receipt = vault.web3.eth.get_transaction_receipt(claim_tx_hash)
        guarded_call = redemption_ticket is not None and tx["to"].lower() == redemption_ticket.owner.lower()
        analysis = analyse_4626_flow_transaction(
            vault=vault,
            tx_hash=claim_tx_hash,
            tx_receipt=receipt,
            direction="redeem",
            hot_wallet=not guarded_call,
        )

        match analysis:
            case TradeSuccess():
                return DepositRedeemEventAnalysis(
                    from_=None,
                    to=None,
                    tx_hash=HexBytes(claim_tx_hash),
                    block_number=tx["blockNumber"],
                    block_timestamp=get_block_timestamp(vault.web3, tx["blockNumber"]),
                    share_count=vault.share_token.convert_to_decimals(analysis.amount_in),
                    denomination_amount=vault.denomination_token.convert_to_decimals(analysis.amount_out),
                )
            case TradeFail():
                return DepositRedeemEventFailure(
                    tx_hash=HexBytes(claim_tx_hash),
                    revert_reason=analysis.revert_reason,
                    protocol=vault.get_protocol_name(),
                    vault_address=vault.address,
                    direction="redeem",
                    phase="transaction",
                    receipt_status=int(receipt["status"]),
                )
            case _:
                raise NotImplementedError(f"Unknown {type(analysis)}")
