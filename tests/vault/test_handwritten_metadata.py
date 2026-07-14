"""Test handwritten vault metadata used when issuers lack strategy descriptions."""

import pytest

from eth_defi.erc_4626.vault_protocol.accountable.vault import AccountableVault
from eth_defi.midas.vault import MidasVault
from eth_defi.vault.flag import get_notes
from eth_defi.vault.handwritten_metadata import PIKU_VAULT_METADATA, format_handwritten_vault_note, get_handwritten_vault_metadata

PIKU_MORINI_VAULT_COUNT = 3


@pytest.mark.parametrize(
    ("address", "expected_short_description", "expected_link_fragment"),
    [
        (
            "0x99351BaEd3d8aB544CCb08aF96A105910fdA71E7",
            "Delta-neutral USD/TRY foreign-exchange arbitrage strategy.",
            "aFXArbUSDTRY",
        ),
        (
            "0x827Ce7E8e35861D9Ac7fE002755767b695A5594a",
            "Turkish equity and single-stock-futures basis-trade strategy.",
            "StockMarketTRBasisTrade",
        ),
        (
            "0x2bf11d2E04Bc40daa95c24B8b90EC4F5c57Dd326",
            "Leveraged USD/TRY carry and futures-basis strategy.",
            "CarryTradeUSDTRYLeverage",
        ),
    ],
)
def test_piku_vaults_have_handwritten_description_and_link(
    address: str,
    expected_short_description: str,
    expected_link_fragment: str,
) -> None:
    """Every published Piku/Morini vault has curated text and its Piku detail URL."""

    metadata = get_handwritten_vault_metadata(1, address)

    assert metadata is not None
    assert metadata.short_description == expected_short_description
    assert metadata.description
    assert expected_link_fragment in metadata.link
    assert metadata.link.startswith("https://piku.co/vaults/detail/")

    note = format_handwritten_vault_note(metadata)
    assert "**Summary:**" in note
    assert f"[Piku vault page]({metadata.link})" in note
    assert "[portfolio overview](https://morini.capital/)" in note
    assert get_notes(address, chain_id=1) == note
    assert get_notes(address, chain_id=8453) is None


def test_piku_handwritten_metadata_covers_three_published_morini_vaults() -> None:
    """The address-scoped table is restricted to the three published Morini vaults."""

    assert len(PIKU_VAULT_METADATA) == PIKU_MORINI_VAULT_COUNT


@pytest.mark.parametrize(
    "address",
    [
        "0x827Ce7E8e35861D9Ac7fE002755767b695A5594a",
        "0x2bf11d2E04Bc40daa95c24B8b90EC4F5c57Dd326",
    ],
)
def test_midas_adapter_uses_piku_handwritten_metadata(address: str) -> None:
    """Midas-issued Morini products replace generic Midas metadata with Piku text."""

    metadata = get_handwritten_vault_metadata(1, address)
    assert metadata is not None

    vault = type(
        "PikuMidasVault",
        (),
        {
            "chain_id": 1,
            "address": address,
            "get_protocol_name": lambda _self: "Midas",
        },
    )()

    assert MidasVault.description.fget(vault) == metadata.description
    assert MidasVault.short_description.fget(vault) == metadata.short_description
    assert MidasVault.get_link(vault) == metadata.link
    assert MidasVault.get_notes(vault) == format_handwritten_vault_note(metadata)


def test_accountable_adapter_uses_piku_handwritten_metadata() -> None:
    """The Piku FX vault replaces Accountable's generic API metadata and homepage."""

    address = "0x99351BaEd3d8aB544CCb08aF96A105910fdA71E7"
    metadata = get_handwritten_vault_metadata(1, address)
    assert metadata is not None

    vault = type("PikuAccountableVault", (), {"chain_id": 1, "address": address})()

    assert AccountableVault.description.fget(vault) == metadata.description
    assert AccountableVault.short_description.fget(vault) == metadata.short_description
    assert AccountableVault.get_link(vault) == metadata.link
