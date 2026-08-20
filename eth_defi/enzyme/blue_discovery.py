"""Enzyme Blue vault discovery through persistent Dispatcher events.

An Enzyme Blue fund has two proxy contracts: the ERC-20 ``VaultProxy`` and
its ``ComptrollerProxy`` accessor.  ``Dispatcher.VaultProxyDeployed`` is the
canonical, release-independent event that records both addresses.  Querying
the persistent Dispatcher, rather than each historic FundDeployer, discovers
all Blue releases with one HyperSync stream per chain.

See https://docs.enzyme.finance/enzyme-blue-protocol/architecture/persistent
and https://docs.enzyme.finance/enzyme-blue-protocol/architecture/release.
"""

import dataclasses
import datetime
from types import SimpleNamespace
from typing import Any

from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3

ENZYME_BLUE_VAULT_DEPLOYED_EVENT_SIGNATURE = "VaultProxyDeployed(address,address,address,address,address,string)"
INDEXED_FUND_DEPLOYER_TOPIC_COUNT = 2


@dataclasses.dataclass(slots=True, frozen=True)
class EnzymeBlueDeployment:
    """Reviewed persistent Dispatcher deployment for a Blue chain."""

    #: EVM chain id.
    chain_id: int
    #: Persistent Dispatcher address.
    dispatcher: HexAddress
    #: First block at which the Dispatcher can emit fund deployment events.
    first_block: int


#: Deployment constants from Enzyme's chain contract pages.  The starting
#: blocks are the first supported Blue release blocks in our existing Enzyme
#: deployment registry; scanning begins there, but a single persistent
#: Dispatcher yields every later release and migration.
ENZYME_BLUE_DEPLOYMENTS: dict[int, EnzymeBlueDeployment] = {
    1: EnzymeBlueDeployment(1, HexAddress("0xC3DC853dD716bd5754f421ef94fdCbac3902ab32"), 11_632_494),
    137: EnzymeBlueDeployment(137, HexAddress("0x2e25271297537B8124b8f883a92fFd95C4032733"), 25_825_795),
    8453: EnzymeBlueDeployment(8453, HexAddress("0xD79FCD6eb56115f9757EC4C90fc2C5D143f83C16"), 23_178_791),
    42161: EnzymeBlueDeployment(42161, HexAddress("0x8da28441a4c594fD2fac72726C1412d8Cf9E4A19"), 230_330_758),
}


@dataclasses.dataclass(slots=True, frozen=True)
class EnzymeBlueVaultFactoryCandidate:
    """A Blue fund decoded from ``Dispatcher.VaultProxyDeployed``."""

    chain: int
    #: Canonical VaultProxy/share-token address.
    address: HexAddress
    #: Persistent Dispatcher which emitted the authoritative event.
    dispatcher_address: HexAddress
    #: Release's FundDeployer, useful for protocol-fee configuration.
    fund_deployer: HexAddress
    #: Paired ComptrollerProxy at fund creation.
    vault_accessor: HexAddress
    #: Manager-supplied fund name from the deployment event.
    fund_name: str
    created_block: int
    created_at: datetime.datetime
    transaction_hash: str
    log_index: int


def fetch_enzyme_blue_vault_deployed_event_topic() -> str:
    """Return the Blue ``Dispatcher.VaultProxyDeployed`` topic0."""

    return Web3.to_hex(Web3.keccak(text=ENZYME_BLUE_VAULT_DEPLOYED_EVENT_SIGNATURE))


def fetch_enzyme_blue_deployments_for_chain(chain_id: int) -> list[EnzymeBlueDeployment]:
    """Return reviewed Blue Dispatcher deployments for ``chain_id``."""

    deployment = ENZYME_BLUE_DEPLOYMENTS.get(chain_id)
    return [deployment] if deployment else []


def fetch_enzyme_blue_dispatchers_for_chain(chain_id: int) -> list[HexAddress]:
    """Return persistent Blue Dispatcher addresses for ``chain_id``."""

    return [deployment.dispatcher for deployment in fetch_enzyme_blue_deployments_for_chain(chain_id)]


def fetch_enzyme_blue_first_block(chain_id: int) -> int | None:
    """Return the first supported Blue discovery block for ``chain_id``."""

    deployment = ENZYME_BLUE_DEPLOYMENTS.get(chain_id)
    return deployment.first_block if deployment else None


def _normalise_log(log: Any) -> Any:
    """Create a common RPC/HyperSync log surface for ABI-layout decoding."""

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


def _decode_int(value: int | str | None) -> int:
    """Normalise a HyperSync integer-like log field."""

    if value is None:
        return 0
    if isinstance(value, int):
        return value
    return int(value, 16) if value.startswith("0x") else int(value)


def _decode_topic_address(topic: str) -> HexAddress:
    """Decode one indexed address topic."""

    return HexAddress(Web3.to_checksum_address(f"0x{topic[-40:]}"))


def decode_enzyme_blue_vault_deployed_event(log: Any) -> tuple[HexAddress, HexAddress, str]:
    """Decode vault, accessor, and name from a Dispatcher deployment event."""

    normalised = _normalise_log(log)
    payload = bytes.fromhex(normalised.data.removeprefix("0x"))
    vault_proxy, vault_accessor, fund_name = Web3().codec.decode(["address", "address", "string"], payload)
    return HexAddress(Web3.to_checksum_address(vault_proxy)), HexAddress(Web3.to_checksum_address(vault_accessor)), fund_name


def create_enzyme_blue_factory_candidate(web3: Web3, chain_id: int, log: Any, timestamp: datetime.datetime) -> EnzymeBlueVaultFactoryCandidate:
    """Create a Blue scanner candidate from a reviewed Dispatcher event."""

    del web3
    normalised = _normalise_log(log)
    if len(normalised.topics) < INDEXED_FUND_DEPLOYER_TOPIC_COUNT:
        message = "Enzyme Blue VaultProxyDeployed event omitted its FundDeployer topic"
        raise ValueError(message)
    vault_proxy, vault_accessor, fund_name = decode_enzyme_blue_vault_deployed_event(normalised)
    return EnzymeBlueVaultFactoryCandidate(
        chain=chain_id,
        address=HexAddress(vault_proxy.lower()),
        dispatcher_address=HexAddress(normalised.address.lower()),
        fund_deployer=HexAddress(_decode_topic_address(normalised.topics[1]).lower()),
        vault_accessor=HexAddress(vault_accessor.lower()),
        fund_name=fund_name,
        created_block=_decode_int(normalised.block_number),
        created_at=timestamp,
        transaction_hash=normalised.transaction_hash or "",
        log_index=_decode_int(normalised.log_index),
    )


def is_enzyme_blue_factory_log(chain_id: int, address: HexAddress | str, topic0: str | None) -> bool:
    """Check whether a log is an official configured Blue deployment event."""

    return topic0 == fetch_enzyme_blue_vault_deployed_event_topic() and address.lower() in {item.lower() for item in fetch_enzyme_blue_dispatchers_for_chain(chain_id)}
