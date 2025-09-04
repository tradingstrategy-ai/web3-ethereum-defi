"""Deposit/redemption flow for ERC-7540 vaults."""

from dataclasses import dataclass
from pprint import pformat
from typing import cast

from eth_defi.event_reader.conversion import convert_bytes32_to_uint

from eth_defi.vault.deposit_redeem import DepositTicket, CannotParseRedemptionTransaction, VaultDepositManager
from eth_defi.vault.deposit_redeem import DepositRequest, RedemptionRequest, RedemptionTicket

import datetime
from decimal import Decimal

from web3.contract.contract import ContractFunction
from eth_typing import HexAddress
from hexbytes import HexBytes
from web3._utils.events import EventLogErrorFlags


@dataclass(slots=True)
class ERC7540DepositTicket(DepositTicket):
    """Asynchronous deposit request for ERC-7540 vaults."""

    #: Lagoon deposit request ID
    request_id: int

    # TODO
    # referral: HexAddress


class ERC7540DepositRequest(DepositRequest):
    """Asynchronous deposit request for ERC-7540 vaults."""

    def parse_deposit_transaction(self, tx_hashes: list[HexBytes]) -> ERC7540DepositTicket:
        """Parse the transaction receipt to get the actual shares redeemed.

        - Assumes only one redemption request per vault per transaction

        - Most throw an

        :raise CannotParseRedemptionTransaction:
            If we did not know how to parse the transaction
        """

        from eth_defi.lagoon.vault import LagoonVault
        from eth_defi.lagoon.vault import LagoonVersion

        tx_hash = tx_hashes[-1]

        receipt = self.vault.web3.eth.get_transaction_receipt(tx_hash)
        assert receipt is not None, f"Transaction is not yet mined: {tx_hash.hex()}"

        vault = cast(LagoonVault, self.vault)

        logs = receipt["logs"]

        if vault.version == LagoonVersion.legacy:
            # Lagoon changed Referral event signature?
            # https://basescan.org/address/0x45b6969152a186bafc524048f36a160fac096d50#code
            referral_log = None
            for log in logs:
                if log["topics"][0].hex() == "bb58420bb8ce44e11b84e214cc0de10ce5e7c24d0355b2815c3d758b514cae72":
                    referral_log = log

            assert referral_log, f"Cannot find Referral event in logs: {logs} at {tx_hash.hex()}, receipt: {pformat(receipt)}"
            topics = referral_log["topics"]
            # event Referral(address indexed referral, address indexed owner, uint256 indexed requestId, uint256 assets);
            request_id = convert_bytes32_to_uint(topics[-1])

        else:
            logs = vault.vault_contract.events.DepositRequested().process_receipt(receipt, errors=EventLogErrorFlags.Ignore)
            if len(logs) != 1:
                raise CannotParseRedemptionTransaction(f"Expected exactly one DepositRequested event, got logs: {logs} at {tx_hash.hex()}")

            log = logs[0]
            request_id = log["args"]["requestId"]

        return ERC7540DepositTicket(
            vault_address=vault.address,
            owner=self.owner,
            to=self.to,
            raw_amount=self.raw_amount,
            tx_hash=tx_hashes[-1],
            request_id=request_id,
        )


@dataclass(slots=True)
class ERC7540RedemptionTicket(RedemptionTicket):
    """Asynchronous deposit request for ERC-7540 vaults."""

    request_id: int


class ERC7540RedemptionRequest(RedemptionRequest):
    """Synchronous deposit request for ERC-7540 vaults."""

    def parse_redeem_transaction(self, tx_hashes: list[HexBytes]) -> RedemptionTicket:
        from eth_defi.lagoon.vault import LagoonVault
        from eth_defi.lagoon.vault import LagoonVersion

        tx_hash = tx_hashes[-1]

        receipt = self.vault.web3.eth.get_transaction_receipt(tx_hash)
        assert receipt is not None, f"Transaction is not yet mined: {tx_hash.hex()}"

        vault = cast(LagoonVault, self.vault)

        logs = vault.vault_contract.events.RedeemRequest().process_receipt(receipt, errors=EventLogErrorFlags.Discard)

        if len(logs) != 1:
            raise CannotParseRedemptionTransaction(f"Expected exactly one DepositRequested event, got logs: {logs} at {tx_hash.hex()}")

        log = logs[0]
        request_id = log["args"]["requestId"]

        return ERC7540RedemptionTicket(
            vault_address=vault.address,
            owner=self.owner,
            to=self.to,
            raw_shares=self.raw_shares,
            tx_hash=tx_hashes[-1],
            request_id=request_id,
        )


