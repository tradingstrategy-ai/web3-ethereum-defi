"""Unit tests for D2 public-balance deposit eligibility."""

from eth_defi.erc_4626.vault_protocol.d2.vault import D2Vault


def test_d2_balance_eligibility_is_not_kyc() -> None:
    """Test D2's public asset minimum is not reported as KYC.

    1. Construct a D2 adapter without making an RPC call.
    2. Query its vault-wide identity policy.
    3. Confirm the public economic condition is permissionless.
    """
    # 1. Construct a D2 adapter without making an RPC call.
    vault = object.__new__(D2Vault)

    # 2. Query its vault-wide identity policy.
    permissioned = vault.is_whitelisted_deposit()

    # 3. Confirm the public economic condition is permissionless.
    assert permissioned is False
