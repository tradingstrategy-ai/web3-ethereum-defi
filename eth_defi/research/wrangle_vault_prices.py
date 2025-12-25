"""Clean vault price data.

.. _wrangle vault:

- Denormalise data to a single DataFrame
- Remove abnormalities in the price data
- Reduce data by removing hourly changes that are below our epsilon threshold
- Generate returns data
"""

import pickle
import warnings
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
from eth_typing import HexAddress

from eth_defi.chain import get_chain_name
from eth_defi.token import is_stablecoin_like
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase, DEFAULT_VAULT_DATABASE, VaultRow, DEFAULT_RAW_PRICE_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE

from tqdm.auto import tqdm


from IPython.display import display

#: For manual debugging, we process these vaults first
PRIORITY_SORT_IDS = [
    "8453-0x0d877dc7c8fa3ad980dfdb18b48ec9f8768359c4",
]


def get_vaults_by_id(rows: dict[VaultSpec, VaultRow]) -> dict[str, VaultRow]:
    """Build a dictionary of vaults by their chain-address id.

    :param rows:
        Metadata rows from vault database
    :return:
        Dictionary of vaults by their chain-address id
    """
    vaults_by_id = {f"{vault['_detection_data'].chain}-{vault['_detection_data'].address}": vault for vault in rows.values()}
    return vaults_by_id


def assign_unique_names(
    rows: dict[VaultSpec, VaultRow],
    prices_df: pd.DataFrame,
    logger=print,
    duplicate_nav_threshold=1000,
) -> pd.DataFrame:
    """Ensure all vaults have unique human-readable name.

    - Rerwrite metadata rows
    - Find duplicate vault names
    - Add a running counter to the name to make it unique
    """
    vaults_by_id = get_vaults_by_id(rows)

    # We use name later as DF index, so we need to make sure they are unique
    counter = 1
    duplicate_names_with_nav = 0
    used_names = set()
    empty_names = set()

    for id, vault in vaults_by_id.items():
        # TODO: hack
        # 40acres forgot to name their vault
        if vault["Name"] == "Vault":
            vault["Name"] == "40acres"

        if vault["Name"] in (None, ""):
            empty_names.add(id)

        if vault["Name"] in used_names:
            chain_name = get_chain_name(vault["_detection_data"].chain)

            if (vault.get("NAV") or 0) > duplicate_nav_threshold:
                duplicate_names_with_nav += 1

            if chain_name not in (vault["Name"] or ""):
                # Don't duplicate Ethereum in Peapod vault names
                vault["Name"] = f"{vault['Name']} ({chain_name}) #{counter}".strip()
            else:
                vault["Name"] = f"{vault['Name']} #{counter}".strip()

            counter += 1

        used_names.add(vault["Name"])

    logger(f"Fixed {counter} duplicate vault names, {len(empty_names)} vaults had empty names, duplicate names with NAV: {duplicate_names_with_nav}")

    if empty_names:
        example_id = list(empty_names)[0]
        example = vaults_by_id[example_id]
        logger(f"Example vault with empty name: {example}")

    # Vaults are identified by their chain and address tuple, make this one human-readable column
    # to make DataFrame wrangling easier
    prices_df["id"] = prices_df["chain"].astype(str) + "-" + prices_df["address"].astype(str)
    prices_df["name"] = prices_df["id"].apply(lambda x: vaults_by_id[x]["Name"] if x in vaults_by_id else None)

    # 40acres fix - they did not name their vault,
    # More about this later
    prices_df["name"] = prices_df["name"].fillna("<unknown>")

    return prices_df


def add_denormalised_vault_data(
    rows: dict[HexAddress, VaultRow],
    prices_df: pd.DataFrame,
    logger=print,
) -> pd.DataFrame:
    """Add denormalised data to the prices DataFrame.

    - Take data from vault database and duplicate it across every row
    - Add protocol name and event count columns
    """

    vaults_by_id = get_vaults_by_id(rows)
    try:
        prices_df["event_count"] = prices_df["id"].apply(lambda x: vaults_by_id[x]["_detection_data"].deposit_count + vaults_by_id[x]["_detection_data"].redeem_count)
        prices_df["protocol"] = prices_df["id"].apply(lambda x: vaults_by_id[x]["Protocol"] if x in vaults_by_id else None)
    except KeyError as e:
        logger(f"Likely metadata issue: {e}")
        raise

    return prices_df


