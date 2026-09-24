"""Unit tests for Lagoon whitelist policy inference."""

from dataclasses import dataclass, field
from types import SimpleNamespace

import eth_abi
import pytest
from eth_typing import HexAddress
from web3 import Web3
from web3.exceptions import BadFunctionCallOutput

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.vault_protocol.lagoon import vault as lagoon_vault_module
from eth_defi.erc_4626.vault_protocol.lagoon.deposit_redeem import ADDRESS_NOT_ALLOWED_SELECTOR, REQUEST_DEPOSIT_SELECTOR, LagoonDepositManager
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LAGOON_MODERN_ROLES_STORAGE_SLOT, LAGOON_MODERN_VERSIONS, LAGOON_VAULT_ABI_BY_VERSION, LagoonVault, LagoonVersion
from eth_defi.provider.fallback import ExtraValueError
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import UnsupportedVaultSimulation, VaultFlowUnavailable

#: Deterministic vault address used by the network-free test doubles.
VAULT_ADDRESS: HexAddress = "0x0000000000000000000000000000000000000001"

#: Deterministic depositor address used by the policy tests.
OWNER_ADDRESS: HexAddress = "0x0000000000000000000000000000000000000002"

#: Historical block propagated through version and storage test doubles.
FIXED_TEST_BLOCK = 123


@dataclass(slots=True)
class FakeCall:
    """Return or raise one configured contract-call result."""

    #: Value returned by ``call()``, or the exception it raises.
    result: object

    #: Keyword arguments received by each simulated contract call.
    call_kwargs: list[dict[str, object]] = field(default_factory=list, init=False)

    def call(self, **kwargs: object) -> object:
        """Return or raise the configured result.

        The recorded keyword arguments let tests verify that historical block
        identifiers reach the final contract call.

        :param kwargs:
            Simulated Web3 contract-call options.
        :return:
            Configured call result.
        """
        self.call_kwargs.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@dataclass(slots=True)
class FakeWhitelistFunctions:
    """Minimal Lagoon versioned access function container."""

    #: Value returned by the legacy global-policy getter.
    activated: bool | Exception

    #: Access result keyed by lower-case account address.
    members: dict[str, bool]

    #: Accounts queried through the legacy membership view.
    membership_queries: list[HexAddress] = field(default_factory=list, init=False)

    #: Accounts queried through the modern access view.
    access_queries: list[HexAddress] = field(default_factory=list, init=False)

    def isWhitelistActivated(self) -> FakeCall:  # noqa: N802
        """Return the configured global-policy call."""
        return FakeCall(self.activated)

    def isWhitelisted(self, address: HexAddress) -> FakeCall:  # noqa: N802
        """Return configured membership and record the queried address."""
        self.membership_queries.append(address)
        return FakeCall(self.members[address.lower()])

    def isAllowed(self, address: HexAddress) -> FakeCall:  # noqa: N802
        """Return configured v0.6 access and record the queried address."""
        self.access_queries.append(address)
        return FakeCall(self.members[address.lower()])


@dataclass(slots=True)
class FakeFeeFunctions:
    """Return one deterministic modern Lagoon fee tuple."""

    #: Reusable fee call whose invocation arguments are observable.
    fee_call: FakeCall = field(default_factory=lambda: FakeCall((100, 2_000, 300, 400, 500)))

    def feeRates(self) -> FakeCall:  # noqa: N802
        """Return management, performance, entry, exit and haircut rates."""
        return self.fee_call


def create_lagoon_policy_vault(
    version: LagoonVersion,
    activated: bool | Exception,  # noqa: FBT001
    members: dict[str, bool],
) -> tuple[LagoonVault, FakeWhitelistFunctions]:
    """Create a network-free Lagoon vault with deterministic policy views.

    :param version:
        Lagoon compatibility branch exercised by the test.
    :param activated:
        Result or error returned by the legacy policy getter.
    :param members:
        Account access results keyed by lower- or mixed-case address.
    :return:
        Partially constructed vault and its observable function double.
    """
    functions = FakeWhitelistFunctions(activated, {address.lower(): value for address, value in members.items()})
    vault = object.__new__(LagoonVault)
    vault.spec = VaultSpec(chain_id=1, vault_address=VAULT_ADDRESS)
    vault.default_block_identifier = "latest"
    vault.__dict__["version"] = version
    vault.__dict__["whitelist_contract"] = SimpleNamespace(functions=functions)
    vault.__dict__["access_contract"] = SimpleNamespace(functions=functions)
    return vault, functions


