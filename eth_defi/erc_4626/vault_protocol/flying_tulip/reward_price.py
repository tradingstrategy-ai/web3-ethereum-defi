"""Onchain FT/ftUSD reward-price collection from the Ethereum Curve pool.

The deployed Curve pool quotes its ``price_oracle()`` as FT per ftUSD. The
historical performance reader needs ftUSD per FT, so this module records both
the raw inverse oracle and its integer-normalised reciprocal. It never falls
back to a dashboard, an offchain API or a current spot quote.

The reviewed Curve pool's verified contract interface is available at
https://etherscan.io/address/0x68102ff5406475881462880a8da3c9bc9181ad6c#code.
The raw Curve state reads used here are limited to the one reviewed canonical
market and are covered by the fixed-block fork regression test. They value FT
rewards; they are not used to discover or classify Flying Tulip vaults.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from eth_typing import HexAddress
from tqdm_loggable.auto import tqdm
from web3 import Web3

from eth_defi.erc_4626.vault_protocol.flying_tulip.constants import (
    FLYING_TULIP_CURVE_CANONICAL_START_TIMESTAMP,
    FLYING_TULIP_FT_ETHEREUM,
    FLYING_TULIP_FT_FTUSD_CURVE_POOL,
    FLYING_TULIP_FTUSD,
    FLYING_TULIP_MAX_ORACLE_AGE_SECONDS,
)
from eth_defi.erc_4626.vault_protocol.flying_tulip.historical_context import FlyingTulipHistoricalContextStore, RewardPriceObservation
from eth_defi.event_reader.timestamp_cache import DEFAULT_TIMESTAMP_CACHE_FOLDER, load_timestamp_cache
from eth_defi.hypersync.hypersync_timestamp import fetch_block_timestamps_using_hypersync_cached
from eth_defi.hypersync.session import ThrottledHypersyncClient
from eth_defi.token import fetch_erc20_details

logger = logging.getLogger(__name__)

#: Curve's integer price-oracle scale.
CURVE_PRICE_ORACLE_SCALE = 10**18

#: A valid inverse oracle must be positive and fit the EVM uint256 domain.
MAX_UINT256 = 2**256 - 1

#: Keep every targeted timestamp-cache request observably below one minute.
FLYING_TULIP_TIMESTAMP_WINDOW_BLOCKS = 2_000


@dataclass(slots=True, frozen=True)
class FlyingTulipRewardPricePrefillResult:
    """Summarise an Ethereum Curve oracle prefill for one source chain."""

    #: sftUSD source chain whose settlements were priced.
    chain_id: int
    #: Number of post-Curve settlement timestamps considered.
    epochs_considered: int
    #: Number of distinct Ethereum blocks selected for pricing.
    price_blocks_resolved: int
    #: Number of previously absent price rows inserted.
    prices_inserted: int
    #: Number of selected observations rejected as too old.
    prices_stale: int


def _encode_uint256_call(signature: str, argument: int | None = None) -> bytes:
    """Encode a stable no-ABI Curve view call.

    :param signature:
        Solidity function signature.
    :param argument:
        Optional uint256 argument.
    :return:
        Calldata for the given function.
    """

    selector = Web3.keccak(text=signature)[:4]
    return selector if argument is None else selector + argument.to_bytes(32, "big")


def _decode_uint256(value: bytes, label: str) -> int:
    """Decode one ABI-encoded uint256 response with strict width validation.

    :param value:
        ABI return bytes.
    :param label:
        Diagnostic source label.
    :return:
        Native unsigned integer.
    """

    if len(value) != 32:
        raise ValueError(f"Flying Tulip {label} returned {len(value)} bytes, expected 32")
    return int.from_bytes(value, "big")


def fetch_curve_pool_configuration(web3: Web3, block_identifier: int | str = "latest") -> None:
    """Validate deployed Curve token order and inverse-oracle orientation.

    A one-FT ``get_dy(0, 1, 10**18)`` quote must agree broadly with the
    reciprocal of ``price_oracle()``. This checks the token order and prevents
    accidentally treating the pool's FT-per-ftUSD oracle as ftUSD per FT.

    :param web3:
        Archive-capable Ethereum connection.
    :param block_identifier:
        Fixed validation block.
    :return:
        None.
    """

    pool = FLYING_TULIP_FT_FTUSD_CURVE_POOL
    coin0 = HexAddress(
        Web3.to_checksum_address(
            _decode_uint256(
                web3.eth.call({"to": pool, "data": _encode_uint256_call("coins(uint256)", 0)}, block_identifier=block_identifier),
                "coins(0)",
            )
            .to_bytes(20, "big")
            .hex()
        )
    )
    coin1 = HexAddress(
        Web3.to_checksum_address(
            _decode_uint256(
                web3.eth.call({"to": pool, "data": _encode_uint256_call("coins(uint256)", 1)}, block_identifier=block_identifier),
                "coins(1)",
            )
            .to_bytes(20, "big")
            .hex()
        )
    )
    if coin0.lower() != FLYING_TULIP_FT_ETHEREUM.lower() or coin1.lower() != FLYING_TULIP_FTUSD.lower():
        raise ValueError(f"Unexpected Flying Tulip Curve token order: {coin0}, {coin1}")
    ft = fetch_erc20_details(web3, coin0, chain_id=1)
    ftusd = fetch_erc20_details(web3, coin1, chain_id=1)
    if ft.decimals != 18 or ftusd.decimals != 6:
        raise ValueError(f"Unexpected Flying Tulip Curve token decimals: FT={ft.decimals}, ftUSD={ftusd.decimals}")
    dy = _decode_uint256(
        web3.eth.call(
            {"to": pool, "data": Web3.keccak(text="get_dy(uint256,uint256,uint256)")[:4] + (0).to_bytes(32, "big") + (1).to_bytes(32, "big") + (10**18).to_bytes(32, "big")},
            block_identifier=block_identifier,
        ),
        "get_dy(FT,ftUSD)",
    )
    if dy <= 0:
        raise ValueError("Flying Tulip Curve FT/ftUSD quote is non-positive")
    raw_oracle = _decode_uint256(
        web3.eth.call({"to": pool, "data": _encode_uint256_call("price_oracle()")}, block_identifier=block_identifier),
        "price_oracle",
    )
    oracle_quote = CURVE_PRICE_ORACLE_SCALE**2 // raw_oracle
    dy_in_oracle_scale = dy * 10 ** (18 - ftusd.decimals)
    if not 95 * oracle_quote <= 100 * dy_in_oracle_scale <= 105 * oracle_quote:
        raise ValueError(f"Flying Tulip Curve oracle and one-FT quote disagree: oracle={oracle_quote}, get_dy={dy_in_oracle_scale}")


def fetch_curve_reward_price(web3: Web3, ethereum_block_number: int, block_timestamp: int) -> RewardPriceObservation:
    """Read and normalise the FT/ftUSD Curve oracle at one Ethereum block.

    :param web3:
        Archive-capable Ethereum connection.
    :param ethereum_block_number:
        Exact historical state block.
    :param block_timestamp:
        Timestamp from the cache-aware Hypersync block map.
    :return:
        Raw and normalised Curve oracle provenance.
    """

    pool = FLYING_TULIP_FT_FTUSD_CURVE_POOL
    raw_oracle = _decode_uint256(web3.eth.call({"to": pool, "data": _encode_uint256_call("price_oracle()")}, block_identifier=ethereum_block_number), "price_oracle")
    updated_at = _decode_uint256(web3.eth.call({"to": pool, "data": _encode_uint256_call("last_timestamp()")}, block_identifier=ethereum_block_number), "last_timestamp")
    if not 0 < raw_oracle <= MAX_UINT256:
        raise ValueError(f"Flying Tulip Curve oracle is outside uint256 domain: {raw_oracle}")
    if updated_at > block_timestamp:
        raise ValueError(f"Flying Tulip Curve oracle update {updated_at} is after block timestamp {block_timestamp}")
    return RewardPriceObservation(
        ethereum_block_number=ethereum_block_number,
        ethereum_block_timestamp=block_timestamp,
        pool_address=pool,
        raw_oracle=raw_oracle,
        oracle_updated_at=updated_at,
        raw_ft_price_in_ftusd=CURVE_PRICE_ORACLE_SCALE**2 // raw_oracle,
    )


def _fetch_targeted_ethereum_timestamp_windows(
    hypersync_client: ThrottledHypersyncClient,
    epoch_timestamps: tuple[int, ...],
    timestamp_cache_path: Path,
) -> None:
    """Fill only cache gaps which bracket foreign-chain settlements.

    The shared cache may intentionally contain sparse historical samples.
    Densifying every unrelated interior gap can take hours. An ASOF join finds
    the cached Ethereum blocks immediately before and after each settlement;
    fetching those compact windows is sufficient to identify the exact
    greatest Ethereum block whose timestamp is not later than the settlement.

    :param hypersync_client:
        Throttle-aware Ethereum Hypersync client.
    :param epoch_timestamps:
        Unique foreign-chain settlement timestamps.
    :param timestamp_cache_path:
        Shared cache folder containing Ethereum timestamp data.
    :return:
        None.
    """

    if not epoch_timestamps:
        return
    timestamp_db = load_timestamp_cache(1, timestamp_cache_path)
    try:
        timestamp_db.con.execute("CREATE TEMP TABLE flying_tulip_target_timestamps (timestamp UBIGINT PRIMARY KEY)")
        timestamp_db.con.executemany("INSERT INTO flying_tulip_target_timestamps VALUES (?)", ((value,) for value in epoch_timestamps))
        brackets = timestamp_db.con.execute(
            """
            SELECT target.timestamp, previous.block_number, following.block_number
            FROM flying_tulip_target_timestamps AS target
            ASOF LEFT JOIN block_timestamps AS previous
                ON target.timestamp >= previous.timestamp
            ASOF LEFT JOIN block_timestamps AS following
                ON target.timestamp < following.timestamp
            ORDER BY target.timestamp
            """
        ).fetchall()
    finally:
        timestamp_db.close()
    missing_brackets = [(int(previous), int(following)) for _timestamp, previous, following in brackets if previous is not None and following is not None and int(following) > int(previous) + 1]
    if any(previous is None or following is None for _timestamp, previous, following in brackets):
        message = "Flying Tulip settlement timestamps fall outside the shared Ethereum timestamp-cache boundaries"
        raise ValueError(message)
    unique_brackets = tuple(dict.fromkeys(missing_brackets))
    bounded_windows = []
    for previous, following in unique_brackets:
        for start_block in range(previous, following, FLYING_TULIP_TIMESTAMP_WINDOW_BLOCKS):
            end_block = min(start_block + FLYING_TULIP_TIMESTAMP_WINDOW_BLOCKS, following)
            if end_block > start_block + 1:
                bounded_windows.append((start_block, end_block))
    with tqdm(total=len(bounded_windows), desc="Flying Tulip targeted timestamp windows", unit="window") as progress_bar:
        for previous, following in bounded_windows:
            timestamps = fetch_block_timestamps_using_hypersync_cached(
                hypersync_client,
                1,
                previous,
                following,
                cache_path=timestamp_cache_path,
                display_progress=False,
            )
            timestamps.timestamp_db.close()
            progress_bar.update()


def fetch_and_store_flying_tulip_reward_prices(
    *,
    ethereum_web3: Web3,
    ethereum_hypersync_client: ThrottledHypersyncClient,
    chain_id: int,
    ethereum_start_block: int,
    ethereum_end_block: int,
    context_path: Path,
    timestamp_cache_path: Path = DEFAULT_TIMESTAMP_CACHE_FOLDER,
) -> FlyingTulipRewardPricePrefillResult:
    """Map stored epochs to Ethereum and persist their canonical Curve prices.

    Only epochs at or after the canonical Curve market's deployment timestamp
    are considered. The caller supplies an Ethereum range containing those
    epochs. Compact gaps in the shared timestamp cache are filled through
    Hypersync, then each foreign-chain settlement is mapped to the greatest
    Ethereum block whose timestamp is not later than that settlement.

    :param ethereum_web3:
        Archive-capable Ethereum provider for historical Curve state calls.
    :param ethereum_hypersync_client:
        Throttle-aware Ethereum Hypersync client for timestamp cache fills.
    :param chain_id:
        Source sftUSD deployment chain.
    :param ethereum_start_block:
        Inclusive, timestamp-cache Ethereum range boundary.
    :param ethereum_end_block:
        Exclusive, snapped Ethereum safe head.
    :param context_path:
        Shared contextual cache path.
    :param timestamp_cache_path:
        Shared ``block-timestamp`` cache directory.
    :return:
        Price prefill diagnostics.
    """

    if ethereum_web3.eth.chain_id != 1:
        raise ValueError(f"FT reward price source must be Ethereum, got chain {ethereum_web3.eth.chain_id}")
    if not 1 <= ethereum_start_block < ethereum_end_block:
        raise ValueError(f"Invalid Ethereum price range: [{ethereum_start_block}, {ethereum_end_block})")
    fetch_curve_pool_configuration(ethereum_web3, ethereum_end_block - 1)
    with FlyingTulipHistoricalContextStore(context_path, read_only=True) as source_store:
        epoch_rows = source_store.connection.execute(
            "SELECT block_timestamp, block_number FROM flying_tulip_epoch_context WHERE chain_id = ? AND block_timestamp >= ? ORDER BY block_timestamp",
            (chain_id, FLYING_TULIP_CURVE_CANONICAL_START_TIMESTAMP),
        ).fetchall()
    epoch_timestamps = tuple(dict.fromkeys(int(row[0]) for row in epoch_rows))
    if chain_id != 1:
        _fetch_targeted_ethereum_timestamp_windows(ethereum_hypersync_client, epoch_timestamps, timestamp_cache_path)
    mapped_blocks: dict[int, tuple[int, int]] = {}
    if chain_id == 1:
        mapped_blocks.update((int(timestamp), (int(block_number), int(timestamp))) for timestamp, block_number in epoch_rows)
    elif epoch_timestamps:
        timestamp_db = load_timestamp_cache(1, timestamp_cache_path)
        try:
            timestamp_db.con.execute("CREATE TEMP TABLE flying_tulip_epoch_timestamps (timestamp UBIGINT PRIMARY KEY)")
            timestamp_db.con.executemany("INSERT INTO flying_tulip_epoch_timestamps VALUES (?)", ((value,) for value in epoch_timestamps))
            mapped_rows = timestamp_db.con.execute(
                """
                WITH ethereum_blocks AS (
                    SELECT block_number, timestamp
                    FROM block_timestamps
                    WHERE block_number BETWEEN ? AND ?
                )
                SELECT target.timestamp, source.block_number, source.timestamp
                FROM flying_tulip_epoch_timestamps AS target
                ASOF LEFT JOIN ethereum_blocks AS source
                    ON target.timestamp >= source.timestamp
                ORDER BY target.timestamp
                """,
                (ethereum_start_block, ethereum_end_block - 1),
            ).fetchall()
        finally:
            timestamp_db.close()
        for settlement_timestamp, block_number, block_timestamp in mapped_rows:
            if block_number is None:
                logger.warning("Flying Tulip chain %d epoch at %d predates supported Ethereum Curve history", chain_id, settlement_timestamp)
                continue
            mapped_blocks[int(settlement_timestamp)] = (int(block_number), int(block_timestamp))
    with FlyingTulipHistoricalContextStore(context_path) as store:
        inserted = stale = 0
        price_blocks = sorted(set(mapped_blocks.values()))
        with tqdm(total=len(price_blocks), desc=f"Flying Tulip Curve prices for chain {chain_id}", unit="price") as progress_bar:
            for ethereum_block_number, block_timestamp in price_blocks:
                existing = store.connection.execute("SELECT 1 FROM flying_tulip_reward_price_context WHERE ethereum_block_number = ?", (ethereum_block_number,)).fetchone()
                if existing is not None:
                    progress_bar.update()
                    continue
                observation = fetch_curve_reward_price(ethereum_web3, ethereum_block_number, block_timestamp)
                if block_timestamp - observation.oracle_updated_at > FLYING_TULIP_MAX_ORACLE_AGE_SECONDS:
                    stale += 1
                    logger.warning("Flying Tulip Curve oracle at Ethereum block %d is stale by %d seconds", ethereum_block_number, block_timestamp - observation.oracle_updated_at)
                else:
                    inserted += int(store.insert_reward_price(observation))
                progress_bar.update()
                progress_bar.set_postfix({"inserted": inserted, "stale": stale})
    return FlyingTulipRewardPricePrefillResult(chain_id, len(epoch_timestamps), len(mapped_blocks), inserted, stale)
