"""Deterministic Flying Tulip reward-equivalence replay tests."""

from decimal import Decimal
from pathlib import Path

import pytest
from eth_utils import to_checksum_address

from eth_defi.erc_4626.vault_protocol.flying_tulip.constants import FLYING_TULIP_CURVE_CANONICAL_START_TIMESTAMP, FLYING_TULIP_FT_FTUSD_CURVE_POOL, FLYING_TULIP_RATE_RAY
from eth_defi.erc_4626.vault_protocol.flying_tulip.historical_context import ZERO_ADDRESS_TOPIC, EpochSettlement, FlyingTulipHistoricalContextStore, RewardPriceObservation, SupplyChange, is_mint_transfer

CHAIN_ID = 1
VAULT_ADDRESS = to_checksum_address("0xeb48218a4c35c814c7678cbcae88c6ee037f7625")


def _epoch(epoch_id: int, block_number: int, timestamp: int, raw_reward: int, raw_stake_time: int, *, rate_ray: int | None = None) -> EpochSettlement:
    """Create a minimal valid source epoch for replay coverage."""

    return EpochSettlement(
        chain_id=CHAIN_ID,
        vault_address=VAULT_ADDRESS,
        epoch_id=epoch_id,
        block_number=block_number,
        block_timestamp=FLYING_TULIP_CURVE_CANONICAL_START_TIMESTAMP + timestamp,
        transaction_hash=f"0x{epoch_id:064x}",
        log_index=1,
        raw_reward_amount=raw_reward,
        raw_stake_time=raw_stake_time,
        raw_rate_ray=raw_reward * FLYING_TULIP_RATE_RAY // raw_stake_time if rate_ray is None else rate_ray,
    )


def _mint(block_number: int, raw_amount: int) -> SupplyChange:
    """Create a source mint before an epoch in the same or later block."""

    return SupplyChange(
        chain_id=CHAIN_ID,
        vault_address=VAULT_ADDRESS,
        block_number=block_number,
        block_timestamp=block_number * 10,
        transaction_hash=f"0x{block_number + 1_000:064x}",
        log_index=0,
        is_mint=True,
        raw_amount=raw_amount,
    )


def _burn(block_number: int, raw_amount: int) -> SupplyChange:
    """Create a source burn during a tracked epoch."""

    return SupplyChange(
        chain_id=CHAIN_ID,
        vault_address=VAULT_ADDRESS,
        block_number=block_number,
        block_timestamp=block_number * 10,
        transaction_hash=f"0x{block_number + 2_000:064x}",
        log_index=0,
        is_mint=False,
        raw_amount=raw_amount,
    )


def _price(timestamp: int, raw_price: int, *, oracle_updated_at: int | None = None) -> RewardPriceObservation:
    """Create a deterministic FT/ftUSD price join."""

    return RewardPriceObservation(
        ethereum_block_number=25_822_053,
        ethereum_block_timestamp=FLYING_TULIP_CURVE_CANONICAL_START_TIMESTAMP + timestamp,
        pool_address=FLYING_TULIP_FT_FTUSD_CURVE_POOL,
        raw_oracle=10**36 // raw_price,
        oracle_updated_at=FLYING_TULIP_CURVE_CANONICAL_START_TIMESTAMP + (oracle_updated_at if oracle_updated_at is not None else timestamp),
        raw_ft_price_in_ftusd=raw_price,
    )


def test_zero_address_transfer_classification() -> None:
    """Treat zero-address senders as mints and recipients as burns."""

    account_topic = f"0x{'1' * 64}"

    assert is_mint_transfer(ZERO_ADDRESS_TOPIC, account_topic)
    assert not is_mint_transfer(account_topic, ZERO_ADDRESS_TOPIC)
    with pytest.raises(ValueError, match="does not have a zero-address endpoint"):
        is_mint_transfer(account_topic, f"0x{'2' * 64}")


def test_source_coverage_advances_without_events(tmp_path: Path) -> None:
    """Persist an empty completed range so dormant vault scans resume at its end."""

    with FlyingTulipHistoricalContextStore(tmp_path / "context.duckdb") as store:
        assert store.fetch_next_source_block(CHAIN_ID, VAULT_ADDRESS) is None
        store.set_source_scan_end_block(CHAIN_ID, VAULT_ADDRESS, 123)
        store.set_source_scan_end_block(CHAIN_ID, VAULT_ADDRESS, 100)

        assert store.fetch_next_source_block(CHAIN_ID, VAULT_ADDRESS) == 123


def test_replay_compounds_external_reward_equivalence(tmp_path: Path) -> None:
    """Skip the unverifiable first epoch and compound the first valid reward."""

    with FlyingTulipHistoricalContextStore(tmp_path / "context.duckdb") as store:
        store.insert_supply_change(_mint(10, 100 * 10**6))
        store.insert_epoch(_epoch(1, 20, 100, 1 * 10**18, 100 * 100 * 10**6))
        store.insert_epoch(_epoch(2, 30, 200, 10 * 10**18, 100 * 100 * 10**6))
        store.insert_reward_price(_price(200, 2 * 10**18))

        observations = list(store.iter_share_price_observations(CHAIN_ID, VAULT_ADDRESS, 6, 18, 1, 100, 1))

    assert len(observations) == 2
    baseline, observation = observations
    assert baseline.epoch_id == 1
    assert baseline.share_price == Decimal(1)
    assert baseline.total_assets == Decimal(100)
    assert observation.epoch_id == 2
    assert observation.total_supply == Decimal(100)
    assert observation.share_price == Decimal("1.2")
    assert observation.total_assets == Decimal(120)