class ERC7540DepositManager(VaultDepositManager):
    """ERC-7540 async deposit/redeem flow.

    - Currently coded for Lagoon, but should work with any vault.

    Example:

    .. code-block:: python


    """

    def __init__(self, vault: "eth_defi.erc_7540.vault.ERC7540Vault"):
        from eth_defi.lagoon.vault import LagoonVault

        assert isinstance(vault, LagoonVault), f"Got {type(vault)}"
        self.vault = vault

    def create_deposit_request(
        self,
        owner: HexAddress,
        to: HexAddress = None,
        amount: Decimal = None,
        raw_amount: int = None,
        check_max_deposit=True,
        check_enough_token=True,
    ) -> ERC7540DepositRequest:
        assert not to, f"Unsupported to={to}"

        if not raw_amount:
            raw_amount = self.vault.denomination_token.convert_to_raw(amount)

        func = self.vault.request_deposit(
            owner,
            raw_amount,
        )

        return ERC7540DepositRequest(
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
    ) -> ERC7540RedemptionRequest:
        """Start the process to get shares to money"""
        assert not raw_shares, f"Unsupported raw_shares={raw_shares}"
        assert not to, f"Unsupported to={to}"

        if not raw_shares:
            raw_shares = self.vault.share_token.convert_to_raw(shares)

        func = self.vault.request_redeem(
            owner,
            raw_shares,
        )
        return ERC7540RedemptionRequest(
            vault=self.vault,
            owner=owner,
            to=owner,
            funcs=[func],
            shares=shares,
            raw_shares=raw_shares,
        )

    def finish_deposit(
        self,
        deposit_ticket: DepositTicket,
    ) -> ContractFunction:
        """Return bound call to claim our shares"""
        return self.vault.vault_contract.functions.deposit(
            deposit_ticket.raw_amount,
            deposit_ticket.to,
            deposit_ticket.owner,
        )

    def can_finish_deposit(
        self,
        deposit_ticket: ERC7540DepositTicket,
    ):
        """Check if our ticket is ready do finish.

        - Function signature: claimableDepositRequest(uint256 requestId, address controller)
        - If the returned value is > 0, the request is settled and claimable.
        """
        assets = self.vault.vault_contract.functions.claimableDepositRequest(
            deposit_ticket.request_id,
            deposit_ticket.owner,
        ).call()
        return assets > 0

    def can_finish_redeem(
        self,
        redemption_ticket: ERC7540RedemptionTicket,
    ):
        assets = self.vault.vault_contract.functions.claimableRedeemRequest(
            redemption_ticket.request_id,
            redemption_ticket.owner,
        ).call()
        return assets > 0

    def can_create_redemption_request(self, owner: HexAddress) -> bool:
        return True

    def has_synchronous_deposit(self) -> bool:
        """Does this vault support synchronous deposits?

        - E.g. ERC-7540 vaults
        """
        return False

    def has_synchronous_redemption(self) -> bool:
        """Does this vault support synchronous deposits?

        - E.g. ERC-7540 vaults
        """
        return False

    def estimate_redemption_delay(self) -> datetime.timedelta:
        return datetime.timedelta(seconds=0)

    def get_redemption_delay_over(self, address: HexAddress | str) -> datetime.datetime:
        return datetime.datetime(1970, 1, 1)

    def is_redemption_in_progress(self, owner: HexAddress) -> bool:
        raw_amount = self.vault.vault_contract.functions.pendingRedeemRequest(0, owner).call()
        return raw_amount > 0

    def is_deposit_in_progress(self, owner: HexAddress) -> bool:
        """Check pending ERC-7540 request.

        - To check if an address has an unsettled deposit in progress on an ERC-7540 contract without knowing the specific request ID, query the pendingDepositRequest view function from the contract's interface (IERC7540Vault) using a request ID of 0. According to the ERC-7540 specification, passing requestId=0 aggregates the pending deposit amounts across all requests for the given controller (address), returning the total pending assets as a uint256. A value greater than 0 indicates one or more unsettled deposits in progress that have not yet been fulfilled by the vault operator.
        """
        raw_amount = self.vault.vault_contract.functions.pendingDepositRequest(0, owner).call()
        return raw_amount > 0

    def finish_redemption(
        self,
        redemption_ticket: RedemptionTicket,
    ) -> ContractFunction:
        return self.vault.vault_contract.functions.redeem(
            redemption_ticket.raw_shares,
            redemption_ticket.to,
            redemption_ticket.owner,
        )
