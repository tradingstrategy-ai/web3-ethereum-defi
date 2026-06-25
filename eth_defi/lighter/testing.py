"""Anvil-fork test helpers for the Lighter guard integration.

This module holds only the test-specific deployment helper; the library
deployment and whitelisting come from :py:mod:`eth_defi.lighter.deployment` so
production and tests share one code path.

See ``eth_defi/lighter/README-lighter-guard.md`` and
``tests/guard/test_guard_lighter_lagoon.py``.
"""

import logging

from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.deploy import GUARD_LIBRARIES, deploy_contract
from eth_defi.trace import assert_transaction_success_with_explanation

logger = logging.getLogger(__name__)


def deploy_lighter_simple_vault(
    web3: Web3,
    deployer: HexAddress | str,
    asset_manager: HexAddress | str,
    owner: HexAddress | str,
    lighter_lib: Contract,
) -> Contract:
    """Deploy ``SimpleVaultV0`` with ``LighterLib`` linked (other libs zeroed).

    Mirrors the Hypercore test fixture: only the library under test gets a real
    address, the rest stay :py:data:`~eth_defi.abi.ZERO_ADDRESS` from
    :py:data:`~eth_defi.deploy.GUARD_LIBRARIES`. The library itself is deployed
    via :py:func:`~eth_defi.lighter.deployment.deploy_lighter_lib`.

    :param web3:
        Web3 connection (Anvil fork).

    :param deployer:
        Deployer address (becomes the initial owner before transfer).

    :param asset_manager:
        Asset manager address (the allowed guard sender).

    :param owner:
        Final guard owner (typically the Safe / governance).

    :param lighter_lib:
        Deployed ``LighterLib`` contract to link.

    :return:
        The deployed ``SimpleVaultV0`` contract.
    """
    libraries = {**GUARD_LIBRARIES, "LighterLib": lighter_lib.address}
    vault = deploy_contract(web3, "guard/SimpleVaultV0.json", deployer, asset_manager, libraries=libraries)
    assert_transaction_success_with_explanation(
        web3,
        vault.functions.initialiseOwnership(owner).transact({"from": deployer}),
    )
    return vault
