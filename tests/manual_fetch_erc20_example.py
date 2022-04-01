"""A sample script to manually test brokeness of BSC nodes.

"""
from web3 import Web3, HTTPProvider

from eth_defi.balances import fetch_erc20_balances_by_transfer_event

# node = ""https://bsc-dataseed1.defibit.io/""
# node = "https://bsc-dataseed.binance.org/"
node = "https://rpc.ankr.com/bsc"


web3 = Web3(HTTPProvider(node))

print(f"{node} current block is {web3.eth.block_number:,}")

address_does_not_exist = "0x6564b5053C381a8D840B40d78bA229e2d8e912ed"

# ValueError: {'code': -32000, 'message': 'exceed maximum block range: 5000'}
balances = fetch_erc20_balances_by_transfer_event(web3, address_does_not_exist, from_block=None)
print("Empty", balances)

# https://bscscan.com/address/0xb87dd4b8dadc588ff085624c05844bc144b2be50
some_small_holder = "0xb87dd4b8dadc588ff085624c05844bc144b2be50"
balances = fetch_erc20_balances_by_transfer_event(web3, some_small_holder, from_block=15281071)
print("Small holder", balances)