def filter_vaults_by_stablecoin(
    rows: dict[HexAddress, VaultRow],
    prices_df: pd.DataFrame,
    logger=print,
) -> pd.DataFrame:
    """Reduce vaults to stablecoin vaults only.


    - In this notebooks, we focus on stablecoin yield
    - Do not consider WETH, other native token vaults, as their returns calculation
      would need to match the appreciation of underlying assets
    - [is_stablecoin_like](https://web3-ethereum-defi.readthedocs.io/api/core/_autosummary/eth_defi.token.is_stablecoin_like.html?highlight=is_stablecoin_like#eth_defi.token.is_stablecoin_like) supports GHO, crvUSD and other DeFi/algorithmic stablecoins
    - Note that this picks up very few EUR and other fiat-nominated vaults

    """

    usd_vaults = [v for v in rows.values() if is_stablecoin_like(v["Denomination"])]
    logger(f"We have {len(usd_vaults)} stablecoin-nominated vaults out of {len(rows)} total vaults")

    # Build chain-address strings for vaults we are interested in
    allowed_vault_ids = set(str(v["_detection_data"].chain) + "-" + v["_detection_data"].address for v in usd_vaults)

    # Filter out prices to contain only data for vaults we are interested in
    prices_df = prices_df.loc[prices_df["id"].isin(allowed_vault_ids)]
    logger(f"Filtered out prices have {len(prices_df):,} rows")

    return prices_df


def calculate_vault_returns(prices_df: pd.DataFrame, logger=print):
    """Calculate returns for each vault.

    - Filter out reads for which we did not get a proper share price
    - Add ``returns_1h`` columns

    Example of input data:

    .. code-block:: none

             chain                                     address  block_number           timestamp  share_price  ...  errors                                                id  name  event_count            protocol
        207  42161  0x487cdc7d21ac8765eff6c0e681aea36ae1594471      13294721 2022-05-30 19:59:22          1.0  ...          42161-0x487cdc7d21ac8765eff6c0e681aea36ae1594471  LDAI           17  <unknown ERC-4626>

    """
    assert isinstance(prices_df, pd.DataFrame), "prices_df must be a pandas DataFrame"

    missing_share_price_mask = prices_df["share_price"].isna()
    bad_share_price_df = prices_df[missing_share_price_mask]
    if len(bad_share_price_df) > 0:
        logger(f"We have NaN share price for {len(bad_share_price_df):,} rows, these will be dropped")
        prices_df = prices_df[~missing_share_price_mask]

    assert prices_df["share_price"].isna().sum() == 0, "share_price column must not contain NaN values"
    prices_df = prices_df.copy()
    prices_df["returns_1h"] = prices_df.groupby("id")["share_price"].pct_change()
    return prices_df


def clean_returns(
    rows: dict[HexAddress, VaultRow],
    prices_df: pd.DataFrame,
    logger=print,
    outlier_threshold=0.50,  # Set threshold we suspect not valid returns for one day
    display: Callable = lambda x: x,
    returns_col="returns_1h",
) -> pd.DataFrame:
    """Clean returns data by removing rows with NaN or infinite values.

    - In returns data we have outliers that are likely not real returns, or one-time events that cannot repeat.
        - Floating point errors: [Share price may jumps wildly when a vault TVL is near zero](https://x.com/0xSEM/status/1914748782102630455)
        - Bugs: Vault share price method to estimate returns does not work for a particular airdrop
        - Airdrops: Vault gets an irregular rewards that will not repeat, and thus are not good to estimate the
          future performance
    - We clean returns by doing an assumptions
      - Daily returns higher than static outlier
      - Daily TVL max does not make sense
      - Daily TVL min does not make sense
      - Daily TVL % below lifetime average TVL

    """

    returns_df = prices_df

    high_returns_mask = returns_df[returns_col] > outlier_threshold
    outlier_returns = returns_df[high_returns_mask]

    # Sort by return value (highest first)
    outlier_returns = outlier_returns.sort_values(by=returns_col, ascending=False)

    # Display the results
    logger(f"Found {len(outlier_returns)} outlier returns > {outlier_threshold:%}")
    display(outlier_returns[["name", "id", returns_col, "share_price", "total_assets"]].head(3))

    # Show the distribution of these outliers by vault
    outlier_counts = outlier_returns.groupby("name").size().sort_values(ascending=False)
    print("\nTop outlier too high return row count by vault:")

    display(outlier_counts.head(3))

    # Clean up obv too high returns
    returns_df.loc[returns_df[returns_col] > outlier_threshold, returns_col] = 0

    return returns_df


