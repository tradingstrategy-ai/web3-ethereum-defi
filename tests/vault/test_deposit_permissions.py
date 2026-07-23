"""Unit tests for vault deposit-permission reporting."""

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
from eth_defi.vault.deposit_redeem import VaultDepositPermission, VaultFlowUnavailable

ACCESS_DELAY = 3600


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


@pytest.mark.parametrize(
    ("whitelisted", "expected"),
    [
        (True, VaultDepositPermission.whitelisted),
        (False, VaultDepositPermission.permissionless),
    ],
)
def test_fetch_deposit_permission_maps_boolean_policy(whitelisted: bool, expected: VaultDepositPermission) -> None:  # noqa: FBT001
    """Scanner exports the enum value for supported boolean probes."""
    assert fetch_deposit_permission(PermissionVault(whitelisted)) is expected


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
