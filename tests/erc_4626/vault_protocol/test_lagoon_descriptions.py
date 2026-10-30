"""Single-line, first-sentence Lagoon summaries without onchain reads."""

import os

import pytest

from eth_defi.erc_4626.vault_protocol.lagoon import offchain_metadata
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault

#: First sentence supplied by Lagoon for Coinshift's leveraged USPC vault.
COINSHIFT_FIRST_SENTENCE = "Gami's Coinshift Leveraged USPC Vault targets 12%+ net annualized returns by applying fixed-rate leverage across the Coinshift ecosystem, including liquidity pools, yield monetization, and money market supply."

#: The provider's extra summary sentence must not be copied into listings.
COINSHIFT_EXTRA_SENTENCE = "Built on top of USPC, the vault benefits from a strong institutional-grade foundation, with USPC fully backed by a diversified portfolio of institutional-grade assets including BlackRock ICS USD Liquidity, Fidelity ILF USD, and Apollo ACRED."


def test_lagoon_coinshift_cached_summary_uses_first_sentence() -> None:
    """Shorten Coinshift's cached provider summary without altering source text.

    Only metadata properties are exercised, so construction intentionally skips
    the onchain ABI/version probes performed by the normal constructor.
    """
    metadata = {
        "description": "Full strategy.\n\nAdditional strategy details.",
        "short_description": f"{COINSHIFT_FIRST_SENTENCE}\\n{COINSHIFT_EXTRA_SENTENCE}",
    }
    vault = LagoonVault.__new__(LagoonVault)
    vault.__dict__["lagoon_metadata"] = metadata

    assert vault.short_description == COINSHIFT_FIRST_SENTENCE
    assert vault.description == "Full strategy.\n\nAdditional strategy details."
    assert COINSHIFT_EXTRA_SENTENCE in metadata["short_description"]


@pytest.mark.parametrize("metadata", [None, {}, {"description": None, "short_description": ""}])
def test_lagoon_missing_descriptions(metadata: dict | None) -> None:
    """Preserve missing and empty values instead of inventing strategy text.

    :param metadata:
        Incomplete cached Lagoon record.
    """
    vault = LagoonVault.__new__(LagoonVault)
    vault.__dict__["lagoon_metadata"] = metadata
    assert vault.description is None
    assert vault.short_description == ("" if metadata else None)


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("CI") == "true", reason="Lagoon API returns HTTP 500 from GitHub Actions datacenter IPs; see tests/lagoon/test_lagoon_metadata.py")
def test_lagoon_coinshift_live_summary_uses_first_sentence() -> None:
    """Exercise Coinshift's real Lagoon detail response and adapter summary.

    Query only the public metadata endpoint for the reviewed vault; this check
    does not scan other vaults, require credentials or read onchain state.
    """
    raw = offchain_metadata._fetch_vault_detail(1, "0x09252d2c4afca9b1479efdd39faa53de9ff23114")
    assert raw is not None
    assert raw.get("shortDescription")
    vault = LagoonVault.__new__(LagoonVault)
    vault.__dict__["lagoon_metadata"] = offchain_metadata._parse_vault_detail(raw)
    assert vault.short_description == COINSHIFT_FIRST_SENTENCE
    assert all(character not in vault.short_description for character in ("\n", "\r", "\\n", "\\r"))
