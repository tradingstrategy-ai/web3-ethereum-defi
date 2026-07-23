"""Protocol-neutral ERC-7540 deposit and redemption flows."""

import datetime
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal

import eth_abi
from eth_typing import BlockIdentifier, HexAddress, HexStr
from hexbytes import HexBytes
from web3 import Web3
from web3.contract.contract import ContractFunction
from web3.exceptions import BadFunctionCallOutput
from web3.logs import DISCARD

from eth_defi.abi import get_topic_signature_from_event
from eth_defi.erc_7540.vault import ERC7540Vault
from eth_defi.event_reader.conversion import BadAddressError, convert_bytes32_to_address, convert_bytes32_to_uint, convert_int256_bytes_to_int
from eth_defi.event_reader.multicall_batcher import EncodedCall
from eth_defi.middleware import ProbablyNodeHasNoBlock
from eth_defi.timestamp import get_block_timestamp
from eth_defi.vault.deposit_redeem import (
    AsyncVaultRequestStatus,
    CannotParseRedemptionTransaction,
    DepositRedeemEventAnalysis,
    DepositRedeemEventFailure,
    DepositRequest,
    DepositTicket,
    RedemptionRequest,
    RedemptionTicket,
    VaultDepositManager,
    VaultFlowUnavailable,
)
from eth_defi.vault.flow_events import (
    PendingVaultFlow,
    VaultFlowDirection,
    create_pending_vault_flow,
    decode_indexed_event_address,
    decode_indexed_event_uint,
    event_data_to_bytes,
    fetch_vault_flow_logs_hypersync,
    normalise_event_topic,
)


@dataclass(slots=True)
class ERC7540DepositTicket(DepositTicket):
    """Asynchronous deposit request for ERC-7540 vaults."""

    #: ERC-7540 request identifier.
    request_id: int


