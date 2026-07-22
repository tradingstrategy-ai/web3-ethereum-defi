"""ERC-4626 deposit and redeem requests."""

import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from eth_typing import BlockIdentifier, HexAddress
from hexbytes import HexBytes
from web3.contract.contract import ContractFunction

from eth_defi.erc_4626.analysis import analyse_4626_flow_transaction
from eth_defi.erc_4626.estimate import estimate_4626_deposit, estimate_4626_redeem
from eth_defi.erc_4626.flow import deposit_4626, redeem_4626
from eth_defi.timestamp import get_block_timestamp
from eth_defi.trade import TradeFail, TradeSuccess
from eth_defi.vault.deposit_redeem import DepositRedeemEventAnalysis, DepositRedeemEventFailure, DepositRequest, DepositTicket, RedemptionRequest, RedemptionTicket, VaultDepositManager

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
    """Standard synchronous ERC-4626 deposit and redemption flow.

    **Supported simulation path**

    Standard ERC-4626 ``deposit`` and ``redeem`` calls complete in their
    originating transaction.  :meth:`force_settle` therefore accepts
    ``None`` and performs the Anvil-validated shared no-op.

    **Known limitations**

    This manager is suitable only for vaults whose selected asset uses the
    standard ERC-4626 entry points.  Queued, delegated, multi-asset and
    protocol-specific settlement flows must provide a specialised manager.
    """

    def __init__(self, vault: "ERC4626Vault"):
        from eth_defi.erc_4626.vault import ERC4626Vault

        assert isinstance(vault, ERC4626Vault), f"Got {type(vault)}"
        self.vault = vault

    def create_deposit_request(
        self,
        owner: HexAddress,
        to: HexAddress = None,
        amount: Decimal = None,
        raw_amount: int = None,
        check_max_deposit=True,
        check_enough_token=True,
    ) -> ERC4626DepositRequest:
        if not raw_amount:
            assert self.vault.denomination_token is not None, "Vault denomination token data missing: likely flaky RPC"
            raw_amount = self.vault.denomination_token.convert_to_raw(amount)

        func = deposit_4626(
            self.vault,
            owner,
            raw_amount=raw_amount,
            check_max_deposit=check_max_deposit,
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
    ) -> ERC4626RedemptionRequest:
        assert raw_shares or shares, "Either raw_shares or shares must be supplied"
        assert not to, f"Unsupported to={to}"

        if not raw_shares:
            raw_shares = self.vault.share_token.convert_to_raw(shares)

        func = redeem_4626(
            self.vault,
            owner,
            raw_amount=raw_shares,
            check_enough_token=True,
            check_max_redeem=True,
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
