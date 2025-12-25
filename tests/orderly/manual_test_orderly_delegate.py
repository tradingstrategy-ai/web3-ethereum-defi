import os

import pytest
from eth_account import Account
from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_contract, get_deployed_contract, get_function_selector
from eth_defi.confirmation import broadcast_and_wait_transactions_to_complete
from eth_defi.deploy import deploy_contract
from eth_defi.hotwallet import HotWallet
from eth_defi.orderly.api import OrderlyApiClient
from eth_defi.orderly.vault import OrderlyVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation

JSON_RPC_ARBITRUM_SEPOLIA = os.environ.get("JSON_RPC_ARBITRUM_SEPOLIA")
HOT_WALLET_PRIVATE_KEY = os.environ.get("HOT_WALLET_PRIVATE_KEY")

SIMPLE_VAULT_ADDRESS = "0x5446c9554AAE8992204F3CA75EB971a50BF22Ee3"
ORDERLY_VAULT_ADDRESS = "0x0EaC556c0C2321BA25b9DC01e4e3c95aD5CDCd2f"

web3 = create_multi_provider_web3(JSON_RPC_ARBITRUM_SEPOLIA)
assert web3.eth.chain_id == 421614


def vault_delegate(vault_address: str = SIMPLE_VAULT_ADDRESS, broker_id: str = "woofi_pro"):
    hw = HotWallet(Account.from_key(HOT_WALLET_PRIVATE_KEY))
    hw.sync_nonce(web3)

    simple_vault = get_deployed_contract(web3, "guard/SimpleVaultV0.json", vault_address)

    assert simple_vault.functions.owner().call() == hw.address

    broker_hash = web3.keccak(text=broker_id)

    # TODO: this should be fixed later
    tx = simple_vault.functions.delegate(ORDERLY_VAULT_ADDRESS, (broker_hash, hw.address)).build_transaction(
        {
            "from": hw.address,
            "gas": 500_000,
            "chainId": web3.eth.chain_id,
        }
    )

    signed = hw.sign_transaction_with_new_nonce(tx)

    print(tx)

    receipts = broadcast_and_wait_transactions_to_complete(web3, [signed])

    # # https://stackoverflow.com/a/39292086/315168
    # assert len(receipts) == 1
    # receipt = next(iter(receipts.values()))

    # print(f"Transaction mined in block {receipt.blockNumber:,}, view it at {receipt.transactionHash.hex()}")

    return tx

    # tx = "0x077d46405f33d908a23f9e057425f898d1f69913f33498714c1eb3779021da0d"


def confirm_signer():
    client = OrderlyApiClient(
        account=Account.from_key(HOT_WALLET_PRIVATE_KEY),
        broker_id="woofi_pro",
        chain_id=web3.eth.chain_id,
        is_testnet=True,
    )

    tx = "0x077d46405f33d908a23f9e057425f898d1f69913f33498714c1eb3779021da0d"
    r = client.delegate_signer(
        delegate_contract=SIMPLE_VAULT_ADDRESS,
        delegate_tx_hash=tx,
    )

    print(r)
    # {"success":true,"data":{"user_id":228526,"account_id":"0x5b90565f86c3cf18c88a4be7b55fef17c8836bd4dc16b895704dc70c4ae9b06b","valid_signer":"0x7d9e9dcdFe12cCD5831eD7e4292833D3217872C0"},"timestamp":1752442838963}


def register_key():
    client = OrderlyApiClient(
        account=Account.from_key(HOT_WALLET_PRIVATE_KEY),
        broker_id="woofi_pro",
        chain_id=web3.eth.chain_id,
        is_testnet=True,
    )

    r = client.register_key(
        delegate_contract=SIMPLE_VAULT_ADDRESS,
    )

    print(r)

    # {'success': True, 'data': {'id': 158700, 'orderly_key': 'ed25519:DAtMWbaiLTuidyweqacCwRMon6eJ7ahHEmvvtmouuxPV'}, 'timestamp': 1752479715634}


if __name__ == "__main__":
    # confirm_signer()
    register_key()
