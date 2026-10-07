"""Checks for stablecoin metadata with overlapping token tickers."""

from pathlib import Path

from eth_defi.feed.sources import load_feeder_metadata
from eth_defi.stablecoin_metadata import STABLECOINS_DATA_DIR, build_stablecoin_metadata_json, is_stablecoin_like, load_all_stablecoin_metadata
from eth_defi.vault.denomination import DenominationFamily, classify_denomination


def test_ousd_issuers_remain_distinct() -> None:
    """Keep both OUSD issuers discoverable under their shared ticker.

    Separate metadata files give each issuer its own category, contracts and
    public slug. The symbol lookup must retain both files.

    :return:
        Nothing.
    """
    by_symbol = load_all_stablecoin_metadata()
    assert {entry["name"] for entry in by_symbol["OUSD"]} == {"Origin Dollar", "Open USD"}

    origin = build_stablecoin_metadata_json(STABLECOINS_DATA_DIR / "ousd.yaml")[0]
    open_standard = build_stablecoin_metadata_json(STABLECOINS_DATA_DIR / "ousd-open-standard.yaml")[0]
    assert origin["category"] == "yield_bearing"
    assert open_standard["category"] == "stablecoin"
    assert origin["slug"] == "ousd"
    assert open_standard["slug"] == "ousd-open-standard"
    assert {contract["chain"] for contract in open_standard["contract_addresses"]} == {"base", "ethereum", "tempo"}


def test_plume_pusd_classification_and_metadata() -> None:
    """Include Plume USD without confusing its ticker with PolyQuity PUSD.

    The cleaned vault export reads the classification set independently of
    rich metadata. Both layers must identify the same Plume token, and its
    feed alias must point at the issuer's source-bearing curator record.

    :return: Nothing.
    """
    assert is_stablecoin_like("pUSD")
    assert classify_denomination("pUSD") == DenominationFamily.stablecoin
    metadata = build_stablecoin_metadata_json(STABLECOINS_DATA_DIR / "pusd.yaml")[0]
    assert metadata["symbol"] == "pUSD"
    assert metadata["category"] == "stablecoin"
    assert metadata["contract_addresses"] == [{"chain": "plume", "address": "0xdddD73F5Df1F0DC31373357beAC77545dC5A6f3F"}]
    feeds = Path(__file__).resolve().parents[1] / "eth_defi/data/feeds"
    alias = load_feeder_metadata(feeds / "stablecoins/pusd.yaml")
    issuer = load_feeder_metadata(feeds / "curators/plume.yaml")
    assert alias["canonical-feeder-id"] == issuer["feeder-id"]
    assert issuer["twitter"]
    assert not issuer.get("canonical-feeder-id")
