"""Tests for the Rysk Premium DeFi option-vault adapter."""

from decimal import Decimal
from pathlib import Path

import duckdb
from web3 import Web3

from eth_defi.erc_4626.classification import _get_hardcoded_protocol_features  # noqa: PLC2701
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.rysk.api import RyskPremiumSnapshot, fetch_rysk_premium_pools, fetch_rysk_premium_snapshots
from eth_defi.erc_4626.vault_protocol.rysk.constants import RYSK_PREMIUM_POOLS, is_rysk_premium_test_pool
from eth_defi.erc_4626.vault_protocol.rysk.historical_context import RyskHistoricalContextStore
from eth_defi.erc_4626.vault_protocol.rysk.vault import RyskVault
from eth_defi.tokenised_fund.vault import TokenisedFundVault
from eth_defi.vault.base import VaultBase, VaultSpec
from eth_defi.vault.flag import VaultFlag


def test_rysk_catalogue_classification_is_chain_aware() -> None:
    """Classify a known Rysk product only on its configured chain.

    The same address on an unrelated network must not inherit Rysk features.

    :return:
        None.
    """

    chain_id, address = next(iter(RYSK_PREMIUM_POOLS))

    assert _get_hardcoded_protocol_features(address, chain_id=chain_id) == {
        ERC4626Feature.rysk_premium_like,
        ERC4626Feature.share_price_equivalence,
    }
    assert _get_hardcoded_protocol_features(address, chain_id=42161) is None


def test_rysk_adapter_is_not_a_tokenised_fund() -> None:
    """Keep Rysk under the generic vault protocol surface and off the fund flag.

    Rysk Premium is a DeFi options pool without a legal fund structure.

    :return:
        None.
    """

    chain_id, address = next(iter(RYSK_PREMIUM_POOLS))
    vault = RyskVault(Web3(), VaultSpec(chain_id=chain_id, vault_address=address))

    assert isinstance(vault, VaultBase)
    assert not isinstance(vault, TokenisedFundVault)
    assert VaultFlag.tokenised_fund not in vault.get_flags()


def test_rysk_history_uses_greatest_final_epoch_source_block(tmp_path: Path) -> None:
    """Use final withdrawal PPS from the greatest source block in each epoch.

    Non-final actions are ignored and a later source block deterministically
    supersedes an earlier record for the same epoch.

    :param tmp_path:
        Temporary directory for the isolated DuckDB context database.
    :return:
        None.
    """

    chain_id, address = next(iter(RYSK_PREMIUM_POOLS))
    snapshots = (
        RyskPremiumSnapshot(chain_id, address, 100, 1_700_000_000, "0x" + "1" * 64, "EPOCH", 1, 1_000_000, 1_010_000, None),
        RyskPremiumSnapshot(chain_id, address, 110, 1_700_000_100, "0x" + "2" * 64, "EPOCH", 1, 1_005_000, 1_020_000, None),
        RyskPremiumSnapshot(chain_id, address, 120, 1_700_000_200, "0x" + "3" * 64, "DEPOSIT", 2, 1_030_000, 1_020_000, None),
    )

    with RyskHistoricalContextStore(tmp_path / "history.duckdb") as store:
        assert store.insert_snapshots(snapshots) == (3, 3)
        observations = tuple(
            store.iter_finalised_share_prices(
                chain_id=chain_id,
                pool_address=address,
                start_block=0,
                end_block=1_000,
                collateral_decimals=6,
            )
        )

    assert len(observations) == 1
    expected_latest_correction_block = 110
    assert observations[0].block_number == expected_latest_correction_block
    assert observations[0].withdrawal_share_price == Decimal("1.02")


def test_rysk_history_accepts_large_reported_tvl(tmp_path: Path) -> None:
    """Preserve 128-bit source TVLs published by the Premium API.

    Some raw collateral quantities exceed DuckDB's unsigned 64-bit range.

    :param tmp_path:
        Temporary directory for the isolated DuckDB context database.
    :return:
        None.
    """

    chain_id, address = next(iter(RYSK_PREMIUM_POOLS))
    large_raw_tvl = 10**21
    snapshot = RyskPremiumSnapshot(chain_id, address, 100, 1_700_000_000, "0x" + "4" * 64, "EPOCH", 1, 1_000_000, 1_010_000, large_raw_tvl)

    with RyskHistoricalContextStore(tmp_path / "history.duckdb") as store:
        assert store.insert_snapshot(snapshot)
        stored_tvl = store.connection.execute("SELECT raw_tvl FROM rysk_premium_historical_context").fetchone()[0]

    assert stored_tvl == large_raw_tvl


