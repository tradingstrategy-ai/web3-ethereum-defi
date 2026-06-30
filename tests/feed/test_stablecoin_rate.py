"""Tests for stablecoin CoinGecko rate refresh and depeg lookups."""

import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from eth_defi.feed import stablecoin_rate
from eth_defi.feed.stablecoin_rate import StablecoinRateFeeder, build_depegged_stablecoin_lookups, iter_stablecoin_rate_targets, refresh_stablecoin_rates
from eth_defi.stablecoin_metadata import build_stablecoin_metadata_json

USDC_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
USDX_ADDRESS = "0x1111111111111111111111111111111111111111"


@dataclass(slots=True)
class DummyCoinGeckoResponse:
    """Small response object for mocked CoinGecko tests."""

    payload: dict[str, Any]

    def raise_for_status(self) -> None:
        """Mock successful HTTP response."""

    def json(self) -> dict[str, Any]:
        """Return the mocked JSON body."""
        return self.payload


def _write_usdc_yaml(data_dir: Path) -> Path:
    """Create a minimal standard USDC stablecoin YAML file."""
    yaml_path = data_dir / "usdc.yaml"
    yaml_path.write_text(
        f"""symbol: USDC
name: Circle USDC
short_description: USD Coin
long_description: ''
category: stablecoin
links:
  homepage: https://www.circle.com/usdc
  coingecko: https://www.coingecko.com/en/coins/usd-coin
  defillama: https://defillama.com/stablecoin/usd-coin
  twitter: https://x.com/circle
slug: usdc
contract_addresses:
  - chain: ethereum
    address: '{USDC_ADDRESS}'
checks:
  twitter_last_post_at: ''
  domain_up_at: ''
  marked_dead_at: ''
  information_found_missing_at: ''
"""
    )
    return yaml_path


def _write_usdx_yaml(data_dir: Path) -> Path:
    """Create a minimal multi-entry USDX YAML file with Kava USDX metadata."""
    yaml_path = data_dir / "usdx.yaml"
    yaml_path.write_text(
        f"""symbol: USDX
category: stablecoin
entries:
- name: Stables Labs USDX
  short_description: Stables Labs USDX
  long_description: ''
  coingecko_id: usdx-money-usdx
  coingecko_link: https://www.coingecko.com/en/coins/usdx-money-usdx
  coingecko_id_source: manual
  links:
    homepage: https://usdx.money/
    coingecko: https://www.coingecko.com/en/coins/stables-labs-usdx
    defillama: ''
    twitter: https://x.com/usdx_money
  checks:
    twitter_last_post_at: ''
    domain_up_at: ''
    marked_dead_at: ''
    information_found_missing_at: ''
- name: Kava USDX
  short_description: Kava USDX
  long_description: ''
  coingecko_id: usdx
  coingecko_link: https://www.coingecko.com/en/coins/usdx
  coingecko_id_source: manual
  links:
    homepage: https://www.kava.io/
    coingecko: https://www.coingecko.com/en/coins/kava-lend
    defillama: ''
    twitter: https://x.com/KAVA_CHAIN
  contract_addresses:
    - chain: kava
      address: '{USDX_ADDRESS}'
  checks:
    twitter_last_post_at: ''
    domain_up_at: ''
    marked_dead_at: ''
    information_found_missing_at: ''
slug: usdx
"""
    )
    return yaml_path


def _write_fiat_stablecoin_yaml(data_dir: Path, symbol: str, name: str, coingecko_id: str) -> Path:
    """Create a minimal non-USD fiat stablecoin YAML file."""
    yaml_path = data_dir / f"{symbol.lower()}.yaml"
    yaml_path.write_text(
        f"""symbol: {symbol}
name: {name}
short_description: {name}
long_description: ''
category: stablecoin
coingecko_id: {coingecko_id}
coingecko_link: https://www.coingecko.com/en/coins/{coingecko_id}
coingecko_id_source: manual
links:
  homepage: ''
  coingecko: https://www.coingecko.com/en/coins/{coingecko_id}
  defillama: ''
  twitter: ''
slug: {symbol.lower()}
checks:
  twitter_last_post_at: ''
  domain_up_at: ''
  marked_dead_at: ''
  information_found_missing_at: ''
"""
    )
    return yaml_path