class ERC7540DepositRequest(DepositRequest):
    """Asynchronous deposit request for ERC-7540 vaults."""

    def parse_deposit_transaction(self, tx_hashes: list[HexBytes]) -> ERC7540DepositTicket:
        """Parse one standard ERC-7540 deposit-request transaction.

        The final receipt must contain exactly one standard
        ``DepositRequest`` event from which the request identifier is read.

        :param tx_hashes:
            Transaction hashes belonging to the request.
        :return:
            Parsed request ticket with its ERC-7540 request identifier.
        :raise CannotParseRedemptionTransaction:
            If the receipt does not contain exactly one ``DepositRequest`` event.
        """
        tx_hash = tx_hashes[-1]
        receipt = self.vault.web3.eth.get_transaction_receipt(tx_hash)
        assert receipt is not None, f"Transaction is not yet mined: {tx_hash.hex()}"

        logs = self.vault.vault_contract.events.DepositRequest().process_receipt(receipt, errors=DISCARD)
        if len(logs) != 1:
            raise CannotParseRedemptionTransaction(f"Expected exactly one DepositRequest event, got logs: {logs} at {tx_hash.hex()}")
        request_id = logs[0]["args"]["requestId"]

        web3 = self.vault.web3
        tx = web3.eth.get_transaction(tx_hash)

        block_number = tx["blockNumber"]
        block_timestamp = get_block_timestamp(web3, block_number)
        gas_used = receipt["gasUsed"]

        return ERC7540DepositTicket(
            vault_address=self.vault.address,
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
    """Asynchronous redemption request for ERC-7540 vaults."""

    #: ERC-7540 request identifier.
    request_id: int


class ERC7540RedemptionRequest(RedemptionRequest):
    """Asynchronous redemption request for ERC-7540 vaults."""

    def parse_redeem_transaction(self, tx_hashes: list[HexBytes]) -> ERC7540RedemptionTicket:
        """Parse one standard ERC-7540 redemption-request transaction.

        The final receipt must contain exactly one standard ``RedeemRequest``
        event from which the request identifier is read.

        :param tx_hashes:
            Transaction hashes belonging to the request.
        :return:
            Parsed request ticket with its ERC-7540 request identifier.
        :raise CannotParseRedemptionTransaction:
            If the receipt does not contain exactly one ``RedeemRequest`` event.
        """
        tx_hash = tx_hashes[-1]
        receipt = self.vault.web3.eth.get_transaction_receipt(tx_hash)
        assert receipt is not None, f"Transaction is not yet mined: {tx_hash.hex()}"

        logs = self.vault.vault_contract.events.RedeemRequest().process_receipt(receipt, errors=DISCARD)
        if len(logs) != 1:
            raise CannotParseRedemptionTransaction(f"Expected exactly one RedeemRequest event, got logs: {logs} at {tx_hash.hex()}")

        log = logs[0]
        request_id = log["args"]["requestId"]

        return ERC7540RedemptionTicket(
            vault_address=self.vault.address,
            owner=self.owner,
            to=self.to,
            raw_shares=self.raw_shares,
            tx_hash=tx_hashes[-1],
            request_id=request_id,
        )


class ERC7540DepositManager(VaultDepositManager):
    """Protocol-neutral ERC-7540 asynchronous deposit and redemption flow.

    **Supported simulation path**

    This manager builds standard ERC-7540 request and claim calls. Settlement
    is operator-specific, so the generic implementation deliberately has no
    Anvil settlement driver. Protocol adapters such as Lagoon may subclass the
    manager and implement :meth:`force_settle`.

    **Known limitations**

    The generic manager does not model protocol-specific access policies,
    settlement roles, partial settlements, cancellation or reclaim.

    See the canonical `ERC-7540 specification
    <https://eips.ethereum.org/EIPS/eip-7540>`__ for the request lifecycle.
    """

    #: Request wrapper used for deposits. Protocol subclasses may replace it.
    deposit_request_class = ERC7540DepositRequest

    #: Request wrapper used for redemptions. Protocol subclasses may replace it.
    redemption_request_class = ERC7540RedemptionRequest

    def __init__(self, vault: ERC7540Vault):
        """Initialise the manager for an ERC-7540 vault adapter.

        Protocol-specific subclasses may reuse this constructor after
        validating a narrower vault type.

        :param vault:
            Protocol adapter implementing the ERC-7540 vault interface.
        """
        assert isinstance(vault, ERC7540Vault), f"Got {type(vault)}"
        super().__init__(vault)

    def fetch_vault_flow_events(
        self,
        hypersync_client,
        start_block: int,
        end_block: int,
    ) -> Iterator[PendingVaultFlow]:
        """Fetch standard ERC-7540 request events using Hypersync.

        ERC-7540 vaults emit ``DepositRequest`` for deposit requests and
        ``RedeemRequest`` for redemption requests. Protocol-specific legacy
        events must be handled by a protocol manager subclass.

        :param hypersync_client:
            Configured Hypersync client for the vault chain.

        :param start_block:
            Inclusive start block.

        :param end_block:
            Inclusive end block.

        :return:
            Iterator of indexed pending vault flow events in chain order.
        """
        vault = self.vault
        deposit_topic = normalise_event_topic(get_topic_signature_from_event(vault.vault_contract.events.DepositRequest))
        redeem_topic = normalise_event_topic(get_topic_signature_from_event(vault.vault_contract.events.RedeemRequest))
        topic_map = {
            deposit_topic: VaultFlowDirection.deposit,
            redeem_topic: VaultFlowDirection.redeem,
        }

        logs = fetch_vault_flow_logs_hypersync(
            hypersync_client=hypersync_client,
            vault_address=vault.address,
            topic0_list=list(topic_map.keys()),
            start_block=start_block,
            end_block=end_block,
        )
        chain_id = self.web3.eth.chain_id
        vault_address = Web3.to_checksum_address(vault.address)

        for log in logs:
            topic0 = normalise_event_topic(log.topics[0])
            direction = topic_map.get(topic0)
            if direction == VaultFlowDirection.deposit:
                # event DepositRequest(address indexed controller, address indexed owner, uint256 indexed requestId, address sender, uint256 assets)
                request_id = decode_indexed_event_uint(log.topics[3])
                controller = Web3.to_checksum_address(decode_indexed_event_address(log.topics[1]))
                owner = Web3.to_checksum_address(decode_indexed_event_address(log.topics[2]))
                _sender, raw_assets = eth_abi.decode(["address", "uint256"], event_data_to_bytes(log.data))
                raw_assets = int(raw_assets)
                # Recovered tickets use the controller for owner/to because ERC-7540
                # claim/status calls are controller-scoped. The economic owner is
                # still exposed separately as PendingVaultFlow.owner.
                ticket = ERC7540DepositTicket(
                    vault_address=vault.address,
                    owner=controller,
                    to=controller,
                    raw_amount=raw_assets,
                    tx_hash=HexBytes(log.transaction_hash),
                    gas_used=0,
                    block_number=log.block_number,
                    block_timestamp=log.block_timestamp,
                    request_id=request_id,
                )
                yield create_pending_vault_flow(
                    chain_id=chain_id,
                    vault_address=vault_address,
                    owner=owner,
                    controller=controller,
                    direction=VaultFlowDirection.deposit,
                    status=AsyncVaultRequestStatus.pending,
                    request_id=request_id,
                    settlement_id=None,
                    raw_assets=raw_assets,
                    raw_shares=None,
                    log=log,
                    ticket_data=self.serialize_deposit_ticket(ticket),
                )
            elif direction == VaultFlowDirection.redeem:
                # event RedeemRequest(address indexed controller, address indexed owner, uint256 indexed requestId, address sender, uint256 shares)
                controller = Web3.to_checksum_address(decode_indexed_event_address(log.topics[1]))
                owner = Web3.to_checksum_address(decode_indexed_event_address(log.topics[2]))
                request_id = decode_indexed_event_uint(log.topics[3])
                _sender, raw_shares = eth_abi.decode(["address", "uint256"], event_data_to_bytes(log.data))
                raw_shares = int(raw_shares)
                ticket = ERC7540RedemptionTicket(
                    vault_address=vault.address,
                    owner=controller,
                    to=controller,
                    raw_shares=raw_shares,
                    tx_hash=HexBytes(log.transaction_hash),
                    request_id=request_id,
                )
                yield create_pending_vault_flow(
                    chain_id=chain_id,
                    vault_address=vault_address,
                    owner=owner,
                    controller=controller,
                    direction=VaultFlowDirection.redeem,
                    status=AsyncVaultRequestStatus.pending,
                    request_id=request_id,
                    settlement_id=None,
                    raw_assets=None,
                    raw_shares=raw_shares,
                    log=log,
                    ticket_data=self.serialize_redemption_ticket(ticket),
                )

    def create_deposit_request(
        self,
        owner: HexAddress,
        to: HexAddress = None,
        amount: Decimal = None,
        raw_amount: int = None,
        check_max_deposit=True,
        check_enough_token=True,
    ) -> ERC7540DepositRequest:
        """Build the standard ERC-7540 ``requestDeposit`` transaction.

        :param owner:
            Controller and owner of the request.
        :param to:
            Unsupported separate receiver; ERC-7540 requests use ``owner``.
        :param amount:
            Human-readable denomination-token amount.
        :param raw_amount:
            Raw denomination-token amount, overriding ``amount``.
        :param check_max_deposit:
            Reserved for parity with the common manager API.
        :param check_enough_token:
            Reserved for parity with the common manager API.
        :return:
            Request wrapper ready for signing and parsing.
        :raise VaultFlowUnavailable:
            If the vault's optional pause view reports that requests are paused.
        """
        assert not to, f"Unsupported to={to}"

        self._assert_deposit_request_available(owner)

        # TODO: check_max_deposit
        # TODO: check_enough_token

        if not raw_amount:
            assert self.vault.denomination_token is not None, f"Vault {self.vault.address} denomination token data missing: likely flaky RPC"
            raw_amount = self.vault.denomination_token.convert_to_raw(amount)

        func = self.vault.vault_contract.functions.requestDeposit(
            raw_amount,
            owner,
            owner,
        )

        return self.deposit_request_class(
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
        """Build the standard ERC-7540 ``requestRedeem`` transaction.

        :param owner:
            Controller and owner of the request.
        :param to:
            Unsupported separate receiver; ERC-7540 requests use ``owner``.
        :param shares:
            Human-readable vault-share amount.
        :param raw_shares:
            Raw vault-share amount, overriding ``shares``.
        :param check_max_deposit:
            Reserved for parity with the common manager API.
        :param check_enough_token:
            Whether to verify that the owner still holds the requested shares.
        :return:
            Request wrapper ready for signing and parsing.
        """
        assert not to, f"Unsupported to={to}"

        if not raw_shares:
            assert self.vault.share_token is not None, f"Vault {self.vault.address} share token data missing: likely flaky RPC"
            raw_shares = self.vault.share_token.convert_to_raw(shares)

        func = self.vault.request_redeem(
            owner,
            raw_shares,
            check_enough_token=check_enough_token,
        )
        return self.redemption_request_class(
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
        """Build the ERC-7540 transaction that claims deposit shares.

        The request must already be settled. The ticket supplies the
        controller, receiver and settled asset amount.

        :param deposit_ticket:
            Settled asynchronous deposit ticket.
        :return:
            Bound three-argument ``deposit`` claim function.
        """
        return self.vault.vault_contract.functions.deposit(
            deposit_ticket.raw_amount,
            deposit_ticket.to,
            deposit_ticket.owner,
        )

    def can_finish_deposit(
        self,
        deposit_ticket: ERC7540DepositTicket,
    ) -> bool:
        """Check whether a deposit request is ready to claim.

        The ERC-7540 ``claimableDepositRequest`` view returns a positive asset
        amount after the operator has settled the request.

        :param deposit_ticket:
            Deposit request to inspect.
        :return:
            ``True`` when the request has claimable assets.
        """
        assets = self.vault.vault_contract.functions.claimableDepositRequest(
            deposit_ticket.request_id,
            deposit_ticket.owner,
        ).call()
        return assets > 0

    def can_finish_redeem(
        self,
        redemption_ticket: ERC7540RedemptionTicket,
    ) -> bool:
        """Check whether a redemption request is ready to claim.

        The ERC-7540 ``claimableRedeemRequest`` view returns a positive share
        amount after the operator has settled the request.

        :param redemption_ticket:
            Redemption request to inspect.
        :return:
            ``True`` when the request has claimable shares.
        """
        assets = self.vault.vault_contract.functions.claimableRedeemRequest(
            redemption_ticket.request_id,
            redemption_ticket.owner,
        ).call()
        return assets > 0

    # --- Async vault lifecycle: ticket serialisation ---
    #
    # The base implementations drop ``request_id``, which ERC-7540 needs to
    # query claimableDepositRequest()/claimableRedeemRequest() after a process
    # restart, and the base reconstruct_redemption_ticket() raises
    # NotImplementedError. Override all four so the trade-executor settlement
    # retry module can persist and rebuild ERC-7540 tickets.

    def serialize_deposit_ticket(self, ticket: ERC7540DepositTicket) -> dict:
        """Serialise an ERC-7540 deposit ticket.

        The generic base ticket payload is extended with the ERC-7540 request
        identifier needed for later status checks.

        :param ticket:
            Deposit ticket to serialise.
        :return:
            Persistent ticket data including ``vault_request_id``.
        """
        data = super().serialize_deposit_ticket(ticket)
        data["vault_request_id"] = ticket.request_id
        return data

    def reconstruct_deposit_ticket(self, data: dict) -> ERC7540DepositTicket:
        """Reconstruct a deposit ticket from persistent data.

        Both the current string form and the legacy integer form of raw token
        amounts are accepted for backwards compatibility.

        :param data:
            Data produced by :py:meth:`serialize_deposit_ticket`.
        :return:
            Reconstructed ERC-7540 deposit ticket.
        """
        ts = data.get("vault_request_block_timestamp")
        return ERC7540DepositTicket(
            vault_address=data["vault_address"],
            owner=data["vault_owner"],
            to=data.get("vault_to", data["vault_owner"]),
            # int() accepts both the current string form and legacy int form
            raw_amount=int(data["vault_raw_amount"]),
            tx_hash=HexBytes(data["vault_request_tx_hash"]),
            request_id=data["vault_request_id"],
            gas_used=data.get("vault_request_gas_used", 0),
            block_number=data.get("vault_request_block_number", 0),
            block_timestamp=datetime.datetime.fromisoformat(ts) if ts else None,
        )

    def serialize_redemption_ticket(self, ticket: ERC7540RedemptionTicket) -> dict:
        """Serialise an ERC-7540 redemption ticket.

        The ERC-7540 request identifier is retained so a restarted process can
        query the request-specific claimable amount.

        :param ticket:
            Redemption ticket to serialise.
        :return:
            Persistent ticket data including ``vault_request_id``.
        """
        data = super().serialize_redemption_ticket(ticket)
        data["vault_request_id"] = ticket.request_id
        return data

    def reconstruct_redemption_ticket(self, data: dict) -> ERC7540RedemptionTicket:
        """Reconstruct a redemption ticket from persistent data.

        Both the current string form and the legacy integer form of raw share
        amounts are accepted for backwards compatibility.

        :param data:
            Data produced by :py:meth:`serialize_redemption_ticket`.
        :return:
            Reconstructed ERC-7540 redemption ticket.
        """
        return ERC7540RedemptionTicket(
            vault_address=data["vault_address"],
            owner=data["vault_owner"],
            to=data.get("vault_to", data["vault_owner"]),
            # int() accepts both the current string form and legacy int form
            raw_shares=int(data["vault_raw_amount"]),
            tx_hash=HexBytes(data["vault_request_tx_hash"]),
            request_id=data["vault_request_id"],
        )

    # --- Async vault lifecycle: settlement status ---

    def get_deposit_request_status(self, ticket: ERC7540DepositTicket) -> AsyncVaultRequestStatus:
        """Map ERC-7540 deposit state to the generic status enum.

        Check the request-specific claimable amount **first**: an aggregate
        ``pendingDepositRequest(0, owner)`` query lumps together all of the
        owner's requests, so probing it first would report an already-settled
        request as still pending whenever another request is outstanding.
        The generic interface has no reclaim signal, so ``reclaimable`` is
        never returned.

        :param ticket:
            Deposit request to inspect.
        :return:
            Claimable, pending or empty request status.
        """
        if self.can_finish_deposit(ticket):
            return AsyncVaultRequestStatus.claimable
        if self.is_deposit_in_progress(ticket.owner):
            return AsyncVaultRequestStatus.pending
        return AsyncVaultRequestStatus.none

    def get_redemption_request_status(self, ticket: ERC7540RedemptionTicket) -> AsyncVaultRequestStatus:
        """Map ERC-7540 redemption state to the generic status enum.

        Request-specific claimable is checked before the aggregate pending
        query for the same reason as :py:meth:`get_deposit_request_status`.

        :param ticket:
            Redemption request to inspect.
        :return:
            Claimable, pending or empty request status.
        """
        if self.can_finish_redeem(ticket):
            return AsyncVaultRequestStatus.claimable
        if self.is_redemption_in_progress(ticket.owner):
            return AsyncVaultRequestStatus.pending
        return AsyncVaultRequestStatus.none

    def can_create_deposit_request(self, owner: HexAddress) -> bool:
        """Return whether the vault is currently open for a deposit request.

        The generic ERC-7540 layer checks only the optional pause flag.
        Protocol-specific admission policies belong in manager subclasses.

        :param owner:
            Request owner and controller used by ``requestDeposit``.
        :return:
            ``False`` when the vault reports that requests are paused.
        """
        return not self._is_vault_paused()

    def can_create_redemption_request(self, owner: HexAddress) -> bool:
        """Return whether the vault is currently open for a redemption request.

        The generic ERC-7540 layer checks only the optional pause flag;
        protocol-specific redemption rules belong in manager subclasses.

        :param owner:
            Request owner and controller.
        :return:
            ``False`` when the vault reports that requests are paused.
        """
        return not self._is_vault_paused()

    def _assert_deposit_request_available(self, owner: HexAddress) -> None:
        """Reject a deposit request when the optional pause flag is active.

        :param owner:
            Request owner and controller used by ``requestDeposit``.
        :raise VaultFlowUnavailable:
            If the vault is paused.
        """
        if self._is_vault_paused():
            raise VaultFlowUnavailable(
                "ERC-7540 deposit requests are paused",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                caller=owner,
                direction="deposit",
                phase="preflight",
            )

    def _is_vault_paused(self) -> bool:
        """Read an ERC-7540 vault's optional ``paused()`` flag defensively.

        ERC-7540 does not require this view. A missing function is therefore
        treated as an unpaused vault rather than an adapter failure.

        :return:
            ``True`` only when the optional view exists and reports a pause.
        """
        paused_call = EncodedCall.from_keccak_signature(
            address=self.vault.vault_address,
            signature=Web3.keccak(text="paused()")[0:4],
            function="paused",
            data=b"",
            extra_data=None,
        )
        try:
            result = paused_call.call(
                self.vault.web3,
                block_identifier="latest",
                silent_error=True,
                ignore_error=True,
            )
            return convert_int256_bytes_to_int(result) != 0
        except (ValueError, BadFunctionCallOutput, BadAddressError, ProbablyNodeHasNoBlock):
            return False

    def has_synchronous_deposit(self) -> bool:
        """Report that deposits use the asynchronous request lifecycle.

        :return:
            Always ``False`` for ERC-7540.
        """
        return False

    def has_synchronous_redemption(self) -> bool:
        """Report that redemptions use the asynchronous request lifecycle.

        :return:
            Always ``False`` for ERC-7540.
        """
        return False

    def estimate_redemption_delay(self) -> datetime.timedelta:
        """Return no deterministic protocol-level redemption delay.

        Settlement timing is controlled by each vault operator and is outside
        the ERC-7540 standard.

        :return:
            Zero-duration sentinel for an unspecified delay.
        """
        return datetime.timedelta(seconds=0)

    def get_redemption_delay_over(self, address: HexAddress | str) -> datetime.datetime:
        """Return the legacy sentinel for an unknown account deadline.

        Generic ERC-7540 vaults do not expose a deterministic per-account
        settlement deadline.

        :param address:
            Request controller.
        :return:
            Naive Unix epoch sentinel retained for backwards compatibility.
        """
        return datetime.datetime(1970, 1, 1)

    def is_redemption_in_progress(self, owner: HexAddress) -> bool:
        """Check whether a controller has pending redemption shares.

        Request identifier zero aggregates the controller's pending redemption
        shares under ERC-7540.

        :param owner:
            Controller address to inspect.
        :return:
            ``True`` if the controller has pending redemption shares.
        """
        raw_amount = self.vault.vault_contract.functions.pendingRedeemRequest(0, owner).call()
        return raw_amount > 0

    def is_deposit_in_progress(self, owner: HexAddress) -> bool:
        """Check pending ERC-7540 request.

        Query ``pendingDepositRequest(0, owner)`` when the request identifier
        is not known. Per ERC-7540, request identifier zero aggregates the
        controller's pending deposit assets. A value greater than zero means
        at least one request remains unsettled.

        :param owner:
            Controller address to inspect.

        :return:
            ``True`` if the controller has pending deposit assets.
        """
        raw_amount = self.vault.vault_contract.functions.pendingDepositRequest(0, owner).call()
        return raw_amount > 0

    def finish_redemption(
        self,
        redemption_ticket: RedemptionTicket,
    ) -> ContractFunction:
        """Build the ERC-7540 transaction that claims redeemed assets.

        The request must already be settled. The ticket supplies the
        controller, receiver and settled share amount.

        :param redemption_ticket:
            Settled asynchronous redemption ticket.
        :return:
            Bound three-argument ``redeem`` claim function.
        """
        return self.vault.vault_contract.functions.redeem(
            redemption_ticket.raw_shares,
            redemption_ticket.to,
            redemption_ticket.owner,
        )

    def estimate_deposit(self, owner: HexAddress, amount: Decimal, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Estimate shares for a denomination-token amount.

        ERC-7540 retains the ERC-4626 ``convertToShares`` preview used here;
        the estimate does not imply that the request is currently admissible.

        :param owner:
            Request controller. The standard preview is not owner-specific.
        :param amount:
            Human-readable denomination-token amount.
        :param block_identifier:
            Block at which to read the conversion rate.
        :return:
            Estimated human-readable share amount.
        """
        assert self.vault.denomination_token is not None, f"Vault {self.vault.address} denomination token data missing: likely flaky RPC"
        raw_amount = self.vault.denomination_token.convert_to_raw(amount)
        raw_shares = self.vault.vault_contract.functions.convertToShares(raw_amount).call(block_identifier=block_identifier)
        return self.vault.share_token.convert_to_decimals(raw_shares)

    def estimate_redeem(self, owner: HexAddress, shares: Decimal, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Estimate denomination tokens for a vault-share amount.

        ERC-7540 retains the ERC-4626 ``convertToAssets`` preview used here;
        the estimate does not imply that the request is currently admissible.

        :param owner:
            Request controller. The standard preview is not owner-specific.
        :param shares:
            Human-readable vault-share amount.
        :param block_identifier:
            Block at which to read the conversion rate.
        :return:
            Estimated human-readable denomination-token amount.
        """
        assert self.vault.share_token is not None, f"Vault {self.vault.address} share token data missing: likely flaky RPC"
        raw_shares = self.vault.share_token.convert_to_raw(shares)
        raw_amount = self.vault.vault_contract.functions.convertToAssets(raw_shares).call(block_identifier=block_identifier)
        return self.vault.denomination_token.convert_to_decimals(raw_amount)

    def analyse_deposit(
        self,
        claim_tx_hash: HexBytes | str,
        deposit_ticket: DepositTicket | None,
    ) -> DepositRedeemEventAnalysis | DepositRedeemEventFailure:
        """Analyse a completed ERC-7540 deposit claim.

        The method decodes the ERC-4626 ``Deposit`` event emitted by the
        second-stage claim transaction. Protocol subclasses may extend the
        accepted event signatures for legacy deployments.

        :param claim_tx_hash:
            Deposit-claim transaction hash.
        :param deposit_ticket:
            Original asynchronous request ticket.
        :return:
            Decoded token movement, or a failed-transaction diagnostic.
        :raise RuntimeError:
            If a successful receipt does not contain an accepted event.
        """
        tx_hash = claim_tx_hash
        assert isinstance(tx_hash, (HexBytes, str)), f"Got {type(claim_tx_hash)}"

        assert deposit_ticket is not None, "DepositTicket must be given to analyse a multi-stage deposit"

        vault = self.vault
        web3 = self.web3

        receipt = web3.eth.get_transaction_receipt(tx_hash)

        if receipt["status"] != 1:
            return DepositRedeemEventFailure(tx_hash=tx_hash, revert_reason=receipt["revert_"])

        tx = web3.eth.get_transaction(tx_hash)

        # function _deposit(uint256 assets, address receiver, address controller) internal virtual returns (uint256 shares) {
        # emit Deposit(controller, receiver, assets, shares);

        # ERC-4626 Deposit(indexed sender, indexed owner, assets, shares)
        deposit_signatures = self.get_deposit_event_signatures()

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
            raise RuntimeError(f"Expected exactly one Deposit event, got logs: {logs} at {tx_hash.hex()}, our signatures are {deposit_signatures}")

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

    def get_deposit_event_signatures(self) -> set[HexStr]:
        """Return accepted claim-deposit event signatures.

        ERC-7540 claims retain the ERC-4626 ``Deposit`` event shape.

        :return:
            Standard ERC-4626 ``Deposit`` topic emitted by ERC-7540 claims.
        """
        return {normalise_event_topic(get_topic_signature_from_event(self.vault.vault_contract.events.Deposit))}

    def analyse_redemption(
        self,
        claim_tx_hash: HexBytes | str,
        redemption_ticket: RedemptionTicket | None,
    ) -> DepositRedeemEventAnalysis | DepositRedeemEventFailure:
        """Analyse a completed ERC-7540 redemption claim.

        The method decodes the ERC-4626 ``Withdraw`` event emitted by the
        second-stage claim transaction. Protocol subclasses may extend the
        accepted event signatures for legacy deployments.

        :param claim_tx_hash:
            Redemption-claim transaction hash.
        :param redemption_ticket:
            Original asynchronous request ticket.
        :return:
            Decoded token movement, or a failed-transaction diagnostic.
        :raise RuntimeError:
            If a successful receipt does not contain an accepted event.
        """
        tx_hash = claim_tx_hash
        assert isinstance(tx_hash, (HexBytes, str)), f"Got {type(claim_tx_hash)}"

        assert redemption_ticket is not None, "RedemptionTicket must be given to analyse a multi-stage redemption"

        vault = self.vault
        web3 = self.web3

        receipt = web3.eth.get_transaction_receipt(tx_hash)

        if receipt["status"] != 1:
            return DepositRedeemEventFailure(tx_hash=tx_hash, revert_reason=receipt["revert_"])

        tx = web3.eth.get_transaction(tx_hash)

        redemption_signatures = self.get_redemption_event_signatures()

        deposit_log = None
        logs = receipt["logs"]

        for log in receipt["logs"]:
            sig = log["topics"][0].hex()
            if not sig.startswith("0x"):
                sig = "0x" + sig

            if sig in redemption_signatures:
                deposit_log = log
                break

        if deposit_log is None:
            raise RuntimeError(f"Expected exactly one Withdraw event, got logs: {logs} at {tx_hash.hex()}, our signatures are {redemption_signatures}")

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

    def get_redemption_event_signatures(self) -> set[HexStr]:
        """Return accepted claim-redemption event signatures.

        ERC-7540 claims retain the ERC-4626 ``Withdraw`` event shape.

        :return:
            Standard ERC-4626 ``Withdraw`` topic emitted by ERC-7540 claims.
        """
        return {normalise_event_topic(get_topic_signature_from_event(self.vault.vault_contract.events.Withdraw))}


__all__ = [
    "ERC7540DepositManager",
    "ERC7540DepositRequest",
    "ERC7540DepositTicket",
    "ERC7540RedemptionRequest",
    "ERC7540RedemptionTicket",
]