def test_rysk_history_migrates_legacy_tvl_column(tmp_path: Path) -> None:
    """Widen early 64-bit TVL source tables without dropping their rows.

    Opening the context store must preserve legacy values while changing only
    the column's representable integer range.

    :param tmp_path:
        Temporary directory for the isolated DuckDB context database.
    :return:
        None.
    """

    path = tmp_path / "history.duckdb"
    legacy_raw_tvl = 10
    connection = duckdb.connect(str(path))
    try:
        connection.execute(
            """
            CREATE TABLE rysk_premium_historical_context (
                chain_id UINTEGER NOT NULL,
                pool_address VARCHAR NOT NULL,
                block_number UBIGINT NOT NULL,
                block_timestamp UBIGINT NOT NULL,
                transaction_hash VARCHAR NOT NULL,
                action VARCHAR NOT NULL,
                epoch UBIGINT NOT NULL,
                raw_deposit_pps UBIGINT NOT NULL,
                raw_withdrawal_pps UBIGINT NOT NULL,
                raw_tvl UBIGINT,
                source_id VARCHAR NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO rysk_premium_historical_context VALUES (999, '0x377476409d8eb5eac7197cdb906773ce4f4edcf4', 100, 1_700_000_000, '0x1111111111111111111111111111111111111111111111111111111111111111', 'EPOCH', 1, 1000000, 1000000, ?, 'legacy')",
            [legacy_raw_tvl],
        )
    finally:
        connection.close()

    with RyskHistoricalContextStore(path) as store:
        (data_type,) = store.connection.execute("SELECT data_type FROM information_schema.columns WHERE table_name = 'rysk_premium_historical_context' AND column_name = 'raw_tvl'").fetchone()
        (raw_tvl,) = store.connection.execute("SELECT raw_tvl FROM rysk_premium_historical_context WHERE source_id = 'legacy'").fetchone()

    assert data_type == "HUGEINT"
    assert raw_tvl == legacy_raw_tvl


def test_rysk_history_scales_pps_by_collateral_precision(tmp_path: Path) -> None:
    """Scale kHYPE PPS with its 18 native token decimals, not six.

    The contextual store accepts collateral precision explicitly so non-USDC
    products retain their correct share-price equivalent.

    :param tmp_path:
        Temporary directory for the isolated DuckDB context database.
    :return:
        None.
    """

    chain_id, address = next(iter(RYSK_PREMIUM_POOLS))
    snapshot = RyskPremiumSnapshot(chain_id, address, 100, 1_700_000_000, "0x" + "5" * 64, "EPOCH", 1, 10**18, 10**18, None)

    with RyskHistoricalContextStore(tmp_path / "history.duckdb") as store:
        assert store.insert_snapshot(snapshot)
        observations = tuple(
            store.iter_finalised_share_prices(
                chain_id=chain_id,
                pool_address=address,
                start_block=0,
                end_block=1_000,
                collateral_decimals=18,
            )
        )

    assert observations[0].withdrawal_share_price == Decimal(1)


def test_rysk_public_catalogue_and_snapshot_api() -> None:
    """Exercise the unauthenticated Rysk catalogue and one live snapshot feed.

    This real integration check validates the current application response
    shape without assuming which user-facing pool already has epoch history.

    :return:
        None.
    """

    pools = fetch_rysk_premium_pools()
    assert pools
    assert {pool.option_type for pool in pools} >= {"put", "call"}

    for pool in pools:
        if is_rysk_premium_test_pool(pool):
            continue
        snapshots = tuple(fetch_rysk_premium_snapshots(pool))
        if snapshots:
            assert all(snapshot.pool == pool.address for snapshot in snapshots)
            break
    else:
        message = "No current Rysk Premium pool has public snapshots"
        raise AssertionError(message)
