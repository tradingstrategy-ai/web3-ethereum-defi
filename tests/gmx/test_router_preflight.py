"""Tests for the GMX router/guard allowlist preflight.

Without a preflight, a router that the guard does not allow is only discovered by
broadcasting an order and watching it revert with ``Target not allowed`` — once per
order, gas spent each time, and with exits affected the bot cannot flatten risk.
"""

import pytest
from web3 import Web3

from eth_defi.gmx.whitelist import GMXRouterNotWhitelisted, assert_gmx_router_whitelisted

V22C_EXCHANGE_ROUTER = "0x7dE39FF2e232A2203196788d37e234cF8F1b83f1"
ORDER_VAULT = "0x31eF83a530Fde1B38EE9A18093A333D8Bbbc40D5"
OTHER_ORDER_VAULT = "0xD3D60D22d415aD43b7e64b510D86A30f19B1B12C"
GUARD_ADDRESS = "0x33e391A6dce6874198177BdCC89B2230A2BC6202"


class _Call:
    """Stand-in for a web3 bound function returning a fixed value."""

    def __init__(self, value):
        self._value = value

    def call(self):
        return self._value


class _GuardFunctions:
    def __init__(self, allowed: bool, order_vault: str):
        self._allowed = allowed
        self._order_vault = order_vault

    def isAllowedGMXRouter(self, router):  # noqa: N802 - mirrors the Solidity ABI name
        return _Call(self._allowed)

    def gmxOrderVaults(self, router):  # noqa: N802 - mirrors the Solidity ABI name
        return _Call(self._order_vault)


class _StubGuard:
    """Minimal duck-typed GuardV0 for network-free preflight tests."""

    def __init__(self, allowed: bool = True, order_vault: str = ORDER_VAULT):
        self.address = Web3.to_checksum_address(GUARD_ADDRESS)
        self.functions = _GuardFunctions(allowed, Web3.to_checksum_address(order_vault))


def test_passes_when_router_is_whitelisted():
    assert_gmx_router_whitelisted(_StubGuard(allowed=True), V22C_EXCHANGE_ROUTER)


def test_raises_when_router_not_whitelisted():
    """This is the exact production failure the preflight is meant to catch early."""
    with pytest.raises(GMXRouterNotWhitelisted) as exc_info:
        assert_gmx_router_whitelisted(_StubGuard(allowed=False), V22C_EXCHANGE_ROUTER)

    message = str(exc_info.value)
    assert V22C_EXCHANGE_ROUTER in message
    assert "Target not allowed" in message, "error should name the on-chain revert it predicts"
    assert "whitelistGMX" in message, "error should state the remediation"


def test_order_vault_mismatch_raises():
    """A router allowed but mapped to the wrong OrderVault still fails at order time."""
    guard = _StubGuard(allowed=True, order_vault=OTHER_ORDER_VAULT)
    with pytest.raises(GMXRouterNotWhitelisted, match="OrderVault"):
        assert_gmx_router_whitelisted(guard, V22C_EXCHANGE_ROUTER, order_vault=ORDER_VAULT)


def test_order_vault_match_passes():
    guard = _StubGuard(allowed=True, order_vault=ORDER_VAULT)
    assert_gmx_router_whitelisted(guard, V22C_EXCHANGE_ROUTER, order_vault=ORDER_VAULT)


def test_accepts_lowercase_addresses():
    """Addresses arriving from config or JSON are not always checksummed."""
    guard = _StubGuard(allowed=True, order_vault=ORDER_VAULT)
    assert_gmx_router_whitelisted(
        guard,
        V22C_EXCHANGE_ROUTER.lower(),
        order_vault=ORDER_VAULT.lower(),
    )
