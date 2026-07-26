"""Unit tests for vault deposit-permission reporting."""

from types import SimpleNamespace

import pytest
from eth_abi.exceptions import DecodingError
from hexbytes import HexBytes
from requests.exceptions import RequestException
from web3.exceptions import BadFunctionCallOutput, ContractLogicError, MismatchedABI, Web3RPCError

from eth_defi.erc_4626.scan import fetch_deposit_permission
from eth_defi.erc_4626.vault_protocol.ipor.deposit_redeem import IPORDepositManager
from eth_defi.erc_4626.vault_protocol.ipor.vault import IPORVault
from eth_defi.provider.fallback import ExtraValueError
from eth_defi.vault.base import VaultBase, VaultSpec
from eth_defi.vault.deposit_redeem import (
    VaultDepositManager,
    VaultDepositPermission,
    VaultFlowUnavailable,
    WhitelistingRequired,
)

ACCESS_DELAY = 3600

OWNER_ADDRESS = "0x0000000000000000000000000000000000000002"
VAULT_ADDRESS = "0x0000000000000000000000000000000000000001"
CHAIN_ID = 8453


def _make_whitelist_manager(*, whitelisted_deposit, account_whitelisted) -> SimpleNamespace:
    """Build a stand-in manager exposing only what ``check_deposit_whitelist`` reads.

    :param whitelisted_deposit:
        Value returned, or exception raised, by ``is_whitelisted_deposit``.
    :param account_whitelisted:
        Value returned, or exception raised, by ``is_account_whitelisted``.
    :return:
        Object with a ``vault`` attribute matching the reads used by the helper.
    """

    def is_whitelisted_deposit() -> bool:
        if isinstance(whitelisted_deposit, Exception):
            raise whitelisted_deposit
        return whitelisted_deposit

    def is_account_whitelisted(address: str) -> bool:
        if isinstance(account_whitelisted, Exception):
            raise account_whitelisted
        return account_whitelisted

    vault = SimpleNamespace(
        address=VAULT_ADDRESS,
        chain_id=CHAIN_ID,
        get_protocol_name=lambda: "test-protocol",
        is_whitelisted_deposit=is_whitelisted_deposit,
        is_account_whitelisted=is_account_whitelisted,
    )
    return SimpleNamespace(vault=vault)


def test_check_deposit_whitelist_permissionless_allows() -> None:
    """A permissionless vault never raises a whitelist error."""
    manager = _make_whitelist_manager(whitelisted_deposit=False, account_whitelisted=False)
    VaultDepositManager.check_deposit_whitelist(manager, OWNER_ADDRESS)


def test_check_deposit_whitelist_member_allows() -> None:
    """A whitelisted account passes the preflight."""
    manager = _make_whitelist_manager(whitelisted_deposit=True, account_whitelisted=True)
    VaultDepositManager.check_deposit_whitelist(manager, OWNER_ADDRESS)


def test_check_deposit_whitelist_non_member_raises() -> None:
    """An applicable whitelist that excludes the owner raises WhitelistingRequired."""
    manager = _make_whitelist_manager(whitelisted_deposit=True, account_whitelisted=False)
    with pytest.raises(WhitelistingRequired) as exc_info:
        VaultDepositManager.check_deposit_whitelist(manager, OWNER_ADDRESS)
    error = exc_info.value
    assert isinstance(error, VaultFlowUnavailable)
    assert error.caller == OWNER_ADDRESS
    assert error.vault_address == VAULT_ADDRESS
    assert error.direction == "deposit"
    assert error.phase == "preflight"
    # Message must be self-describing for diagnostics: chain id, vault and depositor.
    assert error.reason.count(OWNER_ADDRESS) >= 1
    assert VAULT_ADDRESS in error.reason
    assert str(CHAIN_ID) in error.reason


def test_check_deposit_whitelist_unknown_policy_allows() -> None:
    """An undeterminable vault-wide policy must not raise WhitelistingRequired."""
    manager = _make_whitelist_manager(
        whitelisted_deposit=NotImplementedError("policy unknown"),
        account_whitelisted=False,
    )
    VaultDepositManager.check_deposit_whitelist(manager, OWNER_ADDRESS)


def test_check_deposit_whitelist_unknown_membership_allows() -> None:
    """An unqueryable per-account membership must not raise WhitelistingRequired."""
    manager = _make_whitelist_manager(
        whitelisted_deposit=True,
        account_whitelisted=NotImplementedError("membership unknown"),
    )
    VaultDepositManager.check_deposit_whitelist(manager, OWNER_ADDRESS)


