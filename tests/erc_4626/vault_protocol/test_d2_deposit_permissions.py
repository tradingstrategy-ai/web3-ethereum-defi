"""Unit tests for D2 vault token-gated deposit admission."""

from eth_defi.erc_4626.vault_protocol.d2.vault import D2Vault


def test_d2_token_eligibility_requires_deposit_admission_check() -> None:
    """D2's mapping-or-token-balance gate must run before a deposit request."""
    vault = object.__new__(D2Vault)

    assert vault.is_whitelisted_deposit() is True
