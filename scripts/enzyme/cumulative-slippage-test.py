"""Test cumulative slippage tolerance of Enzyme vault."""

import os

from eth_account import Account
from eth_account.signers.local import LocalAccount

from eth_defi.abi import encode_function_call, get_deployed_contract
from eth_defi.compat import construct_sign_and_send_raw_middleware
from eth_defi.enzyme.deployment import POLYGON_DEPLOYMENT, EnzymeDeployment
from eth_defi.enzyme.generic_adapter import execute_calls_for_generic_adapter
from eth_defi.enzyme.vault import Vault
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.uniswap_v2.deployment import FOREVER_DEADLINE, fetch_deployment

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

generic_adapter = get_deployed_contract(web3, f"VaultSpecificGenericAdapter.json", "0x8C35a027FE7986FA5736813869C0A2A7A991BEDd")
vault.generic_adapter = generic_adapter

wmatic = fetch_erc20_details(web3, "0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270").contract
# balance = wmatic.contract.functions.balanceOf(vault.address).call()
# balance = 1

# USDC
usdc = fetch_erc20_details(web3, "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174").contract
balance = int(usdc.functions.balanceOf(vault.address).call())

# bound_transfer = prepare_transfer_sneaky(
#     deployment,
#     vault,
#     generic_adapter,
#     token.contract,
#     receiver,
#     balance,
# )

swap_amount = balance
token_in = usdc
token_out = wmatic

uniswap_v2 = fetch_deployment(
    web3,
    "0x5757371414417b8C6CAad45bAeF941aBc7d3Ab32",
    "0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff",
    "0x96e8ac4277198ff8b6f785478aa9a39f403cb768dd02cbee326c3e7da348845f",
)

# Prepare the swap parameters
token_in_swap_amount = swap_amount
spend_asset_amounts = [token_in_swap_amount]
spend_assets = [token_in]
path = [token_in.address, token_out.address]
expected_outgoing_amount, expected_incoming_amount = uniswap_v2.router.functions.getAmountsOut(token_in_swap_amount, path).call()
extra_slippage = int(expected_incoming_amount * 0.05)
expected_incoming_amount -= extra_slippage
incoming_assets = [token_out]
min_incoming_assets_amounts = [expected_incoming_amount]

# The vault performs a swap on Uniswap v2
encoded_approve = encode_function_call(token_in.functions.approve, [uniswap_v2.router.address, token_in_swap_amount])

# fmt: off
encoded_swapExactTokensForTokens = encode_function_call(
    uniswap_v2.router.functions.swapExactTokensForTokens,
    [token_in_swap_amount, 1, path, generic_adapter.address, FOREVER_DEADLINE]
)

transfer_spent = extra_slippage - 5

encoded_transfer = encode_function_call(wmatic.functions.transfer, [receiver, transfer_spent])

bound_call = execute_calls_for_generic_adapter(
    comptroller=vault.comptroller,
    external_calls=(
        (token_in, encoded_approve),
        (uniswap_v2.router, encoded_swapExactTokensForTokens),
        (wmatic, encoded_transfer),
    ),
    generic_adapter=generic_adapter,
    incoming_assets=incoming_assets,
    integration_manager=deployment.contracts.integration_manager,
    min_incoming_asset_amounts=min_incoming_assets_amounts,
    spend_asset_amounts=spend_asset_amounts,
    spend_assets=spend_assets,
)


tx_hash = bound_call.transact({"from": account.address})
print("Broadcasting", tx_hash.hex())
receipt = web3.eth.wait_for_transaction_receipt(tx_hash)

print("Receipt", receipt)
