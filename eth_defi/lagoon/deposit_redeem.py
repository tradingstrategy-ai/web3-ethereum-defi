"""Deposit/redemption flow for ERC-7540 vaults."""

from dataclasses import dataclass
from pprint import pformat
from typing import cast

from eth_defi.abi import get_topic_signature_from_event, ZERO_ADDRESS_STR
from eth_defi.event_reader.conversion import convert_bytes32_to_uint, convert_bytes32_to_address
from eth_defi.timestamp import get_block_timestamp
from eth_typing import HexAddress, HexStr

from eth_defi.vault.deposit_redeem import DepositTicket, CannotParseRedemptionTransaction, VaultDepositManager, DepositRedeemEventAnalysis, DepositRedeemEventFailure
from eth_defi.vault.deposit_redeem import DepositRequest, RedemptionRequest, RedemptionTicket

import datetime
from decimal import Decimal

from web3.contract.contract import ContractFunction
from eth_typing import HexAddress, BlockIdentifier
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
                # There may or may not be 0x prefix here because web3.py madness
                if log["topics"][0].hex().endswith("bb58420bb8ce44e11b84e214cc0de10ce5e7c24d0355b2815c3d758b514cae72"):
                    referral_log = log

            assert referral_log, f"Cannot find Referral event in logs: {logs} at {tx_hash}, receipt: {pformat(receipt)} for vault {vault}, version {vault.version.value}"
            topics = referral_log["topics"]
            # event Referral(address indexed referral, address indexed owner, uint256 indexed requestId, uint256 assets);
            request_id = convert_bytes32_to_uint(topics[-1])

        else:
            logs = vault.vault_contract.events.DepositRequested().process_receipt(receipt, errors=EventLogErrorFlags.Discard)
            if len(logs) != 1:
                raise CannotParseRedemptionTransaction(f"Expected exactly one DepositRequested event, got logs: {logs} at {tx_hash.hex()}")

            log = logs[0]
            request_id = log["args"]["requestId"]

        web3 = self.vault.web3
        tx = web3.eth.get_transaction(tx_hash)

        block_number = tx["blockNumber"]
        block_timestamp = get_block_timestamp(web3, block_number)
        gas_used = receipt["gasUsed"]

        return ERC7540DepositTicket(
            vault_address=vault.address,
            owner=self.owner,
            to=self.to,
            raw_amount=self.raw_amount,
            tx_hash=HexBytes(tx_hashes[-1]),
            request_id=request_id,
            gas_used=gas_used,
            block_number=block_number,
            block_timestamp=block_timestamp,
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

        # TODO: check_max_deposit
        # TODO: check_enough_token

        if not raw_amount:
            raw_amount = self.vault.denomination_token.convert_to_raw(amount)

        func = self.vault.vault_contract.functions.requestDeposit(
            raw_amount,
            owner,
            owner,
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

    def can_create_deposit_request(self, owner: HexAddress) -> bool:
        return True

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

    def estimate_deposit(self, owner: HexAddress, amount: Decimal, block_identifier: BlockIdentifier = "latest") -> Decimal:
        raw_amount = self.vault.denomination_token.convert_to_raw(amount)
        raw_shares = self.vault.vault_contract.functions.convertToShares(raw_amount).call(block_identifier=block_identifier)
        return self.vault.share_token.convert_to_decimals(raw_shares)

    def estimate_redeem(self, owner: HexAddress, shares: Decimal, block_identifier: BlockIdentifier = "latest") -> Decimal:
        raw_shares = self.vault.share_token.convert_to_raw(shares)
        raw_amount = self.vault.vault_contract.functions.convertToAssets(raw_shares).call(block_identifier=block_identifier)
        return self.vault.denomination_token.convert_to_decimals(raw_amount)

    def analyse_deposit(
        self,
        claim_tx_hash: HexBytes | str,
        deposit_ticket: DepositTicket | None,
    ) -> DepositRedeemEventAnalysis | DepositRedeemEventFailure:
        tx_hash = claim_tx_hash
        assert isinstance(tx_hash, (HexBytes, str)), f"Got {type(claim_tx_hash)}"

        assert deposit_ticket is not None, "DepositTicket must be given to analyse multi stage deposit"

        vault = self.vault
        web3 = self.web3

        receipt = web3.eth.get_transaction_receipt(tx_hash)

        if receipt["status"] != 1:
            return DepositRedeemEventFailure(tx_hash=tx_hash, revert_reason=receipt["revert_"])

        tx = web3.eth.get_transaction(tx_hash)

        # function _deposit(uint256 assets, address receiver, address controller) internal virtual returns (uint256 shares) {
        # emit Deposit(controller, receiver, assets, shares);

        # Looks like ERC-7545 does not have standard events for this?
        # We picked up from Lagoon.
        # Deposit (index_topic_1 address sender, index_topic_2 address owner, uint256 assets, uint256 shares)
        deposit_signatures: set[HexStr] = {
            # Lagoon 0.5
            get_topic_signature_from_event(vault.vault_contract.events.Deposit),
            # Some legacy version?
            # See test_erc_7540_deposit_722_capital
            "0xdcbc1c05240f31ff3ad067ef1ee35ce4997762752e3a095284754544f4c709d7",
        }

        deposit_log = None
        logs = receipt["logs"]
        for log in receipt["logs"]:
            sig = log["topics"][0].hex()
            if not sig.startswith("0x"):
                sig = "0x" + sig

            if sig in deposit_signatures:
                deposit_log = log
                break

        if deposit_log is None:
            raise RuntimeError(f"Expected exactly one DepositRequested event, got logs: {logs} at {tx_hash.hex()}, our signatures are {deposit_signatures}")

        raw_amount = convert_bytes32_to_uint(deposit_log["data"][0:32])
        raw_share_count = convert_bytes32_to_uint(deposit_log["data"][32:64])

        return DepositRedeemEventAnalysis(
            from_=convert_bytes32_to_address(deposit_log["topics"][1]),
            to=convert_bytes32_to_address(deposit_log["topics"][2]),
            share_count=vault.share_token.convert_to_decimals(raw_share_count),
            denomination_amount=vault.denomination_token.convert_to_decimals(raw_amount),
            tx_hash=tx_hash,
            block_number=tx["blockNumber"],
            block_timestamp=get_block_timestamp(web3, tx["blockNumber"]),
        )

    def analyse_redemption(
        self,
        claim_tx_hash: HexBytes | str,
        redemption_ticket: RedemptionTicket | None,
    ) -> DepositRedeemEventAnalysis | DepositRedeemEventFailure:
        tx_hash = claim_tx_hash
        assert isinstance(tx_hash, (HexBytes, str)), f"Got {type(claim_tx_hash)}"

        assert redemption_ticket is not None, "RedemptionTicket must be given to analyse multi stage deposit"

        vault = self.vault
        web3 = self.web3

        receipt = web3.eth.get_transaction_receipt(tx_hash)

        if receipt["status"] != 1:
            return DepositRedeemEventFailure(tx_hash=tx_hash, revert_reason=receipt["revert_"])

        tx = web3.eth.get_transaction(tx_hash)

        # Looks like ERC-7545 does not have standard events for this?
        # We picked up from Lagoon.
        deposit_signatures: set[HexStr] = {
            # Lagoon 0.5
            # emit Withdraw(msg.sender, receiver, controller, assets, shares);
            get_topic_signature_from_event(vault.vault_contract.events.Withdraw),
            # Some legacy version?
            # See test_erc_7540_deposit_722_capital
            # Withdraw (index_topic_1 address caller, index_topic_2 address receiver, index_topic_3 address owner, uint256 assets, uint256 shares)View Source
            "0xfbde797d201c681b91056529119e0b02407c7bb96a4a2c75c01fc9667232c8db",
        }

        deposit_log = None
        logs = receipt["logs"]

        for log in receipt["logs"]:
            sig = log["topics"][0].hex()
            if not sig.startswith("0x"):
                sig = "0x" + sig

            if sig in deposit_signatures:
                deposit_log = log
                break

        if deposit_log is None:
            raise RuntimeError(f"Expected exactly one DepositRequested event, got logs: {logs} at {tx_hash.hex()}, our signatures are {deposit_signatures}")

        raw_amount = convert_bytes32_to_uint(deposit_log["data"][0:32])
        raw_share_count = convert_bytes32_to_uint(deposit_log["data"][32:64])

        return DepositRedeemEventAnalysis(
            from_=convert_bytes32_to_address(deposit_log["topics"][2]),
            to=convert_bytes32_to_address(deposit_log["topics"][3]),
            share_count=vault.share_token.convert_to_decimals(raw_share_count),
            denomination_amount=vault.denomination_token.convert_to_decimals(raw_amount),
            tx_hash=tx_hash,
            block_number=tx["blockNumber"],
            block_timestamp=get_block_timestamp(web3, tx["blockNumber"]),
        )
