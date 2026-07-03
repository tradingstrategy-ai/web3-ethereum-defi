"""Vault metrics integration tests for stablecoin depeg data."""

import datetime
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from eth_defi.erc_4626.core import ERC4262VaultDetection
from eth_defi.feed.stablecoin_rate import StablecoinRateFeeder, iter_stablecoin_rate_targets, refresh_stablecoin_rates
from eth_defi.research.vault_metrics import calculate_lifetime_metrics, export_lifetime_row
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.risk import VaultTechnicalRisk

VAULT_ADDRESS = "0x2222222222222222222222222222222222222222"
USDC_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
USDX_ADDRESS = "0x1111111111111111111111111111111111111111"
EURE_ADDRESS = "0x3333333333333333333333333333333333333333"


def _write_depegged_usdx_yaml(data_dir: Path) -> None:
    """Create a USDX stablecoin fixture already marked as depegged."""
    (data_dir / "usdx.yaml").write_text(
        f"""symbol: USDX
name: Kava USDX
short_description: Kava USDX
long_description: ''
category: stablecoin
source_currency: usd
source_currency_source: manual
coingecko_id: usdx
coingecko_link: https://www.coingecko.com/en/coins/usdx
coingecko_id_source: manual
coingecko_id_verified_at: '2026-06-26T12:00:00'
usd_rate: 0.646809
usd_rate_fetched_at: '2026-06-26T12:00:00'
usd_rate_updated_at: '2026-06-26T08:56:08'
peg_rate: 0.646809
peg_rate_currency: usd
source_currency_usd_rate: 1.0
source_currency_usd_rate_fetched_at: '2026-06-26T12:00:00'
source_currency_usd_rate_source: fawazahmed0
rate_fetch_failed_at: ''
rate_fetch_failed_reason: ''
depegged_at: '2026-06-26T12:00:00'
links:
  homepage: https://www.kava.io/
  coingecko: https://www.coingecko.com/en/coins/usdx
  defillama: ''
  twitter: https://x.com/KAVA_CHAIN
slug: usdx
contract_addresses:
  - chain: ethereum
    address: '{USDX_ADDRESS}'
checks:
  twitter_last_post_at: ''
  domain_up_at: ''
  marked_dead_at: ''
  information_found_missing_at: ''
"""
    )


def _write_depegged_eure_yaml(data_dir: Path) -> None:
    """Create a EUR stablecoin fixture marked as depegged in its native currency."""
    (data_dir / "eure.yaml").write_text(
        f"""symbol: EURe
name: Monerium EUR emoney
short_description: Monerium EUR emoney
long_description: ''
category: stablecoin
source_currency: eur
source_currency_source: manual
coingecko_id: monerium-eur-money
coingecko_link: https://www.coingecko.com/en/coins/monerium-eur-money
coingecko_id_source: manual
coingecko_id_verified_at: '2026-06-26T12:00:00'
usd_rate: 1.0
usd_rate_fetched_at: '2026-06-26T12:00:00'
usd_rate_updated_at: '2026-06-26T08:56:08'
peg_rate: 0.87934359
peg_rate_currency: eur
source_currency_usd_rate: 1.137211905985
source_currency_usd_rate_fetched_at: '2026-06-26T12:00:00'
source_currency_usd_rate_source: fawazahmed0
rate_fetch_failed_at: ''
rate_fetch_failed_reason: ''
depegged_at: '2026-06-26T12:00:00'
links:
  homepage: https://monerium.com/
  coingecko: https://www.coingecko.com/en/coins/monerium-eur-money
  defillama: ''
  twitter: https://x.com/monerium
slug: eure
contract_addresses:
  - chain: ethereum
    address: '{EURE_ADDRESS}'
checks:
  twitter_last_post_at: ''
  domain_up_at: ''
  marked_dead_at: ''
  information_found_missing_at: ''
"""
    )


