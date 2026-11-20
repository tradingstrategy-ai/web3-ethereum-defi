"""Registry-backed Buoy identity and factory discovery.

Only EIP-1167 clones registered by the official current/legacy factories are
admitted. The API is not consulted. Factory events provide creation boundaries.
"""

from collections.abc import Iterator

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3

from eth_defi.abi import get_deployed_contract
from eth_defi.erc_4626.vault_protocol.buoy.constants import BUOY_CHAIN_ID, BUOY_FACTORIES, BUOY_IMPLEMENTATIONS, BUOY_ROUTER


def fetch_buoy_factories(web3: Web3, block_identifier: BlockIdentifier = "latest") -> tuple[HexAddress, ...]:
    """Resolve current, legacy and previously reviewed factories.

    Retained addresses preserve historical coverage after Router retirement.
    Calls are pinned to the caller's observation block.

    :param web3: Configured HyperEVM connection.
    :param block_identifier: Registry observation block.
    :return: Deduplicated factory addresses; empty on other chains.
    """
    if web3.eth.chain_id != BUOY_CHAIN_ID:
        return ()
    router = get_deployed_contract(web3, "buoy/Router.json", BUOY_ROUTER)
    current = router.functions.factory().call(block_identifier=block_identifier)
    count = router.functions.legacyFactoriesCount().call(block_identifier=block_identifier)
    legacy = (router.functions.legacyFactories(i).call(block_identifier=block_identifier) for i in range(count))
    return tuple(dict.fromkeys(HexAddress(address.lower()) for address in (*BUOY_FACTORIES, current, *legacy)))


def fetch_buoy_implementation(web3: Web3, address: HexAddress, block_identifier: BlockIdentifier = "latest") -> HexAddress:
    """Read a reviewed clone's immutable implementation.

    Reject arbitrary proxies rather than decoding a target from an unrelated
    bytecode offset. Unknown generations remain unsupported.

    :param web3: Configured connection.
    :param address: Vault share-token address.
    :param block_identifier: Code observation block.
    :return: Lowercase reviewed implementation address.
    :raises ValueError: Not a reviewed EIP-1167 clone.
    """
    code = web3.eth.get_code(Web3.to_checksum_address(address), block_identifier=block_identifier).hex()
    if len(code) != 90 or not code.startswith("363d3d373d3d3d363d73") or not code.endswith("5af43d82803e903d91602b57fd5bf3"):
        raise ValueError(f"Not a Buoy minimal proxy: {address}")
    implementation = HexAddress("0x" + code[20:60])
    if implementation not in BUOY_IMPLEMENTATIONS:
        raise ValueError(f"Unreviewed Buoy implementation {implementation} at {address}")
    return implementation


def fetch_buoy_vaults(web3: Web3, block_identifier: BlockIdentifier = "latest") -> Iterator[HexAddress]:
    """Enumerate official registered vaults independently of website metadata.

    Pagination bounds each registry call. The adapter separately verifies the
    implementation so unknown generations can still be discovered as leads.

    :param web3: Configured HyperEVM connection.
    :param block_identifier: Single fixed registry observation block.
    :return: Unique lowercase registered addresses.
    """
    seen = set()
    for address in fetch_buoy_factories(web3, block_identifier):
        factory = get_deployed_contract(web3, "buoy/VaultFactory.json", address)
        count = factory.functions.vaultsCount().call(block_identifier=block_identifier)
        for offset in range(0, count, 100):
            for vault in factory.functions.allVaults(offset, min(100, count - offset)).call(block_identifier=block_identifier):
                vault = HexAddress(vault.lower())
                if vault not in seen:
                    seen.add(vault)
                    yield vault
