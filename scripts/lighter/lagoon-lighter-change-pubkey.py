"""Print a Lighter ``changePubKey`` (API-key registration) transaction for the Safe Transaction Builder.

Registering a Lighter API key for a Safe-controlled (Lagoon vault) account is an
on-chain ``ZkLighter.changePubKey(accountIndex, apiKeyIndex, pubKey)`` call made
**from the Safe** (Lighter recommends the on-chain ChangePubKey for multisigs).
This is a privileged setup action by the Safe owners (governance) — it is *not*
part of the asset-manager guard whitelist, so it goes directly through the Safe,
not the ``TradingStrategyModule``'s restricted ``performCall`` path.

This CLI does **not** sign or send anything. It prints the transaction so you
can paste it into the Safe{Wallet} **Transaction Builder** (use "Custom data":
the ``To`` address, ``ETH value`` 0, and the raw ``Data`` hex), then collect the
multisig signatures in the Safe UI. (Same idea as the manual guard-migration
instructions printed by trade-executor's ``lagoon-deploy-vault`` command.)

Generate the API keypair off-chain first (the ``lighter-python`` SDK); pass the
resulting public key as ``PUB_KEY`` (40-byte hex).

Example::

    SAFE_ADDRESS=0xYourSafe ACCOUNT_INDEX=12345 API_KEY_INDEX=4 \
        PUB_KEY=0x0101...<40 bytes> \
        python scripts/lighter/lagoon-lighter-change-pubkey.py

Environment variables
---------------------

``SAFE_ADDRESS``   The Lagoon vault's Gnosis Safe (the Lighter account owner). Required.
``ACCOUNT_INDEX``  The Lighter account index. Required.
``API_KEY_INDEX``  API-key slot, 4-254 (0-3 reserved). Default 4.
``PUB_KEY``        New API-key public key, 40-byte hex. Required.
``ZK_LIGHTER``     ZkLighter contract address. Default: Lighter mainnet proxy.
"""

import json
import os

from web3 import Web3

from eth_defi.abi import get_abi_by_filename
from eth_defi.lighter.constants import LIGHTER_L1_CONTRACT
from eth_defi.lighter.pubkey import encode_change_pubkey, validate_lighter_pubkey

#: Lighter deposits/withdrawals live on Ethereum mainnet.
ETHEREUM_CHAIN_ID = 1


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _change_pubkey_abi() -> dict:
    """Return the ``changePubKey`` ABI fragment (for the Safe Transaction Builder)."""
    abi = get_abi_by_filename("lighter/ZkLighter.json")["abi"]
    for entry in abi:
        if entry.get("name") == "changePubKey":
            return entry
    msg = "changePubKey not found in ZkLighter ABI"
    raise RuntimeError(msg)


def main():
    safe_address = Web3.to_checksum_address(_require("SAFE_ADDRESS"))
    account_index = int(_require("ACCOUNT_INDEX"))
    api_key_index = int(os.environ.get("API_KEY_INDEX", "4"))
    pubkey = bytes.fromhex(_require("PUB_KEY").removeprefix("0x"))
    zk_lighter = Web3.to_checksum_address(os.environ.get("ZK_LIGHTER", LIGHTER_L1_CONTRACT))

    # Fail fast on a malformed pubkey before printing anything.
    validate_lighter_pubkey(pubkey)

    # Encode the calldata. No RPC connection is needed — a provider-less Web3 is
    # enough for ABI encoding.
    target, data = encode_change_pubkey(Web3(), account_index, api_key_index, pubkey, zk_lighter)
    data_hex = "0x" + data.hex()
    pubkey_hex = "0x" + pubkey.hex()
    call = f"{target}.changePubKey({account_index}, {api_key_index}, {pubkey_hex})"

    lines = [
        "Lighter changePubKey — Safe Transaction Builder instructions",
        "",
        "Propose this as a Safe transaction FROM the vault Safe (NOT through the",
        "TradingStrategyModule). In the Safe{Wallet} Transaction Builder choose",
        '"Custom data" and enter the To / value / Data below; owners then co-sign.',
        "",
        f"  Chain: Ethereum mainnet (chainId {ETHEREUM_CHAIN_ID})",
        f"  Safe (from):     {safe_address}",
        f"  To (target):     {target}",
        "  ETH value:       0",
        "  Function:        changePubKey(uint48,uint8,bytes)",
        f"  Args:            accountIndex={account_index}, apiKeyIndex={api_key_index}, pubKey={pubkey_hex}",
        f"  Call:            {call}",
        f"  Data (calldata): {data_hex}",
        "",
        "  changePubKey ABI (for the Transaction Builder ABI mode):",
        json.dumps(_change_pubkey_abi(), indent=2),
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    main()
