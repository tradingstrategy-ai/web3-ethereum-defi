"""Deposit/redemption flow for ERC-7540 vaults."""

import datetime
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from pprint import pformat
from typing import cast

import eth_abi
from eth_typing import BlockIdentifier, HexAddress, HexStr
from hexbytes import HexBytes
from web3 import Web3
from web3._utils.events import EventLogErrorFlags
from web3.contract.contract import ContractFunction
from web3.exceptions import BadFunctionCallOutput

from eth_defi.abi import ZERO_ADDRESS_STR, get_topic_signature_from_event
from eth_defi.event_reader.conversion import BadAddressError, convert_bytes32_to_address, convert_bytes32_to_uint, convert_int256_bytes_to_int
from eth_defi.event_reader.multicall_batcher import EncodedCall
from eth_defi.middleware import ProbablyNodeHasNoBlock
from eth_defi.provider.anvil import is_anvil, make_anvil_custom_rpc_request
from eth_defi.timestamp import get_block_timestamp
from eth_defi.vault.flow_events import (
    PendingVaultFlow,
    VaultFlowDirection,
    create_pending_vault_flow,
    decode_indexed_event_address,
    decode_indexed_event_uint,
    decode_single_uint256_event_data,
    event_data_to_bytes,
    fetch_vault_flow_logs_hypersync,
    normalise_event_topic,
)
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
    UnsupportedVaultSimulation,
    VaultForcedSettlementResult,
)


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

        from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault, LagoonVersion

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
            # ERC-7540 standard event is named DepositRequest (not DepositRequested).
            # Lagoon v0.5+ vaults emit this; the legacy branch above parses the
            # Referral event instead.
            logs = vault.vault_contract.events.DepositRequest().process_receipt(receipt, errors=EventLogErrorFlags.Discard)
            if len(logs) != 1:
                raise CannotParseRedemptionTransaction(f"Expected exactly one DepositRequest event, got logs: {logs} at {tx_hash.hex()}")

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
        from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault, LagoonVersion

        tx_hash = tx_hashes[-1]

        receipt = self.vault.web3.eth.get_transaction_receipt(tx_hash)
        assert receipt is not None, f"Transaction is not yet mined: {tx_hash.hex()}"

        vault = cast(LagoonVault, self.vault)

        logs = vault.vault_contract.events.RedeemRequest().process_receipt(receipt, errors=EventLogErrorFlags.Discard)

        if len(logs) != 1:
            raise CannotParseRedemptionTransaction(f"Expected exactly one RedeemRequest event, got logs: {logs} at {tx_hash.hex()}")

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

    **Supported simulation path**

    Lagoon deposit and redemption requests can be moved to claimable state on
    an Anvil fork through :meth:`force_settle`. The manager resolves the
    deployed valuation-manager and Safe roles and delegates the low-level calls to
    :func:`eth_defi.erc_4626.vault_protocol.lagoon.testing.force_lagoon_settle`.

    **Known limitations**

    This manager currently supports Lagoon only. It does not model partial
    settlements, repeated epochs, operator delegation, cancellation or
    reclaim.
    """

    def __init__(self, vault: "eth_defi.erc_7540.vault.ERC7540Vault"):
        from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault

        assert isinstance(vault, LagoonVault), f"Got {type(vault)}"
        self.vault = vault

    def force_settle(
        self,
        ticket: DepositTicket | RedemptionTicket | None,
    ) -> VaultForcedSettlementResult:
        """Force one Lagoon settlement round on an Anvil fork.

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

    def fetch_vault_flow_events(
        self,
        hypersync_client,
        start_block: int,
        end_block: int,
    ) -> Iterator[PendingVaultFlow]:
        """Fetch Lagoon ERC-7540 request events using Hypersync.

        Lagoon vaults emit ``DepositRequest`` for deposit requests and
        ``RedeemRequest`` for redemption requests. Some deployments also emit
        ``Referral`` with the same request id and asset amount. ``Referral`` is
        treated as a fallback only, because modern referred deposits can emit
        both events in the same transaction.

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
        deposit_topic = get_topic_signature_from_event(vault.vault_contract.events.DepositRequest).lower()
        redeem_topic = get_topic_signature_from_event(vault.vault_contract.events.RedeemRequest).lower()
        topic_map = {
            deposit_topic: VaultFlowDirection.deposit,
            redeem_topic: VaultFlowDirection.redeem,
        }
        if hasattr(vault.vault_contract.events, "Referral"):
            referral_topic = get_topic_signature_from_event(vault.vault_contract.events.Referral).lower()
            topic_map[referral_topic] = VaultFlowDirection.deposit

        logs = fetch_vault_flow_logs_hypersync(
            hypersync_client=hypersync_client,
            vault_address=vault.address,
            topic0_list=list(topic_map.keys()),
            start_block=start_block,
            end_block=end_block,
        )
        chain_id = self.web3.eth.chain_id
        vault_address = Web3.to_checksum_address(vault.address)
        deposit_request_ids = {decode_indexed_event_uint(log.topics[3]) for log in logs if normalise_event_topic(log.topics[0]) == deposit_topic}

        for log in logs:
            topic0 = normalise_event_topic(log.topics[0])
            direction = topic_map.get(topic0)
            if direction == VaultFlowDirection.deposit:
                # event DepositRequest(address indexed controller, address indexed owner, uint256 indexed requestId, address sender, uint256 assets)
                # Legacy event Referral(address indexed referral, address indexed owner, uint256 indexed requestId, uint256 assets) has no separate controller.
                request_id = decode_indexed_event_uint(log.topics[3])
                if topic0 == deposit_topic:
                    controller = Web3.to_checksum_address(decode_indexed_event_address(log.topics[1]))
                    owner = Web3.to_checksum_address(decode_indexed_event_address(log.topics[2]))
                    _sender, raw_assets = eth_abi.decode(["address", "uint256"], event_data_to_bytes(log.data))
                    raw_assets = int(raw_assets)
                else:
                    if request_id in deposit_request_ids:
                        continue
                    # Confirmed from the live legacy Lagoon vault ABI/event shape:
                    # Referral only indexes referral, owner and requestId, and the
                    # non-indexed data only carries assets. It does not emit a
                    # controller/sender, so only self-controller legacy requests can
                    # be reconstructed from events alone.
                    owner = Web3.to_checksum_address(decode_indexed_event_address(log.topics[2]))
                    controller = owner
                    raw_assets = decode_single_uint256_event_data(log.data)
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
        assert not to, f"Unsupported to={to}"

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
            assert self.vault.share_token is not None, f"Vault {self.vault.address} share token data missing: likely flaky RPC"
            raw_shares = self.vault.share_token.convert_to_raw(shares)

        func = self.vault.request_redeem(
            owner,
            raw_shares,
            check_enough_token=check_enough_token,
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

    # --- Async vault lifecycle: ticket serialisation ---
    #
    # The base implementations drop ``request_id``, which ERC-7540 needs to
    # query claimableDepositRequest()/claimableRedeemRequest() after a process
    # restart, and the base reconstruct_redemption_ticket() raises
    # NotImplementedError. Override all four so the trade-executor settlement
    # retry module can persist and rebuild Lagoon tickets.

    def serialize_deposit_ticket(self, ticket: ERC7540DepositTicket) -> dict:
        """Serialise a Lagoon ERC-7540 deposit ticket, including ``request_id``."""
        data = super().serialize_deposit_ticket(ticket)
        data["vault_request_id"] = ticket.request_id
        return data

    def reconstruct_deposit_ticket(self, data: dict) -> ERC7540DepositTicket:
        """Reconstruct an :py:class:`ERC7540DepositTicket` from a serialised dict."""
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
        """Serialise a Lagoon ERC-7540 redemption ticket, including ``request_id``."""
        data = super().serialize_redemption_ticket(ticket)
        data["vault_request_id"] = ticket.request_id
        return data

    def reconstruct_redemption_ticket(self, data: dict) -> ERC7540RedemptionTicket:
        """Reconstruct an :py:class:`ERC7540RedemptionTicket` from a serialised dict."""
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
        """Map Lagoon ERC-7540 deposit state to the generic status enum.

        Check the request-specific claimable amount **first**: an aggregate
        ``pendingDepositRequest(0, owner)`` query lumps together all of the
        owner's requests, so probing it first would report an already-settled
        request as still pending whenever another request is outstanding.
        Lagoon has no on-chain reclaim, so ``reclaimable`` is never returned.
        """
        if self.can_finish_deposit(ticket):
            return AsyncVaultRequestStatus.claimable
        if self.is_deposit_in_progress(ticket.owner):
            return AsyncVaultRequestStatus.pending
        return AsyncVaultRequestStatus.none

    def get_redemption_request_status(self, ticket: ERC7540RedemptionTicket) -> AsyncVaultRequestStatus:
        """Map Lagoon ERC-7540 redemption state to the generic status enum.

        Request-specific claimable is checked before the aggregate pending
        query for the same reason as :py:meth:`get_deposit_request_status`.
        """
        if self.can_finish_redeem(ticket):
            return AsyncVaultRequestStatus.claimable
        if self.is_redemption_in_progress(ticket.owner):
            return AsyncVaultRequestStatus.pending
        return AsyncVaultRequestStatus.none

    def can_create_deposit_request(self, owner: HexAddress) -> bool:
        return not self._is_vault_paused()

    def can_create_redemption_request(self, owner: HexAddress) -> bool:
        return not self._is_vault_paused()

    def _is_vault_paused(self) -> bool:
        """Read Lagoon's optional paused() flag defensively."""
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
        assert self.vault.denomination_token is not None, f"Vault {self.vault.address} denomination token data missing: likely flaky RPC"
        raw_amount = self.vault.denomination_token.convert_to_raw(amount)
        raw_shares = self.vault.vault_contract.functions.convertToShares(raw_amount).call(block_identifier=block_identifier)
        return self.vault.share_token.convert_to_decimals(raw_shares)

    def estimate_redeem(self, owner: HexAddress, shares: Decimal, block_identifier: BlockIdentifier = "latest") -> Decimal:
        assert self.vault.share_token is not None, f"Vault {self.vault.address} share token data missing: likely flaky RPC"
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
