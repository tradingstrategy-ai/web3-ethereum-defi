"""Centaur attribution and maintained Lighter listing metadata."""

from pathlib import Path

import pytest
from PIL import Image

from eth_defi.feed.sources import load_feeder_metadata
from eth_defi.lighter.constants import LIGHTER_CHAIN_ID, LIGHTER_ETHEREUM, LIGHTER_ROBINHOOD
from eth_defi.lighter.daily_metrics import LighterDailyMetricsDatabase
from eth_defi.lighter.tags import get_strategy_tags
from eth_defi.lighter.vault_data_export import create_lighter_pool_row, merge_into_vault_database
from eth_defi.vault.curator import build_curator_metadata_json, get_curator_name, identify_curator, is_protocol_curator
from eth_defi.vault.handwritten_metadata import MORINI_CAPITAL_VAULT_METADATA, get_handwritten_vault_metadata
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase

#: Exact identities linked from Centaur's official Lighter pool documentation.
#: https://docs.centaur.io/strategies/lighter-pools/overview
POOLS = (
    (281474976488848, "Post Fiat", "post-fiat", "goodalexander"),
    (281474976488851, "Inverse 1000x", "inverse-1000x", "jvb_xyz"),
    (281474976488853, "Serenity", "serenity", "aleabitoreddit"),
    (281474976488856, "Lazy Villager", "lazy-villager", "lazyvillager1"),
    (281474976489166, "Kole World", "kole-world", "captain_kole"),
    (281474976489165, "Black Bull", "black-bull", "blknoiz06"),
)


@pytest.mark.parametrize(("account_index", "name", "slug", "trader"), POOLS)
@pytest.mark.parametrize("source_description", [None, "Stale upstream description."])
def test_centaur_pool_metadata(account_index: int, name: str, slug: str, trader: str, source_description: str | None) -> None:
    """Export reviewed descriptions and preserve native identity and links.

    Test both missing and stale API copy, including the fee-use distinction
    and the inverse pool's exclusion of market-wide index calls.

    :param account_index: Verified Ethereum Lighter pool account index.
    :param name: Public pool strategy name.
    :param slug: Official strategy documentation slug.
    :param trader: Unaffiliated trader's public X handle.
    :param source_description: Missing or stale API description.
    :return: ``None``; assertions validate the exported row.
    """
    display_name = f"Centaur Strategies: {name}"
    spec, row = create_lighter_pool_row(account_index, display_name, source_description, 4_000, None)
    assert spec.chain_id == LIGHTER_CHAIN_ID
    assert spec.vault_address == f"lighter-pool-{account_index}"
    assert row["Name"] == display_name
    assert row["Link"] == f"https://app.lighter.xyz/public-pools/{account_index}"
    assert row["_short_description"]
    assert row["_short_description"] != row["_description"]
    assert f"https://docs.centaur.io/strategies/lighter-pools/vaults/{slug}" in row["_description"]
    assert trader in row["_description"]
    assert "not affiliated with Centaur" in row["_description"]
    if name == "Black Bull":
        assert "buy and permanently burn $ANSEM on Solana" in row["_description"]
        assert "rebates" not in row["_description"]
    else:
        assert "random, discretionary rebates" in row["_description"]
        assert "depositors with realised losses" in row["_description"]
        assert "not insurance and do not guarantee recovery of losses or any return" in row["_description"]
        assert "No depositor has a right to a rebate" in row["_description"]
        assert "alter, suspend or stop the programme" in row["_description"]
        assert "https://docs.centaur.io/strategies/lighter-pools/fees" in row["_description"]
    if name == "Inverse 1000x":
        assert "opposite side" in row["_description"]
        assert "Market-wide index calls are excluded" in row["_description"]
    expected_tags = {
        StrategyTag.directional_trading,
        StrategyTag.discretionary_trading,
        StrategyTag.perpetual_futures,
        StrategyTag.sentiment_trading,
        StrategyTag.social_trading,
    }
    assert row["_strategy_tags"] == expected_tags
    assert get_strategy_tags(spec.vault_address.upper()) == expected_tags
    assert get_handwritten_vault_metadata(LIGHTER_CHAIN_ID, spec.vault_address.upper()) is not None
    for title in (display_name, "Renamed pool"):
        assert identify_curator(LIGHTER_CHAIN_ID, "", title, spec.vault_address.upper(), "lighter") == "centaur"
    # An unrecognised pool still resolves the organisation's full brand.
    assert identify_curator(LIGHTER_CHAIN_ID, "", display_name, "lighter-pool-123", "lighter") == "centaur"


@pytest.mark.parametrize(("account_index", "name"), [(account_index, name) for account_index, name, _, _ in POOLS])
def test_centaur_overrides_are_deployment_scoped(account_index: int, name: str) -> None:
    """Do not claim a Robinhood pool that reuses an Ethereum account index.

    An unbranded pool in the other deployment must keep its API description
    and the native default tags, with no exact Centaur attribution.

    :param account_index: Ethereum pool account index to reuse on Robinhood.
    :param name: Bare pool name, without the Centaur brand.
    :return: ``None``; assertions validate isolation.
    """
    spec, row = create_lighter_pool_row(account_index, name, "Original description. More detail.", 4_000, None, deployment=LIGHTER_ROBINHOOD)
    assert get_handwritten_vault_metadata(spec.chain_id, spec.vault_address) is None
    assert identify_curator(spec.chain_id, "", name, spec.vault_address, "lighter") != "centaur"
    assert get_handwritten_vault_metadata(1, f"lighter-pool-{account_index}") is None
    assert identify_curator(1, "", name, f"lighter-pool-{account_index}", "lighter") != "centaur"
    assert row["_description"] == "Original description. More detail."
    assert row["_short_description"] == "Original description."
    assert row["_strategy_tags"] == {StrategyTag.perpetual_futures}


