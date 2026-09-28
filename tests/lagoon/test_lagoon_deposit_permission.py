"""Lagoon whitelist policy reporting tests."""

import os

import pytest
from hexbytes import HexBytes
from web3 import Web3

from eth_defi.erc_4626.vault_protocol.lagoon.deposit_redeem import NOT_WHITELISTED_SELECTOR, REQUEST_DEPOSIT_SELECTOR
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault, LagoonVersion
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import ETHEREUM_MIDNIGHT_BLOCK
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests"),
    pytest.mark.xdist_group("fork:ethereum:midnight"),
]

#: Simulated wallet from trade-executor's unsupported-vault report.
REPORT_CALLER = "0xa2b04c6a053ab2efbc699f5dd0f0957742a41629"

#: Historical private v0.5 vaults reported as inaccessible to an unapproved wallet.
REPORTED_RESTRICTED_VAULTS = (
    "0x3be67ba2d3fec744d1d2b5d564c83f57372578e4",
    "0x9fdbaaa76194d56e49cade12c1f216f47d2b865e",
    "0xf10801bcc3deaf467fb8b3dbb7430111822e6dab",
    "0xba6cfe8a9d199cd7f3e50114c4e4ec66f2d52c87",
    "0xef39d77c7fb6224ac974c5fa4e3151a6c6ce9594",
    "0xb993c32f578e5156369330787cf8c8fe033bf40e",
    "0xcb58582b0d52ce5feecb06ba9ce66598b0d57886",
    "0x175ea882b492c9b7a6d5852fe9da560dc7af1c72",
)


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Create a shared fixed-block Ethereum fork for permission reads.

    :param anvil_fork_pool:
        Session-scoped shared fork manager.
    :return:
        Web3 connected to the canonical Ethereum midnight fork.
    """

    return anvil_fork_pool.get_web3(JSON_RPC_ETHEREUM, ETHEREUM_MIDNIGHT_BLOCK)


@pytest.mark.parametrize("vault_address", REPORTED_RESTRICTED_VAULTS)
def test_reported_lagoon_private_vault_memberships_without_policy_getter(web3: Web3, vault_address: str) -> None:
    """Detect v0.5 policy using its zero-address whitelist sentinel.

    The v0.5 source retains ``isWhitelisted(address)`` and returns false for
    the zero address when its whitelist is enabled. This makes every reported
    vault a verified restricted deployment, while the report wallet remains
    a known non-member.
    """
    vault = LagoonVault(web3, VaultSpec(chain_id=1, vault_address=vault_address), default_block_identifier=ETHEREUM_MIDNIGHT_BLOCK)

    assert vault.version == LagoonVersion.v_0_5_0
    assert vault.is_whitelisted_deposit() is True
    assert vault.is_account_whitelisted(REPORT_CALLER) is False

    manager = vault.get_deposit_manager()
    assert manager.can_create_deposit_request(REPORT_CALLER) is False
    with pytest.raises(VaultFlowUnavailable, match="not allowed") as exc_info:
        manager.create_deposit_request(REPORT_CALLER, raw_amount=1)
    assert exc_info.value.decoded_error == "NotWhitelisted"
    assert exc_info.value.function_selector == REQUEST_DEPOSIT_SELECTOR
    assert exc_info.value.error_selector == NOT_WHITELISTED_SELECTOR
    assert exc_info.value.function_selector == HexBytes("0x85b77f45")
    assert exc_info.value.error_selector == HexBytes("0x584a7938")


@pytest.mark.parametrize(
    ("vault_address", "expected_version", "expected_whitelisted"),
    (
        ("0x22f99228f3ba7cfc7189ddf14366970fe0cef0cb", LagoonVersion.v_0_6_0, True),
        ("0x23b27310451f2754de34d9c04aa24e8be367124a", LagoonVersion.v_0_5_0, False),
        ("0xba6cfe8a9d199cd7f3e50114c4e4ec66f2d52c87", LagoonVersion.v_0_5_0, True),
        ("0xef39d77c7fb6224ac974c5fa4e3151a6c6ce9594", LagoonVersion.v_0_5_0, True),
        ("0xf10801bcc3deaf467fb8b3dbb7430111822e6dab", LagoonVersion.v_0_5_0, True),
        ("0xfd104766499a3ff60ea85b5c6015ba9e32b8c891", LagoonVersion.v_0_5_0, True),
    ),
)
def test_reviewed_private_lagoon_vault_deposit_permissions(
    web3: Web3,
    vault_address: str,
    expected_version: LagoonVersion,
    expected_whitelisted: bool,  # noqa: FBT001
) -> None:
    """Characterise the reviewed Ethereum private vault access modes."""

    vault = LagoonVault(web3, VaultSpec(chain_id=1, vault_address=vault_address), default_block_identifier=ETHEREUM_MIDNIGHT_BLOCK)

    assert vault.version is expected_version
    assert vault.is_whitelisted_deposit() is expected_whitelisted
    assert vault.is_account_whitelisted(REPORT_CALLER) is (not expected_whitelisted)