class PermissionVault:
    """Minimal scan adapter used to exercise permission error boundaries."""

    address = "0x0000000000000000000000000000000000000001"

    def __init__(self, result: bool | Exception) -> None:  # noqa: FBT001
        """Initialise a vault with a deterministic permission probe result."""
        self.result = result

    def is_whitelisted_deposit(self) -> bool:
        """Return or raise the configured probe result."""
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class PolicyAndAvailabilityVault:
    """Fake adapter proving KYC status ignores availability and asset eligibility."""

    address = "0x0000000000000000000000000000000000000002"

    def __init__(
        self,
        *,
        kyc_required: bool,
        deposits_paused: bool,
        max_deposit: int,
        epoch_open: bool,
        required_asset_balance: int,
        manager_capability: bool,
    ) -> None:
        """Initialise independent KYC, eligibility and temporary-state values."""
        self.kyc_required = kyc_required
        self.deposits_paused = deposits_paused
        self.max_deposit = max_deposit
        self.epoch_open = epoch_open
        self.required_asset_balance = required_asset_balance
        self.manager_capability = manager_capability

    def is_whitelisted_deposit(self) -> bool:
        """Return the configured KYC policy only."""
        return self.kyc_required


@pytest.mark.parametrize(
    ("kyc_required", "expected"),
    [
        (True, VaultDepositPermission.whitelisted),
        (False, VaultDepositPermission.permissionless),
    ],
)
def test_fetch_deposit_permission_maps_boolean_policy(kyc_required: bool, expected: VaultDepositPermission) -> None:  # noqa: FBT001
    """Scanner exports the enum value for supported boolean probes."""
    assert fetch_deposit_permission(PermissionVault(kyc_required)) is expected


@pytest.mark.parametrize(
    ("availability", "expected"),
    [
        ({"kyc_required": False, "deposits_paused": True, "max_deposit": 0, "epoch_open": False, "required_asset_balance": 1_000_000, "manager_capability": False}, VaultDepositPermission.permissionless),
        ({"kyc_required": False, "deposits_paused": False, "max_deposit": 0, "epoch_open": True, "required_asset_balance": 0, "manager_capability": True}, VaultDepositPermission.permissionless),
        ({"kyc_required": True, "deposits_paused": True, "max_deposit": 0, "epoch_open": False, "required_asset_balance": 1_000_000, "manager_capability": False}, VaultDepositPermission.whitelisted),
        ({"kyc_required": True, "deposits_paused": False, "max_deposit": 10**18, "epoch_open": True, "required_asset_balance": 0, "manager_capability": True}, VaultDepositPermission.whitelisted),
    ],
)
def test_fetch_deposit_permission_is_independent_from_availability(
    availability: dict[str, bool | int],
    expected: VaultDepositPermission,
) -> None:
    """A pause, cap, epoch, asset condition and manager do not define KYC status."""
    vault = PolicyAndAvailabilityVault(**availability)

    assert fetch_deposit_permission(vault) is expected


@pytest.mark.parametrize(
    "exception",
    [
        NotImplementedError("unsupported"),
        ConnectionError("transport failure"),
        TimeoutError("timeout"),
        DecodingError("decode failure"),
        BadFunctionCallOutput("method unavailable"),
        ContractLogicError("view reverted"),
        ExtraValueError({"code": 3, "message": "execution reverted"}),
        MismatchedABI("ABI mismatch"),
        RequestException("HTTP failure"),
        Web3RPCError("RPC failure"),
    ],
)
def test_fetch_deposit_permission_maps_allowed_read_failures_to_unknown(exception: Exception) -> None:
    """Only the documented transport and ABI failures become unknown."""
    assert fetch_deposit_permission(PermissionVault(exception)) is VaultDepositPermission.unknown


@pytest.mark.parametrize(
    "exception",
    [
        AttributeError("programming error"),
        KeyError("programming error"),
        RuntimeError("programming error"),
        TypeError("programming error"),
        ValueError("programming error"),
    ],
)
def test_fetch_deposit_permission_propagates_programming_errors(exception: Exception) -> None:
    """Scanner must not turn adapter defects into unknown metadata."""
    with pytest.raises(type(exception), match="programming error"):
        fetch_deposit_permission(PermissionVault(exception))


def test_vault_base_permission_methods_require_protocol_mapping() -> None:
    """Default base methods cannot silently classify unsupported protocols."""
    with pytest.raises(NotImplementedError):
        VaultBase.is_whitelisted_deposit(object())
    with pytest.raises(NotImplementedError):
        VaultBase.is_account_whitelisted(object(), "0x0000000000000000000000000000000000000001")


def test_deposit_permission_enum_values_are_json_safe() -> None:
    """Public report values remain stable snake-case strings."""
    assert [permission.value for permission in VaultDepositPermission] == ["whitelisted", "permissionless", "unknown"]


def test_ipor_delayed_access_is_not_immediately_admissible() -> None:
    """Keep scheduled IPOR membership distinct from immediate admission."""
    vault = object.__new__(IPORVault)
    vault.spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    vault.fetch_selector_access = lambda *_: (False, ACCESS_DELAY)
    manager = IPORDepositManager(vault)

    with pytest.raises(VaultFlowUnavailable, match="delayed execution") as exc_info:
        manager._assert_immediate_access(
            "0x0000000000000000000000000000000000000002",
            HexBytes("0x6e553f65"),
            "deposit",
        )

    assert exc_info.value.decoded_error is None
    assert exc_info.value.access_delay == ACCESS_DELAY