def clean_by_tvl(
    rows: dict[HexAddress, VaultRow],
    prices_df: pd.DataFrame,
    logger=print,
    tvl_threshold_min=1000.00,
    tvl_threshold_max=99_000_000_000,  # USD 99B
    tvl_threshold_min_dynamic=0.02,
    returns_col="returns_1h",
) -> pd.DataFrame:
    """TVL-based threshold filtering of returns.

    - Clean returns from TVL-manipulation outliers
    - See https://x.com/moo9000/status/1914746350216077544 for manipulation example
    """

    returns_df = prices_df

    # TVL based cleaning.
    # Create a mask based on TVL conditions.
    # Clean up returns during low TVL periods
    # pd.Timestamp("2024-02-10")
    mask = returns_df["total_assets"] < tvl_threshold_min
    mask |= returns_df["total_assets"] > tvl_threshold_max

    # Clean up by dynamic TVL threshold filtering
    #
    # Morpho Steakhouse USDT Compounder by Yearn case, and similars
    # https://x.com/moo9000/status/1914746350216077544

    # Calculate all-time average of total_assets for each vault
    avg_assets_by_vault = returns_df.groupby("id")["total_assets"].mean()
    returns_df["avg_assets_by_vault"] = returns_df["id"].map(avg_assets_by_vault)
    returns_df["dynamic_tvl_threshold"] = returns_df["id"].map(avg_assets_by_vault) * tvl_threshold_min_dynamic

    # Create a mask for rows where total_assets is below the threshold
    below_threshold_mask = returns_df["total_assets"] < returns_df["dynamic_tvl_threshold"]
    mask |= below_threshold_mask
    # Count how many data points will be affected
    affected_count = below_threshold_mask.sum()
    logger(f"Setting returns to zero for {affected_count:,} / {len(returns_df):,} data points where total_assets < {tvl_threshold_min_dynamic:.2%} of all-time average TVL")

    # We also need to expand the mask,
    # so that we zero the returns of the following day
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        mask = mask | mask.groupby(returns_df["id"]).shift(1).fillna(False)

    # Set daily_returns to zero where the mask is True
    returns_df.loc[mask, returns_col] = 0
    returns_df["tvl_filtering_mask"] = mask

    return returns_df


