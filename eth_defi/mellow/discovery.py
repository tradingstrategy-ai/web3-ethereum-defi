"""Mellow Core Vault discovery helpers."""

import dataclasses
import datetime
import os
from types import SimpleNamespace
from typing import Any

from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3

MELLOW_CREATED_EVENT_SIGNATURE = "Created(address,uint256,address,bytes)"

INDEXED_CREATED_EVENT_TOPIC_COUNT = 4


@dataclasses.dataclass(slots=True, frozen=True)
class MellowFactoryCandidate:
    """Mellow vault candidate decoded from ``Factory.Created``.

    :param chain:
        EVM chain id.

    :param address:
        Canonical Mellow ``Vault`` proxy address.

    :param factory_address:
        Factory address that emitted the creation log.

    :param factory_version:
        Factory deployment version.

    :param owner:
        Owner argument from the factory event.

    :param created_block:
        Block where the vault was created.

    :param created_at:
        Block timestamp as naive UTC datetime.

    :param transaction_hash:
        Creation transaction hash.

    :param log_index:
        Log index in the creation transaction.

    :param init_params:
        Raw ABI-encoded init parameters.
    """

    chain: int
    address: HexAddress
    factory_address: HexAddress
    factory_version: int
    owner: HexAddress
    created_block: int
    created_at: datetime.datetime
    transaction_hash: str
    log_index: int
    init_params: bytes


def fetch_mellow_created_event_topic() -> str:
    """Return the Mellow factory creation topic.

    :return:
        Topic0 for ``Created(address,uint256,address,bytes)``.
    """

    return Web3.to_hex(Web3.keccak(text=MELLOW_CREATED_EVENT_SIGNATURE))


def fetch_mellow_factories_for_chain(chain_id: int) -> list[HexAddress]:
    """Return configured Mellow Core Vault factories for a chain.

    Mainnet, Plasma and Arbitrum share the documented Core factory address.
    Monad has a chain-specific Core factory. Base is intentionally opt-in
    through ``MELLOW_BASE_VAULT_FACTORY`` until a canonical Core factory is
    confirmed.

    :param chain_id:
        EVM chain id.

    :return:
        List of factory addresses.
    """

    env_by_chain = {
        1: ("MELLOW_ETHEREUM_VAULT_FACTORY", "0x4E38F679e46B3216f0bd4B314E9C429AFfB1dEE3"),
        9745: ("MELLOW_PLASMA_VAULT_FACTORY", "0x4E38F679e46B3216f0bd4B314E9C429AFfB1dEE3"),
        42161: ("MELLOW_ARBITRUM_VAULT_FACTORY", "0x4E38F679e46B3216f0bd4B314E9C429AFfB1dEE3"),
        143: ("MELLOW_MONAD_VAULT_FACTORY", "0x04c0287DEdE16e0C04A1C2A52F31400a88f1dF4c"),
        8453: ("MELLOW_BASE_VAULT_FACTORY", ""),
    }

    env = env_by_chain.get(chain_id)
    if env is None:
        return []

    env_var, default = env
    raw = os.environ.get(env_var, default).strip()
    if not raw:
        return []

    return [HexAddress(Web3.to_checksum_address(part.strip())) for part in raw.split(",") if part.strip()]


def _decode_hypersync_int(value: int | str | None) -> int:
    """Decode a Hypersync integer-like value.

    :param value:
        Integer or hex string.

    :return:
        Decoded integer.
    """

    if value is None:
        return 0
    if isinstance(value, int):
        return value
    if value.startswith("0x"):
        return int(value, 16)
    return int(value)


def _topic_to_address(topic: str) -> HexAddress:
    """Decode an indexed address topic.

    :param topic:
        32-byte topic.

    :return:
        Checksummed address.
    """

    return HexAddress(Web3.to_checksum_address(f"0x{topic[-40:]}"))


