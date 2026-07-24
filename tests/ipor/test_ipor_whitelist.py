"""Historical IPOR AccessManager admission tests."""

import os

import pytest

from eth_defi.erc_4626.vault_protocol.ipor.vault import IPORVault
from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(
    JSON_RPC_ETHEREUM is None,
    reason="JSON_RPC_ETHEREUM needed to run these tests",
)

#: Simulated wallet encoded in each reported AccessManagedUnauthorized revert.
REPORT_CALLER = "0xa2b04c6a053ab2efbc699f5dd0f0957742a41629"


@pytest.mark.parametrize(
    ("block_number", "vault_addresses"),
    [
        (25_588_603, ["0x95b2ed8f821570f85fd0e3e6e7088c6296587088"]),
        (
            25_588_627,
            [
                "0x888e1d3c509c80e24cab8a4872e164b7e5a6eb10",
                "0xc825779c89120eeef746c51130b362478e181d39",
                "0x4c5a611694c426cae9335d53e95b885090cf8c31",
                "0x32f07401eb177f2c0fc4f95f3928050d88dae7ed",
                "0xc2a119ea6de75e4b1451330321cb2474eb8d82d4",
            ],
        ),
    ],
)
def test_reported_ipor_vaults_reject_the_report_caller(
    block_number: int,
    vault_addresses: list[str],
) -> None:
    """Map each historical restricted IPOR vault to AccessManager admission.

    Cases are grouped by report block so one Anvil launch verifies all vaults
    observed at the same historical state.
    """
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=block_number)
    try:
        web3 = create_multi_provider_web3(launch.json_rpc_url)
        for address in vault_addresses:
            vault = IPORVault(web3, VaultSpec(chain_id=1, vault_address=address))
            selector = vault.get_deposit_function_selector()

            assert selector.hex() == "6e553f65"
            assert vault.is_whitelisted_deposit() is True
            assert vault.fetch_selector_access(REPORT_CALLER, selector) == (False, 0)
            assert vault.is_account_whitelisted(REPORT_CALLER) is False

            manager = vault.get_deposit_manager()
            assert manager.can_create_deposit_request(REPORT_CALLER) is False
            with pytest.raises(VaultFlowUnavailable, match="does not allow immediate") as exc_info:
                manager.create_deposit_request(REPORT_CALLER, raw_amount=1)
            assert exc_info.value.function_selector == selector
            assert exc_info.value.access_delay == 0
    finally:
        launch.close()