def filter_unneeded_row(
    prices_df: pd.DataFrame,
    logger=print,
    epsilon=0.0025,  #
) -> pd.DataFrame:
    """Dedpulicate data rows with epsilon.

    - Reduce data size by elimating rows where the value changes is too little
    - Remove rows where the total asset/share price/total supply change has been too small

    .. note ::

        This filter conly yields 2% savings in row count, so it turned out not to be worth of the problems.

    :param prices_df:
        Assume sorted by timestsamp

    :param epsilon:
        Tolerance for floating point comparison

    """

    original_row_count = len(prices_df)

    invalid_share_price_entry_count = 0
    total_removed = 0
    total_rows = 0

    def _filter_pair_for_almost_duplicate_entries(group):
        """Filter a single group using range-based filtering."""

        nonlocal invalid_share_price_entry_count
        nonlocal total_removed
        nonlocal total_rows

        if len(group) <= 1:
            return group  # Keep groups with only one row

        # chain-address id for debug
        id = group.iloc[0]["id"]

        keep_mask = pd.Series(True, index=group.index)
        i = 0

        start_total_assets = None
        start_total_supply = None
        start_share_price = None

        while i < len(group) - 1:
            # Start from current position
            start_idx = i
            current_idx = i + 1

            start_total_assets = start_total_assets or group.loc[start_idx]["total_assets"]
            start_total_supply = start_total_supply or group.iloc[start_idx]["total_supply"]
            start_share_price = start_share_price or group.iloc[start_idx]["share_price"]

            if pd.isna(start_share_price) or pd.isna(start_total_supply) or pd.isna(start_total_assets):
                invalid_share_price_entry_count += 1
                i += 1
                continue

            # assert not pd.isna(start_share_price), "Start share price should not be NaN"

            # Find the end of the sequence where all changes are below epsilon
            while current_idx < len(group):
                # Calculate relative changes from the start position

                total_assets_change = abs((group.iloc[current_idx]["total_assets"] - start_total_assets) / start_total_assets)
                share_price_change = abs((group.iloc[current_idx]["share_price"] - start_share_price) / start_share_price)
                total_supply_change = abs((group.iloc[current_idx]["total_supply"] - start_total_supply) / start_total_supply)

                # Check if ANY change exceeds epsilon
                if total_assets_change > epsilon or share_price_change > epsilon or total_supply_change > epsilon:
                    break

                current_idx += 1

            # If we found a sequence of small changes, mark intermediate rows for removal
            if current_idx > start_idx + 1:
                # Keep start row, remove intermediate rows, keep end row (if it exists)
                for j in range(start_idx + 1, current_idx):
                    keep_mask.iloc[j] = False

            # Move to the next position
            i = max(current_idx, start_idx + 1)

        filtered_group = group[keep_mask]

        total_rows += len(group)
        total_removed += len(group) - len(filtered_group)

        # Get the latest progress bar
        # created by progress_apply()
        pbar = list(tqdm._instances)[-1]
        pbar.set_postfix(
            {
                "removed": f"{total_removed:,}",
                "removed_pct": f"{total_removed / total_rows:.2%}",
                "total": f"{total_rows:,}",
                "invalid_share_price_entries": f"{invalid_share_price_entry_count:,}",
            }
        )

        return filtered_group

    # Apply the filter function to each group
    tqdm.pandas(
        desc="Filtering non-relevant changes rows",
        unit="vault",
        unit_scale=True,
    )

    with warnings.catch_warnings():
        # Abort on bad share price dividsion
        # /Users/moo/code/trade-executor/deps/web3-ethereum-defi/eth_defi/research/wrangle_vault_prices.py:298: RuntimeWarning: invalid value encountered in scalar divide
        #   total_assets_change = abs((group.iloc[current_idx]['total_assets'] - start_total_assets) / start_total_assets)
        warnings.filterwarnings("error", category=RuntimeWarning)

        # Don't let groupby re-sort as the filtering loop depends on the order of rows
        filtered_df = prices_df.groupby("id", group_keys=True, sort=False).progress_apply(_filter_pair_for_almost_duplicate_entries)

    rows_left = len(filtered_df)
    removed_count = original_row_count - rows_left
    logger(f"Filtered too small change rows: {original_row_count:,} -> {rows_left:,} ({removed_count:,}) epsilon={epsilon}, invalid share price entries {invalid_share_price_entry_count:,}")

    # groupby() added id as an MultiIndex(id, timestamp), but unwind this change back,
    # as other functions do not expect it
    filtered_df = filtered_df.droplevel("id")

    return filtered_df