def test_refresh_stablecoin_rates_updates_usdc_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """USDC refresh stores CoinGecko rate fields and feeder lookup data."""
    _write_usdc_yaml(tmp_path)

    def fake_get(url: str, params: dict[str, str], headers: dict[str, str], timeout: float) -> DummyCoinGeckoResponse:
        assert url == stablecoin_rate.COINGECKO_SIMPLE_PRICE_URL
        assert params["ids"] == "usd-coin"
        assert "usd" in params["vs_currencies"].split(",")
        assert "User-Agent" in headers
        assert timeout == 20.0
        return DummyCoinGeckoResponse(
            {
                "usd-coin": {
                    "usd": 0.9998,
                    "last_updated_at": 1782464168,
                }
            }
        )

    monkeypatch.setattr(stablecoin_rate.requests, "get", fake_get)
    now_ = datetime.datetime(2026, 6, 26, 12, 0, 0)

    summary = refresh_stablecoin_rates(data_dir=tmp_path, now_=now_, force=True)

    assert summary.files_scanned == 1
    assert summary.entries_seen == 1
    assert summary.rates_fetched == 1
    assert summary.failed_count == 0
    assert summary.depegged_count == 0

    target = next(iter_stablecoin_rate_targets(tmp_path))
    assert target.coingecko_id == "usd-coin"
    assert target.coingecko_link == "https://www.coingecko.com/en/coins/usd-coin"
    assert target.coingecko_id_source == "url"
    assert target.usd_rate == pytest.approx(0.9998)
    assert target.usd_rate_fetched_at == now_
    assert target.rate_fetch_failed_reason is None
    assert target.depegged_at is None

    metadata = build_stablecoin_metadata_json(tmp_path / "usdc.yaml")[0]
    assert metadata["coingecko_id"] == "usd-coin"
    assert metadata["usd_rate"] == pytest.approx(0.9998)
    assert metadata["usd_rate_fetched_at"] == "2026-06-26T12:00:00"

    feeder = StablecoinRateFeeder(data_dir=tmp_path)
    rate = feeder.get_denomination_token_rate_section(1, USDC_ADDRESS, "USDC")
    assert rate.coingecko_id == "usd-coin"
    assert rate.usd_rate == pytest.approx(0.9998)
    assert rate.usd_rate_fetched_at == now_
    assert rate.usd_rate_source == "coingecko"
    assert feeder.is_depegged_stablecoin_token(1, USDC_ADDRESS, "USDC") is False


def test_refresh_stablecoin_rates_marks_usdx_depegged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """USDX depeg refresh stamps ``depegged_at`` and blacklisting lookups."""
    _write_usdx_yaml(tmp_path)

    def fake_get(url: str, params: dict[str, str], headers: dict[str, str], timeout: float) -> DummyCoinGeckoResponse:
        assert url == stablecoin_rate.COINGECKO_SIMPLE_PRICE_URL
        assert set(params["ids"].split(",")) == {"usdx", "usdx-money-usdx"}
        assert "User-Agent" in headers
        assert timeout > 0
        return DummyCoinGeckoResponse(
            {
                "usdx-money-usdx": {"usd": 1.0, "last_updated_at": 1782464168},
                "usdx": {"usd": 0.646809, "last_updated_at": 1782464168},
            }
        )

    monkeypatch.setattr(stablecoin_rate.requests, "get", fake_get)
    now_ = datetime.datetime(2026, 6, 26, 12, 0, 0)

    summary = refresh_stablecoin_rates(data_dir=tmp_path, now_=now_, force=True)

    assert summary.entries_seen == 2
    assert summary.rates_fetched == 2
    assert summary.depegged_count == 1
    assert summary.failed_count == 0

    kava_target = next(target for target in iter_stablecoin_rate_targets(tmp_path) if target.name == "Kava USDX")
    assert kava_target.usd_rate == pytest.approx(0.646809)
    assert kava_target.depegged_at == now_

    feeder = StablecoinRateFeeder(data_dir=tmp_path)
    assert feeder.is_depegged_stablecoin_token(2222, USDX_ADDRESS, "USDX") is True
    assert feeder.is_depegged_stablecoin_token(2222, None, "USDX") is False

    rate = feeder.get_denomination_token_rate_section(2222, USDX_ADDRESS, "USDX")
    assert rate.coingecko_id == "usdx"
    assert rate.usd_rate == pytest.approx(0.646809)
    assert rate.usd_rate_source == "coingecko"


