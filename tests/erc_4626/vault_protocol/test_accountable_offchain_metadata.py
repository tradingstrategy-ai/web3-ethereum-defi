"""Unit tests for Accountable's offchain vault metadata parsing."""

from unittest.mock import Mock

import pytest
from web3 import Web3

from eth_defi.erc_4626.vault_protocol.accountable.offchain_metadata import _parse_vault_metadata  # noqa: PLC2701
from eth_defi.erc_4626.vault_protocol.accountable.vault import AccountableVault
from eth_defi.vault.base import VaultSpec, normalise_vault_description
from eth_defi.vault.handwritten_metadata import ACCOUNTABLE_HANDWRITTEN_VAULT_METADATA, TRAVESSIA_TAURI_VAULT_METADATA, get_handwritten_vault_metadata


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


def test_accountable_copied_summary_handles_literal_newlines() -> None:
    """Use only the first sentence even when the API escapes its line breaks.

    Source metadata is preserved in the full description and shortened only
    when it is copied into the listing summary.
    """
    strategy = r"Earn 4.5% in money markets.\nFurther strategy details."
    metadata = _parse_vault_metadata({}, {"loan": {"vault_strategy": strategy}})
    assert metadata["description"] == strategy
    assert metadata["short_description"] == "Earn 4.5% in money markets."


@pytest.mark.parametrize("address", ("0x9c3da70cd7fedeaef528d060ea99ace133e08fd2", "0x9c3da70cD7feDEAeF528d060ea99AcE133E08Fd2"))
def test_travessia_metadata_resolves_without_accountable_api(monkeypatch: pytest.MonkeyPatch, address: str) -> None:
    """Resolve the delisted vault's strategy and homepage without network reads.

    Both address forms must reach the same handwritten metadata before the
    adapter attempts to fetch Accountable metadata.

    :param monkeypatch:
        Fixture replacing the API lookup with a failing network guard.
    :param address:
        Lower-case or checksummed Avalanche share-token address.
    :return:
        None. Assertions validate offline adapter behaviour.
    """
    fetch_metadata = Mock(side_effect=AssertionError("Handwritten metadata must not fetch Accountable's API"))
    monkeypatch.setattr("eth_defi.erc_4626.vault_protocol.accountable.vault.fetch_accountable_vault_metadata", fetch_metadata)
    vault = AccountableVault(Web3(), VaultSpec(chain_id=43114, vault_address=address))
    metadata = get_handwritten_vault_metadata(43114, address)

    assert metadata is not None
    assert vault.short_description == "USDT loans funding Tauri Agrícola's grain purchases and transport in Brazil."
    assert vault.description == metadata.description
    assert "grain purchases and transport" in vault.description
    assert "Rabobank, Avalanche, RedStone, Accountable and Royco" in vault.description
    assert vault.get_link() == "https://www.travessiacredit.com/vaults/43114/tauri/0x9c3da70cD7feDEAeF528d060ea99AcE133E08Fd2"
    fetch_metadata.assert_not_called()


@pytest.mark.parametrize(
    ("chain_id", "address"),
    (
        (1, "0x9c3da70cd7fedeaef528d060ea99ace133e08fd2"),
        (43114, "0x0000000000000000000000000000000000000001"),
        (143, "0xf84a37545e313e8177d80039158ed478c7dc9079"),
        (1, "0x15ebd88e8898233128ffcc6e9c2dab1d2d7e1e86"),
        (143, "0x1eee675492a0f975229464b8dc8c795a0f1fe02a"),
        (143, "0x45c54b41bc9f7c7cfdfe9ba383ec6973ebf2cc98"),
    ),
)
def test_travessia_metadata_is_scoped_to_reviewed_deployment(chain_id: int, address: str) -> None:
    """Keep unrelated addresses and unreviewed deployments unchanged.

    A matching address on another chain must not inherit the Avalanche USDT
    strategy, and other share tokens need their own review.

    :param chain_id:
        Chain identifier outside the reviewed deployment.
    :param address:
        Address which must not resolve to handwritten metadata.
    :return:
        None. Assertions validate deployment isolation.
    """
    assert get_handwritten_vault_metadata(chain_id, address) is None


@pytest.mark.parametrize(("chain_id", "address"), tuple(TRAVESSIA_TAURI_VAULT_METADATA | ACCOUNTABLE_HANDWRITTEN_VAULT_METADATA))
def test_reviewed_accountable_descriptions_work_offline(monkeypatch: pytest.MonkeyPatch, chain_id: int, address: str) -> None:
    """Resolve every reviewed deployment before accessing Accountable's API.

    Descriptions, summaries and official links must all be available when
    the upstream marketplace delists a deployment.

    :param monkeypatch:
        Fixture guarding the upstream API lookup.
    :param chain_id:
        Reviewed EVM chain identifier.
    :param address:
        Reviewed share-token address.
    :return:
        None. Assertions validate adapter routing without network access.
    """
    fetch_metadata = Mock(side_effect=AssertionError("Reviewed metadata must resolve offline"))
    monkeypatch.setattr("eth_defi.erc_4626.vault_protocol.accountable.vault.fetch_accountable_vault_metadata", fetch_metadata)
    vault = AccountableVault(Web3(), VaultSpec(chain_id, Web3.to_checksum_address(address)))
    metadata = get_handwritten_vault_metadata(chain_id, address)

    assert metadata is not None
    assert vault.description == metadata.description
    assert vault.short_description == metadata.short_description
    assert vault.get_link() == metadata.link
    assert vault.description and vault.short_description
    assert vault.description == normalise_vault_description(vault.description)
    assert all(separator not in vault.short_description for separator in ("\n", "\r", r"\n", r"\r"))
    fetch_metadata.assert_not_called()


def test_travessia_usdt_does_not_inherit_pelican_metadata() -> None:
    """Prefer Ethereum's Tauri strategy over a same-address Pelican API entry.

    An unrelated deployment on chain 1672 must not supply the Ethereum
    vault's investment description or destination link.

    :return:
        None. Assertions validate the reviewed cross-chain collision repair.
    """
    vault = AccountableVault(Web3(), VaultSpec(1, "0xe4ee0fe4b09021fcdf76b165aa1e8eaf7d447c35"))
    vault.__dict__["accountable_metadata"] = {
        "description": "Pelican pGAIN invests in CLO tranches.",
        "short_description": "Pelican pGAIN.",
        "loan_address": "0x27b0b7Acd42D5C91db91446605770D3D3c338a42",
    }

    assert "Tauri Agrícola" in vault.description
    assert "Pelican" not in vault.description
    assert vault.short_description.startswith("USDT deposits")
    assert vault.get_link() == "https://www.travessiacredit.com/vaults/1/tauri/0xe4EE0fe4B09021FCdF76B165AA1e8eAF7d447c35"
