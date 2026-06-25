"""Register a Lighter API key (changePubKey) from a Gnosis Safe.

Creating a Lighter API key for a Safe-controlled (Lagoon vault) account is an
on-chain ``ZkLighter.changePubKey(accountIndex, apiKeyIndex, pubKey)`` call made
from the Safe. These tests run against an Anvil fork of Ethereum mainnet (Lighter
is L1) and exercise :py:mod:`eth_defi.lighter.pubkey`.
"""

import os

import pytest
from web3 import Web3
from web3.exceptions import ContractLogicError

from eth_defi.hotwallet import HotWallet
from eth_defi.lighter.pubkey import (
    PUB_KEY_BYTES_SIZE,
    encode_change_pubkey,
    execute_change_pubkey,
    validate_lighter_pubkey,
)
from eth_defi.lighter.testing import register_lighter_account_on_anvil
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil, set_balance
from eth_defi.provider.fallback import ExtraValueError
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.safe.deployment import deploy_safe

#: A guard/Safe revert surfaces as web3's ContractLogicError or, through the
#: eth_defi multi-provider, as ExtraValueError.
REVERT_ERRORS = (ContractLogicError, ExtraValueError)

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(
    JSON_RPC_ETHEREUM is None,
    reason="Set JSON_RPC_ETHEREUM env to run Lighter changePubKey tests",
)

#: A well-formed 40-byte Lighter pubkey: each 8-byte little-endian limb is
#: 0x0101010101010101 — below the Goldilocks modulus and non-zero.
VALID_PUBKEY = b"\x01" * PUB_KEY_BYTES_SIZE


@pytest.fixture(scope="module")
def anvil_ethereum_fork() -> AnvilLaunch:
    """Fork Ethereum mainnet at a fixed block where ZkLighter is current."""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=25_000_000)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture()
def web3(anvil_ethereum_fork: AnvilLaunch) -> Web3:
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, default_http_timeout=(3, 250.0))
    assert web3.eth.chain_id == 1
    return web3


@pytest.fixture()
def deployer(web3: Web3) -> HotWallet:
    hot_wallet = HotWallet.create_for_testing(web3, eth_amount=0)
    set_balance(web3, hot_wallet.address, 10 * 10**18)
    hot_wallet.sync_nonce(web3)
    return hot_wallet


@pytest.fixture()
def safe(web3: Web3, deployer: HotWallet):
    """A single-owner Safe standing in for the Lagoon vault's Safe."""
    return deploy_safe(web3, deployer.account, [deployer.address], 1, post_deploy_delay_seconds=0.0)


def test_validate_lighter_pubkey():
    """Client-side pubkey validation mirrors the contract checks.

    1. A canonical 40-byte key is accepted.
    2. A wrong-length key is rejected.
    3. An all-zero key is rejected.
    4. A key whose first limb exceeds the Goldilocks modulus is rejected.
    """
    # 1. Canonical key accepted
    validate_lighter_pubkey(VALID_PUBKEY)

    # 2. Wrong length
    with pytest.raises(ValueError, match="40 bytes"):
        validate_lighter_pubkey(b"\x01" * 39)

    # 3. All zero
    with pytest.raises(ValueError, match="all zero"):
        validate_lighter_pubkey(b"\x00" * PUB_KEY_BYTES_SIZE)

    # 4. First limb 0xffffffffffffffff >= Goldilocks modulus (0xffffffff00000001)
    with pytest.raises(ValueError, match="Goldilocks"):
        validate_lighter_pubkey((b"\xff" * 8) + (b"\x01" * 32))


def test_encode_change_pubkey_rejects_reserved_api_key_index():
    """encode_change_pubkey enforces the 2..254 user-key range.

    1. Reserved indices 0 and 1 (web/mobile) are rejected.
    2. An out-of-range index 255 is rejected.
    3. A valid index 2 encodes successfully.
    """
    web3 = Web3()  # provider-less: enough for ABI encoding

    # 1. & 2. Reserved / out-of-range indices rejected
    for bad_index in (0, 1, 255):
        with pytest.raises(ValueError, match="api_key_index"):
            encode_change_pubkey(web3, account_index=1, api_key_index=bad_index, pubkey=VALID_PUBKEY)

    # 3. Valid index encodes (selector 0x17010c68)
    _, data = encode_change_pubkey(web3, account_index=1, api_key_index=2, pubkey=VALID_PUBKEY)
    assert data.hex().startswith("17010c68")


def test_change_pubkey_requires_registered_account(web3: Web3, deployer: HotWallet, safe):
    """An unregistered Safe cannot register an API key.

    ZkLighter.changePubKey binds to msg.sender's registered account
    (``masterAccountIndex = validateAndGetAccountIndexFromAddress(msg.sender)``),
    so a fresh Safe with no Lighter account is rejected. Account registration is
    an off-chain prerequisite.

    1. Execute changePubKey through the unregistered Safe.
    2. Assert it reverts: the inner AccountIsNotRegistered surfaces as a Safe
       ``GS013`` execution failure.
    """
    # 1. & 2. Unregistered Safe -> the inner changePubKey reverts (Safe GS013)
    with pytest.raises(REVERT_ERRORS, match="GS013"):
        execute_change_pubkey(
            web3,
            safe,
            deployer.private_key.hex(),
            account_index=1,
            api_key_index=2,
            pubkey=VALID_PUBKEY,
        )


def test_change_pubkey_via_safe(web3: Web3, deployer: HotWallet, safe):
    """A registered Safe can register a Lighter API key on L1 via changePubKey.

    Lighter recommends the on-chain ChangePubKey for multisigs; this proves the
    Safe path works end-to-end against the live ZkLighter contract once the
    account exists.

    1. Forge the Safe's Lighter account registration (Anvil override, since the
       real registration is an off-chain step that cannot be simulated).
    2. Build + sign + execute the changePubKey Safe transaction.
    3. Assert it succeeds (the L1 contract enqueues the priority request).
    """
    account_index = 12345

    # 1. Forge registration so msg.sender (the Safe) is a known Lighter account
    register_lighter_account_on_anvil(web3, safe.address, account_index)

    # 2. Execute changePubKey through the Safe (single owner signs)
    tx_hash = execute_change_pubkey(
        web3,
        safe,
        deployer.private_key.hex(),
        account_index=account_index,
        api_key_index=2,
        pubkey=VALID_PUBKEY,
    )

    # 3. The Safe transaction mined successfully
    assert web3.eth.get_transaction_receipt(tx_hash)["status"] == 1