def test_lagoon_v1_uses_modern_abi_and_fee_fields() -> None:
    """v1 routes to the v0.6 ABI and exposes all generic fee fields."""
    assert LagoonVersion.v_1_0_0 in LAGOON_MODERN_VERSIONS
    assert LAGOON_VAULT_ABI_BY_VERSION[LagoonVersion.v_1_0_0] == "lagoon/v0.6.0/Vault.json"

    vault = object.__new__(LagoonVault)
    vault.spec = VaultSpec(chain_id=1, vault_address=VAULT_ADDRESS)
    vault.default_block_identifier = "latest"
    vault.__dict__["version"] = LagoonVersion.v_1_0_0
    fee_functions = FakeFeeFunctions()
    vault.__dict__["vault_contract"] = SimpleNamespace(functions=fee_functions)

    assert vault.get_management_fee(1) == pytest.approx(0.01)
    assert vault.get_performance_fee(1) == pytest.approx(0.2)
    assert vault.get_deposit_fee(1) == pytest.approx(0.03)
    assert vault.get_withdraw_fee(1) == pytest.approx(0.04)
    assert vault.has_custom_fees() is True

    fee_functions.fee_call.call_kwargs.clear()
    vault.default_block_identifier = FIXED_TEST_BLOCK
    fee_data = vault.get_fee_data()

    assert fee_data.management == pytest.approx(0.01)
    assert fee_data.performance == pytest.approx(0.2)
    assert fee_data.deposit == pytest.approx(0.03)
    assert fee_data.withdraw == pytest.approx(0.04)
    assert fee_functions.fee_call.call_kwargs == [{"block_identifier": FIXED_TEST_BLOCK}]


