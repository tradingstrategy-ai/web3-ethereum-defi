"""Propose a Lighter ``changePubKey`` (API-key registration) for a Lagoon-vault Safe.

Registering a Lighter API key for a Safe-controlled account is an on-chain
``ZkLighter.changePubKey(accountIndex, apiKeyIndex, pubKey)`` call made **from
the Safe** (Lighter recommends the on-chain ChangePubKey for multisigs). This
is a privileged setup action by the Safe owners (governance) — it is *not* part
of the asset-manager guard whitelist, so it goes directly through the Safe, not
the ``TradingStrategyModule``'s restricted ``performCall`` path.

This CLI builds that call (validating the pubkey) and, depending on ``MODE``:

- ``propose`` (default): posts a signed Safe transaction to the Safe
  Transaction Service so the remaining owners can co-sign in the Safe UI.
- ``execute``: builds, signs and executes immediately (single-owner Safe).

Set ``SIMULATE=true`` to dry-run on an Anvil Ethereum-mainnet fork: the call is
executed as if sent by the Safe (impersonated via Anvil) so you can confirm it
would succeed before touching the real multisig. No signatures or funds needed.

Generate the API keypair off-chain first (the ``lighter-python`` SDK); pass the
resulting public key as ``PUB_KEY`` (40-byte hex).

Examples::

    # Dry-run on a fork
    SIMULATE=true JSON_RPC_ETHEREUM="https://eth.llamarpc.com" \
        SAFE_ADDRESS=0xYourSafe ACCOUNT_INDEX=123 API_KEY_INDEX=2 \
        PUB_KEY=0x0101...<40 bytes> python scripts/lighter/lagoon-lighter-change-pubkey.py

    # Propose to the Safe Transaction Service (real multisig)
    JSON_RPC_ETHEREUM=... SAFE_ADDRESS=0xYourSafe ACCOUNT_INDEX=123 API_KEY_INDEX=2 \
        PUB_KEY=0x0101... PRIVATE_KEY=0x<proposer owner key> MODE=propose \
        python scripts/lighter/lagoon-lighter-change-pubkey.py

Environment variables
---------------------

``JSON_RPC_ETHEREUM``  Ethereum mainnet RPC. Required.
``SAFE_ADDRESS``       The Lagoon vault's Gnosis Safe (the Lighter account owner). Required.
``ACCOUNT_INDEX``      The Lighter account index. Required.
``API_KEY_INDEX``      API-key slot, 2-254 (0-1 reserved). Default 2.
``PUB_KEY``            New API-key public key, 40-byte hex. Required.
``ZK_LIGHTER``         ZkLighter contract address. Default: Lighter mainnet proxy.
``PRIVATE_KEY``        Proposing/executing owner key. Required unless ``SIMULATE``.
``MODE``               ``propose`` (default) or ``execute``. Ignored when ``SIMULATE``.
``SIMULATE``           ``true`` to dry-run on an Anvil mainnet fork.
``SIMULATE_FORK_BLOCK``Fork block (default 25000000).
"""

import logging
import os

from eth_defi.abi import get_deployed_contract
from eth_defi.lighter.constants import LIGHTER_L1_CONTRACT
from eth_defi.lighter.pubkey import (
    execute_change_pubkey,
    propose_change_pubkey,
    validate_lighter_pubkey,
)
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.safe.safe_compat import create_safe_ethereum_client
from eth_defi.trace import assert_transaction_success_with_explanation

logger = logging.getLogger(__name__)


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"{name} is required")
    return value


def main():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

    json_rpc_url = _require("JSON_RPC_ETHEREUM")
    safe_address = _require("SAFE_ADDRESS")
    account_index = int(_require("ACCOUNT_INDEX"))
    api_key_index = int(os.environ.get("API_KEY_INDEX", "2"))
    pubkey = bytes.fromhex(_require("PUB_KEY").removeprefix("0x"))
    zk_lighter = os.environ.get("ZK_LIGHTER", LIGHTER_L1_CONTRACT)
    simulate = os.environ.get("SIMULATE", "").lower() in ("true", "1", "yes")

    # Fail fast on a malformed pubkey before any network interaction.
    validate_lighter_pubkey(pubkey)

    if simulate:
        # Local imports so the fork-only deps are not needed for real runs.
        from eth_defi.provider.anvil import fork_network_anvil
        from eth_defi.safe.simulate import simulate_safe_execution_anvil

        fork_block = int(os.environ.get("SIMULATE_FORK_BLOCK", "25000000"))
        print(f"\nSIMULATE: forking Ethereum mainnet at block {fork_block}...")
        anvil = fork_network_anvil(json_rpc_url, fork_block_number=fork_block)
        try:
            web3 = create_multi_provider_web3(anvil.json_rpc_url, default_http_timeout=(3.0, 180.0))
            assert web3.eth.chain_id == 1
            zk = get_deployed_contract(web3, "lighter/ZkLighter.json", web3.to_checksum_address(zk_lighter))
            func = zk.functions.changePubKey(account_index, api_key_index, pubkey)
            tx_hash = simulate_safe_execution_anvil(web3, safe_address, func)
            assert_transaction_success_with_explanation(web3, tx_hash)
            print(f"\nDry-run OK: ZkLighter.changePubKey would succeed from Safe {safe_address}")
            print(f"  account_index={account_index} api_key_index={api_key_index} tx={tx_hash.hex()}")
        finally:
            anvil.close()
        return

    web3 = create_multi_provider_web3(json_rpc_url)
    assert web3.eth.chain_id == 1, "Lighter changePubKey is on Ethereum mainnet"
    private_key = _require("PRIVATE_KEY")

    # safe_eth Safe object backed by the RPC.
    from safe_eth.safe import Safe

    safe = Safe(web3.to_checksum_address(safe_address), create_safe_ethereum_client(web3))

    mode = os.environ.get("MODE", "propose").lower()
    if mode == "execute":
        tx_hash = execute_change_pubkey(web3, safe, private_key, account_index, api_key_index, pubkey, zk_lighter)
        print(f"\nExecuted changePubKey: {tx_hash.hex()}")
    elif mode == "propose":
        safe_tx = propose_change_pubkey(web3, safe, private_key, account_index, api_key_index, pubkey, zk_lighter)
        print(f"\nProposed changePubKey to the Safe Transaction Service.")
        print(f"  safeTxHash (owners co-sign this): {safe_tx.safe_tx_hash.hex()}")
    else:
        raise ValueError(f"MODE must be 'propose' or 'execute', got '{mode}'")


if __name__ == "__main__":
    main()
