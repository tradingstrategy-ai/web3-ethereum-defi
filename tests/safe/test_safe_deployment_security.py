"""Unit tests for Safe deployment security invariants."""

from types import SimpleNamespace

import pytest
from safe_eth.eth.constants import NULL_ADDRESS
from safe_eth.eth.contracts import get_safe_contract
from web3 import Web3

from eth_defi.erc_4626.vault_protocol.lagoon.deployment import deploy_safe_trading_strategy_module
from eth_defi.safe import deployment as safe_deployment
from eth_defi.safe.deployment import assert_safe_fallback_handler_disabled


def test_deterministic_safe_initializer_disables_fallback_handler():
    """Encode the zero address as the deterministic Safe fallback handler.

    The test decodes the proxy initialiser to prevent an argument-order change
    from silently assigning the zero address to a different Safe ``setup()``
    parameter.

    :return:
        None.
    """
    web3 = Web3()
    initializer = safe_deployment._build_safe_initializer(web3, ["0x0000000000000000000000000000000000000001"], 1)
    _setup, arguments = get_safe_contract(web3, NULL_ADDRESS).decode_function_input(initializer)

    assert arguments["fallbackHandler"] == NULL_ADDRESS


def test_safe_without_fallback_handler_is_accepted():
    """Accept the explicitly handler-free Safe configuration used by Lagoon.

    The deployment invariant permits only Safe's zero-address fallback-handler
    configuration, leaving no handler-provided signature or fallback surface.

    :return:
        None.
    """
    safe = SimpleNamespace(address="0x0000000000000000000000000000000000000001", retrieve_fallback_handler=lambda: NULL_ADDRESS)

    assert_safe_fallback_handler_disabled(safe)


def test_safe_with_fallback_handler_is_rejected():
    """Reject a Safe with a fallback handler before it can become a Lagoon Safe.

    This protects both newly deployed and user-supplied existing Safes from the
    unnecessary authentication surface after the Zodiac/Gnosis Pay incident.

    :return:
        None.
    """
    safe = SimpleNamespace(
        address="0x0000000000000000000000000000000000000001",
        retrieve_fallback_handler=lambda: "0x0000000000000000000000000000000000000002",
    )

    with pytest.raises(ValueError, match="fallback handler"):
        assert_safe_fallback_handler_disabled(safe)


def test_lagoon_module_deployment_rejects_safe_with_fallback_handler():
    """Reject a handler-enabled Safe in the lower-level Lagoon module deployer.

    This ensures a caller cannot evade the high-level Lagoon deployment check by
    directly deploying a TradingStrategyModuleV0 for an unsafe existing Safe.

    :return:
        None.
    """
    safe = SimpleNamespace(
        address="0x0000000000000000000000000000000000000001",
        retrieve_fallback_handler=lambda: "0x0000000000000000000000000000000000000002",
    )

    with pytest.raises(ValueError, match="fallback handler"):
        deploy_safe_trading_strategy_module(
            web3=None,
            deployer=None,
            safe=safe,
        )
