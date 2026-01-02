"""Check a vault onchain data.

Example output::

    Connected to chain 1: Ethereum
    Last block is: 24,147,061
    Chain name: Ethereum
    Vault address: 0x4880799eE5200fC58DA299e965df644fBf46780B
    Vault name: Janus Henderson Anemoy AAA CLO Fund Token
    Vault denominator: <USD Coin (USDC) at 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48, 6 decimals, on chain 1>
    Vault share token: <Janus Henderson Anemoy AAA CLO Fund Token (JAAA) at 0x5a0F93D040De44e78F251b03c43be9CF317Dcf64, 6 decimals, on chain 1>
    TVL: 765948512.664643
    Features: ['erc_7575_like', 'erc_7540_like']

"""

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.classification import create_vault_instance, detect_vault_features
from eth_defi.provider.env import get_json_rpc_env, read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec

# Foxify LP token on Sonic
spec = VaultSpec.parse_string("1-0x4880799ee5200fc58da299e965df644fbf46780b")

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
print("Features:", [f.name for f in features])
print("-" * 80)
