# """Testing utilities for GMX.
#
# - This file aims to emulate the off-chain `Keeper`'s actions
# - Use with pytest and Anvil mainnet forks
#
# Here be the dragons.
#
# TODO: Keep this file for future debugging.This was the first draft we created to emulate keepers
#
# """
#
# import json
# import os
# from decimal import Decimal
# from pathlib import Path
# import logging
# from eth_utils import to_checksum_address
# from eth_abi import encode
# from eth_pydantic_types import HexStr
# from eth_utils import keccak
# from hexbytes import HexBytes
# from web3 import Web3
#
# from eth_defi.provider.anvil import make_anvil_custom_rpc_request, is_anvil
# from eth_defi.provider.named import get_provider_name
# from eth_defi.provider.tenderly import is_tenderly
# from eth_defi.token import TokenDetails
# from eth_defi.trace import assert_transaction_success_with_explanation
# from gmx_python_sdk.scripts.v2.get.get_oracle_prices import OraclePrices
# from gmx_python_sdk.scripts.v2.gmx_utils import create_hash_string, get_reader_contract, get_datastore_contract
# from gmx_python_sdk.scripts.v2.utils.exchange import execute_with_oracle_params
# from gmx_python_sdk.scripts.v2.utils.hash_utils import hash_data
# from gmx_python_sdk.scripts.v2.utils.keys import IS_ORACLE_PROVIDER_ENABLED, MAX_ORACLE_REF_PRICE_DEVIATION_FACTOR, oracle_provider_for_token_key, max_pool_amount_key
#
# from eth_defi.gmx.config import GMXConfig
#
# # Create the ORDER_LIST key directly
# ORDER_LIST = create_hash_string("ORDER_LIST")
#
#
# logger = logging.getLogger(__name__)
# ABIS_PATH = os.path.dirname(os.path.abspath(__file__))
#
#
# def set_opt_code(w3: Web3, bytecode=None, contract_address=None):
#     """Replace contract code with mock bytecode on Anvil or Tenderly."""
#
#     # Ensure bytecode has 0x prefix and contract address is checksummed
#     if isinstance(bytecode, bytes):
#         bytecode = bytecode.hex()
#     if not bytecode.startswith("0x"):
#         bytecode = "0x" + bytecode
#     contract_address = to_checksum_address(contract_address)
#
#     # Use Anvil's RPC to set the contract's bytecode
#     if is_tenderly(w3):
#         # https://docs.tenderly.co/virtual-testnets/admin-rpc#tenderly_setcode
#         response = w3.provider.make_request("tenderly_setCode", [contract_address, bytecode])
#     elif is_anvil(w3):
#         response = w3.provider.make_request("anvil_setCode", [contract_address, bytecode])
#     else:
#         raise NotImplementedError(f"Unsupported RPC backend: {get_provider_name(w3.provider)}")
#
#     # Verify the response from the provider
#     if response.get("result"):
#         logger.info("Code successfully set")
#     else:
#         logger.info(f"Failed to set code: {response.get('error', {}).get('message', 'Unknown error')}")
#
#     # Now verify that the code was actually set by retrieving it
#     deployed_code = w3.eth.get_code(contract_address).hex()
#
#     # Compare the deployed code with the mock bytecode
#     expected_code = bytecode[2:] if bytecode.startswith("0x") else bytecode  # Remove 0x prefix for comparison
#     if deployed_code == expected_code:
#         logger.info("Code verification successful: Deployed bytecode matches mock bytecode")
#     else:
#         logger.info("Code verification failed: Deployed bytecode does not match mock bytecode")
#         logger.info(f"Expected: {expected_code}")
#         logger.info(f"Actual: {deployed_code}")
#
#         # You can also check if the length at least matches
#         if len(deployed_code) == len(expected_code) or len(deployed_code) == len(expected_code.lstrip("0x")):
#             logger.info("Lengths match but content differs")
#         else:
#             logger.info(f"Length mismatch - Expected: {len(expected_code)}, Got: {len(deployed_code)}")
#
#
# def execute_order(config, connection, order_key, deployed_oracle_address, initial_token_address, target_token_address, logger=None, overrides=None):
#     """Execute an order with oracle prices
#
#     :param config: Configuration object containing chain and other settings
#     :param connection: Web3 connection object
#     :param order_key: Key of the order to execute
#     :param deployed_oracle_address: Address of the deployed oracle contract
#     :param initial_token_address: Address of the initial token
#     :param target_token_address: Address of the target token
#     :param logger: Optional logger instance
#     :param overrides: Optional transaction overrides
#     :return: Transaction receipt
#     """
#     if logger is None:
#         import logging
#
#         logger = logging.getLogger(__name__)
#
#     if overrides is None:
#         overrides = {}
#
#     # Process override parameters
#     gas_usage_label = overrides.get("gas_usage_label")
#     oracle_block_number_offset = overrides.get("oracle_block_number_offset")
#
#     # Set token addresses if not provided
#     tokens = overrides.get(
#         "tokens",
#         [
#             initial_token_address,
#             target_token_address,
#         ],
#     )
#
#     # Fetch real-time prices
#     oracle_prices = OraclePrices(chain=config.chain).get_recent_prices()
#
#     # Extract prices for the tokens
#     default_min_prices = []
#     default_max_prices = []
#
#     for token in tokens:
#         if token in oracle_prices:
#             token_data = oracle_prices[token]
#
#             # Get the base price values
#             min_price = int(token_data["minPriceFull"])
#             max_price = int(token_data["maxPriceFull"])
#
#             default_min_prices.append(min_price)
#             default_max_prices.append(max_price)
#         else:
#             # Fallback only if token not found in oracle prices
#             logger.warning(f"Price for token {token} not found, using fallback price")
#             default_min_prices.append(5000 * 10**18 if token == tokens[0] else 1 * 10**9)
#             default_max_prices.append(5000 * 10**18 if token == tokens[0] else 1 * 10**9)
#
#     # Set default parameters if not provided
#     data_stream_tokens = overrides.get("data_stream_tokens", [])
#     data_stream_data = overrides.get("data_stream_data", [])
#     price_feed_tokens = overrides.get("price_feed_tokens", [])
#     precisions = overrides.get("precisions", [1, 1])
#
#     min_prices = default_min_prices
#     max_prices = default_max_prices
#
#     # Get oracle block number if not provided
#     oracle_block_number = overrides.get("oracle_block_number")
#     if not oracle_block_number:
#         oracle_block_number = connection.eth.block_number
#
#     # Apply oracle block number offset if provided
#     if oracle_block_number_offset:
#         if oracle_block_number_offset > 0:
#             # Since we can't "mine" blocks in Python directly, this would be handled differently
#             # in a real application. Here we just adjust the number.
#             pass
#
#         oracle_block_number += oracle_block_number_offset
#
#     # Extract additional oracle parameters
#     oracle_blocks = overrides.get("oracle_blocks")
#     min_oracle_block_numbers = overrides.get("min_oracle_block_numbers")
#     max_oracle_block_numbers = overrides.get("max_oracle_block_numbers")
#     oracle_timestamps = overrides.get("oracle_timestamps")
#     block_hashes = overrides.get("block_hashes")
#
#     oracle_signer = overrides.get("oracle_signer", config.get_signer())
#
#     # Build the parameters for execute_with_oracle_params
#     params = {
#         "key": order_key,
#         "oracleBlockNumber": oracle_block_number,
#         "tokens": tokens,
#         "precisions": precisions,
#         "minPrices": min_prices,
#         "maxPrices": max_prices,
#         "simulate": overrides.get("simulate", False),
#         "gasUsageLabel": gas_usage_label,
#         "oracleBlocks": oracle_blocks,
#         "minOracleBlockNumbers": min_oracle_block_numbers,
#         "maxOracleBlockNumbers": max_oracle_block_numbers,
#         "oracleTimestamps": oracle_timestamps,
#         "blockHashes": block_hashes,
#         "dataStreamTokens": data_stream_tokens,
#         "dataStreamData": data_stream_data,
#         "priceFeedTokens": price_feed_tokens,
#     }
#
#     # Create a fixture-like object with necessary properties
#     fixture = {
#         "config": config,
#         "web3Provider": connection,
#         "chain": config.chain,
#         "accounts": {"signers": [oracle_signer] * 7},
#         "props": {
#             "oracleSalt": hash_data(["uint256", "string"], [config.chain_id, "xget-oracle-v1"]),
#             "signerIndexes": [0, 1, 2, 3, 4, 5, 6],
#         },
#     }
#
#     # Call execute_with_oracle_params with the built parameters
#     return execute_with_oracle_params(fixture, params, config, deployed_oracle_address=deployed_oracle_address)
#
#
# GMX_ADMIN = "0x7A967D114B8676874FA2cFC1C14F3095C88418Eb"
#
#
# def deploy_custom_oracle(w3: Web3, account) -> str:
#     # Delpoy the `Oracle` contract here & then return the deployed bytecode
#     # Check balance
#     balance = w3.eth.get_balance(account)
#
#     # Load contract ABI and bytecode
#     artifacts_path = Path(f"{ABIS_PATH}/mock_abis/Oracle.json")
#
#     with open(artifacts_path) as f:
#         contract_json = json.load(f)
#         abi = contract_json["abi"]
#         bytecode = contract_json["bytecode"]
#
#     # Constructor arguments
#     role_store = "0x3c3d99FD298f679DBC2CEcd132b4eC4d0F5e6e72"
#     data_store = "0xFD70de6b91282D8017aA4E741e9Ae325CAb992d8"
#     event_emitter = "0xC8ee91A54287DB53897056e12D9819156D3822Fb"
#     sequender_uptime_feed = "0xFdB631F5EE196F0ed6FAa767959853A9F217697D"
#
#     # Create contract factory
#     contract = w3.eth.contract(abi=abi, bytecode=bytecode)
#
#     # Prepare transaction for contract deployment
#     nonce = w3.eth.get_transaction_count(account)
#     transaction = contract.constructor(role_store, data_store, event_emitter, sequender_uptime_feed).build_transaction(
#         {
#             "from": account,
#             "nonce": nonce,
#             "gas": 33000000,
#         }
#     )
#
#     # Send transaction
#     tx_hash = w3.eth.send_transaction(transaction)
#     assert_transaction_success_with_explanation(w3, tx_hash)
#
#     # Wait for transaction receipt
#     tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
#     contract_address = tx_receipt["contractAddress"]
#     logger.info(f"🚀 Deployed GmOracleProvider to: {contract_address}")
#
#     # Get deployed contract
#     deployed_contract = w3.eth.contract(address=contract_address, abi=abi)
#
#     # Fetch on-chain bytecode and print its size
#     code = w3.eth.get_code(contract_address)
#
#     # Verify constructor-stored state
#     role_store_address = deployed_contract.functions.roleStore().call()
#     data_store_address = deployed_contract.functions.dataStore().call()
#     event_emitter_address = deployed_contract.functions.eventEmitter().call()
#
#     bytecode = w3.eth.get_code(contract_address)
#
#     original_oracle_contract = to_checksum_address("0x918b60ba71badfada72ef3a6c6f71d0c41d4785c")
#
#     set_opt_code(w3, bytecode, original_oracle_contract)
#
#     return contract_address
#
#
# def deploy_custom_oracle_provider(w3: Web3, account) -> str:
#     # Check balance
#     balance = w3.eth.get_balance(account)
#     # print(f"Deployer balance: {w3.from_wei(balance, 'ether')} ETH")
#
#     # Load contract ABI and bytecode
#     artifacts_path = Path(f"{ABIS_PATH}/mock_abis/GmOracleProvider.json")
#     with open(artifacts_path) as f:
#         contract_json = json.load(f)
#         abi = contract_json["abi"]
#         bytecode = contract_json["bytecode"]
#
#     # Constructor arguments
#     role_store = "0x3c3d99FD298f679DBC2CEcd132b4eC4d0F5e6e72"
#     data_store = "0xFD70de6b91282D8017aA4E741e9Ae325CAb992d8"
#     oracle_store = "0xA8AF9B86fC47deAde1bc66B12673706615E2B011"
#
#     # Create contract factory
#     contract = w3.eth.contract(abi=abi, bytecode=bytecode)
#
#     # Prepare transaction for contract deployment
#     nonce = w3.eth.get_transaction_count(account)
#     transaction = contract.constructor(role_store, data_store, oracle_store).build_transaction(
#         {
#             "from": account,
#             "nonce": nonce,
#             "gas": 33000000,
#         }
#     )
#
#     # Send transaction
#     # import ipdb ; ipdb.set_trace()
#     tx_hash = w3.eth.send_transaction(transaction)
#     assert_transaction_success_with_explanation(w3, tx_hash)
#
#     # Wait for transaction receipt
#     tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
#     contract_address = tx_receipt["contractAddress"]
#     logger.info(f"🚀 Deployed GmOracleProvider to: {contract_address}")
#
#     # Get deployed contract
#     deployed_contract = w3.eth.contract(address=contract_address, abi=abi)
#
#     # Fetch on-chain bytecode and print its size
#     code = w3.eth.get_code(contract_address)
#
#     # Verify constructor-stored state
#     role_store_address = deployed_contract.functions.roleStore().call()
#     data_store_address = deployed_contract.functions.dataStore().call()
#
#     return contract_address
#
#
# def override_storage_slot(contract_address, slot, value, web3):
#     """Override a storage slot in an Anvil fork.
#
#     :param contract_address: The address of the contract
#     :param slot: The storage slot to override (as a hex string)
#     :param value: The value to set (as an integer)
#     :param web3: Web3 object
#     """
#
#     # Check connection
#     if not web3.is_connected():
#         raise Exception(f"Could not connect to Anvil node at {web3.provider.endpoint_uri}")
#
#     # Format the value to a 32-byte hex string with '0x' prefix
#     # First convert to hex without '0x'
#     hex_value = hex(value)[2:]
#
#     # Pad to 64 characters (32 bytes) and add '0x' prefix
#     padded_hex_value = "0x" + hex_value.zfill(64)
#
#     # Make sure the slot has '0x' prefix
#     if not slot.startswith("0x"):
#         slot = "0x" + slot
#
#     # Ensure contract address has '0x' prefix and is checksummed
#     if not contract_address.startswith("0x"):
#         contract_address = "0x" + contract_address
#
#     contract_address = web3.to_checksum_address(contract_address)
#
#     # Call the anvil_setStorageAt RPC method
#     if is_tenderly(web3):
#         result = web3.provider.make_request("tenderly_setStorageAt", [contract_address, slot, padded_hex_value])
#     elif is_anvil(web3):
#         result = web3.provider.make_request("anvil_setStorageAt", [contract_address, slot, padded_hex_value])
#     else:
#         raise NotImplementedError(f"Unsupported RPC backend: {get_provider_name(web3.provider)}")
#
#     # Check for errors
#     if "error" in result:
#         raise Exception(f"Error setting storage: {result['error']}")
#
#     logger.info(f"Successfully set storage at slot {slot} to {padded_hex_value}")
#
#     storage_value = web3.eth.get_storage_at(contract_address, slot)
#
#     return result
#
#
# def get_next_hex_slot(hex_str: str) -> str:
#     """
#     Takes a hex string (like a storage slot) and returns the next slot as a hex string.
#     E.g., '0x...958' → '0x...959'
#     Because some of the last digits will not be numbers. It can be in the range of hex chars A-F. Handle those cases.
#     """
#     int_val = int(hex_str, 16)
#     next_int_val = int_val + 1
#     return hex(next_int_val)
#
#
# def emulate_keepers(
#     gmx_config: GMXConfig,
#     initial_token: TokenDetails,
#     target_token: TokenDetails,
#     w3: Web3,
#     recipient_address: str,
#     debug_logs: bool = False,
#     deployer_address: str | None = None,
# ) -> HexStr:
#     """Fake GMX keeper transaction to fulfill an order.
#
#     - Uses Anvil to spoof GMX Keeper infra on Arbitrum
#
#     :return:
#         The transaction hash of the last of keeper transactions
#     """
#     initial_token_symbol = initial_token.symbol
#     initial_token_address = initial_token.address
#     target_token_symbol = target_token.symbol
#     target_token_address = target_token.address
#
#     if deployer_address is None:
#         deployer_address = recipient_address
#
#     if debug_logs:
#         erc20_abi = [
#             {
#                 "constant": True,
#                 "inputs": [{"name": "_owner", "type": "address"}],
#                 "name": "balanceOf",
#                 "outputs": [{"name": "balance", "type": "uint256"}],
#                 "type": "function",
#             },
#             {
#                 "constant": False,
#                 "inputs": [
#                     {"name": "_to", "type": "address"},
#                     {"name": "_value", "type": "uint256"},
#                 ],
#                 "name": "transfer",
#                 "outputs": [{"name": "", "type": "bool"}],
#                 "type": "function",
#             },
#             {
#                 "constant": True,
#                 "inputs": [],
#                 "name": "decimals",
#                 "outputs": [{"name": "", "type": "uint8"}],
#                 "type": "function",
#             },
#             {
#                 "constant": True,
#                 "inputs": [],
#                 "name": "symbol",
#                 "outputs": [{"name": "", "type": "string"}],
#                 "payable": False,
#                 "stateMutability": "view",
#                 "type": "function",
#             },
#         ]
#
#         initial_token_contract = w3.eth.contract(address=initial_token_address, abi=erc20_abi)
#         target_contract = w3.eth.contract(address=target_token_address, abi=erc20_abi)
#
#         decimals = initial_token_contract.functions.decimals().call()
#         symbol = initial_token_contract.functions.symbol().call()
#
#         # Check initial balances
#         balance = initial_token_contract.functions.balanceOf(recipient_address).call()
#         logger.info(f"Recipient {initial_token_symbol} balance: {Decimal(balance / 10**decimals)} {symbol}")
#
#         target_balance_before = target_contract.functions.balanceOf(recipient_address).call()
#         target_symbol = target_contract.functions.symbol().call()
#         target_decimals = target_contract.functions.decimals().call()
#
#         # Convert both values to Decimal BEFORE division
#         balance_decimal = Decimal(str(target_balance_before)) / Decimal(10**target_decimals)
#
#         # Format to avoid scientific notation and show proper decimal places
#         logger.info(f"Recipient {target_token_symbol} balance before: {balance_decimal:.18f} {target_symbol}")
#
#     deployed: tuple = (None, None)  # (None, None)
#     if not deployed[0]:
#         deployed_oracle_address = deploy_custom_oracle_provider(w3, deployer_address)
#         custom_oracle_contract_address = deploy_custom_oracle(w3, deployer_address)
#     else:
#         deployed_oracle_address = deployed[0]
#         custom_oracle_contract_address = deployed[1]
#
#     try:
#         config = gmx_config.get_config()
#         # order_key = order.create_order_and_get_key()
#
#         data_store = get_datastore_contract(config)
#
#         assert ORDER_LIST.hex().removeprefix("0x") == "0x86f7cfd5d8f8404e5145c91bebb8484657420159dabd0753d6a59f3de3f7b8c1".removeprefix("0x"), "Order list mismatch"
#         order_count = data_store.functions.getBytes32Count(ORDER_LIST).call()
#         if order_count == 0:
#             raise Exception("No orders found")
#
#         # Get the most recent order key
#         order_key = data_store.functions.getBytes32ValuesAt(ORDER_LIST, order_count - 1, order_count).call()[0]
#
#         # reader = get_reader_contract(config)
#         # order_info = reader.functions.getOrder(data_store.address, order_key).call()
#         # print(f"Order: {order_info}")
#
#         # data_store_owner = "0xE7BfFf2aB721264887230037940490351700a068"
#         controller = "0xf5F30B10141E1F63FC11eD772931A8294a591996"
#         oracle_provider = "0x5d6B84086DA6d4B0b6C0dF7E02f8a6A039226530"
#         custom_oracle_provider = deployed_oracle_address  # "0xA1D67424a5122d83831A14Fa5cB9764Aeb15CD99"
#
#         oracle_signer = "0x0F711379095f2F0a6fdD1e8Fccd6eBA0833c1F1f"
#         # set this value to true to pass the provider enabled check in contract
#         # OrderHandler(0xfc9bc118fddb89ff6ff720840446d73478de4153)
#
#         # Set the controller address to have enough balance to execute the transaction
#         balance_in_wei = 10**18  # 1 ETH in wei
#         assert controller == "0xf5F30B10141E1F63FC11eD772931A8294a591996"
#
#         if is_tenderly(w3):
#             make_anvil_custom_rpc_request(w3, "tenderly_setBalance", [controller, hex(balance_in_wei)])
#
#         elif is_anvil(w3):
#             make_anvil_custom_rpc_request(w3, "anvil_setBalance", [controller, hex(balance_in_wei)])
#         else:
#             raise NotImplementedError(f"Unsupported RPC backend: {get_provider_name(w3.provider)}")
#
#         bool_key: str = "0x1153e082323163af55b3003076402c9f890dda21455104e09a048bf53f1ab30c"
#         data_store.functions.setBool(bool_key, True).transact(
#             {
#                 "from": controller,
#                 "gas": 1_000_000,
#             }
#         )
#
#         value = data_store.functions.getBool(bool_key).call()
#
#         assert value, "Value should be true"
#
#         # * Dynamically fetch the storage slot for the oracle provider
#         # ? Get this value dynamically https://github.com/gmx-io/gmx-synthetics/blob/e8344b5086f67518ca8d33e88c6be0737f6ae4a4/contracts/data/Keys.sol#L938
#         # ? Python ref: https://gist.github.com/Aviksaikat/cc69acb525695e44db340d64e9889f5e
#         encoded_data = encode(["bytes32", "address"], [IS_ORACLE_PROVIDER_ENABLED, custom_oracle_provider])
#         slot = f"0x{keccak(encoded_data).hex()}"
#
#         # Enable the oracle provider
#         data_store.functions.setBool(slot, True).transact({"from": controller})
#         is_oracle_provider_enabled: bool = data_store.functions.getBool(slot).call()
#         # print(f"Value: {is_oracle_provider_enabled}")
#         assert is_oracle_provider_enabled, "Value should be true"
#
#         # Each token has its own oracle provider set.
#         # This will tell the token price against USD(C).
#
#         # pass the test `address expectedProvider = dataStore.getAddress(Keys.oracleProviderForTokenKey(token));` in Oracle.sol#L278
#         # Keys.oracleProviderForTokenKey(token)
#
#         # Address slot for the token we are buying
#         # token 0xaf88d065e77c8cC2239327C5EDb3A432268e5831
#         # address_slot: str = "0x233a49594db4e7a962a8bd9ec7298b99d6464865065bd50d94232b61d213f16d"
#         for token in (initial_token_address, target_token_address):
#             address_slot = bytes.fromhex(oracle_provider_for_token_key(token).hex().removeprefix("0x"))
#             logger.info(
#                 "Setting ORACLE_PROVIDER_FOR_TOKEN, token is %s, address slot %s, oracle provider %s",
#                 token,
#                 address_slot.hex(),
#                 custom_oracle_provider,
#             )
#             data_store.functions.setAddress(address_slot, custom_oracle_provider).transact({"from": controller})
#
#             # Double check our set operation succeeded
#             new_address = data_store.functions.getAddress(address_slot).call()
#             assert new_address == custom_oracle_provider, "New address should be the oracle provider"
#
#         # need this to be set to pass the `Oracle._validatePrices` check. Key taken from anvil tx debugger
#         address_key: str = "0xf986b0f912da0acadea6308636145bb2af568ddd07eb6c76b880b8f341fef306"  # "0xf986b0f912da0acadea6308636145bb2af568ddd07eb6c76b880b8f341fef306"
#         data_store.functions.setAddress(address_key, custom_oracle_provider).transact({"from": controller})
#         value = data_store.functions.getAddress(address_key).call()
#         # print(f"Value: {value}")
#         assert value == custom_oracle_provider, "Value should be recipient address"
#
#         # ? Set another key value to pass the test in `Oracle.sol` this time for ChainlinkDataStreamProvider
#         address_key: str = "0x659d3e479f4f2d295ea225e3d439a6b9d6fbf14a5cd4689e7d007fbab44acb8a"
#         data_store.functions.setAddress(address_key, custom_oracle_provider).transact({"from": controller})
#         value = data_store.functions.getAddress(address_key).call()
#         # print(f"Value: {value}")
#         assert value == custom_oracle_provider, "Value should be recipient address"
#
#         # ? Set the `maxRefPriceDeviationFactor` to pass tests in `Oracle.sol`
#         price_deviation_factor_key: bytes = bytes.fromhex(MAX_ORACLE_REF_PRICE_DEVIATION_FACTOR.hex().removeprefix("0x"))
#         # * set some big value to pass the test
#         large_value: int = 10021573904618365809021423188717
#         data_store.functions.setUint(price_deviation_factor_key, large_value).transact({"from": controller})
#         value = data_store.functions.getUint(price_deviation_factor_key).call()
#         # print(f"Value: {value}")
#         assert value == large_value, f"Value should be {large_value}"
#
#         # Override min/max token prices
#         oracle_contract: str = "0x918b60ba71badfada72ef3a6c6f71d0c41d4785c"
#
#         # token_b_max_value_slot: str = "0x636d2c90aa7802b40e3b1937e91c5450211eefbc7d3e39192aeb14ee03e3a958"
#         # token_b_min_value_slot: str = "0x636d2c90aa7802b40e3b1937e91c5450211eefbc7d3e39192aeb14ee03e3a959"
#
#         token_b_max_value_slot = HexBytes(oracle_provider_for_token_key(target_token_address)).hex()
#         # token_b_min_value_slot = token_b_max_value_slot[:-1] + str(int(token_b_max_value_slot[-1]) + 1)
#         token_b_min_value_slot = get_next_hex_slot(token_b_max_value_slot)
#         # print(f"{token_b_max_value_slot=}")
#         # print(f"{token_b_min_value_slot=}")
#
#         oracle_prices = OraclePrices(chain=config.chain).get_recent_prices()
#
#         # target_token_address = target_token_address.lower()
#         # oracle_prices = {k.lower(): v for k, v in oracle_prices.items()}
#         max_price: int = int(oracle_prices[target_token_address]["maxPriceFull"])
#         min_price: int = int(oracle_prices[target_token_address]["minPriceFull"])
#         # min_price = 10
#         # max_price = 2**64 - 1
#
#         max_res = override_storage_slot(oracle_contract, token_b_max_value_slot, max_price, w3)
#         min_res = override_storage_slot(oracle_contract, token_b_min_value_slot, min_price, w3)
#
#         # override_storage_slot(oracle_contract, slot, min_price, w3)
#         # override_storage_slot(oracle_contract, token_b_min_value_slot, max_price, w3)
#
#         # print(f"Max price: {max_price}")
#         # print(f"Min price: {min_price}")
#         # print(f"Max res: {max_res}")
#         # print(f"Min res: {min_res}")
#
#         # * set some big value to pass the test
#         large_value: int = 9914611141387747627324635505610366123
#         key_slot: bytes = bytes.fromhex(max_pool_amount_key("0x1cbba6346f110c8a5ea739ef2d1eb182990e4eb2", target_token_address).hex().removeprefix("0x"))
#         # print(f"{key_slot.hex()=}")
#         data_store.functions.setUint(key_slot, large_value).transact({"from": controller})
#         value = data_store.functions.getUint(key_slot).call()
#         # print(f"Value: {value}")
#         assert value == large_value, f"Value should be {large_value}"
#
#         # print(f"Order key: {order_key.hex()}")
#         overrides = {
#             "simulate": False,
#         }
#         # Execute the order with oracle prices
#         tx_hash = execute_order(
#             config=config,
#             connection=w3,
#             order_key=order_key,
#             deployed_oracle_address=deployed_oracle_address,
#             initial_token_address=initial_token_address,
#             target_token_address=target_token_address,
#             overrides=overrides,
#         )
#         # print(f"Transaction hash: {tx_hash.hex()}")
#
#         if debug_logs:
#             # Check the balances after execution
#             balance = initial_token_contract.functions.balanceOf(recipient_address).call()
#             symbol = initial_token_contract.functions.symbol().call()
#             logger.info(f"Recipient {initial_token_symbol} balance after swap: {Decimal(balance / 10**decimals)} {symbol}")
#
#             target_balance_after = target_contract.functions.balanceOf(recipient_address).call()
#             symbol = target_contract.functions.symbol().call()
#             target_decimals = target_contract.functions.decimals().call()
#
#             balance_decimal = Decimal(str(target_balance_after)) / Decimal(10**target_decimals)
#
#             # Format to avoid scientific notation and show proper decimal places
#             logger.info(f"Recipient {target_token_symbol} balance after swap: {balance_decimal:.18f} {target_symbol}")
#             logger.info(f"Change in {target_token_symbol} balance: {Decimal((target_balance_after - target_balance_before) / 10**target_decimals):.18f}")
#
#         return tx_hash
#     except Exception as e:
#         logger.error(f"Error during swap process: {e!s}")
#         raise e
