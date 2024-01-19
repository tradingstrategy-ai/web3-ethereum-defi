"""An example script how to call the vault as an owner"""
import os

from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3.middleware import construct_sign_and_send_raw_middleware

from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import deploy_contract
from eth_defi.enzyme.deployment import EnzymeDeployment, POLYGON_DEPLOYMENT
from eth_defi.enzyme.erc20 import prepare_transfer, prepare_approve
from eth_defi.enzyme.vault import Vault
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details

vault_address = os.environ["VAULT"]
json_rpc_url = os.environ["JSON_RPC_POLYGON"]
private_key = os.environ["PRIVATE_KEY"]
receiver = "0x7612A94AafF7a552C373e3124654C1539a4486A8"

web3 = create_multi_provider_web3(json_rpc_url)
account: LocalAccount = Account.from_key(private_key)
web3.middleware_onion.add(construct_sign_and_send_raw_middleware(account))

deployment = EnzymeDeployment.fetch_deployment(web3, POLYGON_DEPLOYMENT)
vault = Vault.fetch(web3, vault_address)

# print("Deploying")
# generic_adapter = deploy_contract(
#     web3,
#     f"VaultSpecificGenericAdapter.json",
#     account.address,
#     deployment.contracts.integration_manager.address,
#     vault.address,
# )
# print(f"Generic adapter is {generic_adapter.address}")

generic_adapter = get_deployed_contract(
    web3,
    f"VaultSpecificGenericAdapter.json",
    "0x8C35a027FE7986FA5736813869C0A2A7A991BEDd"
)
vault.generic_adapter = generic_adapter

wmatic = fetch_erc20_details(web3, "0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270")
balance = wmatic.contract.functions.balanceOf(vault.address).call()
# balance = 1

bound_call = prepare_approve(
    deployment,
    vault,
    generic_adapter,
    wmatic.contract,
    receiver,
    balance,
)

tx_hash = bound_call.transact({"from": account.address})
print("Broadcasting", tx_hash.hex())
receipt = web3.eth.wait_for_transaction_receipt(tx_hash)

print("Receipt", receipt)


