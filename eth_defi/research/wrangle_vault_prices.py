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

import pandas as pd

from eth_defi.chain import get_chain_name
from eth_defi.token import is_stablecoin_like

from eth_defi.vault.vaultdb import VaultDatabase

from tqdm.auto import tqdm


from IPython.display import display

#: For manual debugging, we process these vaults first
PRIORITY_SORT_IDS = [
    "8453-0x0d877dc7c8fa3ad980dfdb18b48ec9f8768359c4",
]


def assign_unique_names(
    vault_db: VaultDatabase,
    prices_df: pd.DataFrame,
    logger=print,
) -> pd.DataFrame:
    """Ensure all vaults have unique human-readable name.

    - Find duplicate vault names
    - Add a running counter to the name to make it unique
    """
    vaults_by_id = {f"{vault['_detection_data'].chain}-{vault['_detection_data'].address}": vault for vault in vault_db.values()}

    # We use name later as DF index, so we need to make sure they are unique
    counter = 1
    used_names = set()
    for id, vault in vaults_by_id.items():
        # TODO: hack
        # 40acres forgot to name their vault
        if vault["Name"] == "Vault":
            vault["Name"] == "40acres"

        if vault["Name"] in used_names:
            chain_name = get_chain_name(vault["_detection_data"].chain)
            vault["Name"] = f"{vault['Name']} ({chain_name}) #{counter}"
            counter += 1

        used_names.add(vault["Name"])

    logger(f"Fixed {counter} duplicate vault names")

    # Vaults are identified by their chain and address tuple, make this one human-readable column
    # to make DataFrame wrangling easier
    prices_df["id"] = prices_df["chain"].astype(str) + "-" + prices_df["address"].astype(str)
    prices_df["name"] = prices_df["id"].apply(lambda x: vaults_by_id[x]["Name"] if x in vaults_by_id else None)

    # 40acres fix - they did not name their vault,
    # More about this later
    prices_df["name"] = prices_df["name"].fillna("<unknown>")


def add_denormalised_vaut_data(
    vault_db: VaultDatabase,
    prices_df: pd.DataFrame,
    logger=print,
) -> pd.DataFrame:
    """Add denormalised data to the prices DataFrame.

    - Take data from vault database and duplicate it across every row
    - Add protocol name and event count columns
    """

    vaults_by_id = {f"{vault['_detection_data'].chain}-{vault['_detection_data'].address}": vault for vault in vault_db.values()}
    prices_df["event_count"] = prices_df["id"].apply(lambda x: vaults_by_id[x]["_detection_data"].deposit_count + vaults_by_id[x]["_detection_data"].redeem_count)
    prices_df["protocol"] = prices_df["id"].apply(lambda x: vaults_by_id[x]["Protocol"] if x in vaults_by_id else None)
    return prices_df


