"""Unit tests for D2 vault KYC classification."""

from eth_defi.erc_4626.vault_protocol.d2.vault import D2Vault


def test_d2_token_eligibility_is_not_a_kyc_whitelist() -> None:
    """D2 open dates, lock-ups and asset conditions do not require KYC status."""
    vault = object.__new__(D2Vault)

    assert vault.is_whitelisted_deposit() is False