def fix_outlier_share_prices(
    prices_df: pd.DataFrame,
    logger=print,
    max_diff=0.33,
    look_back=24,
    look_ahead=24,
) -> pd.DataFrame:
    """Fix out rows with share price that is too high.

    - Sometimes share price jump to an outlier value and back
    - This caused abnormal returns in returns calculations, messing all volatility numbers, sharpe,
      charts, etc.
    - The root cause is bad oracles, fat fingers, MEV trades, etc.
    - See ``check-share-price`` script for inspecting individual prices

    Case Fluegel DAO:

    +------------+-------+------------------------------------------+--------------+-------------+-------------+-------------+
    | timestamp  | chain | address                                  | block_number | share_price | total_assets | total_supply |
    +============+=======+==========================================+==============+=============+=============+=============+
    | 2024-07-16 | 8453  | 0x277a3c57f3236a7d458576074d7c3d7046eb26c | 17176415     | 1.60        | 373,740.21  | 232,929.92  |
    | 15:02:57   |       |                                          |              |             |             |             |
    +------------+-------+------------------------------------------+--------------+-------------+-------------+-------------+
    | 2024-07-16 | 8453  | 0x277a3c57f3236a7d458576074d7c3d7046eb26c | 17178215     | 1.63        | 379,832.59  | 232,929.92  |
    | 16:02:57   |       |                                          |              |             |             |             |
    +------------+-------+------------------------------------------+--------------+-------------+-------------+-------------+
    | 2024-07-16 | 8453  | 0x277a3c57f3236a7d458576074d7c3d7046eb26c | 17180015     | 0.33        | 75,744.97   | 232,929.92  |
    | 17:02:57   |       |                                          |              |             |             |             |
    +------------+-------+------------------------------------------+--------------+-------------+-------------+-------------+
    | 2024-07-16 | 8453  | 0x277a3c57f3236a7d458576074d7c3d7046eb26c | 17181815     | 1.64        | 382,282.78  | 232,929.92  |
    | 18:02:57   |       |                                          |              |             |             |             |
    +------------+-------+------------------------------------------+--------------+-------------+-------------+-------------+

    Case Untangle Finance:

    .. code-block:: none

        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-12 23:14:19 (3206): fixing: 1.038721 -> 1.038721, prev: 1.038827, next: 0.444865
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-13 00:14:13 (3207): fixing: 1.038931 -> 1.038931, prev: 1.038801, next: 0.444865
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-13 01:14:09 (3208): fixing: 1.038931 -> 1.038931, prev: 1.038801, next: 0.444865
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-13 02:14:03 (3209): fixing: 1.038931 -> 1.038931, prev: 1.038801, next: 0.444865
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-13 03:14:01 (3210): fixing: 1.038931 -> 1.038931, prev: 1.038801, next: 0.444865
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-13 04:13:56 (3211): fixing: 1.038931 -> 1.038931, prev: 1.038801, next: 0.444865
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-13 05:13:53 (3212): fixing: 1.038931 -> 1.038931, prev: 1.038801, next: 0.468629
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-13 06:13:51 (3213): fixing: 1.039134 -> 1.039134, prev: 1.038439, next: 0.468629
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-13 07:13:45 (3214): fixing: 1.039134 -> 1.039134, prev: 1.038439, next: 0.468629
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-13 08:13:40 (3215): fixing: 1.039134 -> 1.039134, prev: 1.038439, next: 0.468629
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-13 09:13:37 (3216): fixing: 1.039134 -> 1.039134, prev: 1.038439, next: 0.482511
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-13 10:13:27 (3217): fixing: 1.039134 -> 1.039134, prev: 1.038439, next: 0.482511
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-13 11:13:21 (3218): fixing: 1.039134 -> 1.039134, prev: 1.038439, next: 0.482511
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-14 00:11:51 (3230): fixing: 0.444865 -> 1.0405275, prev: 1.038721, next: 1.042334
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-14 01:11:42 (3231): fixing: 0.444865 -> 1.0406325, prev: 1.038931, next: 1.042334
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-14 02:11:33 (3232): fixing: 0.444865 -> 1.0407335, prev: 1.038931, next: 1.042536
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-14 03:11:36 (3233): fixing: 0.444865 -> 1.0407335, prev: 1.038931, next: 1.042536
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-14 04:11:26 (3234): fixing: 0.444865 -> 1.0407335, prev: 1.038931, next: 1.042536
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-14 05:11:17 (3235): fixing: 0.444865 -> 1.0407335, prev: 1.038931, next: 1.042536
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-14 06:11:10 (3236): fixing: 0.468629 -> 1.0407335, prev: 1.038931, next: 1.042536
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-14 07:11:01 (3237): fixing: 0.468629 -> 1.040835, prev: 1.039134, next: 1.042536
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-14 08:10:52 (3238): fixing: 0.468629 -> 1.0406445, prev: 1.039134, next: 1.042155
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-14 11:10:26 (3239): fixing: 0.468629 -> 1.0406445, prev: 1.039134, next: 1.042155
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-14 12:10:18 (3240): fixing: 0.482511 -> 1.0406445, prev: 1.039134, next: 1.042155
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-14 13:10:09 (3241): fixing: 0.482511 -> 1.0406445, prev: 1.039134, next: 1.042155
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-14 14:10:01 (3242): fixing: 0.482511 -> 1.0406445, prev: 1.039134, next: 1.042155
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-15 04:08:39 (3254): fixing: 1.042334 -> 1.042334, prev: 0.444865, next: 1.04251
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-15 05:08:33 (3255): fixing: 1.042334 -> 1.042334, prev: 0.444865, next: 1.04251
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-15 06:08:29 (3256): fixing: 1.042536 -> 1.042536, prev: 0.444865, next: 1.04251
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-15 07:08:24 (3257): fixing: 1.042536 -> 1.042536, prev: 0.444865, next: 1.04251
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-15 08:08:20 (3258): fixing: 1.042536 -> 1.042536, prev: 0.444865, next: 1.04251
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-15 09:08:12 (3259): fixing: 1.042536 -> 1.042536, prev: 0.444865, next: 1.04251
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-15 10:08:07 (3260): fixing: 1.042536 -> 1.042536, prev: 0.468629, next: 1.042519
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-15 11:08:03 (3261): fixing: 1.042536 -> 1.042536, prev: 0.468629, next: 1.042519
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-15 12:07:55 (3262): fixing: 1.042155 -> 1.042155, prev: 0.468629, next: 1.042519
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-15 13:07:51 (3263): fixing: 1.042155 -> 1.042155, prev: 0.468629, next: 1.042519
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-15 14:07:49 (3264): fixing: 1.042155 -> 1.042155, prev: 0.482511, next: 1.042519
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-15 15:07:42 (3265): fixing: 1.042155 -> 1.042155, prev: 0.482511, next: 1.042519
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-15 16:07:36 (3266): fixing: 1.042155 -> 1.042155, prev: 0.482511, next: 1.042354
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-23 02:04:05 (3433): fixing: 1.036482 -> 1.036482, prev: 1.126302, next: 0.487429
        Abnormal share price detected for 42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9 at index 2025-10-24 06:01:21 (3457): fixing: 0.487429 -> 1.041995, prev: 1.036482, next: 1.047508


    """

    # Store unfiltered share prices for the later examination
    prices_df["raw_share_price"] = prices_df["share_price"]

    share_prices_fixed = 0

    def _clean_share_price_for_pair(group: pd.DataFrame) -> pd.DataFrame:
        """Apply rolling window clean up technique that checks if 24h past and future prices are aligned, but the current price is not,
        then overwrite with the avg of past and future prices."""
        nonlocal share_prices_fixed

        # group = group.copy()

        if len(prices_df) == 0:
            return prices_df

        group = group.ffill()

        group["next_price_candidate"] = group["share_price"].shift(-look_ahead).ffill()
        group["prev_price_candidate"] = group["share_price"].shift(look_back).bfill()

        # Calculate forward and backward percentage change for each vault
        group["pct_change_prev"] = (group["prev_price_candidate"] / group["share_price"] - 1).abs()
        group["pct_change_next"] = (group["next_price_candidate"] / group["share_price"] - 1).abs()

        # 2025-10-24 05:01:27  42161  0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9     392792321     1.046227  144437.368503  138055.399019              NaN             NaN         42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9  USDn2           60  Untangle Finance         1.046227
        # 2025-10-24 06:01:21  42161  0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9     392806721     0.487429   67292.321607  138055.399019              NaN             NaN         42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9  USDn2           60  Untangle Finance         0.487429
        # 2025-10-24 07:01:10  42161  0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9     392821121     1.047508  144614.157347  138055.399019              NaN             NaN         42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9  USDn2           60  Untangle Finance         1.047508

        # Mark rows where both changes exceed the threshold (spike and recovery)
        abnormal_mask = (group["pct_change_prev"] > max_diff) | (group["pct_change_next"] > max_diff)

        group["fixed_share_price"] = np.NaN

        # Print pass, figure out damanged entries
        for idx in group[abnormal_mask].index:
            idx_loc = group.index.get_loc(idx)
            current_price = group.iloc[idx_loc]["share_price"]
            next_price = group.iloc[idx_loc]["next_price_candidate"]
            prev_price = group.iloc[idx_loc]["prev_price_candidate"]
            # Start and end of the group
            if pd.isna(next_price) or pd.isna(prev_price):
                continue

            # The next 24h and prev 24h price are less than max diff apart from each other,
            # but the current price is an outlier, fix it
            if prev_price != 0 and abs((next_price - prev_price) / prev_price) < max_diff:
                fixed_price = (next_price + prev_price) / 2
                share_prices_fixed += 1
            else:
                # Maybe a genuine crash
                fixed_price = current_price

            # logger(f"Abnormal share price detected for {vault_id} at index {idx} ({idx_loc}): fixing: {current_price} -> {fixed_price}, prev: {prev_price}, next: {next_price}")
            group.loc[idx, "fixed_share_price"] = fixed_price

        # Apply the fixes
        group.loc[pd.notna(group["fixed_share_price"]), "share_price"] = group["fixed_share_price"]
        # group["id"] = vault_id

        # Don't export extra columns, only needed for calculations and debugging
        del group["prev_price_candidate"]
        del group["next_price_candidate"]
        del group["pct_change_prev"]
        del group["pct_change_next"]
        del group["fixed_share_price"]

        return group

    # TODO: How to fix warning here so that id column is retained?
    # /Users/moo/code/trade-executor/deps/web3-ethereum-defi/eth_defi/research/wrangle_vault_prices.py:575: FutureWarning: DataFrameGroupBy.apply operated on the grouping columns. This behavior is deprecated, and in a future version of pandas the grouping columns will be excluded from the operation. Either pass `include_groups=False` to exclude the groupings or explicitly select the grouping columns after groupby to silence this warning.
    filtered_all_df = prices_df.groupby("id", group_keys=True, sort=False).apply(_clean_share_price_for_pair, include_groups=False)

    change_mask = (filtered_all_df["share_price"] != filtered_all_df["raw_share_price"]) & pd.notna(filtered_all_df["raw_share_price"])
    change_count = len(change_mask[change_mask == True])

    logger(f"Share prices fix count {share_prices_fixed}, updated {change_count:,} / {len(filtered_all_df):,} rows with abnormal share_price spikes (> {max_diff:.2%})")

    # groupby() added id as an MultiIndex(id, timestamp), but unwind this change back,
    # as other functions do not expect it
    filtered_all_df = filtered_all_df.reset_index().set_index("timestamp")

    return filtered_all_df


