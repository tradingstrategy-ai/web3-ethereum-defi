"""Read historical returns of ERC-4626 vaults.

- Use multicall to get rates for multiple vaults once.
"""
from attr import dataclass

from eth_defi.event_reader.multicall_batcher import MulticallWrapper
from eth_defi.vault.base import VaultBase



@dataclass(frozen=True, slots=True)
class VaultSharePriceReader:
    """Wrap reading of the share price."""
    vault: VaultBase
    calls: dict[str, MulticallWrapper]



def prepare_multicalls(
    vaults: list[VaultBase],
):
    calls = []



def create_share_price_reader(
    start_block: int,
    end_block: int,
    step: int,
    vaults: list[VaultBase],
    chunk_size=100,  # steps
):
    pass

    readers = {}
    for vault in vaults:
        readers[vault] = vault.get_share_price_reader()


