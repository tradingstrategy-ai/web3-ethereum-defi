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

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.classification import create_vault_instance, detect_vault_features
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import get_notes, get_vault_special_flags

setup_console_logging(default_log_level="INFO")

# Hub Capital USDC vault on Ethereum
spec = VaultSpec.parse_string("1-0xca790385506b790554571cbc9da73f0130cdcfd5")

json_rpc_url = read_json_rpc_url(spec.chain_id)
web3 = create_multi_provider_web3(json_rpc_url)
name = get_chain_name(web3.eth.chain_id)

print(f"Connected to chain {web3.eth.chain_id}: {name}")
print(f"Last block is: {web3.eth.block_number:,}")

assert web3.eth.chain_id == spec.chain_id

features = detect_vault_features(web3, spec.vault_address)

vault = create_vault_instance(web3, spec.vault_address, features)
print("Features:", [f.name for f in features])

share_price = vault.fetch_share_price("latest")

print("Chain name:", get_chain_name(web3.eth.chain_id))
print("Vault address:", vault.address)
print("Vault name:", vault.name)
print("Vault denominator:", vault.denomination_token)
print("Vault share token:", vault.share_token)
print("Share price:", share_price)
print("TVL:", vault.fetch_nav())
print("-" * 80)

# Vault flags and notes
flags = get_vault_special_flags(spec.vault_address)
notes = get_notes(spec.vault_address)
print("\nFlags and notes:")
print(f"  Flags: {flags or 'None'}")
print(f"  Notes: {notes or 'None'}")
print("-" * 80)

# Check deposit/redemption status
print("\nDeposit/Redemption status:")

# Raw ERC-4626 maxDeposit/maxRedeem values
try:
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    print(f"  maxDeposit(address(0)): {max_deposit}")
except Exception as e:
    print(f"  maxDeposit(address(0)): ERROR - {e}")

try:
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    print(f"  maxRedeem(address(0)): {max_redeem}")
except Exception as e:
    print(f"  maxRedeem(address(0)): ERROR - {e}")

# Protocol-specific close reasons
try:
    deposit_closed = vault.fetch_deposit_closed_reason()
    print(f"  Deposit closed reason: {deposit_closed or '-'}")
except Exception as e:
    print(f"  Deposit closed reason: ERROR - {e}")

try:
    redemption_closed = vault.fetch_redemption_closed_reason()
    print(f"  Redemption closed reason: {redemption_closed or '-'}")
except Exception as e:
    print(f"  Redemption closed reason: ERROR - {e}")

print("-" * 80)
