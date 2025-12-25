"""Check a vault."""

import os

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.classification import create_vault_instance, detect_vault_features
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec

VAULTS = ["0xD086dB2316D18F64Da25046F178D51E1E4D88CB5", "0x6bD82c6087DDDf7E23E8Bf0257bafE458541bbeA", "0x52c93748e8bfC93c7C42DBd09588D8113bc9A316", "0x91a989b2cD4e4e86Dc506E7e6666F04Ae1B15cE9"]

web3 = create_multi_provider_web3(os.environ["JSON_RPC_ARBITRUM"])
name = get_chain_name(web3.eth.chain_id)
print(f"Connected to chain {web3.eth.chain_id}: {name}")
print(f"Last block is: {web3.eth.block_number:,}")


for vault_address in VAULTS:
    spec = VaultSpec(web3.eth.chain_id, vault_address)
    features = detect_vault_features(web3, vault_address)
    vault = create_vault_instance(web3, vault_address, features)

    print("Chain name:", get_chain_name(web3.eth.chain_id))
    print("Vault address:", vault.address)
    print("Vault name:", vault.name)
    print("Vault denominator:", vault.denomination_token)
    print("Vault share token:", vault.share_token)
    print("TVL:", vault.fetch_nav())
    print("-" * 80)
