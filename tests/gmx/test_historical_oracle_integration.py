"""Fixed-block integration test for the GMX vault price pipeline."""

import os
import pickle  # noqa: S403 - trusted local scanner-state fixture
from pathlib import Path

import pandas as pd
import pytest
from eth_utils import to_checksum_address

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.hypersync import session as hypersync_session
from eth_defi.hypersync import utils as hypersync_utils
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.research.vault_metrics import calculate_hourly_returns_for_all_vaults, calculate_lifetime_metrics
from eth_defi.token import TokenDiskCache
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.scan_all_chains import scan_prices_for_chain
from eth_defi.vault.vaultdb import VaultDatabase

pytestmark = pytest.mark.skipif(not os.environ.get("JSON_RPC_ARBITRUM"), reason="requires JSON_RPC_ARBITRUM")


#: Fixed block containing a value-and-supply event for the selected
#: GM market.
GMX_VALUE_EVENT_BLOCK = 305_647_934
GM_MARKET = to_checksum_address("0x0c11Ed89889Fd03394E8d9d685cC5b85be569C99")
EXPECTED_SHARE_PRICE = 1.0000741432025045


def test_gmx_context_to_common_parquet_and_lifetime_metrics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: PLR0914
    """Run one GM observation through the chain pipeline and lifetime metrics."""

    # Isolate the integration test from the process-shared production throttle.
    create_limiter = hypersync_session._create_limiter
    monkeypatch.setattr(hypersync_utils, "_create_limiter", lambda requests_per_minute: create_limiter(requests_per_minute, tmp_path / "hypersync-rate-limit.sqlite"))
    rpc_url = os.environ["JSON_RPC_ARBITRUM"]
    web3 = create_multi_provider_web3(rpc_url)
    context_path = tmp_path / "vault-historical-context.duckdb"
    token_cache = TokenDiskCache(tmp_path / "tokens.sqlite")
    spec = VaultSpec(42161, GM_MARKET)
    raw_path = tmp_path / "vault-prices-1h.parquet"
    detection = ERC4262VaultDetection(
        chain=42161,
        address=GM_MARKET,
        first_seen_at_block=GMX_VALUE_EVENT_BLOCK,
        first_seen_at=pd.Timestamp("2026-08-21").to_pydatetime(),
        features={ERC4626Feature.gmx_gm},
        updated_at=pd.Timestamp("2026-08-21").to_pydatetime(),
        deposit_count=0,
        redeem_count=0,
    )
    vault_row = create_vault_scan_record(web3, detection, GMX_VALUE_EVENT_BLOCK, token_cache)
    vault_db = VaultDatabase(rows={spec: vault_row})
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    vault_db.write(vault_db_path)
    reader_state_path = tmp_path / "reader-state.pickle"
    unrelated_spec = VaultSpec(42161, "0x5000000000000000000000000000000000000001")
    with reader_state_path.open("wb") as out:
        pickle.dump({unrelated_spec: {"last_block": GMX_VALUE_EVENT_BLOCK}}, out)

    success, scan_result = scan_prices_for_chain(
        rpc_url=rpc_url,
        max_workers=1,
        frequency="1h",
        vault_db_path=vault_db_path,
        uncleaned_price_path=raw_path,
        reader_state_path=reader_state_path,
        historical_context_path=context_path,
        start_block=GMX_VALUE_EVENT_BLOCK,
        end_block=GMX_VALUE_EVENT_BLOCK + 1,
        vault_addresses={GM_MARKET.lower()},
        persist_reader_state=True,
    )
    assert success, scan_result
    assert scan_result["gmx_observations_inserted"] == 1
    assert scan_result["rows_written"] == 1
    with reader_state_path.open("rb") as inp:
        assert unrelated_spec in pickle.load(inp)  # noqa: S301 - trusted test-created state

    prices = pd.read_parquet(raw_path).set_index("timestamp")
    assert prices.iloc[0]["share_price"] == pytest.approx(EXPECTED_SHARE_PRICE)
    expected_timestamp = pd.Timestamp(web3.eth.get_block(GMX_VALUE_EVENT_BLOCK)["timestamp"], unit="s")
    assert prices.index[0] == expected_timestamp
    prices["id"] = f"42161-{GM_MARKET.lower()}"
    prices["event_count"] = 0
    returns = calculate_hourly_returns_for_all_vaults(prices)
    lifetime = calculate_lifetime_metrics(returns, vault_db)

    assert len(lifetime) == 1
    assert lifetime.iloc[0]["protocol"] == "GMX"
    assert lifetime.iloc[0]["current_nav"] == pytest.approx(prices.iloc[0]["total_assets"])
