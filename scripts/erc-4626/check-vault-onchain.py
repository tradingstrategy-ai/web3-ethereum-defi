"""Check a vault onchain data."""

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.classification import create_vault_instance, detect_vault_features
from eth_defi.provider.env import get_json_rpc_env, read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec

# Foxify LP token on Sonic
spec = VaultSpec.parse_string("146-0x3ccff8c929b497c1ff96592b8ff592b45963e732")

# JSON_RPC_ARBITRUM, etc.
json_rpc_url = read_json_rpc_url(spec.chain_id)
web3 = create_multi_provider_web3(json_rpc_url)
name = get_chain_name(web3.eth.chain_id)

print(f"Connected to chain {web3.eth.chain_id}: {name}")
print(f"Last block is: {web3.eth.block_number:,}")

assert web3.eth.chain_id == spec.chain_id

features = detect_vault_features(web3, spec.vault_address)
vault = create_vault_instance(web3, spec.vault_address, features)

print("Chain name:", get_chain_name(web3.eth.chain_id))
print("Vault address:", vault.address)
print("Vault name:", vault.name)
print("Vault denominator:", vault.denomination_token)
print("Vault share token:", vault.share_token)
print("TVL:", vault.fetch_nav())
print("-" * 80)