def sort_and_index_vault_prices(
    prices_df: pd.DataFrame,
    priority_ids: list[str],
):
    """Set up the order of vaults for processing.

    - If we do debugging we want vaults we debug go first,
      as the pipeline takes several minutes to run
    """

    assert isinstance(prices_df.index, pd.DatetimeIndex), f"Got: {type(prices_df.index)}"

    # Create a priority column for sorting
    prices_df["sort_priority"] = prices_df["id"].apply(lambda x: 0 if x in priority_ids else 1)

    # Sort by priority first, then by id and timestamp
    prices_df = prices_df.reset_index()
    prices_df = prices_df.sort_values(by=["sort_priority", "id", "timestamp"]).drop("sort_priority", axis=1).set_index("timestamp")
    return prices_df


def process_raw_vault_scan_data(
    rows: dict[VaultSpec, VaultRow] | VaultDatabase,
    prices_df: pd.DataFrame,
    logger=print,
    display: Callable = lambda x: None,
    diagnose_vault_id: str | None = None,
) -> pd.DataFrame:
    """Preprocess vault data for further analysis.

    - Assign unique names to vaults
    - Add denormalised vault data to prices DataFrame
    - Filter out non-stablecoin vaults
    - Calculate returns, rolling metrics

    :param rows:
        Metadata rows from vault database

    :param logger:
        Notebook / console printer function

    :param display:
        Display Pandas DataFrame function
    """

    assign_unique_names(rows, prices_df, logger)

    check_missing_metadata(rows, prices_df["id"], logger)

    prices_df = add_denormalised_vault_data(rows, prices_df, logger)

    if diagnose_vault_id:
        vault_prices_df = prices_df[prices_df["id"] == diagnose_vault_id]
        logger("After add_denormalised_vault_data():")
        display(vault_prices_df)

    prices_df = prices_df.set_index("timestamp")

    prices_df = sort_and_index_vault_prices(prices_df, PRIORITY_SORT_IDS)
    prices_df = filter_vaults_by_stablecoin(rows, prices_df, logger)
    # Disabled as low and does not result to any savings
    # prices_df = filter_unneeded_row(prices_df, logger)

    prices_df = fix_outlier_share_prices(prices_df, logger)

    if diagnose_vault_id:
        vault_prices_df = prices_df[prices_df["id"] == diagnose_vault_id]
        logger("After fix_outlier_share_prices():")
        display(vault_prices_df)

    prices_df = calculate_vault_returns(prices_df)

    prices_df = clean_returns(
        rows,
        prices_df,
        logger=logger,
        display=display,
    )

    if diagnose_vault_id:
        vault_prices_df = prices_df[prices_df["id"] == diagnose_vault_id]
        print("After clean_returns():")
        display(vault_prices_df)

    prices_df = clean_by_tvl(
        rows,
        prices_df,
        logger,
    )
    return prices_df


