"""Enzyme Onyx vault-factory discovery helpers.

The Base deployment uses the Onyx ``SharesFactory``. Its
``ProxyDeployed`` events expose the canonical ``Shares`` token address, which
is also the vault identity used by the shared EVM vault scanner.
"""

# ruff: noqa: EM101

import dataclasses
import datetime
from types import SimpleNamespace
from typing import Any

from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3

ENZYME_SHARES_DEPLOYED_EVENT_SIGNATURE = "ProxyDeployed(address)"

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


def fetch_enzyme_shares_deployed_event_topic() -> str:
    """Return the ``SharesFactory.ProxyDeployed`` topic0."""

    return Web3.to_hex(Web3.keccak(text=ENZYME_SHARES_DEPLOYED_EVENT_SIGNATURE))


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
