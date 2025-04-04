"""Check JSON-RPC connection."""
import os

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec

web3 = create_multi_provider_web3(os.environ["JSON_RPC_URL"])
name = get_chain_name(web3.eth.chain_id)
print(f"Connected to chain {web3.eth.chain_id}: {name}")
print(f"Last block is: {web3.eth.block_number:,}")

vault_address = "0x88979316806b9101C5B7940a42A2408B712fB5BB"
spec = VaultSpec(web3.eth.chain_id, vault_address)
vault = create_vault_instance(web3, vault_address, {})
print(vault.name)