"""Shared fixtures for guard tests.

Provides library linking helpers for SimpleVaultV0 deployment.
All external Forge libraries (CowSwapLib, GmxLib, HypercoreVaultLib)
must be linked when deploying SimpleVaultV0 or TradingStrategyModuleV0.
Tests that don't exercise a specific library link it with ZERO_ADDRESS.
"""

import pytest
from web3 import Web3
from web3.contract import Contract

from eth_defi.deploy import GUARD_LIBRARIES, deploy_contract


def deploy_simple_vault(
    web3: Web3,
    deployer: str,
    asset_manager: str,
    libraries: dict[str, str] | None = None,
) -> Contract:
    """Deploy SimpleVaultV0 with all required Forge libraries linked.

    :param libraries:
        Override specific library addresses.
        Merged with :data:`GUARD_LIBRARIES` defaults.
    """
    libs = {**GUARD_LIBRARIES}
    if libraries:
        libs.update(libraries)
    return deploy_contract(
        web3,
        "guard/SimpleVaultV0.json",
        deployer,
        asset_manager,
        libraries=libs,
    )
