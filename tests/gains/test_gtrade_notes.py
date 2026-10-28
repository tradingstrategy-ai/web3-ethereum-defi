"""Public strategy notes, announcements and warning precedence without RPC."""

import pytest

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.scan import _normalise_scan_note  # noqa: PLC2701 - scanner note contract
from eth_defi.erc_4626.vault_protocol.gains.metadata import REVIEWED_GAINS_VAULTS, format_gains_vault_note
from eth_defi.erc_4626.vault_protocol.gains.vault import GainsVault
from eth_defi.erc_4626.vault_protocol.nova_markets.constants import NOVA_POOL_ADDRESS
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import VAULT_FLAGS_AND_NOTES, VaultFlag, is_flagged_vault


@pytest.mark.parametrize(("chain_id", "address"), REVIEWED_GAINS_VAULTS)
def test_reviewed_note_reaches_scan_metadata(chain_id: int, address: str) -> None:
    """All 24 reviewed deployments have distinct public context, without flags."""
    vault = GainsVault(web3=None, spec=VaultSpec(chain_id, address), features={ERC4626Feature.gains_like})
    note = vault.get_notes()
    assert note
    assert _normalise_scan_note(note, "Protocol description", "Protocol summary") == note
    assert not is_flagged_vault(address)


@pytest.mark.parametrize(
    ("chain_id", "address", "announcement"),
    [
        (137, "0x91993f2101cc758d0deb7279d41e880f7defe827", "7 December 2022"),
        (42161, "0xd3443ee1e91af28e5fb858fbd0d72a63ba8046e0", "27 January 2024"),
        (42161, "0x4beef1113f968326905224d2ca272f3032a9a9f4", "13 March 2025"),
        (8453, "0xb7cb7cb7c3cd96e251c9bf8800b9631134bbadc6", "12 June 2025"),
    ],
)
def test_product_announcement_selection(chain_id: int, address: str, announcement: str) -> None:
    """Each collateral family links its actual historical announcement."""
    note = format_gains_vault_note(chain_id, address)
    assert announcement in note
    assert "not a separate deployment announcement for every chain" in note
    assert "overcollateralisation buffer" in note
    assert "designated epoch" in note


def test_btcusd_partner_dependency() -> None:
    """The Bitcoin-backed stablecoin pool has its own recapitalisation rules."""
    note = format_gains_vault_note(8453, "0xb7cb7cb7c3cd96e251c9bf8800b9631134bbadc6")
    assert "rather than BTC itself" in note
    assert "relies on Bifrost" in note
    assert "instead of minting GNS" in note


def test_unconfirmed_staging_note_does_not_borrow_gusdc_strategy() -> None:
    """A staging name does not establish official affiliation or strategy."""
    note = format_gains_vault_note(42161, "0xb7058370db10f0712eddb297bc3a58c3a2e5c3a7")
    assert "staging" in note
    assert "could not be verified" in note
    assert "**Strategy:**" not in note
    assert "b4dabcf9b991" not in note


def test_manual_warning_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    """A new strategy note must not obscure an explicit existing warning."""
    monkeypatch.setitem(VAULT_FLAGS_AND_NOTES, NOVA_POOL_ADDRESS, (VaultFlag.illiquid, "Reviewed withdrawal warning"))
    vault = GainsVault(web3=None, spec=VaultSpec(42161, NOVA_POOL_ADDRESS), features={ERC4626Feature.gains_like})
    assert vault.get_notes() == "Reviewed withdrawal warning"


def test_notes_do_not_leak_to_other_chains_or_ostium() -> None:
    """The common adapter does not attach gTrade notes to other deployments."""
    assert format_gains_vault_note(1, NOVA_POOL_ADDRESS) is None
    vault = GainsVault(web3=None, spec=VaultSpec(42161, "0x20d419a8e12c45f88fda7c5760bb6923cee27f98"), features={ERC4626Feature.gains_like})
    assert vault.get_notes() is None
