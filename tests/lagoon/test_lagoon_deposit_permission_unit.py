"""Unit tests for Lagoon whitelist policy inference."""

import pytest
from web3.exceptions import BadFunctionCallOutput

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.vault_protocol.lagoon.deposit_redeem import ERC7540DepositManager
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault, LagoonVersion
from eth_defi.provider.fallback import ExtraValueError
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable

VAULT_ADDRESS = "0x0000000000000000000000000000000000000001"
OWNER_ADDRESS = "0x0000000000000000000000000000000000000002"


class FakeCall:
    """Return or raise one configured contract-call result."""

    def __init__(self, result: bool | Exception) -> None:  # noqa: FBT001
        self.result = result

    def call(self) -> bool:
        """Return or raise the configured result."""
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeWhitelistFunctions:
    """Minimal Lagoon whitelist contract function container."""

    def __init__(self, activated: bool | Exception, members: dict[str, bool]) -> None:  # noqa: FBT001
        self.activated = activated
        self.members = members
        self.membership_queries: list[str] = []

    def isWhitelistActivated(self) -> FakeCall:  # noqa: N802
        """Return the configured global-policy call."""
        return FakeCall(self.activated)

    def isWhitelisted(self, address: str) -> FakeCall:  # noqa: N802
        """Return configured membership and record the queried address."""
        self.membership_queries.append(address)
        return FakeCall(self.members[address.lower()])


def create_lagoon_policy_vault(
    version: LagoonVersion,
    activated: bool | Exception,  # noqa: FBT001
    members: dict[str, bool],
) -> tuple[LagoonVault, FakeWhitelistFunctions]:
    """Create a network-free Lagoon vault with deterministic policy views."""
    functions = FakeWhitelistFunctions(activated, {address.lower(): value for address, value in members.items()})
    vault = object.__new__(LagoonVault)
    vault.spec = VaultSpec(chain_id=1, vault_address=VAULT_ADDRESS)
    vault.__dict__["version"] = version
    vault.__dict__["whitelist_contract"] = type("FakeWhitelistContract", (), {"functions": functions})()
    return vault, functions


@pytest.mark.parametrize("activated", [True, False])
def test_lagoon_v04_uses_explicit_policy_getter(activated: bool) -> None:  # noqa: FBT001
    """v0.4 policy is read directly without consulting a sentinel account."""
    vault, functions = create_lagoon_policy_vault(
        LagoonVersion.v_0_4_0,
        activated,
        {ZERO_ADDRESS_STR: False},
    )

    assert vault.is_whitelisted_deposit() is activated
    assert functions.membership_queries == []


@pytest.mark.parametrize(
    ("zero_address_member", "expected_whitelisted"),
    [
        (False, True),
        (True, False),
    ],
)
def test_lagoon_v05_uses_zero_address_sentinel(
    zero_address_member: bool,  # noqa: FBT001
    expected_whitelisted: bool,  # noqa: FBT001
) -> None:
    """v0.5 derives global policy from its documented membership semantics."""
    vault, functions = create_lagoon_policy_vault(
        LagoonVersion.v_0_5_0,
        BadFunctionCallOutput(),
        {ZERO_ADDRESS_STR: zero_address_member},
    )

    assert vault.is_whitelisted_deposit() is expected_whitelisted
    assert functions.membership_queries == [ZERO_ADDRESS_STR]


def test_lagoon_other_versions_do_not_use_v05_sentinel() -> None:
    """A missing getter on another Lagoon version remains an unknown policy."""
    vault, functions = create_lagoon_policy_vault(
        LagoonVersion.v_0_6_0,
        BadFunctionCallOutput(),
        {ZERO_ADDRESS_STR: True},
    )

    with pytest.raises(NotImplementedError, match=r"v0\.6\.0"):
        vault.is_whitelisted_deposit()
    assert functions.membership_queries == []


def test_lagoon_transient_policy_read_error_is_not_reclassified() -> None:
    """An invalid RPC response must propagate to scanner retry handling."""
    error = ExtraValueError("Invalid RPC response")
    vault, functions = create_lagoon_policy_vault(
        LagoonVersion.v_0_4_0,
        error,
        {ZERO_ADDRESS_STR: True},
    )

    with pytest.raises(ExtraValueError, match="Invalid RPC response"):
        vault.is_whitelisted_deposit()
    assert functions.membership_queries == []


def test_lagoon_v05_empty_provider_revert_uses_sentinel() -> None:
    """The provider's deterministic missing-getter wrapper is not transient."""
    missing_getter = ExtraValueError({"code": 3, "message": "execution reverted", "data": "0x"})
    vault, functions = create_lagoon_policy_vault(
        LagoonVersion.v_0_5_0,
        missing_getter,
        {ZERO_ADDRESS_STR: False},
    )

    assert vault.is_whitelisted_deposit() is True
    assert functions.membership_queries == [ZERO_ADDRESS_STR]


def test_lagoon_manager_fails_closed_when_policy_views_are_unknown() -> None:
    """Boolean admission and request construction both reject unknown policy."""
    vault = object.__new__(LagoonVault)
    vault.spec = VaultSpec(chain_id=1, vault_address=VAULT_ADDRESS)
    vault.is_whitelisted_deposit = lambda: (_ for _ in ()).throw(NotImplementedError("unknown policy"))
    manager = ERC7540DepositManager(vault)
    manager._is_vault_paused = lambda: False

    assert manager.can_create_deposit_request(OWNER_ADDRESS) is False
    with pytest.raises(VaultFlowUnavailable, match="cannot be determined"):
        manager.create_deposit_request(OWNER_ADDRESS, raw_amount=1)


def test_lagoon_manager_converts_unknown_membership_to_flow_refusal() -> None:
    """A supported global policy cannot leak a missing membership-view error."""
    vault = object.__new__(LagoonVault)
    vault.spec = VaultSpec(chain_id=1, vault_address=VAULT_ADDRESS)
    vault.is_whitelisted_deposit = lambda: True
    vault.is_account_whitelisted = lambda _owner: (_ for _ in ()).throw(NotImplementedError("unknown membership"))
    manager = ERC7540DepositManager(vault)
    manager._is_vault_paused = lambda: False

    assert manager.can_create_deposit_request(OWNER_ADDRESS) is False
    with pytest.raises(VaultFlowUnavailable, match="membership cannot be determined"):
        manager.create_deposit_request(OWNER_ADDRESS, raw_amount=1)