def test_lagoon_unknown_version_fails_with_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unrecognised version remains an explicit adapter error."""

    class FakeVersionProbe:
        @staticmethod
        def call(web3: object, block_identifier: int) -> bytes:
            del web3
            assert block_identifier == FIXED_TEST_BLOCK
            return eth_abi.encode(["string"], ["v1.1.0"])

    def fake_version_probe(**_kwargs: object) -> FakeVersionProbe:
        return FakeVersionProbe()

    monkeypatch.setattr(lagoon_vault_module.EncodedCall, "from_keccak_signature", fake_version_probe)
    vault = object.__new__(LagoonVault)
    vault.web3 = SimpleNamespace()
    vault.spec = VaultSpec(chain_id=8453, vault_address=VAULT_ADDRESS)
    vault.default_block_identifier = FIXED_TEST_BLOCK

    with pytest.raises(NotImplementedError, match=rf"Unknown Lagoon version v1\.1\.0.*chain 8453.*block {FIXED_TEST_BLOCK}"):
        vault.fetch_version()


def test_lagoon_modern_roles_use_explicit_storage_block() -> None:
    """Modern roles decode consecutive ERC-7201 slots at the requested block."""
    role_addresses: tuple[HexAddress, ...] = (
        "0x0000000000000000000000000000000000000002",
        "0x0000000000000000000000000000000000000003",
        "0x0000000000000000000000000000000000000004",
        "0x0000000000000000000000000000000000000005",
        "0x0000000000000000000000000000000000000006",
    )
    storage_calls: list[tuple[HexAddress, int, int]] = []

    class FakeEth:
        @staticmethod
        def get_storage_at(address: HexAddress, slot: int, block_identifier: int) -> bytes:
            storage_calls.append((address, slot, block_identifier))
            role_address = role_addresses[slot - LAGOON_MODERN_ROLES_STORAGE_SLOT]
            return bytes.fromhex("00" * 12 + role_address[2:])

    vault = object.__new__(LagoonVault)
    vault.web3 = SimpleNamespace(eth=FakeEth())
    vault.spec = VaultSpec(chain_id=8453, vault_address=VAULT_ADDRESS)

    roles = vault._fetch_modern_roles(FIXED_TEST_BLOCK)

    assert roles == tuple(Web3.to_checksum_address(address) for address in role_addresses)
    assert storage_calls == [(VAULT_ADDRESS, LAGOON_MODERN_ROLES_STORAGE_SLOT + offset, FIXED_TEST_BLOCK) for offset in range(5)]


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


@pytest.mark.parametrize(
    ("zero_address_allowed", "expected_whitelisted"),
    [
        (False, True),
        (True, False),
    ],
)
@pytest.mark.parametrize("version", [LagoonVersion.v_0_6_0, LagoonVersion.v_1_0_0])
def test_lagoon_modern_uses_is_allowed_zero_address_sentinel(
    version: LagoonVersion,
    zero_address_allowed: bool,  # noqa: FBT001
    expected_whitelisted: bool,  # noqa: FBT001
) -> None:
    """Modern Lagoon versions derive whitelist mode from ``isAllowed``."""
    vault, functions = create_lagoon_policy_vault(
        version,
        BadFunctionCallOutput(),
        {ZERO_ADDRESS_STR: zero_address_allowed},
    )

    assert vault.is_whitelisted_deposit() is expected_whitelisted
    assert functions.membership_queries == []
    assert functions.access_queries == [ZERO_ADDRESS_STR]


@pytest.mark.parametrize("version", [LagoonVersion.v_0_6_0, LagoonVersion.v_1_0_0])
def test_lagoon_modern_account_admission_uses_is_allowed(version: LagoonVersion) -> None:
    """Modern compatibility routes account checks through ``isAllowed``."""
    vault, functions = create_lagoon_policy_vault(
        version,
        BadFunctionCallOutput(),
        {OWNER_ADDRESS: False},
    )

    assert vault.is_account_whitelisted(OWNER_ADDRESS) is False
    assert functions.membership_queries == []
    assert functions.access_queries == [OWNER_ADDRESS]


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
    manager = LagoonDepositManager(vault)
    manager._is_vault_paused = lambda: False

    assert manager.can_create_deposit_request(OWNER_ADDRESS) is False
    with pytest.raises(VaultFlowUnavailable, match="cannot be determined"):
        manager.create_deposit_request(OWNER_ADDRESS, raw_amount=1)


def test_lagoon_manager_converts_unknown_admission_to_flow_refusal() -> None:
    """A supported global policy cannot leak a missing account-view error."""
    vault = object.__new__(LagoonVault)
    vault.spec = VaultSpec(chain_id=1, vault_address=VAULT_ADDRESS)
    vault.is_whitelisted_deposit = lambda: True
    vault.is_account_whitelisted = lambda _owner: (_ for _ in ()).throw(NotImplementedError("unknown membership"))
    manager = LagoonDepositManager(vault)
    manager._is_vault_paused = lambda: False

    assert manager.can_create_deposit_request(OWNER_ADDRESS) is False
    with pytest.raises(VaultFlowUnavailable, match="admission cannot be determined"):
        manager.create_deposit_request(OWNER_ADDRESS, raw_amount=1)


@pytest.mark.parametrize("version", [LagoonVersion.v_0_6_0, LagoonVersion.v_1_0_0])
def test_lagoon_modern_manager_reports_address_not_allowed(version: LagoonVersion) -> None:
    """A modern denial exposes the v0.6-compatible custom-error selector."""
    vault = object.__new__(LagoonVault)
    vault.spec = VaultSpec(chain_id=1, vault_address=VAULT_ADDRESS)
    vault.__dict__["version"] = version
    vault.is_whitelisted_deposit = lambda: False
    vault.is_account_whitelisted = lambda _owner: False
    manager = LagoonDepositManager(vault)
    manager._is_vault_paused = lambda: False

    with pytest.raises(VaultFlowUnavailable) as exc_info:
        manager.create_deposit_request(OWNER_ADDRESS, raw_amount=1)

    assert exc_info.value.decoded_error == "AddressNotAllowed"
    assert exc_info.value.function_selector == REQUEST_DEPOSIT_SELECTOR
    assert exc_info.value.error_selector == ADDRESS_NOT_ALLOWED_SELECTOR


def test_lagoon_mock_settlement_is_typed_unsupported() -> None:
    """Lagoon accepts the shared mock keyword without invoking its real driver."""
    vault = object.__new__(LagoonVault)
    vault.spec = VaultSpec(chain_id=1, vault_address=VAULT_ADDRESS)
    manager = LagoonDepositManager(vault)

    with pytest.raises(UnsupportedVaultSimulation, match="no local mock settlement driver") as exc_info:
        manager.force_settle(object(), mock=object())
    assert exc_info.value.unsupported_reason == "mock_settlement_driver_not_implemented"
