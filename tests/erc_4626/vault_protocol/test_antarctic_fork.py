"""Read both Antarctic deployments on the shared fixed Arbitrum fork."""

import os
from decimal import Decimal

import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.antarctic.constants import ANTARCTIC_DEPLOYMENTS, ANTARCTIC_USDT, AntarcticDeployment
from eth_defi.erc_4626.vault_protocol.antarctic.vault import AntarcticVault
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import ARBITRUM_MIDNIGHT_BLOCK

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")
pytestmark = [
    pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM is required"),
    pytest.mark.xdist_group("fork:arbitrum:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Reuse the canonical fixed fork for read-only deployment checks.

    No state mutations occur, so per-test snapshot isolation is unnecessary.

    :param anvil_fork_pool: Session-scoped shared fork manager.
    :return: Arbitrum Web3 at the canonical midnight block.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_ARBITRUM, ARBITRUM_MIDNIGHT_BLOCK)


@pytest.mark.parametrize("deployment", ANTARCTIC_DEPLOYMENTS, ids=lambda d: d.product)
def test_antarctic_fixed_fork_metadata(web3: Web3, deployment: AntarcticDeployment) -> None:
    """Autodetect both LP identities and verify their deployed manager settings.

    Exact metadata assertions use the fixed shared fork rather than mutable
    latest-state values. Transaction construction remains unsupported.

    :param web3: Shared fixed Arbitrum fork.
    :param deployment: Reviewed LP token and settlement manager routing.
    :return: None.
    """
    vault = create_vault_instance_autodetect(web3, deployment.address)
    assert isinstance(vault, AntarcticVault)
    assert vault.get_protocol_name() == "Antarctic"
    assert vault.share_token.symbol == deployment.product.upper()
    assert vault.share_token.decimals == 18  # noqa: PLR2004
    assert vault.denomination_token.address.lower() == ANTARCTIC_USDT
    assert vault.denomination_token.decimals == 6  # noqa: PLR2004
    assert vault.fetch_minimum_deposit() == Decimal(10)
    assert vault.get_estimated_lock_up().days == 7  # noqa: PLR2004
    assert vault.get_deposit_manager() is None