def _write_live_usdc_yaml(data_dir: Path) -> None:
    """Create a USDC fixture whose rate is fetched from live CoinGecko."""
    (data_dir / "usdc.yaml").write_text(
        f"""symbol: USDC
name: Circle USDC
short_description: USD Coin
long_description: ''
category: stablecoin
source_currency: usd
source_currency_source: manual
coingecko_id: usd-coin
coingecko_link: https://www.coingecko.com/en/coins/usd-coin
coingecko_id_source: manual
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


def _write_live_usdx_yaml(data_dir: Path) -> None:
    """Create a Kava USDX fixture whose rate is fetched from live CoinGecko."""
    (data_dir / "usdx.yaml").write_text(
        f"""symbol: USDX
name: Kava USDX
short_description: Kava USDX
long_description: ''
category: stablecoin
source_currency: usd
source_currency_source: manual
coingecko_id: usdx
coingecko_link: https://www.coingecko.com/en/coins/usdx
coingecko_id_source: manual
links:
  homepage: https://www.kava.io/
  coingecko: https://www.coingecko.com/en/coins/usdx
  defillama: ''
  twitter: https://x.com/KAVA_CHAIN
slug: usdx
contract_addresses:
  - chain: ethereum
    address: '{USDX_ADDRESS}'
checks:
  twitter_last_post_at: ''
  domain_up_at: ''
  marked_dead_at: ''
  information_found_missing_at: ''
"""
    )


def _calculate_bad_stablecoin_vault_metrics(data_dir: Path, token_symbol: str = "USDX", token_address: str = USDX_ADDRESS) -> pd.DataFrame:
    """Calculate lifetime metrics for a vault that uses a bad stablecoin denomination."""
    chain_id = 1
    vault_id = f"{chain_id}-{VAULT_ADDRESS}"
    spec = VaultSpec(chain_id=chain_id, vault_address=VAULT_ADDRESS)
    detection = ERC4262VaultDetection(
        chain=chain_id,
        address=VAULT_ADDRESS,
        first_seen_at_block=1,
        first_seen_at=datetime.datetime(2026, 1, 1),
        features=set(),
        updated_at=datetime.datetime(2026, 1, 1),
        deposit_count=10,
        redeem_count=10,
    )
    fee_data = FeeData(
        fee_mode=VaultFeeMode.externalised,
        management=0.0,
        performance=0.1,
        deposit=0.0,
        withdraw=0.0,
    )
    vault_row = {
        "Symbol": f"BAD{token_symbol.upper()}",
        "Name": f"Bad {token_symbol} Vault",
        "Address": VAULT_ADDRESS,
        "Denomination": token_symbol,
        "Share token": f"BAD{token_symbol.upper()}",
        "NAV": Decimal("1000"),
        "Shares": Decimal("1000"),
        "Protocol": "Example protocol",
        "Link": "https://example.com/vault",
        "First seen": datetime.datetime(2026, 1, 1),
        "Mgmt fee": 0.0,
        "Perf fee": 0.1,
        "Deposit fee": 0.0,
        "Withdraw fee": 0.0,
        "Features": "",
        "_detection_data": detection,
        "_denomination_token": {"address": token_address, "symbol": token_symbol, "decimals": 6},
        "_share_token": {"address": VAULT_ADDRESS, "symbol": f"BAD{token_symbol.upper()}", "decimals": 18},
        "_fees": fee_data,
        "_flags": set(),
        "_lockup": None,
        "_description": None,
        "_short_description": None,
        "_available_liquidity": None,
        "_utilisation": None,
        "_deposit_closed_reason": None,
        "_deposit_next_open": None,
        "_redemption_closed_reason": None,
        "_redemption_next_open": None,
        "_risk": VaultTechnicalRisk.negligible,
        "_manual_review_status": None,
    }

    index = pd.date_range("2026-01-01", periods=24 * 31, freq="1h")
    prices_df = pd.DataFrame(
        {
            "id": vault_id,
            "total_assets": 1_000.0,
            "share_price": 1.0,
            "event_count": 20,
            "chain": chain_id,
            "block_number": range(len(index)),
        },
        index=index,
    )

    metrics = calculate_lifetime_metrics(
        prices_df,
        {spec: vault_row},
        stablecoin_rate_feeder=StablecoinRateFeeder(data_dir=data_dir),
    )
    return metrics


def _assert_bad_stablecoin_vault_blacklisted(
    metrics: pd.DataFrame,
    expected_symbol: str,
    expected_usd_rate: float,
    expected_fetched_at: datetime.datetime,
    expected_source_currency: str = "usd",
    expected_native_rate: float | None = None,
    expected_native_rate_currency: str | None = None,
    expected_source_currency_usd_rate: float = 1.0,
) -> None:
    """Assert depegged denomination stablecoins make the vault blacklisted and export rate data."""
    assert len(metrics) == 1
    row = metrics.iloc[0]
    assert row["risk"] == VaultTechnicalRisk.blacklisted
    assert row["risk_numeric"] == VaultTechnicalRisk.blacklisted.value
    assert VaultFlag.depegged_denomination_token in row["flags"]
    assert f"Denomination stablecoin {expected_symbol.upper()} is marked as depegged" in row["notes"]

    denomination_token_rate = row["denomination_token_rate"]
    assert denomination_token_rate.source_currency == expected_source_currency
    assert denomination_token_rate.usd_rate == pytest.approx(expected_usd_rate)
    assert denomination_token_rate.usd_rate_fetched_at == expected_fetched_at
    assert denomination_token_rate.usd_rate_source == "coingecko"
    assert denomination_token_rate.native_rate == (pytest.approx(expected_native_rate) if expected_native_rate is not None else None)
    assert denomination_token_rate.native_rate_currency == expected_native_rate_currency
    assert denomination_token_rate.native_rate_fetched_at == (expected_fetched_at if expected_native_rate is not None else None)
    expected_native_rate_source = "coingecko+fawazahmed0" if expected_native_rate is not None else None
    assert denomination_token_rate.native_rate_source == expected_native_rate_source
    assert denomination_token_rate.source_currency_usd_rate == pytest.approx(expected_source_currency_usd_rate)
    assert denomination_token_rate.source_currency_usd_rate_fetched_at == expected_fetched_at
    assert denomination_token_rate.source_currency_usd_rate_source == "fawazahmed0"

    exported = export_lifetime_row(row)
    assert exported["risk"] == "Blacklisted"
    expected_exported_rate = {
        "coingecko_id": denomination_token_rate.coingecko_id,
        "source_currency": expected_source_currency,
        "usd_rate": expected_usd_rate,
        "usd_rate_fetched_at": expected_fetched_at.isoformat(),
        "usd_rate_source": "coingecko",
        "native_rate": expected_native_rate,
        "native_rate_currency": expected_native_rate_currency,
        "native_rate_fetched_at": expected_fetched_at.isoformat() if expected_native_rate is not None else None,
        "native_rate_source": expected_native_rate_source,
        "source_currency_usd_rate": expected_source_currency_usd_rate,
        "source_currency_usd_rate_fetched_at": expected_fetched_at.isoformat(),
        "source_currency_usd_rate_source": "fawazahmed0",
    }
    assert exported["denomination_token_rate"] == expected_exported_rate
    assert "depegged_denomination_token" in exported["flags"]


@pytest.mark.parametrize(
    ("writer", "symbol", "address", "expected_usd_rate", "expected_source_currency", "expected_native_rate", "expected_native_rate_currency", "expected_source_currency_usd_rate"),
    [
        (_write_depegged_usdx_yaml, "USDX", USDX_ADDRESS, 0.646809, "usd", None, None, 1.0),
        (_write_depegged_eure_yaml, "EURe", EURE_ADDRESS, 1.0, "eur", 0.87934359, "eur", 1.137211905985),
    ],
)
def test_calculate_lifetime_metrics_blacklists_depegged_stablecoin_denomination(
    tmp_path: Path,
    writer: Callable[[Path], None],
    symbol: str,
    address: str,
    expected_usd_rate: float,
    expected_source_currency: str,
    expected_native_rate: float | None,
    expected_native_rate_currency: str | None,
    expected_source_currency_usd_rate: float,
) -> None:
    """A vault denominated in a depegged stablecoin is blacklisted in lifetime metrics."""
    writer(tmp_path)

    metrics = _calculate_bad_stablecoin_vault_metrics(tmp_path, token_symbol=symbol, token_address=address)

    _assert_bad_stablecoin_vault_blacklisted(
        metrics,
        expected_symbol=symbol,
        expected_usd_rate=expected_usd_rate,
        expected_fetched_at=datetime.datetime(2026, 6, 26, 12, 0, 0),
        expected_source_currency=expected_source_currency,
        expected_native_rate=expected_native_rate,
        expected_native_rate_currency=expected_native_rate_currency,
        expected_source_currency_usd_rate=expected_source_currency_usd_rate,
    )


@pytest.mark.live
def test_live_coingecko_refresh_blacklists_usdx_denomination_and_keeps_usdc_healthy(tmp_path: Path) -> None:
    """Fetch real CoinGecko rates and blacklist a USDX-denominated vault."""
    _write_live_usdc_yaml(tmp_path)
    _write_live_usdx_yaml(tmp_path)
    now_ = datetime.datetime(2026, 6, 26, 12, 0, 0)

    summary = refresh_stablecoin_rates(data_dir=tmp_path, now_=now_, force=True, timeout=20.0)
    targets = {target.symbol: target for target in iter_stablecoin_rate_targets(tmp_path)}
    failure_reasons = {target.rate_fetch_failed_reason for target in targets.values() if target.rate_fetch_failed_reason}
    if summary.rates_fetched == 0 and failure_reasons == {"coingecko_http_error"}:
        pytest.skip("CoinGecko live API returned an HTTP error during this run")

    assert summary.entries_seen == 2
    assert summary.rates_fetched == 2
    assert summary.failed_count == 0
    assert summary.depegged_count == 1

    usdc_target = targets["USDC"]
    usdx_target = targets["USDX"]
    assert usdc_target.usd_rate == pytest.approx(1.0, abs=0.02)
    assert usdc_target.depegged_at is None
    assert usdx_target.usd_rate is not None
    assert usdx_target.usd_rate < 0.90
    assert usdx_target.depegged_at == now_

    feeder = StablecoinRateFeeder(data_dir=tmp_path)
    assert feeder.is_depegged_stablecoin_token(1, USDC_ADDRESS, "USDC") is False
    assert feeder.is_depegged_stablecoin_token(1, USDX_ADDRESS, "USDX") is True

    metrics = _calculate_bad_stablecoin_vault_metrics(tmp_path)

    _assert_bad_stablecoin_vault_blacklisted(metrics, expected_symbol="USDX", expected_usd_rate=usdx_target.usd_rate, expected_fetched_at=now_)