def decode_mellow_created_event(web3: Web3, log: Any) -> tuple[HexAddress, int, HexAddress, bytes]:
    """Decode a Mellow factory ``Created`` log.

    The verified event layout documented in Mellow Core is non-indexed. The
    decoder also accepts an indexed ``instance/version/owner`` layout so older
    or locally-patched factories are rejected only after both layouts fail.

    :param web3:
        Web3 whose ABI codec is used.

    :param log:
        Hypersync log object.

    :return:
        Vault instance, factory version, owner and raw init params.

    :raise DecodingError:
        Raised if neither supported layout decodes.
    """

    topics = list(log.topics or [])
    data = bytes.fromhex((log.data or "0x")[2:])

    if len(topics) >= INDEXED_CREATED_EVENT_TOPIC_COUNT:
        instance = _topic_to_address(topics[1])
        version = int(topics[2], 16)
        owner = _topic_to_address(topics[3])
        return instance, version, owner, data

    instance, version, owner, init_params = web3.codec.decode(["address", "uint256", "address", "bytes"], data)
    return (
        HexAddress(Web3.to_checksum_address(instance)),
        int(version),
        HexAddress(Web3.to_checksum_address(owner)),
        bytes(init_params),
    )


def _normalise_log_value(value: Any) -> Any:
    """Normalise Web3.py/Hypersync log values for shared decoding.

    :param value:
        Raw log field value.

    :return:
        Hex string for bytes-like values, otherwise the original value.
    """

    if isinstance(value, HexBytes | bytes):
        return Web3.to_hex(value)
    return value


def normalise_mellow_created_log(log: Any) -> Any:
    """Normalise a raw JSON-RPC or Hypersync log for Mellow decoding.

    Hypersync returns attribute-like objects, while the JSON-RPC event reader
    returns dictionaries. This helper creates the small common surface consumed
    by :py:func:`decode_mellow_created_event` and
    :py:func:`create_mellow_factory_candidate`.

    :param log:
        Raw log object.

    :return:
        Namespace with ``address``, ``topics``, ``data``, ``block_number``,
        ``transaction_hash`` and ``log_index`` attributes.
    """

    if isinstance(log, dict):
        return SimpleNamespace(
            address=_normalise_log_value(log["address"]),
            topics=[_normalise_log_value(topic) for topic in log.get("topics", [])],
            data=_normalise_log_value(log.get("data", "0x")),
            block_number=_normalise_log_value(log.get("blockNumber")),
            transaction_hash=_normalise_log_value(log.get("transactionHash", "")),
            log_index=_normalise_log_value(log.get("logIndex")),
        )

    return SimpleNamespace(
        address=_normalise_log_value(log.address),
        topics=[_normalise_log_value(topic) for topic in list(log.topics or [])],
        data=_normalise_log_value(log.data or "0x"),
        block_number=_normalise_log_value(log.block_number),
        transaction_hash=_normalise_log_value(log.transaction_hash or ""),
        log_index=_normalise_log_value(getattr(log, "log_index", None)),
    )


def create_mellow_factory_candidate(
    web3: Web3,
    chain_id: int,
    log: Any,
    timestamp: datetime.datetime,
) -> MellowFactoryCandidate:
    """Create a factory candidate from a Hypersync log.

    :param web3:
        Web3 whose ABI codec is used.

    :param chain_id:
        EVM chain id.

    :param log:
        Hypersync log object.

    :param timestamp:
        Block timestamp.

    :return:
        Decoded factory candidate.
    """

    normalised_log = normalise_mellow_created_log(log)
    instance, version, owner, init_params = decode_mellow_created_event(web3, normalised_log)
    return MellowFactoryCandidate(
        chain=chain_id,
        address=HexAddress(instance.lower()),
        factory_address=HexAddress(normalised_log.address.lower()),
        factory_version=version,
        owner=HexAddress(owner.lower()),
        created_block=_decode_hypersync_int(normalised_log.block_number),
        created_at=timestamp,
        transaction_hash=normalised_log.transaction_hash or "",
        log_index=_decode_hypersync_int(normalised_log.log_index),
        init_params=init_params,
    )


def is_mellow_factory_log(chain_id: int, address: HexAddress | str, topic0: str | None) -> bool:
    """Check if a log is from a configured Mellow factory.

    :param chain_id:
        EVM chain id.

    :param address:
        Log-emitting address.

    :param topic0:
        First log topic.

    :return:
        ``True`` if this log should be decoded as Mellow ``Factory.Created``.
    """

    if topic0 != fetch_mellow_created_event_topic():
        return False

    factories = {factory.lower() for factory in fetch_mellow_factories_for_chain(chain_id)}
    return address.lower() in factories
