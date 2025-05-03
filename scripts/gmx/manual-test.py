"""Manually test GMX transactions."""
import tempfile

from eth_defi.gas import estimate_gas_fees, estimate_gas_price
# from example_scripts.debug_swap import JSON_RPC_BASE
# from utils import _set_paths

from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3

from eth_utils import to_checksum_address

from gmx_python_sdk.scripts.v2.gmx_utils import ConfigManager
from gmx_python_sdk.scripts.v2.order.create_swap_order import SwapOrder
from gmx_python_sdk.scripts.v2.order.order_argument_parser import OrderArgumentParser

import os

# JSON_RPC_BASE = "https://virtual.arbitrum.rpc.tenderly.co/8ebd8115-6fcf-49d4-96cb-d5c75ad4c9ed"  # "https://virtual.arbitrum.rpc.tenderly.co/338aa0f8-ef60-4ae1-baf9-958c3754686d" # os.getenv("ARBITRUM_CHAIN_JSON_RPC")

JSON_RPC_ARBITRUM = os.environ["JSON_RPC_ARBITRUM"]

CONFIG = """
rpcs:
  arbitrum: http://localhost:8545 # https://lb.drpc.org/ogrpc?network=arbitrum&dkey=AiWA4TvYpkijvapnvFlyx_WMaMT3ESgR8JgrKjrWkQAY # https://arbitrum.meowrpc.com
  # arbitrum: https://lb.drpc.org/ogrpc?network=arbitrum&dkey=AiWA4TvYpkijvapnvFlyx_WMaMT3ESgR8JgrKjrWkQAY
  avalanche: avax_rpc
chain_ids:
  arbitrum: 42161
  avalanche: 43114
private_key: "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
user_wallet_address: "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
"""


def main():

    # Addresses
    whale_address = "0xD7a827FBaf38c98E8336C5658E4BcbCD20a4fd2d"
    recipient_address = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
    link_token_address = "0xf97f4df75117a78c1A5a0DBb814Af92458539FB4"  # LINK token contract
    target_address = to_checksum_address("0x2bcC6D6CdBbDC0a4071e48bb3B969b06B3330c07")  # SOL

    SIMULATE = True
    if SIMULATE:
        print("Simulation deployment with Anvil")
        anvil = fork_network_anvil(
            JSON_RPC_ARBITRUM,
            unlocked_addresses=[whale_address],
        )
        w3 = create_multi_provider_web3(
            anvil.json_rpc_url,
            default_http_timeout=(30, 30),
        )
    else:
        print("Base production deployment")
        w3 = create_multi_provider_web3(JSON_RPC_ARBITRUM)

    temp = tempfile.NamedTemporaryFile(delete=False, mode='w')
    temp.write(CONFIG)
    temp.close()

    # 420000042000000028161458831360
    print(w3.eth.chain_id)


    erc20_abi = [
        {
            "constant": True,
            "inputs": [{"name": "_owner", "type": "address"}],
            "name": "balanceOf",
            "outputs": [{"name": "balance", "type": "uint256"}],
            "type": "function",
        },
        {
            "constant": False,
            "inputs": [
                {"name": "_to", "type": "address"},
                {"name": "_value", "type": "uint256"},
            ],
            "name": "transfer",
            "outputs": [{"name": "", "type": "bool"}],
            "type": "function",
        },
        {
            "constant": True,
            "inputs": [],
            "name": "decimals",
            "outputs": [{"name": "", "type": "uint8"}],
            "type": "function",
        },
    ]

    link_contract = w3.eth.contract(address=link_token_address, abi=erc20_abi)
    target_contract = w3.eth.contract(address=target_address, abi=erc20_abi)

    decimals = link_contract.functions.decimals().call()
    amount = 4000 * (10**decimals)  # Transfer 3000 LINK tokens

    tx = link_contract.functions.transfer(recipient_address, amount).build_transaction(
        {
            "from": whale_address,
            "nonce": w3.eth.get_transaction_count(whale_address),
            "gas": 100000,
            "gasPrice": w3.to_wei("1", "gwei"),
        }
    )

    tx_hash = w3.eth.send_transaction(tx)

    # # Wait for transaction receipt
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"Transaction successful with hash: {tx_hash.hex()}")

    balance = link_contract.functions.balanceOf(recipient_address).call()
    print(f"Recipient LINK balance: {balance / (10**decimals)} at {recipient_address}")

    sol_balance_before = target_contract.functions.balanceOf(recipient_address).call()
    print(f"Recipient SOL balance before: {sol_balance_before / 10**decimals} at {recipient_address}")

    config = ConfigManager(chain="arbitrum")
    config.set_config(temp.name)
    config.set_rpc(w3)

    parameters = {
        "chain": "arbitrum",
        # token to use as collateral. Start token swaps into collateral token if
        # different
        "out_token_symbol": "SOL",
        # the token to start with - WETH not supported yet
        "start_token_symbol": "LINK",
        # True for long, False for short
        "is_long": False,
        # Position size in in USD
        "size_delta_usd": 0,
        # if leverage is passed, will calculate number of tokens in
        # start_token_symbol amount
        "initial_collateral_delta": 3000.0,
        # as a percentage
        "slippage_percent": 0.02,
    }

    order_parameters = OrderArgumentParser(config, is_swap=True).process_parameters_dictionary(parameters)

    gas_data = estimate_gas_price(w3)

    order = SwapOrder(
        config=config,
        market_key=order_parameters["swap_path"][-1],
        start_token=order_parameters["start_token_address"],
        out_token=order_parameters["out_token_address"],
        collateral_address=order_parameters["start_token_address"],
        index_token_address=order_parameters["out_token_address"],
        is_long=False,
        size_delta=0,
        initial_collateral_delta_amount=(order_parameters["initial_collateral_delta"]),
        slippage_percent=order_parameters["slippage_percent"],
        swap_path=order_parameters["swap_path"],
        debug_mode=False,
        execution_buffer=2.2,
        max_fee_per_gas=gas_data.max_fee_per_gas,
    )

    # swap_estimate = order.estimated_swap_output(
    #     market,
    #     "0x7f1fa204bb700853D36994DA19F830b6Ad18455C",
    #     parameters["initial_collateral_delta"],
    # )
    # print(f"Estimated swap output: {swap_estimate}")

    balance = link_contract.functions.balanceOf(recipient_address).call()
    print(f"Recipient LINK balance after swap: {balance / (10**decimals)}")

    balance = target_contract.functions.balanceOf(recipient_address).call()
    print(f"Recipient SOL balance: {balance / 10**decimals}")

    print(f"Change is SOL balance: {(balance - sol_balance_before) / 10**decimals}")

    return order


if __name__ == "__main__":
    main()
