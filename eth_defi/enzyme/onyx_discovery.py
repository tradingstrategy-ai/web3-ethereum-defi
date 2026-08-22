"""Enzyme Onyx vault-factory discovery helpers.

The Base deployment uses the Onyx ``SharesFactory``. Its
``ProxyDeployed`` events expose the canonical ``Shares`` token address, which
is also the vault identity used by the shared EVM vault scanner.
"""

# ruff: noqa: EM101

import dataclasses
import datetime
from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any, Literal

from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3

ENZYME_SHARES_DEPLOYED_EVENT_SIGNATURE = "ProxyDeployed(address)"
ENZYME_DEPOSIT_HANDLER_ADDED_EVENT_SIGNATURE = "DepositHandlerAdded(address)"
ENZYME_DEPOSIT_HANDLER_REMOVED_EVENT_SIGNATURE = "DepositHandlerRemoved(address)"

#: Official Base ``SharesFactory`` from Enzyme's Onyx SDK deployment file.
#: https://github.com/enzymefinance/onyx-sdk/blob/main/packages/environment/src/deployments/base.ts
ENZYME_BASE_SHARES_FACTORY = HexAddress("0x5dd79d299e24e49fadac046728e80d1f7414d44c")
ENZYME_BASE_CHAIN_ID = 8453
ABI_WORD_SIZE = 32


@dataclasses.dataclass(slots=True, frozen=True)
class EnzymeVaultFactoryCandidate:
    """An Enzyme Onyx vault decoded from a ``SharesFactory.ProxyDeployed`` log."""

    chain: int
    address: HexAddress
    factory_address: HexAddress
    created_block: int
    created_at: datetime.datetime
    transaction_hash: str
    log_index: int


@dataclasses.dataclass(slots=True, frozen=True)
class EnzymeDepositHandlerUpdate:
    """One mutable Onyx Shares deposit-handler membership update.

    ``Shares`` stores handler authority in a non-enumerable mapping, so callers
    must replay these events through their fixed discovery end block before
    reading current handler configuration.  The event definitions are in the
    canonical Onyx Shares contract:
    https://github.com/enzymefinance/protocol-onyx/blob/main/src/shares/Shares.sol

    """

    #: Shares contract emitting the update.
    shares_address: HexAddress

    #: Handler whose active membership changed.
    handler_address: HexAddress

    #: Whether the handler was added or removed.
    action: Literal["added", "removed"]

    #: Block containing the update.
    block_number: int

    #: Transaction-log ordering key within the block.
    log_index: int


def fetch_enzyme_shares_deployed_event_topic() -> str:
    """Return the ``SharesFactory.ProxyDeployed`` topic0."""

    return Web3.to_hex(Web3.keccak(text=ENZYME_SHARES_DEPLOYED_EVENT_SIGNATURE))


def fetch_enzyme_deposit_handler_event_topics() -> tuple[str, str]:
    """Return topic0 values for mutable Onyx deposit-handler membership.

    :return:
        ``DepositHandlerAdded`` and ``DepositHandlerRemoved`` topics.
    """

    return (
        Web3.to_hex(Web3.keccak(text=ENZYME_DEPOSIT_HANDLER_ADDED_EVENT_SIGNATURE)),
        Web3.to_hex(Web3.keccak(text=ENZYME_DEPOSIT_HANDLER_REMOVED_EVENT_SIGNATURE)),
    )


def fetch_enzyme_shares_factories_for_chain(chain_id: int) -> list[HexAddress]:
    """Return the Enzyme Onyx SharesFactory contracts configured for a chain.

    Base is currently the only supported chain. Discovery deliberately uses
    the reviewed deployment constant instead of an operator-provided address,
    so arbitrary contracts cannot become trusted factory leads.
    """

    if chain_id != ENZYME_BASE_CHAIN_ID:
        return []
    return [ENZYME_BASE_SHARES_FACTORY]


def _decode_int(value: int | str | None) -> int:
    """Normalise a Hypersync integer-like log field."""

    if value is None:
        return 0
    if isinstance(value, int):
        return value
    return int(value, 16) if value.startswith("0x") else int(value)


def _normalise_log(log: Any) -> Any:
    """Create the common log surface used by RPC and Hypersync callers."""

    def normalise(value: Any) -> Any:
        return Web3.to_hex(value) if isinstance(value, HexBytes | bytes) else value

    if isinstance(log, dict):
        return SimpleNamespace(
            address=normalise(log["address"]),
            topics=[normalise(topic) for topic in log.get("topics", [])],
            data=normalise(log.get("data", "0x")),
            block_number=normalise(log.get("blockNumber")),
            transaction_hash=normalise(log.get("transactionHash", "")),
            log_index=normalise(log.get("logIndex")),
        )
    return SimpleNamespace(
        address=normalise(log.address),
        topics=[normalise(topic) for topic in list(log.topics or [])],
        data=normalise(log.data or "0x"),
        block_number=normalise(log.block_number),
        transaction_hash=normalise(log.transaction_hash or ""),
        log_index=normalise(getattr(log, "log_index", None)),
    )


