"""Unit tests for Accountable's offchain vault metadata parsing."""

from eth_defi.erc_4626.vault_protocol.accountable.offchain_metadata import _parse_vault_metadata  # noqa: PLC2701


def test_accountable_strategy_metadata_uses_first_sentence_as_short_description() -> None:
    """Keep the listing summary specific to the vault rather than its manager.

    Accountable separates a manager biography in ``company_info`` from the
    vault's strategy. The strategy is the appropriate source for both the
    full vault description and the listing summary.

    :return:
        None. Assertions validate parsed Accountable API metadata.
    """
    strategy = "This is the vault strategy.\nIt has a separate second sentence."
    metadata = _parse_vault_metadata(
        {
            "loan_name": "Example vault",
            "performance_fee": 200_000,
            "loan_address": "0x0000000000000000000000000000000000000001",
        },
        {
            "loan": {
                "vault_strategy": strategy,
                "company_info": "Example Manager is an institutional asset manager.",
                "company_name": "Example Manager",
            },
        },
    )

    assert metadata["description"] == strategy
    assert metadata["short_description"] == "This is the vault strategy."
    assert metadata["short_description"] != "Example Manager is an institutional asset manager."