def filter_vaults_by_stablecoin(
    vault_db: VaultDatabase,
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

    usd_vaults = [v for v in vault_db.values() if is_stablecoin_like(v["Denomination"])]
    logger(f"We have {len(usd_vaults)} stablecoin-nominated vaults out of {len(vault_db)} total vaults")

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
    vault_db: VaultDatabase,
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
    vault_db: VaultDatabase,
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


def filter_outlier_share_prices(
    prices_df: pd.DataFrame,
    logger=print,
    max_diff=0.33,
) -> pd.DataFrame:
    """Filter out rows with share price that is too high.

    - Sometimes share price jump to an outlier value and back
    - Not sure what is causing this, bad manual reporting, oracle issues?
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

    Case
    """

    # Store unfiltered share prices for the later examination
    prices_df["raw_share_price"] = prices_df["share_price"]

    share_prices_fixed = 0

    def _clean_share_price_for_pair(prices_df: pd.DataFrame) -> pd.DataFrame:
        nonlocal share_prices_fixed

        prices_df = prices_df.copy()

        # Calculate forward and backward percentage change for each vault
        prices_df["pct_change_prev"] = prices_df.groupby("id")["share_price"].pct_change(fill_method=None).abs()
        prices_df["pct_change_next"] = prices_df.groupby("id")["share_price"].pct_change(-1, fill_method=None).abs()

        # Mark rows where both changes exceed the threshold (spike and recovery)
        abnormal_mask = (prices_df["pct_change_prev"] > max_diff) & (prices_df["pct_change_next"] > max_diff)

        # Replace abnormal share_price with average of previous and next
        for idx in prices_df[abnormal_mask].index:
            prev_idx = prices_df.index.get_loc(idx) - 1
            next_idx = prices_df.index.get_loc(idx) + 1
            if prev_idx >= 0 and next_idx < len(prices_df):
                prev_price = prices_df.iloc[prev_idx]["share_price"]
                next_price = prices_df.iloc[next_idx]["share_price"]
                prices_df.at[idx, "share_price"] = (prev_price + next_price) / 2
                share_prices_fixed += 1

        return prices_df

    filtered_all_df = prices_df.groupby("id", group_keys=True, sort=False).apply(_clean_share_price_for_pair)

    change_mask = (filtered_all_df["share_price"] != filtered_all_df["raw_share_price"]) & pd.notna(filtered_all_df["raw_share_price"])
    change_count = len(change_mask[change_mask == True])

    logger(f"Share prices fix count {share_prices_fixed}, filtered out {change_count:,} rows with abnormal share_price spikes (> {max_diff:.2%})")

    # groupby() added id as an MultiIndex(id, timestamp), but unwind this change back,
    # as other functions do not expect it
    filtered_all_df = filtered_all_df.droplevel("id")

    return filtered_all_df


def sort_and_index_vault_prices(
    prices_df: pd.DataFrame,
    priority_ids: list[str],
):
    """Set up the order of vaults for processing.

    - If we do debugging we want vaults we debug go first,
      as the pipeline takes several minutes to run
    """

    # Create a priority column for sorting
    prices_df["sort_priority"] = prices_df["id"].apply(lambda x: 0 if x in priority_ids else 1)

    # Sort by priority first, then by id and timestamp
    prices_df = prices_df.sort_values(by=["sort_priority", "id", "timestamp"]).drop("sort_priority", axis=1).set_index("timestamp")
    return prices_df


def process_raw_vault_scan_data(
    vault_db: VaultDatabase,
    prices_df: pd.DataFrame,
    logger=print,
    display: Callable = lambda x: None,
) -> pd.DataFrame:
    """Preprocess vault data for further analysis.

    - Assign unique names to vaults
    - Add denormalised vault data to prices DataFrame
    - Filter out non-stablecoin vaults
    - Calculate returns, rolling metrics
    """

    assign_unique_names(vault_db, prices_df, logger)

    prices_df = add_denormalised_vaut_data(vault_db, prices_df, logger)

    prices_df = sort_and_index_vault_prices(prices_df, PRIORITY_SORT_IDS)
    prices_df = filter_vaults_by_stablecoin(vault_db, prices_df, logger)
    # Disabled as low and does not result to any savings
    # prices_df = filter_unneeded_row(prices_df, logger)
    prices_df = filter_outlier_share_prices(prices_df, logger)
    prices_df = calculate_vault_returns(prices_df)

    prices_df = clean_returns(
        vault_db,
        prices_df,
        logger=logger,
        display=display,
    )
    prices_df = clean_by_tvl(
        vault_db,
        prices_df,
        logger,
    )
    return prices_df


def generate_cleaned_vault_datasets(
    vault_db_path=Path.home() / ".tradingstrategy" / "vaults" / "vault-db.pickle",
    price_df_path=Path.home() / ".tradingstrategy" / "vaults" / "vault-prices-1h.parquet",
    cleaned_price_df_path=Path.home() / ".tradingstrategy" / "vaults" / "cleaned-vault-prices-1h.parquet",
    logger=print,
    display=display,
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

    vault_db: VaultDatabase = pickle.load(vault_db_path.open("rb"))
    prices_df = pd.read_parquet(price_df_path)

    logger(f"We have {len(vault_db):,} vaults in the vault database and {len(prices_df):,} price rows in the raw prices DataFrame")

    enhanced_prices_df = process_raw_vault_scan_data(
        vault_db,
        prices_df,
        logger,
        display=display,
    )

    # Sort for better compression
    enhanced_prices_df = enhanced_prices_df.sort_values(by=["id", "timestamp"])

    enhanced_prices_df.to_parquet(
        cleaned_price_df_path,
        compression="zstd",
    )

    fsize = cleaned_price_df_path.stat().st_size
    logger(f"Saved cleaned vault prices to {cleaned_price_df_path}, total {len(enhanced_prices_df):,} rows, file size is {fsize / 1024 / 1024:.2f} MB")
