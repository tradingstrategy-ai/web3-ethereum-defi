"""Lagoon whitelist policy reporting tests."""

import os

import pytest

from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault, LagoonVersion
from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(
    JSON_RPC_ETHEREUM is None,
    reason="JSON_RPC_ETHEREUM needed to run these tests",
)

#: Simulated wallet from trade-executor's unsupported-vault report.
REPORT_CALLER = "0xa2b04c6a053ab2efbc699f5dd0f0957742a41629"


@pytest.mark.parametrize(
    ("block_number", "vault_addresses"),
    [
        (25_588_627, ["0x3be67ba2d3fec744d1d2b5d564c83f57372578e4"]),
        (
            25_588_647,
            [
                "0x9fdbaaa76194d56e49cade12c1f216f47d2b865e",
                "0xf10801bcc3deaf467fb8b3dbb7430111822e6dab",
                "0xba6cfe8a9d199cd7f3e50114c4e4ec66f2d52c87",
            ],
        ),
        (
            25_588_672,
            [
                "0xef39d77c7fb6224ac974c5fa4e3151a6c6ce9594",
                "0xb993c32f578e5156369330787cf8c8fe033bf40e",
                "0xcb58582b0d52ce5feecb06ba9ce66598b0d57886",
                "0x175ea882b492c9b7a6d5852fe9da560dc7af1c72",
            ],
        ),
    ],
)
def test_reported_lagoon_private_vault_memberships_without_policy_getter(
    block_number: int,
    vault_addresses: list[str],
) -> None:
    """Detect v0.5 policy using its zero-address whitelist sentinel.

    The v0.5 source retains ``isWhitelisted(address)`` and returns false for
    the zero address when its whitelist is enabled. This makes every reported
    vault a verified restricted deployment, while the report wallet remains
    a known non-member.
    """
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=block_number)
    try:
        web3 = create_multi_provider_web3(launch.json_rpc_url)
        for address in vault_addresses:
            vault = LagoonVault(web3, VaultSpec(chain_id=1, vault_address=address))

            assert vault.version == LagoonVersion.v_0_5_0
            assert vault.is_whitelisted_deposit() is True
            assert vault.is_account_whitelisted(REPORT_CALLER) is False

            manager = vault.get_deposit_manager()
            assert manager.can_create_deposit_request(REPORT_CALLER) is False
            with pytest.raises(VaultFlowUnavailable, match="not whitelisted") as exc_info:
                manager.create_deposit_request(REPORT_CALLER, raw_amount=1)
            assert exc_info.value.decoded_error == "NotWhitelisted"
    finally:
        launch.close()