def test_refresh_stablecoin_rates_preserves_sticky_depegged_at(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Existing depeg timestamps survive repeat depeg checks and later recovery."""
    _write_usdx_yaml(tmp_path)
    previous_depegged_at = datetime.datetime(2026, 6, 25, 12, 0, 0)
    now_ = datetime.datetime(2026, 6, 26, 12, 0, 0)

    stablecoin_rate._update_yaml_entry_fields(
        tmp_path / "usdx.yaml",
        1,
        {
            "usd_rate": 0.646809,
            "usd_rate_fetched_at": previous_depegged_at,
            "peg_rate": 0.646809,
            "peg_rate_currency": "usd",
            "depegged_at": previous_depegged_at,
        },
    )

    prices = [
        {"usdx-money-usdx": {"usd": 1.0, "last_updated_at": 1782464168}, "usdx": {"usd": 0.5, "last_updated_at": 1782464168}},
        {"usdx-money-usdx": {"usd": 1.0, "last_updated_at": 1782464168}, "usdx": {"usd": 1.0, "last_updated_at": 1782464168}},
    ]

    def fake_get(url: str, params: dict[str, str], headers: dict[str, str], timeout: float) -> DummyCoinGeckoResponse:
        assert url == stablecoin_rate.COINGECKO_SIMPLE_PRICE_URL
        assert set(params["ids"].split(",")) == {"usdx", "usdx-money-usdx"}
        assert isinstance(headers, dict)
        assert timeout > 0
        return DummyCoinGeckoResponse(prices.pop(0))

    monkeypatch.setattr(stablecoin_rate.requests, "get", fake_get)

    still_depegged_summary = refresh_stablecoin_rates(data_dir=tmp_path, now_=now_, force=True)
    assert still_depegged_summary.depegged_count == 1
    target = next(target for target in iter_stablecoin_rate_targets(tmp_path) if target.name == "Kava USDX")
    assert target.depegged_at == previous_depegged_at

    recovered_summary = refresh_stablecoin_rates(data_dir=tmp_path, now_=now_ + datetime.timedelta(days=1), force=True)
    assert recovered_summary.depegged_count == 0
    target = next(target for target in iter_stablecoin_rate_targets(tmp_path) if target.name == "Kava USDX")
    assert target.usd_rate == pytest.approx(1.0)
    assert target.depegged_at == previous_depegged_at


def test_refresh_stablecoin_rates_entry_gate_skips_same_day_and_refetches_next_day(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Entry-level daily gate avoids repeat CoinGecko calls after an attempt."""
    _write_usdc_yaml(tmp_path)
    calls: list[dict[str, str]] = []

    def fake_get(url: str, params: dict[str, str], headers: dict[str, str], timeout: float) -> DummyCoinGeckoResponse:
        assert url == stablecoin_rate.COINGECKO_SIMPLE_PRICE_URL
        assert isinstance(headers, dict)
        assert timeout > 0
        calls.append(params)
        return DummyCoinGeckoResponse({"usd-coin": {"usd": 0.9998, "last_updated_at": 1782464168}})

    monkeypatch.setattr(stablecoin_rate.requests, "get", fake_get)

    first_summary = refresh_stablecoin_rates(data_dir=tmp_path, now_=datetime.datetime(2026, 6, 26, 12, 0, 0))
    second_summary = refresh_stablecoin_rates(data_dir=tmp_path, now_=datetime.datetime(2026, 6, 26, 13, 0, 0))
    third_summary = refresh_stablecoin_rates(data_dir=tmp_path, now_=datetime.datetime(2026, 6, 27, 12, 0, 0))

    assert first_summary.rates_fetched == 1
    assert second_summary.rates_fetched == 0
    assert second_summary.files_updated == 0
    assert third_summary.rates_fetched == 1
    assert len(calls) == 2


def test_refresh_stablecoin_rates_records_missing_price_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing CoinGecko price stamps failure fields without depeg flag."""
    _write_usdc_yaml(tmp_path)

    def fake_get(url: str, params: dict[str, str], headers: dict[str, str], timeout: float) -> DummyCoinGeckoResponse:
        assert url == stablecoin_rate.COINGECKO_SIMPLE_PRICE_URL
        assert params["ids"] == "usd-coin"
        assert "User-Agent" in headers
        assert timeout > 0
        return DummyCoinGeckoResponse({})

    monkeypatch.setattr(stablecoin_rate.requests, "get", fake_get)
    now_ = datetime.datetime(2026, 6, 26, 12, 0, 0)

    summary = refresh_stablecoin_rates(data_dir=tmp_path, now_=now_, force=True)

    assert summary.failed_count == 1
    target = next(iter_stablecoin_rate_targets(tmp_path))
    assert target.rate_fetch_failed_at == now_
    assert target.rate_fetch_failed_reason == "coingecko_price_missing"
    assert target.depegged_at is None


@pytest.mark.parametrize(
    ("symbol", "name", "coingecko_id", "peg_currency", "usd_rate"),
    [
        ("CADC", "PayTrie CADC Canadian Dollar stablecoin", "cad-coin", "cad", 0.73),
        ("CJPY", "Yamato CJPY Japanese Yen stablecoin", "convertible-jpy-token", "jpy", 0.0064),
    ],
)
def test_non_usd_fiat_stablecoins_compare_against_their_peg_currency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    symbol: str,
    name: str,
    coingecko_id: str,
    peg_currency: str,
    usd_rate: float,
) -> None:
    """Non-USD fiat stablecoins do not depeg merely because their USD price is below one."""
    _write_fiat_stablecoin_yaml(tmp_path, symbol, name, coingecko_id)

    def fake_get(url: str, params: dict[str, str], headers: dict[str, str], timeout: float) -> DummyCoinGeckoResponse:
        assert url == stablecoin_rate.COINGECKO_SIMPLE_PRICE_URL
        assert params["ids"] == coingecko_id
        assert set(params["vs_currencies"].split(",")) == {"usd", peg_currency}
        assert "User-Agent" in headers
        assert timeout > 0
        return DummyCoinGeckoResponse({coingecko_id: {"usd": usd_rate, peg_currency: 1.0, "last_updated_at": 1782464168}})

    monkeypatch.setattr(stablecoin_rate.requests, "get", fake_get)
    now_ = datetime.datetime(2026, 6, 26, 12, 0, 0)

    summary = refresh_stablecoin_rates(data_dir=tmp_path, now_=now_, force=True)

    assert summary.failed_count == 0
    assert summary.depegged_count == 0
    target = next(iter_stablecoin_rate_targets(tmp_path))
    assert target.usd_rate == pytest.approx(usd_rate)
    assert target.peg_rate == pytest.approx(1.0)
    assert target.peg_rate_currency == peg_currency
    assert target.depegged_at is None


def test_url_derived_sub_cent_price_is_failure_not_depeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Wrong-asset guard rejects URL-derived sub-cent CoinGecko prices."""
    _write_usdc_yaml(tmp_path)

    def fake_get(url: str, params: dict[str, str], headers: dict[str, str], timeout: float) -> DummyCoinGeckoResponse:
        assert url == stablecoin_rate.COINGECKO_SIMPLE_PRICE_URL
        assert params["ids"] == "usd-coin"
        assert "User-Agent" in headers
        assert timeout > 0
        return DummyCoinGeckoResponse(
            {
                "usd-coin": {
                    "usd": 0.00049975,
                    "last_updated_at": 1782199898,
                }
            }
        )

    monkeypatch.setattr(stablecoin_rate.requests, "get", fake_get)
    now_ = datetime.datetime(2026, 6, 26, 12, 0, 0)

    summary = refresh_stablecoin_rates(data_dir=tmp_path, now_=now_, force=True)

    assert summary.failed_count == 1
    assert summary.depegged_count == 0
    target = next(iter_stablecoin_rate_targets(tmp_path))
    assert target.usd_rate is None
    assert target.rate_fetch_failed_reason == "coingecko_price_missing"
    assert target.depegged_at is None


def test_manual_sub_cent_price_is_trusted_as_depeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Manual CoinGecko ids are trusted even when a failed stablecoin trades below one cent."""
    (tmp_path / "usdx.yaml").write_text(
        """symbol: USDX
name: Stables Labs USDX
short_description: USDX is a USD stablecoin.
long_description: ''
category: stablecoin
coingecko_id: usdx-money-usdx
coingecko_link: https://www.coingecko.com/en/coins/usdx-money-usdx
coingecko_id_source: manual
links:
  homepage: https://usdx.money/
  coingecko: https://www.coingecko.com/en/coins/usdx-money-usdx
  defillama: ''
  twitter: ''
slug: usdx
checks:
  twitter_last_post_at: ''
  domain_up_at: ''
  marked_dead_at: ''
  information_found_missing_at: ''
"""
    )

    def fake_get(url: str, params: dict[str, str], headers: dict[str, str], timeout: float) -> DummyCoinGeckoResponse:
        assert url == stablecoin_rate.COINGECKO_SIMPLE_PRICE_URL
        assert params["ids"] == "usdx-money-usdx"
        assert "User-Agent" in headers
        assert timeout > 0
        return DummyCoinGeckoResponse({"usdx-money-usdx": {"usd": 0.0076, "last_updated_at": 1782464168}})

    monkeypatch.setattr(stablecoin_rate.requests, "get", fake_get)
    now_ = datetime.datetime(2026, 6, 26, 12, 0, 0)

    summary = refresh_stablecoin_rates(data_dir=tmp_path, now_=now_, force=True)

    assert summary.failed_count == 0
    assert summary.depegged_count == 1
    target = next(iter_stablecoin_rate_targets(tmp_path))
    assert target.usd_rate == pytest.approx(0.0076)
    assert target.depegged_at == now_


def test_peg_detection_avoids_substring_false_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Peg inference does not read currency names from inside unrelated words."""
    (tmp_path / "mim.yaml").write_text(
        """symbol: MIM
name: Abracadabra MIM
short_description: Magic Internet Money is a USD-pegged stablecoin.
long_description: ''
category: stablecoin
coingecko_id: magic-internet-money
coingecko_link: https://www.coingecko.com/en/coins/magic-internet-money
coingecko_id_source: manual
links:
  homepage: https://abracadabra.money/
  coingecko: https://www.coingecko.com/en/coins/magic-internet-money
  defillama: ''
  twitter: ''
slug: mim
checks:
  twitter_last_post_at: ''
  domain_up_at: ''
  marked_dead_at: ''
  information_found_missing_at: ''
"""
    )

    def fake_get(url: str, params: dict[str, str], headers: dict[str, str], timeout: float) -> DummyCoinGeckoResponse:
        assert url == stablecoin_rate.COINGECKO_SIMPLE_PRICE_URL
        assert params["ids"] == "magic-internet-money"
        assert set(params["vs_currencies"].split(",")) == {"usd"}
        assert "User-Agent" in headers
        assert timeout > 0
        return DummyCoinGeckoResponse({"magic-internet-money": {"usd": 0.444314, "last_updated_at": 1782464168}})

    monkeypatch.setattr(stablecoin_rate.requests, "get", fake_get)
    now_ = datetime.datetime(2026, 6, 26, 12, 0, 0)

    summary = refresh_stablecoin_rates(data_dir=tmp_path, now_=now_, force=True)

    assert summary.failed_count == 0
    assert summary.depegged_count == 1
    target = next(iter_stablecoin_rate_targets(tmp_path))
    assert target.peg_rate_currency == "usd"
    assert target.peg_rate == pytest.approx(0.444314)


def test_yield_bearing_share_token_is_not_depegged_by_low_share_price(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-nominal yield-bearing share token prices are exported but not depegged."""
    (tmp_path / "cusdc.yaml").write_text(
        """symbol: cUSDC
name: Compound USDC
short_description: Compound USDC
long_description: ''
category: yield_bearing
coingecko_id: compound-usd-coin
coingecko_link: https://www.coingecko.com/en/coins/compound-usd-coin
coingecko_id_source: manual
links:
  homepage: https://compound.finance/
  coingecko: https://www.coingecko.com/en/coins/compound-usd-coin
  defillama: ''
  twitter: ''
slug: cusdc
checks:
  twitter_last_post_at: ''
  domain_up_at: ''
  marked_dead_at: ''
  information_found_missing_at: ''
"""
    )

    def fake_get(url: str, params: dict[str, str], headers: dict[str, str], timeout: float) -> DummyCoinGeckoResponse:
        assert url == stablecoin_rate.COINGECKO_SIMPLE_PRICE_URL
        assert params["ids"] == "compound-usd-coin"
        assert set(params["vs_currencies"].split(",")) == {"usd"}
        assert "User-Agent" in headers
        assert timeout > 0
        return DummyCoinGeckoResponse({"compound-usd-coin": {"usd": 0.02531505, "last_updated_at": 1782464168}})

    monkeypatch.setattr(stablecoin_rate.requests, "get", fake_get)
    now_ = datetime.datetime(2026, 6, 26, 12, 0, 0)

    summary = refresh_stablecoin_rates(data_dir=tmp_path, now_=now_, force=True)

    assert summary.failed_count == 0
    assert summary.depegged_count == 0
    target = next(iter_stablecoin_rate_targets(tmp_path))
    assert target.usd_rate == pytest.approx(0.02531505)
    assert target.peg_rate_currency == "usd"
    assert target.depegged_at is None


def test_depegged_symbol_blacklist_ignores_ambiguous_symbols(tmp_path: Path) -> None:
    """Symbol-only blacklisting is used only when a depegged symbol is unique."""
    (tmp_path / "usdx-a.yaml").write_text(
        """symbol: USDX
name: USDX A
short_description: USDX A
long_description: ''
category: stablecoin
coingecko_id: usdx-a
coingecko_link: https://www.coingecko.com/en/coins/usdx-a
coingecko_id_source: manual
usd_rate: 0.5
usd_rate_fetched_at: '2026-06-26T12:00:00'
depegged_at: '2026-06-26T12:00:00'
links:
  homepage: ''
  coingecko: ''
  defillama: ''
  twitter: ''
slug: usdx-a
checks:
  twitter_last_post_at: ''
  domain_up_at: ''
  marked_dead_at: ''
  information_found_missing_at: ''
"""
    )
    (tmp_path / "usdx-b.yaml").write_text(
        """symbol: USDX
name: USDX B
short_description: USDX B
long_description: ''
category: stablecoin
coingecko_id: usdx-b
coingecko_link: https://www.coingecko.com/en/coins/usdx-b
coingecko_id_source: manual
usd_rate: 1.0
usd_rate_fetched_at: '2026-06-26T12:00:00'
links:
  homepage: ''
  coingecko: ''
  defillama: ''
  twitter: ''
slug: usdx-b
checks:
  twitter_last_post_at: ''
  domain_up_at: ''
  marked_dead_at: ''
  information_found_missing_at: ''
"""
    )

    depegged_contracts, depegged_symbols = build_depegged_stablecoin_lookups(tmp_path)

    assert depegged_contracts == set()
    assert depegged_symbols == set()
    assert StablecoinRateFeeder(tmp_path).is_depegged_stablecoin_token(1, None, "USDX") is False


def test_depegged_multi_entry_blacklists_by_contract_not_symbol(tmp_path: Path) -> None:
    """Multi-entry depegged tokens (e.g. USDX) blacklist by contract address, never by ticker.

    Reproduces the production gap where USDX-denominated vaults stayed listed: a
    multi-entry stablecoin YAML must blacklist the dead token by its pinned
    contract address while leaving an unrelated token that merely reuses the
    same ``USDX``/``USDx`` ticker (e.g. Axis USD) untouched.

    1. Write a multi-entry USDX YAML where both entries are depegged and the
       first carries ``contract_addresses``.
    2. Build the depeg lookups and assert the contract is indexed but the
       ambiguous ``USDX`` symbol is not.
    3. Assert the dead token matches by contract while a same-ticker token at a
       different address does not.
    """
    # 1. Multi-entry USDX YAML: both entries dead, first one pinned by address.
    dead_address = "0xf3527ef8de265eaa3716fb312c12847bfba66cef"
    (tmp_path / "usdx.yaml").write_text(
        """symbol: USDX
category: stablecoin
entries:
- name: Stables Labs USDX
  short_description: ''
  long_description: ''
  contract_addresses:
    - chain: ethereum
      address: '0xf3527ef8dE265eAa3716FB312c12847bFBA66Cef'
    - chain: binance
      address: '0xf3527ef8dE265eAa3716FB312c12847bFBA66Cef'
  coingecko_id: usdx-money-usdx
  usd_rate: 0.0076
  usd_rate_fetched_at: '2026-06-26T12:00:00'
  depegged_at: '2026-06-26T12:00:00'
  links:
    homepage: ''
    coingecko: ''
    defillama: ''
    twitter: ''
  checks:
    twitter_last_post_at: ''
    domain_up_at: ''
    marked_dead_at: ''
    information_found_missing_at: ''
- name: Kava USDX
  short_description: ''
  long_description: ''
  coingecko_id: kava-lend
  usd_rate: 0.63
  usd_rate_fetched_at: '2026-06-26T12:00:00'
  depegged_at: '2026-06-26T12:00:00'
  links:
    homepage: ''
    coingecko: ''
    defillama: ''
    twitter: ''
  checks:
    twitter_last_post_at: ''
    domain_up_at: ''
    marked_dead_at: ''
    information_found_missing_at: ''
slug: usdx
"""
    )

    # 2. The pinned address is indexed; the ambiguous USDX ticker is not.
    depegged_contracts, depegged_symbols = build_depegged_stablecoin_lookups(tmp_path)
    assert (1, dead_address) in depegged_contracts
    assert (56, dead_address) in depegged_contracts
    assert "USDX" not in depegged_symbols

    # 3. Dead token matches by contract; a same-ticker token elsewhere does not.
    feeder = StablecoinRateFeeder(tmp_path)
    assert feeder.is_depegged_stablecoin_token(1, dead_address, "USDX") is True
    other_address = "0xa1fa77779e6866fa3ef48fc0720657e042158387"  # Axis USD reuses the USDx ticker
    assert feeder.is_depegged_stablecoin_token(8453, other_address, "USDX") is False
    assert feeder.is_depegged_stablecoin_token(8453, None, "USDX") is False


def test_stablecoin_yaml_atomic_write_preserves_existing_file_mode(tmp_path: Path) -> None:
    """Atomic YAML updates keep existing package file permissions."""
    yaml_path = _write_usdc_yaml(tmp_path)
    yaml_path.chmod(0o644)

    stablecoin_rate._update_yaml_entry_fields(yaml_path, None, {"usd_rate": 1.0})

    assert yaml_path.stat().st_mode & 0o777 == 0o644
    target = next(iter_stablecoin_rate_targets(tmp_path))
    assert target.usd_rate == pytest.approx(1.0)


def test_ambiguous_symbol_matches_by_contract_only(tmp_path: Path) -> None:
    """``ambiguous_symbol`` entries blacklist by contract address only, never by the shared ticker.

    Tickers such as ``sUSD`` are reused by several unrelated tokens (Synthetix sUSD,
    YieldFi Stable Token, Solaris USD). Marking the Synthetix one depegged must not
    blacklist the healthy ticker-mates, so a flagged entry is matched by contract only.

    1. Write a single depegged ``sUSD`` entry flagged ``ambiguous_symbol`` with a pinned contract.
    2. Build the lookups and assert the contract is indexed but the ``SUSD`` symbol is not.
    3. Assert the pinned Synthetix contract matches while a same-ticker token at another address does not.
    """
    # 1. A flat, single-owner depegged sUSD entry that is flagged ambiguous.
    (tmp_path / "susd.yaml").write_text(
        """symbol: SUSD
name: Synthetix sUSD
short_description: ''
long_description: ''
category: stablecoin
contract_addresses:
  - chain: ethereum
    address: '0x57Ab1ec28D129707052df4dF418D58a2D46d5f51'
ambiguous_symbol: true
slug: susd
coingecko_id: nusd
usd_rate: 0.23
usd_rate_fetched_at: '2026-06-28T00:00:00'
depegged_at: '2026-06-28T00:00:00'
links:
  homepage: ''
  coingecko: ''
  defillama: ''
  twitter: ''
checks:
  twitter_last_post_at: ''
  domain_up_at: ''
  marked_dead_at: ''
  information_found_missing_at: ''
"""
    )

    # 2. Contract indexed, but the ambiguous SUSD ticker is deliberately excluded.
    depegged_contracts, depegged_symbols = build_depegged_stablecoin_lookups(tmp_path)
    assert (1, "0x57ab1ec28d129707052df4df418d58a2d46d5f51") in depegged_contracts
    assert "SUSD" not in depegged_symbols

    # 3. Pinned Synthetix contract matches; a same-ticker token elsewhere stays safe.
    feeder = StablecoinRateFeeder(tmp_path)
    assert feeder.is_depegged_stablecoin_token(1, "0x57Ab1ec28D129707052df4dF418D58a2D46d5f51", "sUSD") is True
    yieldfi_susd = "0x4f8e1426a9d10bddc11d26042ad270f16ccb95f2"  # YieldFi Stable Token reuses the sUSD ticker
    assert feeder.is_depegged_stablecoin_token(1, yieldfi_susd, "sUSD") is False
    assert feeder.is_depegged_stablecoin_token(1, None, "sUSD") is False


def test_non_evm_depegged_entry_suppresses_unactionable_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """``non_evm`` depegged entries are silent; ordinary unenforceable ones still warn.

    A depegged entry in a multi-entry (shared-ticker) file that carries no
    ``contract_addresses`` cannot blacklist anything. When the token only exists
    off EVM (e.g. Acala aUSD on Polkadot, Kava USDX on Cosmos), no EVM vault can
    ever be denominated in it, so the otherwise-emitted warning is pure noise and
    must be suppressed by the ``non_evm: true`` flag. An equally unenforceable
    entry *without* the flag must still warn, so the flag is the only difference.

    1. Write a multi-entry YAML with two depegged, contract-less entries — one
       flagged ``non_evm``, one not.
    2. Build the lookups while capturing logs and assert nothing is pinned.
    3. Assert the unflagged entry warns and the ``non_evm`` entry does not.
    """
    # 1. Two depegged entries sharing the ``XUSD`` ticker, neither pinned by address.
    (tmp_path / "xusd.yaml").write_text(
        """symbol: XUSD
category: stablecoin
entries:
- name: Cosmos XUSD
  short_description: ''
  long_description: ''
  non_evm: true
  coingecko_id: cosmos-xusd
  usd_rate: 0.2
  usd_rate_fetched_at: '2026-06-28T00:00:00'
  depegged_at: '2026-06-28T00:00:00'
  links:
    homepage: ''
    coingecko: ''
    defillama: ''
    twitter: ''
  checks:
    twitter_last_post_at: ''
    domain_up_at: ''
    marked_dead_at: ''
    information_found_missing_at: ''
- name: EVM XUSD
  short_description: ''
  long_description: ''
  coingecko_id: evm-xusd
  usd_rate: 0.3
  usd_rate_fetched_at: '2026-06-28T00:00:00'
  depegged_at: '2026-06-28T00:00:00'
  links:
    homepage: ''
    coingecko: ''
    defillama: ''
    twitter: ''
  checks:
    twitter_last_post_at: ''
    domain_up_at: ''
    marked_dead_at: ''
    information_found_missing_at: ''
slug: xusd
"""
    )

    # 2. Neither contract-less entry can be pinned by address or by the shared ticker.
    with caplog.at_level("WARNING", logger="eth_defi.feed.stablecoin_rate"):
        depegged_contracts, depegged_symbols = build_depegged_stablecoin_lookups(tmp_path)
    assert depegged_contracts == set()
    assert depegged_symbols == set()

    # 3. Only the unflagged EVM entry warns; the non_evm one is silent.
    warnings = [r.message for r in caplog.records if "cannot be blacklisted" in r.message]
    assert any("EVM XUSD" in m for m in warnings), warnings
    assert not any("Cosmos XUSD" in m for m in warnings), warnings


def test_packaged_non_evm_depegs_emit_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    """The packaged Acala aUSD and Kava USDX depegs are flagged ``non_evm`` and stay silent.

    These two tokens have no ERC-20 on any indexed chain, so no
    ``contract_addresses`` could be added — they are silenced via ``non_evm:
    true`` instead. Lock in that building the shipped data emits no
    unenforceable-depeg warning for the four symbols this fix covers, without
    coupling the assertion to unrelated future depegs.
    """
    with caplog.at_level("WARNING", logger="eth_defi.feed.stablecoin_rate"):
        build_depegged_stablecoin_lookups()
    noise = [r.message for r in caplog.records if "cannot be blacklisted" in r.message]
    # Scope to the symbols addressed here: AUSD/USDX silenced via non_evm, and
    # DUSD/USDN now pinned by contract — none of them may warn anymore.
    covered = [m for m in noise if any(sym in m for sym in ("AUSD", "dUSD", "USDN", "USDX"))]
    assert covered == [], covered


def test_packaged_duplicate_symbol_stablecoins_match_by_contract_only() -> None:
    """Guard the real packaged data: shared-ticker depegged stablecoins match by contract, never by symbol.

    ``USDX``, ``sUSD`` and ``USDR`` are each reused by several distinct tokens
    (some healthy, e.g. Axis USD, YieldFi sUSD). This regression test locks in the
    contract-only handling so a future edit to the packaged YAML cannot re-enable
    ticker matching and blacklist the healthy same-ticker tokens.

    1. Build the depeg lookups from the packaged ``eth_defi/data/stablecoins`` directory.
    2. Assert each shared ticker is absent from the symbol set (so it is never symbol-matched).
    3. Assert each known dead token is pinned by contract address instead.
    """
    # 1. Use the real shipped stablecoin metadata.
    depegged_contracts, depegged_symbols = build_depegged_stablecoin_lookups()

    # 2. The ambiguous shared tickers must not be symbol-matched.
    for ticker in ("USDX", "SUSD", "USDR"):
        assert ticker not in depegged_symbols, f"{ticker} is a shared ticker and must match by contract only"

    # 3. The dead tokens behind those tickers must be pinned by contract.
    assert (1, "0xf3527ef8de265eaa3716fb312c12847bfba66cef") in depegged_contracts  # Stables Labs USDX
    assert (1, "0x57ab1ec28d129707052df4df418d58a2d46d5f51") in depegged_contracts  # Synthetix sUSD
    assert (137, "0x40379a439d4f6795b6fc9aa5687db461677a2dba") in depegged_contracts  # Tangible Real USD
    assert (1, "0x7b43e3875440b44613dc3bc08e7763e6da63c8f8") in depegged_contracts  # StablR USD
    assert (1, "0x5bc25f649fc4e26069ddf4cf4010f9f706c23831") in depegged_contracts  # DefiDollar DUSD
    assert (1, "0x674c6ad92fd080e4004b2312b45f796a192d27a0") in depegged_contracts  # Neutrino USD (USDN)