def decode_enzyme_shares_deployed_event(log: Any) -> HexAddress:
    """Decode the Shares address from an official factory log.

    ``SharesFactory.ProxyDeployed`` has one non-indexed address argument.
    The factory only deploys Shares beacon proxies, making this an authoritative
    and compact source for all Onyx vault identities.
    """

    normalised = _normalise_log(log)
    payload = bytes.fromhex(normalised.data.removeprefix("0x"))
    if len(payload) != ABI_WORD_SIZE:
        raise ValueError("Enzyme ProxyDeployed event did not contain one Shares address")
    (shares,) = Web3().codec.decode(["address"], payload)
    return HexAddress(Web3.to_checksum_address(shares))


def decode_enzyme_deposit_handler_event(log: Any) -> EnzymeDepositHandlerUpdate:
    """Decode an Onyx Shares handler add/remove event.

    The handler address is the event's sole non-indexed argument.  Callers must
    still filter ``shares_address`` against factory-confirmed Onyx candidates,
    because a topic-only Hypersync query can encounter an unrelated contract
    that copied the same event signature.

    :param log:
        RPC- or Hypersync-shaped log object.
    :return:
        Ordered handler membership update.
    :raises ValueError:
        If the topic or ABI payload is not a reviewed handler event.
    """

    normalised = _normalise_log(log)
    added_topic, removed_topic = fetch_enzyme_deposit_handler_event_topics()
    topic0 = normalised.topics[0] if normalised.topics else None
    if topic0 == added_topic:
        action: Literal["added", "removed"] = "added"
    elif topic0 == removed_topic:
        action = "removed"
    else:
        raise ValueError(f"Unsupported Enzyme deposit-handler event topic {topic0}")

    payload = bytes.fromhex(normalised.data.removeprefix("0x"))
    if len(payload) != ABI_WORD_SIZE:
        raise ValueError("Enzyme deposit-handler event did not contain one handler address")
    (handler,) = Web3().codec.decode(["address"], payload)
    return EnzymeDepositHandlerUpdate(
        shares_address=HexAddress(Web3.to_checksum_address(normalised.address).lower()),
        handler_address=HexAddress(Web3.to_checksum_address(handler).lower()),
        action=action,
        block_number=_decode_int(normalised.block_number),
        log_index=_decode_int(normalised.log_index),
    )


def reconstruct_active_enzyme_deposit_handlers(
    candidate_addresses: set[HexAddress],
    updates: Iterable[EnzymeDepositHandlerUpdate],
) -> dict[HexAddress, tuple[HexAddress, ...]]:
    """Replay handler membership for factory-confirmed Onyx Shares vaults.

    Updates are ordered by block and log index before replay. Events emitted by
    contracts outside the reviewed factory candidate set are discarded.

    :param candidate_addresses:
        Lower-case Shares addresses established by the official factory.
    :param updates:
        Handler events collected through the same fixed end block.
    :return:
        Every candidate mapped to a deterministic tuple of active handlers.
        Candidates without an active handler are retained with an empty tuple.
    """

    active: dict[HexAddress, set[HexAddress]] = {HexAddress(address.lower()): set() for address in candidate_addresses}
    for update in sorted(updates, key=lambda item: (item.block_number, item.log_index)):
        shares_address = HexAddress(update.shares_address.lower())
        if shares_address not in active:
            continue
        if update.action == "added":
            active[shares_address].add(HexAddress(update.handler_address.lower()))
        else:
            active[shares_address].discard(HexAddress(update.handler_address.lower()))
    return {shares_address: tuple(sorted(handlers)) for shares_address, handlers in sorted(active.items())}


def create_enzyme_factory_candidate(web3: Web3, chain_id: int, log: Any, timestamp: datetime.datetime) -> EnzymeVaultFactoryCandidate:
    """Create a shared-scanner candidate from an Enzyme factory log."""

    del web3  # Decoder uses the protocol's stable ABI layout only.
    normalised = _normalise_log(log)
    shares = decode_enzyme_shares_deployed_event(normalised)
    return EnzymeVaultFactoryCandidate(
        chain=chain_id,
        address=HexAddress(shares.lower()),
        factory_address=HexAddress(normalised.address.lower()),
        created_block=_decode_int(normalised.block_number),
        created_at=timestamp,
        transaction_hash=normalised.transaction_hash or "",
        log_index=_decode_int(normalised.log_index),
    )


def is_enzyme_factory_log(chain_id: int, address: HexAddress | str, topic0: str | None) -> bool:
    """Check whether a log is an official configured Enzyme factory event."""

    return chain_id == ENZYME_BASE_CHAIN_ID and topic0 == fetch_enzyme_shares_deployed_event_topic() and address.lower() == ENZYME_BASE_SHARES_FACTORY.lower()