def check_missing_metadata(
    rows: dict,
    price_ids: pd.Series,
    logger=print,
):
    """Check that we have metadata for all vaults in the prices DataFrame.

    Vault id is in format: 56-0x10c90bfcfb3d2a7ae814da1548ae3a7fc31c35a0'

    :param rows:
        Metadata rows from vault database
    """

    assert isinstance(price_ids, pd.Series)

    price_ids = sorted(list(price_ids.unique()))

    vaults_by_id = get_vaults_by_id(rows)

    # assert "56-0x10c90bfcfb3d2a7ae814da1548ae3a7fc31c35a0" in price_ids
    # assert "56-0x10c90bfcfb3d2a7ae814da1548ae3a7fc31c35a0" in vaults_by_id

    logger(f"Price data has {len(price_ids):,} unique vault ids, vault database has {len(vaults_by_id):,} vault ids")

    assert len(price_ids) > 0, "No vault ids in price data"

    missing_count = 0

    for vault_id in price_ids:
        if vault_id not in vaults_by_id:
            missing_count += 1
            logger(f"Missing metadata for vault id {vault_id}")

    assert not missing_count, f"Missing vault metadata for {missing_count:,} vault ids, cannot continue"


def generate_cleaned_vault_datasets(
    vault_db_path=DEFAULT_VAULT_DATABASE,
    price_df_path=DEFAULT_UNCLEANED_PRICE_DATABASE,
    cleaned_price_df_path=Path.home() / ".tradingstrategy" / "vaults" / "cleaned-vault-prices-1h.parquet",
    logger=print,
    display=display,
    diagnose_vault_id: str | None = None,
):
    """A command line script entry point to take raw scanned vault price data and clean it up to a format that can be analysed.

    - Reads ``vault-prices-1h.parquet`` and generates ``vault-prices-1h-cleaned.parquet``
    - Calculate returns and various performance metrics to be included with prices data
    - Clean returns from abnormalities

    .. note::

        Drops non-stablecoin vaults. The cleaning is currently applicable
        for stable vaults only.
    """

    assert vault_db_path.exists()
    assert price_df_path.exists()

    logger(f"Loading vault database {vault_db_path}")
    vault_db: VaultDatabase = pickle.load(vault_db_path.open("rb"))

    logger(f"Loading prices {price_df_path}")
    prices_df = pd.read_parquet(price_df_path)

    logger(f"We have {vault_db.get_lead_count():,} vault leads in the vault database and {len(prices_df):,} price rows in the raw prices DataFrame")

    rows = vault_db.rows

    enhanced_prices_df = process_raw_vault_scan_data(
        rows,
        prices_df,
        logger,
        display=display,
        diagnose_vault_id=diagnose_vault_id,
    )

    # Sort for better compression
    enhanced_prices_df = enhanced_prices_df.sort_values(by=["id", "timestamp"])

    enhanced_prices_df.to_parquet(
        cleaned_price_df_path,
        compression="zstd",
    )

    fsize = cleaned_price_df_path.stat().st_size
    logger(f"Saved cleaned vault prices to {cleaned_price_df_path}, total {len(enhanced_prices_df):,} rows, file size is {fsize / 1024 / 1024:.2f} MB")


def forward_fill_vault(
    vault_df: pd.DataFrame,
) -> pd.DataFrame:
    """Forward fill missing vault prices up to max_gap_hours.

    - For displaying, calculating metrics, etc. we want continuous time series
    - Align random sample interval to 1h

    :param vault_df:
        Price data for a single vault.

        Assume 1h price data.

    """
    assert isinstance(vault_df.index, pd.DatetimeIndex), f"Got: {type(vault_df.index)}"
    resampled = vault_df.resample("h").last().ffill()
    return resampled
