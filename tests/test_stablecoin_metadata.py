"""Checks for stablecoin metadata with overlapping token tickers."""

from eth_defi.stablecoin_metadata import STABLECOINS_DATA_DIR, build_stablecoin_metadata_json, load_all_stablecoin_metadata


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
