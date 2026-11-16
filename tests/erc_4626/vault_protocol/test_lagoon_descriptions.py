"""Single-line, first-sentence Lagoon summaries without onchain reads."""

import os

import pytest

from eth_defi.erc_4626.vault_protocol.lagoon import offchain_metadata
from eth_defi.erc_4626.vault_protocol.lagoon import vault as adapter
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.erc_7540.vault import ERC7540Vault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import VaultFlag

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
    vault.spec = VaultSpec(1, "0x1111111111111111111111111111111111111111")
    vault.__dict__["_lagoon_scan_snapshot"] = offchain_metadata.LagoonMetadataSnapshot(metadata, True, "success", None)

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
    vault.spec = VaultSpec(1, "0x1111111111111111111111111111111111111111")
    vault.__dict__["_lagoon_scan_snapshot"] = offchain_metadata.LagoonMetadataSnapshot(metadata, True, "success", None)
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
    vault.spec = VaultSpec(1, "0x09252d2c4afca9b1479efdd39faa53de9ff23114")
    vault.__dict__["_lagoon_scan_snapshot"] = offchain_metadata.LagoonMetadataSnapshot(offchain_metadata._parse_vault_detail(raw), True, "success", None)
    assert vault.short_description == COINSHIFT_FIRST_SENTENCE
    assert all(character not in vault.short_description for character in ("\n", "\r", "\\n", "\\r"))


@pytest.mark.parametrize("listed", [False, None])
def test_lagoon_unlisted_detail_keeps_unofficial_flag(monkeypatch: pytest.MonkeyPatch, *, listed: bool | None) -> None:
    """Detail metadata must not approve an unlisted or unconfirmed deployment.

    Catalogue failures and real omission remain distinct for user-facing notes.
    """

    vault = LagoonVault.__new__(LagoonVault)
    vault.spec = VaultSpec(1, "0x1111111111111111111111111111111111111111")
    monkeypatch.setattr(ERC7540Vault, "get_flags", lambda _self: set())
    monkeypatch.setattr(ERC7540Vault, "get_notes", lambda _self: None)
    vault.__dict__["_lagoon_scan_snapshot"] = offchain_metadata.LagoonMetadataSnapshot({"description": "Known strategy.", "short_description": "Summary."}, listed, "success", None)
    assert vault.description == "Known strategy."
    assert vault.get_flags() == {VaultFlag.unofficial}
    assert ("could not be verified" in vault.get_notes()) is (listed is None)


def test_lagoon_long_lived_adapter_recovers_and_freezes_each_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Standalone reads expire while a pinned row keeps one consistent snapshot.

    Starting a second row must release a prior miss or observation.
    """

    vault = LagoonVault.__new__(LagoonVault)
    vault.spec = VaultSpec(1, "0x1111111111111111111111111111111111111111")
    state = offchain_metadata.LagoonMetadataSnapshot(None, None, "unavailable", None)
    monkeypatch.setattr(adapter, "fetch_lagoon_metadata_snapshot", lambda *_args: state)
    assert vault.description is None
    state = offchain_metadata.LagoonMetadataSnapshot({"description": "New strategy."}, False, "success", None)
    assert vault.description == "New strategy."
    vault.fetch_metadata_snapshot_for_scan()
    state = offchain_metadata.LagoonMetadataSnapshot({"description": "Changed strategy."}, True, "success", None)
    assert vault.description == "New strategy."
    assert vault.lagoon_metadata_snapshot.listed is False
    vault.fetch_metadata_snapshot_for_scan()
    assert vault.description == "Changed strategy."
    assert vault.lagoon_metadata_snapshot.listed is True


@pytest.mark.parametrize(
    ("accessor", "expected"),
    (("description", "Full strategy."), ("short_description", "Summary."), ("manager_name", "Example curator")),
)
def test_lagoon_accessor_reads_one_metadata_snapshot(accessor: str, expected: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep one accessor coherent if the next metadata observation differs.

    Standalone accesses use an expiring source, so a second read could lose
    the payload checked by the first. This test isolates the accessor itself
    and checks that it reads ``lagoon_metadata`` exactly once.

    :param accessor: Metadata-backed property to exercise.
    :param expected: Value from the first observation.
    :param monkeypatch: Replace only the provider-facing metadata property.
    :return: ``None`` after verifying a single source observation.
    """
    observations = iter([{"description": "Full strategy.", "short_description": "Summary.", "curators": [{"name": "Example curator"}]}, None])
    vault = LagoonVault.__new__(LagoonVault)
    vault.spec = VaultSpec(1, "0x1111111111111111111111111111111111111111")
    monkeypatch.setattr(LagoonVault, "lagoon_metadata", property(lambda _self: next(observations)))
    assert getattr(vault, accessor) == expected
    assert next(observations) is None