def test_centaur_does_not_match_unrelated_serenity() -> None:
    """Keep generic product names out of global curator aliases.

    Serenity occurs in other curators' vault names and is insufficient
    evidence of Centaur responsibility on its own.

    :return: ``None``; assertions validate false-positive controls.
    """
    for title in ("Serenity", "DeTrade Serenity USDC"):
        assert identify_curator(1, "", title, "0x0000000000000000000000000000000000000001", "morpho") != "centaur"


def test_centaur_curator_export() -> None:
    """Load Centaur through the shared feeder schema and public exporter.

    Public copy must retain responsibility, fee-use and affiliation details,
    while both theme assets are available as transparent square PNGs.

    :return: ``None``; assertions validate metadata and image assets.
    """
    yaml_path = Path("eth_defi/data/feeds/curators/centaur.yaml")
    feeder = load_feeder_metadata(yaml_path)
    assert feeder["feeder-id"] == "centaur"
    metadata = build_curator_metadata_json(yaml_path, public_url="https://example.invalid")
    assert metadata["name"] == get_curator_name("centaur") == "Centaur"
    assert metadata["website"] == "https://www.centaur.io/"
    assert metadata["short_description"] == "Centaur extracts structured trading signals from public trader posts and operates signal-based public pools on Lighter."
    assert "[Centaur](https://www.centaur.io/)" in metadata["long_description"]
    assert "operator makes every final trade decision" in metadata["long_description"]
    assert "unaffiliated" in metadata["long_description"]
    assert "random, discretionary rebates" in metadata["long_description"]
    assert "depositors with realised losses" in metadata["long_description"]
    assert "not insurance and do not guarantee recovery of losses or any return" in metadata["long_description"]
    assert "No depositor has a right to a rebate" in metadata["long_description"]
    assert "alter, suspend or stop the programme" in metadata["long_description"]
    assert "$ANSEM" in metadata["long_description"]
    assert metadata["protocol_curator"] is False
    assert not is_protocol_curator("centaur")
    for variant in ("light", "dark"):
        assert metadata["logos"][variant] == f"https://example.invalid/curator-metadata/centaur/{variant}.png"
        with Image.open(Path("eth_defi/data/vaults/formatted_logos/centaur") / f"{variant}.png") as logo:
            assert logo.size == (256, 256)
            assert logo.mode == "RGBA"
            assert logo.getchannel("A").getextrema() == (0, 255)


def test_handwritten_morini_metadata_is_preserved() -> None:
    """Preserve existing EVM lookup behaviour while adding native identifiers.

    Every maintained Morini product remains accessible using mixed-case
    addresses, and the lookup remains scoped to its chain.

    :return: ``None``; assertions validate backwards compatibility.
    """
    for (chain_id, address), metadata in MORINI_CAPITAL_VAULT_METADATA.items():
        assert get_handwritten_vault_metadata(chain_id, address.upper()) == metadata
        assert get_handwritten_vault_metadata(9998, address) is None


def test_centaur_metadata_refreshes_existing_rows(tmp_path: Path) -> None:
    """Refresh stale persisted descriptions through the ordinary Lighter merge.

    All six pools are updated from a file-backed metrics database without a
    price rescan or bespoke metadata migration.

    :param tmp_path: Isolated directory for metrics and metadata databases.
    :return: ``None``; assertions validate persisted refreshed rows.
    """
    metadata_path = tmp_path / "vault-metadata-db.pickle"
    vault_db = VaultDatabase()
    db = LighterDailyMetricsDatabase(tmp_path / "lighter.duckdb")
    try:
        for account_index, name, _, _ in POOLS:
            spec, stale = create_lighter_pool_row(account_index, f"Centaur Strategies: {name}", None, 4_000, None)
            stale["_description"] = "Stale persisted description."
            stale["_short_description"] = None
            vault_db.rows[spec] = stale
            db.upsert_pool_metadata(deployment=LIGHTER_ETHEREUM.slug, account_index=account_index, name=f"Centaur Strategies: {name}", description="Stale API copy.", total_asset_value=4_000)
        vault_db.write(metadata_path)
        merge_into_vault_database(db, metadata_path)
        refreshed = VaultDatabase.read(metadata_path)
        assert len(refreshed.rows) == len(POOLS)
        for spec, row in refreshed.rows.items():
            assert row["_description"] != "Stale persisted description."
            assert row["_description"] != "Stale API copy."
            assert row["_short_description"]
            assert StrategyTag.discretionary_trading in row["_strategy_tags"]
            assert identify_curator(spec.chain_id, "", row["Name"], spec.vault_address, "lighter") == "centaur"
    finally:
        db.close()
