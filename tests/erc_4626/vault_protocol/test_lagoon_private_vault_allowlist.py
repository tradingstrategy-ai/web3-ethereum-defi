"""Tests for explicit listing of reviewed private Lagoon vaults."""

import datetime
import json
from pathlib import Path

import pytest
from eth_typing import HexAddress
from pytest import MonkeyPatch
from web3 import Web3

from eth_defi.erc_4626.vault_protocol.lagoon import offchain_metadata
from eth_defi.erc_4626.vault_protocol.lagoon.offchain_metadata import LAGOON_PRIVATE_VAULT_ALLOWLIST, PRIVATE_LAGOON_VAULT_NOTE, is_lagoon_private_vault_allowlisted
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.erc_7540.vault import ERC7540Vault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import MISSING_IN_PROTOCOL_FRONTEND, VaultFlag

ETHEREUM_CHAIN_ID = 1
PRIVATE_VAULT_ADDRESS = HexAddress("0xba6cfe8a9d199cd7f3e50114c4e4ec66f2d52c87")

EXPECTED_PRIVATE_VAULTS = frozenset(
    {
        (1, HexAddress("0x22f99228f3ba7cfc7189ddf14366970fe0cef0cb")),
        (1, HexAddress("0x23b27310451f2754de34d9c04aa24e8be367124a")),
        (1, HexAddress("0xba6cfe8a9d199cd7f3e50114c4e4ec66f2d52c87")),
        (1, HexAddress("0xef39d77c7fb6224ac974c5fa4e3151a6c6ce9594")),
        (1, HexAddress("0xf10801bcc3deaf467fb8b3dbb7430111822e6dab")),
        (1, HexAddress("0xfd104766499a3ff60ea85b5c6015ba9e32b8c891")),
        (42161, HexAddress("0x1723cb57af58efb35a013870c90fcc3d60174a4e")),
        (42161, HexAddress("0xc047d64dafe9e6ac76508835c17c6719f9278c1c")),
    }
)

EXCLUDED_PRIVATE_VAULTS = (
    (42161, HexAddress("0x8ecccad4c08f2e225515e6b223b46a723bc5cac3")),
    (42161, HexAddress("0xc55c9be465a44717062669d9ebef4a7785cfc2a3")),
)


def create_lagoon_vault(chain_id: int, address: HexAddress, metadata: offchain_metadata.LagoonVaultMetadata | None = None) -> LagoonVault:
    """Create a minimal Lagoon vault for private-listing tests.

    :param chain_id:
        Deployment chain ID.

    :param address:
        Vault contract address.

    :param metadata:
        Cached Lagoon detail metadata, if available.

    :return:
        Minimal Lagoon vault instance.
    """

    vault = object.__new__(LagoonVault)
    vault.spec = VaultSpec(chain_id=chain_id, vault_address=address)
    vault.__dict__["lagoon_metadata"] = metadata
    return vault


def test_private_lagoon_vault_allowlist_is_exact() -> None:
    """Include only the eight reviewed plausible private deployments."""

    assert LAGOON_PRIVATE_VAULT_ALLOWLIST == EXPECTED_PRIVATE_VAULTS
    assert LAGOON_PRIVATE_VAULT_ALLOWLIST.isdisjoint(EXCLUDED_PRIVATE_VAULTS)


def test_private_lagoon_vault_allowlist_normalises_address_case() -> None:
    """Match allowlisted vaults independently of checksum casing."""

    assert is_lagoon_private_vault_allowlisted(ETHEREUM_CHAIN_ID, Web3.to_checksum_address(PRIVATE_VAULT_ADDRESS)) is True
    assert is_lagoon_private_vault_allowlisted(42161, PRIVATE_VAULT_ADDRESS) is False


@pytest.mark.parametrize(("chain_id", "address"), sorted(EXPECTED_PRIVATE_VAULTS))
def test_private_lagoon_vault_is_not_flagged_unofficial(chain_id: int, address: HexAddress, monkeypatch: MonkeyPatch) -> None:
    """Keep every allowlisted hidden vault eligible for public export."""

    monkeypatch.setattr(ERC7540Vault, "get_flags", lambda _self: set())
    monkeypatch.setattr(ERC7540Vault, "get_notes", lambda _self: None)
    vault = create_lagoon_vault(chain_id, address)

    assert vault.get_flags() == set()
    assert vault.get_notes() == PRIVATE_LAGOON_VAULT_NOTE