def test_replay_rebases_after_curve_becomes_canonical(tmp_path: Path) -> None:
    """Exclude pre-Curve rewards and initialise the supported suffix at 1.0."""

    with FlyingTulipHistoricalContextStore(tmp_path / "context.duckdb") as store:
        store.insert_supply_change(_mint(10, 100 * 10**6))
        store.insert_epoch(_epoch(1, 20, -100, 50 * 10**18, 100 * 100 * 10**6))
        store.insert_epoch(_epoch(2, 30, 100, 50 * 10**18, 200 * 100 * 10**6))
        store.insert_epoch(_epoch(3, 40, 200, 10 * 10**18, 100 * 100 * 10**6))
        store.insert_reward_price(_price(200, 2 * 10**18))

        observations = list(store.iter_share_price_observations(CHAIN_ID, VAULT_ADDRESS, 6, 18, 1, 100, 1))

    assert [observation.epoch_id for observation in observations] == [2, 3]
    assert observations[0].share_price == Decimal(1)
    assert observations[1].share_price == Decimal("1.2")


def test_replay_validates_average_stake_against_epoch_maximum_supply(tmp_path: Path) -> None:
    """Allow a valid average stake above ending supply after an epoch burn."""

    with FlyingTulipHistoricalContextStore(tmp_path / "context.duckdb") as store:
        store.insert_supply_change(_mint(10, 100 * 10**6))
        store.insert_supply_change(_burn(25, 50 * 10**6))
        store.insert_epoch(_epoch(1, 20, 100, 0, 0, rate_ray=0))
        store.insert_epoch(_epoch(2, 30, 200, 10 * 10**18, 100 * 100 * 10**6))
        store.insert_reward_price(_price(200, 2 * 10**18))

        observations = list(store.iter_share_price_observations(CHAIN_ID, VAULT_ADDRESS, 6, 18, 1, 100, 1))

    assert observations[-1].total_supply == Decimal(50)
    assert observations[-1].share_price == Decimal("1.2")
    assert observations[-1].total_assets == Decimal(60)


def test_replay_stops_at_missing_reward_price(tmp_path: Path) -> None:
    """Do not bridge a historical price gap by treating it as a zero reward."""

    with FlyingTulipHistoricalContextStore(tmp_path / "context.duckdb") as store:
        store.insert_supply_change(_mint(10, 100 * 10**6))
        store.insert_epoch(_epoch(1, 20, 100, 1 * 10**18, 100 * 100 * 10**6))
        store.insert_epoch(_epoch(2, 30, 200, 10 * 10**18, 100 * 100 * 10**6))

        observations = list(store.iter_share_price_observations(CHAIN_ID, VAULT_ADDRESS, 6, 18, 1, 100, 1))

    assert len(observations) == 1
    assert observations[0].epoch_id == 1
    assert observations[0].share_price == Decimal(1)


def test_replay_stops_at_stale_reward_price(tmp_path: Path) -> None:
    """Do not bridge a Curve-oracle freshness gap with an old cached price."""

    with FlyingTulipHistoricalContextStore(tmp_path / "context.duckdb") as store:
        store.insert_supply_change(_mint(10, 100 * 10**6))
        store.insert_epoch(_epoch(1, 20, 100, 1 * 10**18, 100 * 100 * 10**6))
        store.insert_epoch(_epoch(2, 30, 8 * 24 * 60 * 60, 10 * 10**18, (8 * 24 * 60 * 60 - 100) * 100 * 10**6))
        store.insert_reward_price(_price(100, 2 * 10**18))

        observations = list(store.iter_share_price_observations(CHAIN_ID, VAULT_ADDRESS, 6, 18, 1, 100, 1))

    assert len(observations) == 1
    assert observations[0].epoch_id == 1


def test_replay_rejects_invalid_rate_ray(tmp_path: Path) -> None:
    """Validate the onchain reward-rate identity before emitting a row."""

    with FlyingTulipHistoricalContextStore(tmp_path / "context.duckdb") as store:
        store.insert_supply_change(_mint(10, 100 * 10**6))
        store.insert_epoch(_epoch(1, 20, 100, 1 * 10**18, 100 * 100 * 10**6))
        store.insert_epoch(_epoch(2, 30, 200, 10 * 10**18, 100 * 100 * 10**6, rate_ray=0))
        store.insert_reward_price(_price(200, 2 * 10**18))

        with pytest.raises(ValueError, match="rateRay identity"):
            list(store.iter_share_price_observations(CHAIN_ID, VAULT_ADDRESS, 6, 18, 1, 100, 1))
