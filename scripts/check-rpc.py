"""Check JSON-RPC connection."""
import os

from eth_defi.chain import get_chain_name
from eth_defi.provider.multi_provider import create_multi_provider_web3

web3 = create_multi_provider_web3(os.environ["JSON_RPC_URL"])
name = get_chain_name(web3.eth.chain_id)
print(f"Connected to chain {web3.eth.chain_id}: {name}")
print(f"Last block is: {web3.eth.block_number:,}")