@pytest.mark.parametrize(("chain_id", "address"), EXCLUDED_PRIVATE_VAULTS)
def test_excluded_private_lagoon_vault_remains_unofficial(chain_id: int, address: HexAddress, monkeypatch: MonkeyPatch) -> None:
    """Keep the two reviewed but implausible hidden deployments excluded."""

    monkeypatch.setattr(ERC7540Vault, "get_flags", lambda _self: set())
    monkeypatch.setattr(ERC7540Vault, "get_notes", lambda _self: None)
    vault = create_lagoon_vault(chain_id, address)

    assert vault.get_flags() == {VaultFlag.unofficial}
    assert vault.get_notes() == MISSING_IN_PROTOCOL_FRONTEND


def test_private_lagoon_vault_detail_is_fetched_outside_visible_listing(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Add allowlisted private vault details to the cached Lagoon catalogue.

    :param tmp_path:
        Temporary cache directory.

    :param monkeypatch:
        Pytest monkeypatch fixture.
    """

    monkeypatch.setattr(offchain_metadata, "LAGOON_PRIVATE_VAULT_ALLOWLIST", frozenset({(ETHEREUM_CHAIN_ID, PRIVATE_VAULT_ADDRESS)}))

    def fetch_listing_page(_chain_id: int, **_kwargs: object) -> dict[str, object]:
        """Return an empty public listing."""

        return {"vaults": [], "hasNextPage": False}

    def fetch_vault_detail(chain_id: int, address: HexAddress, **_kwargs: object) -> dict[str, object]:
        """Return metadata for the hidden allowlisted deployment."""

        assert chain_id == ETHEREUM_CHAIN_ID
        assert address == PRIVATE_VAULT_ADDRESS
        return {
            "name": "Der base USDC",
            "description": None,
            "shortDescription": None,
            "curators": [],
        }

    monkeypatch.setattr(offchain_metadata, "_fetch_vault_listing_page", fetch_listing_page)
    monkeypatch.setattr(offchain_metadata, "_fetch_vault_detail", fetch_vault_detail)

    vaults = offchain_metadata.fetch_lagoon_vaults_for_chain(
        ETHEREUM_CHAIN_ID,
        cache_path=tmp_path,
        now_=datetime.datetime(2026, 9, 28),  # noqa: DTZ001 - Repository convention is naive UTC.
    )

    private_checksum_address = HexAddress(Web3.to_checksum_address(PRIVATE_VAULT_ADDRESS))
    assert set(vaults) == {private_checksum_address}
    assert vaults[private_checksum_address]["name"] == "Der base USDC"


def test_empty_visible_listing_does_not_replace_complete_stale_cache_with_private_vaults(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Retain a complete stale catalogue when the visible listing is unexpectedly empty.

    :param tmp_path:
        Temporary cache directory.

    :param monkeypatch:
        Pytest monkeypatch fixture.
    """

    stale_address = HexAddress(Web3.to_checksum_address("0x2222222222222222222222222222222222222222"))
    stale_metadata = {
        stale_address: {
            "name": "Known visible vault",
            "description": "Previously fetched metadata",
            "short_description": None,
            "curator_name": None,
            "curator_url": None,
            "average_settlement": None,
        }
    }
    cache_file = offchain_metadata._get_cache_file(tmp_path, ETHEREUM_CHAIN_ID)
    cache_file.write_text(json.dumps(stale_metadata), encoding="utf-8")

    monkeypatch.setattr(offchain_metadata, "LAGOON_PRIVATE_VAULT_ALLOWLIST", frozenset({(ETHEREUM_CHAIN_ID, PRIVATE_VAULT_ADDRESS)}))
    monkeypatch.setattr(offchain_metadata, "_fetch_vault_listing_page", lambda *_args, **_kwargs: {"vaults": [], "hasNextPage": False})

    def fail_detail_fetch(*_args: object, **_kwargs: object) -> None:
        """Fail if private details are fetched after an invalid empty listing."""

        message = "Private detail fetch must not replace a complete stale catalogue"
        raise AssertionError(message)

    monkeypatch.setattr(offchain_metadata, "_fetch_vault_detail", fail_detail_fetch)

    vaults = offchain_metadata.fetch_lagoon_vaults_for_chain(
        ETHEREUM_CHAIN_ID,
        cache_path=tmp_path,
        now_=datetime.datetime(2026, 9, 30),  # noqa: DTZ001 - Repository convention is naive UTC.
        max_cache_duration=datetime.timedelta(0),
    )

    assert vaults == stale_metadata
