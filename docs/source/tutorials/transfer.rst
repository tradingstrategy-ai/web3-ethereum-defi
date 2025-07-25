.. meta::
   :description: Python example code for performing ERC-20 transfer()

ERC-20 token transfer with web3.py
----------------------------------

This is a tutorial on how to transfer ERC-20 token in Python
with `web3-ethereum-defi <https://github.com/tradingstrategy-ai/web3-ethereum-defi>`_ package.

You need

- A private key in hexadecimal format

- ETH or other EVM native token for gas fees

- `JSON-RPC node to connect to the blockchain <https://ethereumnodes.com/>`_

- A Python virtual environment with `web3-ethereum-defi <https://github.com/tradingstrategy-ai/web3-ethereum-defi>`_ installed

- Understanding how to operate command line and command line applications

`For any questions please join to Discord chat <https://tradingstrategy.ai/community>`__.

Set up
~~~~~~

Import your private key to an environment variable in UNIX shell:

.. code-block:: shell

    export PRIVATE_KEY="0x1111111111111"

Set up your JSON-RPC connection:

.. code-block:: shell

    export JSON_RPC_URL="https://"

Transfer script
~~~~~~~~~~~~~~~

Then create the following script:

.. code-block:: python

    """Manual transfer script.

    - For a hardcoded token, asks to address and amount where to transfer tokens.

    - Waits for the transaction to complete
    """
    import datetime
    import os
    import sys
    from decimal import Decimal

    from eth_account import Account
    from eth_account.signers.local import LocalAccount
    from web3 import HTTPProvider, Web3
    from web3.middleware import construct_sign_and_send_raw_middleware

    from eth_defi.abi import get_deployed_contract
    from eth_defi.token import fetch_erc20_details
    from eth_defi.confirmation import wait_transactions_to_complete

    # What is the token we are transferring.
    # Replace with your own token address.
    ERC_20_TOKEN_ADDRESS = "0x0aC7B3733cBeE5D87A80fbf331f4A0bD01f17386"

    # Connect to JSON-RPC node
    json_rpc_url = os.environ["JSON_RPC_URL"]
    web3 = Web3(HTTPProvider(json_rpc_url))
    print(f"Connected to blockchain, chain id is {web3.eth.chain_id}. the latest block is {web3.eth.block_number:,}")

    # Read and setup a local private key
    private_key = os.environ.get("PRIVATE_KEY")
    assert private_key is not None, "You must set PRIVATE_KEY environment variable"
    assert private_key.startswith("0x"), "Private key must start with 0x hex prefix"
    account: LocalAccount = Account.from_key(private_key)
    web3.middleware_onion.add(construct_sign_and_send_raw_middleware(account))

    # Show users the current status of token and his address
    erc_20 = get_deployed_contract(web3, "ERC20MockDecimals.json", ERC_20_TOKEN_ADDRESS)
    token_details = fetch_erc20_details(web3, ERC_20_TOKEN_ADDRESS)

    print(f"Token details are {token_details}")

    balance = erc_20.functions.balanceOf(account.address).call()
    eth_balance = web3.eth.get_balance(account.address)

    print(f"Your balance is: {token_details.convert_to_decimals(balance)} {token_details.symbol}")
    print(f"Your have {eth_balance/(10**18)} ETH for gas fees")

    # Ask for transfer details
    decimal_amount = input("How many tokens to transfer? ")
    to_address = input("Give destination Ethereum address? ")

    # Some input validation
    try:
        decimal_amount = Decimal(decimal_amount)
    except ValueError as e:
        raise AssertionError(f"Not a good decimal amount: {decimal_amount}") from e

    assert web3.is_checksum_address(to_address), f"Not a checksummed Ethereum address: {to_address}"

    # Fat-fingering check
    print(f"Confirm transferring {decimal_amount} {token_details.symbol} to {to_address}")
    confirm = input("Ok [y/n]?")
    if not confirm.lower().startswith("y"):
        print("Aborted")
        sys.exit(1)

    # Convert a human-readable number to fixed decimal with 18 decimal places
    raw_amount = token_details.convert_to_raw(decimal_amount)
    tx_hash = erc_20.functions.transfer(to_address, raw_amount).transact({"from": account.address})

    # This will raise an exception if we do not confirm within the timeout
    print(f"Broadcasted transaction {tx_hash.hex()}, now waiting 5 minutes for mining")
    wait_transactions_to_complete(web3, [tx_hash], max_timeout=datetime.timedelta(minutes=5))

    print("All ok!")

Running
~~~~~~~

Run the script:

.. code-block:: shell

    python scripts/erc20-manual-transfer.py

Example output

.. code-block:: none

    Connected to blockchain, chain id is 1. the latest block is 14,627,918
    Token details are <XXX (XXX) at 0x0aC7B3733cBeE5D87A80fbf331f4A0bD01f17386>
    Your balance is: 369999999 XXX
    Your have : 0.2679961495972585 ETH for gas fees
    How many tokens to transfer? 1
    Give destination Ethereum address? 0x6449299d1d268c4008b4fB992afd04AB5fAec4E6
    Confirm transferring 1 XXX to 0x6449299d1d268c4008b4fB992afd04AB5fAec4E6
    Ok [y/n]?y
    Broadcasted transaction 0xfed8c07b1da1d4348d3ea0ec678f30082fc8e944ada4b0f6510b5a7c05ceb910, now waiting 5 minutes for mining
    All ok!

More information
~~~~~~~~~~~~~~~~

- `Private key management with web3.py <https://web3py.readthedocs.io/en/latest/web3.eth.account.html#read-a-private-key-from-an-environment-variable>`_
