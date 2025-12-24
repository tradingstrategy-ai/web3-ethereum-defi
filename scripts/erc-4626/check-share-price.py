"""Check a vault share price at certain blocks."""

import os

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.classification import create_vault_instance, detect_vault_features
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.timestamp import get_block_timestamp
from eth_defi.vault.base import VaultSpec

# Unagii
# https://docs.unagii.com/unagii-vaults/token-contracts
vault_address = "0x09dab27cc3758040eea0f7b51df2aee14bc003d6"

web3 = create_multi_provider_web3(os.environ["JSON_RPC_ETHEREUM"])
name = get_chain_name(web3.eth.chain_id)
print(f"Connected to chain {web3.eth.chain_id}: {name}")
print(f"Last block is: {web3.eth.block_number:,}")

block_numbers = [16810699, 16810999, 16811299]

spec = VaultSpec(web3.eth.chain_id, vault_address)
features = detect_vault_features(web3, vault_address)
vault: ERC4626Vault = create_vault_instance(web3, vault_address, features)

print("Chain name:", get_chain_name(web3.eth.chain_id))
print("Vault address:", vault.address)
print("Vault name:", vault.name)
print("Vault denominator:", vault.denomination_token)
print("Vault share token:", vault.share_token)

for block_number in block_numbers:
    timestamp = get_block_timestamp(web3, block_number)
    share_price = vault.fetch_share_price(block_identifier=block_number)
    print(f"Share price at block {block_number} ({timestamp}): {share_price:.6f} {vault.denomination_token.symbol}")
