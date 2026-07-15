"""Clean vault price data.

.. _wrangle vault:

- Denormalise data to a single DataFrame
- Remove abnormalities in the price data
- Reduce data by removing hourly changes that are below our epsilon threshold
- Generate returns data

The input is the raw scanner parquet conforming to
:py:class:`~eth_defi.vault.base.RawVaultPriceRow`.
The output is a cleaned DataFrame conforming to
:py:class:`CleanedVaultPriceRow`, consumed by
:py:func:`~eth_defi.research.vault_metrics.calculate_lifetime_metrics`.
"""

import os
import pickle
import tempfile
import warnings
from bisect import bisect_right
from pathlib import Path
from typing import Callable, TypedDict

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from eth_typing import HexAddress
from IPython.display import display
from tqdm_loggable.auto import tqdm

from eth_defi.chain import get_chain_name
from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID
from eth_defi.token import is_stablecoin_like
from eth_defi.types import Percent
from eth_defi.vault.base import VaultSpec, verify_parquet_file
from eth_defi.vault.settlement_data import (
    merge_vault_settlements_into_cleaned_prices,
)
from eth_defi.vault.vaultdb import DEFAULT_RAW_PRICE_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_VAULT_DATABASE, VaultDatabase, VaultRow
from eth_defi.version_info import stamp_parquet_schema_metadata

#: NAV at or below this value counts as a complete Hypercore wipe-out.
HYPERCORE_ZERO_NAV_EPSILON = 0.000001

#: New capital must reach this NAV before a recapitalised vault is tracked again.
MIN_HYPERCORE_RECAPITALISATION_ASSETS = 1_000.0

#: Ignore isolated zero-NAV observations that recover before this delay.
MIN_HYPERCORE_RECAPITALISATION_RECOVERY_DELAY = pd.Timedelta(days=7)


class CleanedVaultPriceRow(TypedDict, total=False):
    """Schema for a single row in the cleaned vault price DataFrame.

    This is the enriched format produced by the cleaning pipeline in this module
    and consumed by
    :py:func:`~eth_defi.research.vault_metrics.calculate_lifetime_metrics`.

    It extends :py:class:`~eth_defi.vault.base.RawVaultPriceRow` with
    denormalised metadata columns (``id``, ``name``, ``event_count``,
    ``protocol``) and computed columns (``returns_1h``).
    The DataFrame uses a :py:class:`~pandas.DatetimeIndex` built from the
    ``timestamp`` column.

    Columns are grouped by availability:

    - **General** columns are present for all vault protocols.
    - **ERC-4626 only** columns come from on-chain ERC-4626 calls and are
      NaN / empty for native protocols.
    - **Lending only** columns are populated for lending protocol vaults
      (IPOR, Euler, Morpho, Gearbox, etc.) and NaN for others.
    - **Hypercore only** columns come from the Hyperliquid native vault API
      and are NaN for all other protocols.
    - **Native protocol flow** columns are populated for native protocols
      that provide daily deposit/withdrawal data (Hypercore, GRVT, Lighter,
      Hibachi) and NaN for ERC-4626 vaults.
    """

    # -- General columns (all protocols) --

    #: EVM chain id (e.g. ``1`` for Ethereum, ``8453`` for Base).
    #:
    #: Native (non-EVM) protocols use synthetic in-house chain ids:
    #:
    #: - ``9999`` — Hypercore (native Hyperliquid vaults),
    #:   see :py:data:`~eth_defi.hyperliquid.constants.HYPERCORE_CHAIN_ID`
    #: - ``9998`` — Lighter DEX pools,
    #:   see :py:data:`~eth_defi.lighter.constants.LIGHTER_CHAIN_ID`
    #: - ``9997`` — Hibachi native vaults,
    #:   see :py:data:`~eth_defi.hibachi.constants.HIBACHI_CHAIN_ID`
    #: - ``325`` — GRVT (Gravity Markets),
    #:   see :py:data:`~eth_defi.grvt.constants.GRVT_CHAIN_ID`
    #:
    #: The full mapping lives in :py:data:`~eth_defi.chain.CHAIN_NAMES`.
    #:
    #: General — present for all protocols.
    chain: int

    #: Vault contract address, lowercase.
    #:
    #: Address formats vary by protocol:
    #:
    #: - EVM vaults: ``0x``-prefixed hex (e.g. ``"0xabcd..."``)
    #: - Hypercore: ``0x``-prefixed hex (Hyperliquid vault addresses)
    #: - GRVT: platform-specific id (e.g. ``"vlt:xxx"``)
    #: - Lighter: synthetic id (e.g. ``"lighter-pool-281474976710654"``)
    #: - Hibachi: synthetic id (e.g. ``"hibachi-vault-2"``)
    #:
    #: See :py:func:`~eth_defi.utils.is_good_multichain_address` for
    #: the validation function that accepts all these formats.
    #:
    #: General — present for all protocols.
    address: str

    #: Block number of the on-chain read.
    #: For native protocols without blocks this is a synthetic sequence number.
    #:
    #: General — present for all protocols.
    block_number: int

    #: Naive UTC timestamp (also used as the DatetimeIndex).
    #:
    #: General — present for all protocols.
    timestamp: "pd.Timestamp"

    #: Share price in denomination token units.
    #:
    #: For ERC-4626 vaults this is read directly from the contract
    #: (``convertToAssets(1e decimals)``).
    #: GRVT, Lighter, and Hibachi provide native share prices from their
    #: respective APIs.
    #: Hypercore (native Hyperliquid vaults) does not expose a historical share
    #: price or supply. Its cleaned value is a PnL/NAV economic-performance
    #: index starting at ``1.0`` for each retained capital epoch. The scanner's
    #: synthetic input remains available in ``raw_share_price``.
    #:
    #: General — present for all protocols.
    share_price: float

    #: Total assets under management (TVL) in denomination token units.
    #:
    #: General — present for all protocols.
    total_assets: float

    #: Total supply of vault share tokens.
    #: Hypercore has no exposed historical token supply; its value is synthetic
    #: index units calculated as ``total_assets / share_price`` and must not be
    #: interpreted as an on-chain share count.
    #:
    #: General — present for all protocols.
    total_supply: float

    #: Performance fee at time of read (e.g. 0.20 = 20%). NaN if unknown.
    #:
    #: General — present for all protocols.
    performance_fee: float

    #: Management fee at time of read (e.g. 0.02 = 2%). NaN if unknown.
    #:
    #: General — present for all protocols.
    management_fee: float

    #: Comma-separated RPC error messages, or empty string if no errors.
    #:
    #: Example values: ``"total_supply call failed"``,
    #: ``"total_assets zero: 0"``, ``"total_supply call missing"``.
    #: Always empty for native protocols.
    #:
    #: General — present for all protocols (always empty for native protocols).
    errors: str

    #: Dynamic poll frequency used when taking this sample.
    #: Empty string if not set.
    #:
    #: Example values: ``"1h"``, ``"4h"``, ``"24h"``.
    #: The scanner adjusts frequency based on vault TVL and activity;
    #: low-TVL vaults may be polled less frequently.
    #:
    #: General — present for all protocols (may be empty for native protocols).
    vault_poll_frequency: str

    # -- Denormalised metadata columns added by the cleaning pipeline --

    #: Vault identifier string: ``"<chain_id>-<address>"``.
    #:
    #: General — present for all protocols.
    id: str

    #: Human-readable vault name (unique within the dataset).
    #:
    #: General — present for all protocols.
    name: str

    #: Total deposit + redeem events observed for this vault.
    #:
    #: Zero if the protocol does not support on-chain deposit/redeem event tracking
    #: (e.g. native vaults like GRVT, Lighter, Hibachi).
    #:
    #: General — present for all protocols.
    event_count: int

    #: Protocol name (e.g. ``"Morpho"``, ``"Yearn"``, ``"Hyperliquid"``).
    #:
    #: General — present for all protocols.
    protocol: str

    # -- Computed columns --

    #: Hourly return as ``pct_change()`` of ``share_price`` within each vault group.
    #: Despite the name, for native protocols (Hypercore, GRVT, Lighter) the
    #: interval may be daily or irregular — the column name is kept for
    #: backward compatibility.
    #:
    #: General — present for all protocols.
    returns_1h: float

    # -- Vault state pass-through columns (from VAULT_STATE_COLUMNS) --

    #: Maximum deposit amount allowed (ERC-4626 ``maxDeposit``). NaN if unknown.
    #:
    #: ERC-4626 only — NaN for native protocols.
    max_deposit: float

    #: Maximum redeem amount allowed (ERC-4626 ``maxRedeem``). NaN if unknown.
    #:
    #: ERC-4626 only — NaN for native protocols.
    max_redeem: float

    #: Whether deposits were open: ``"true"``, ``"false"``, or ``""``.
    #:
    #: ERC-4626 only — empty for native protocols.
    deposits_open: str

    #: Whether redemptions were open: ``"true"``, ``"false"``, or ``""``.
    #:
    #: ERC-4626 only — empty for native protocols.
    redemption_open: str

    #: Whether the vault was actively trading: ``"true"``, ``"false"``, or ``""``.
    #: Currently only supported for D2 Finance vaults.
    #:
    #: Protocol-specific — empty for most protocols.
    trading: str

    #: Available liquidity for immediate withdrawal in denomination token units. NaN if not applicable.
    #:
    #: Lending only — IPOR, Euler, Morpho, Gearbox, etc. NaN for other protocols.
    available_liquidity: float

    #: Utilisation ratio (0.0–1.0) for lending vaults. NaN if not applicable.
    #:
    #: .. warning::
    #:
    #:    This metric measures **capital deployment efficiency**
    #:    (how much of the vault's AUM is lent out), not redeemable liquidity.
    #:    For single-market vaults (Euler EVK, Gearbox, Silo) high utilisation
    #:    does mean low available liquidity.
    #:    For multi-market aggregators (Morpho, Euler Earn, IPOR) a vault can
    #:    show 95% utilisation yet have substantial instantly redeemable
    #:    liquidity in low-utilisation underlying markets.
    #:    See ``README-vault-redeemable.md`` and ``README-utilisation.md``
    #:    in :py:mod:`eth_defi.erc_4626.vault_protocol` for details.
    #:
    #: Lending only — IPOR, Euler, Morpho, Gearbox, etc. NaN for other protocols.
    utilisation: float

    #: Unified reason why deposits are closed (e.g. ``"Vault deposits disabled"``).
    #: Empty string if deposits are open. Derived from ``deposits_open`` for
    #: ERC-4626 vaults or set directly by native protocol exporters.
    #:
    #: General — present for all protocols (empty when deposits are open).
    deposit_closed_reason: str

    #: When this price row was actually written/fetched (naive UTC). NaT for old data.
    #:
    #: General — present for all protocols.
    written_at: "pd.Timestamp"

    #: Latest asynchronous vault settlement timestamp in the interval ending at this price row.
    #:
    #: General — populated by merging ``vault-settlements.duckdb`` after
    #: cleaning. ``NaT`` means no known settlement occurred since the previous
    #: cleaned price row.
    vault_settlement_at: "pd.Timestamp"

    # -- Hypercore only columns --
    # Populated for native Hyperliquid vaults (chain 9999). NaN for all other protocols.

    #: Fraction of vault assets controlled by the leader (0.0–1.0).
    #:
    #: Hypercore only — NaN for all other protocols.
    leader_fraction: float

    #: Commission rate charged by the vault leader (0.0–1.0).
    #:
    #: Hypercore only — NaN for all other protocols.
    leader_commission: float

    #: Number of followers in the vault.
    #:
    #: Hypercore only — NaN for all other protocols.
    follower_count: float

    #: Cumulative PnL of the vault leader account in USD.
    #:
    #: Hypercore only — NaN for all other protocols.
    account_pnl: float

    #: Cumulative trading volume of the vault in USD.
    #:
    #: Hypercore only — NaN for all other protocols.
    cumulative_volume: float

    #: Hypercore scanner source: ``"daily"`` or ``"hf"``.
    #:
    #: Used during wrangling to reconcile overlapping synthetic share prices.
    #: Hypercore only — NaN for all other protocols.
    hypercore_source: str

    #: The row starts a new performance epoch after a complete wipe-out.
    #:
    #: Hypercore only — false for ordinary observations.
    epoch_reset: bool

    #: Provenance of the cleaned Hypercore PnL/NAV approximation.
    #:
    #: ``approximated_pnl_nav`` marks a daily economic checkpoint,
    #: ``approximated_pnl_nav_clipped`` a positive checkpoint capped at 100%,
    #: ``approximated_pnl_nav_wipe_out`` a terminal NAV-corroborated loss, and
    #: ``approximated_pnl_nav_carried`` an ordinary non-checkpoint row.
    #: ``deferred_pnl_nav`` means inputs were missing and
    #: ``deferred_pnl_nav_outlier`` means an uncorroborated negative PnL step
    #: was not allowed to zero a funded vault.
    #: Hypercore only — empty for ordinary observations and other protocols.
    hypercore_repair_status: str

    # -- Native protocol flow columns --
    # Populated for native protocols with daily deposit/withdrawal data
    # (Hypercore, GRVT, Lighter, Hibachi). NaN for ERC-4626 vaults.

    #: Number of deposit events in the latest day.
    #:
    #: Native protocol flow — Hypercore, GRVT, Lighter, Hibachi. NaN for ERC-4626 vaults.
    daily_deposit_count: float

    #: Number of withdrawal events in the latest day.
    #:
    #: Native protocol flow — Hypercore, GRVT, Lighter, Hibachi. NaN for ERC-4626 vaults.
    daily_withdrawal_count: float

    #: Total USD deposited in the latest day.
    #:
    #: Native protocol flow — Hypercore, GRVT, Lighter, Hibachi. NaN for ERC-4626 vaults.
    daily_deposit_usd: float

    #: Total USD withdrawn in the latest day.
    #:
    #: Native protocol flow — Hypercore, GRVT, Lighter, Hibachi. NaN for ERC-4626 vaults.
    daily_withdrawal_usd: float


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


#: Vault state and pass-through columns added by the historical scanner.
#: Ensure these are always present in cleaned data,
#: even when processing old scan data that lacks them.
#: See :py:class:`CleanedVaultPriceRow` for column semantics.
VAULT_STATE_COLUMNS = {
    "max_deposit": float("nan"),
    "max_redeem": float("nan"),
    "deposits_open": "",
    "redemption_open": "",
    "trading": "",
    "available_liquidity": float("nan"),
    "utilisation": float("nan"),
    "leader_fraction": float("nan"),
    "leader_commission": float("nan"),
    "daily_deposit_count": float("nan"),
    "daily_withdrawal_count": float("nan"),
    "daily_deposit_usd": float("nan"),
    "daily_withdrawal_usd": float("nan"),
    "follower_count": float("nan"),
    "account_pnl": float("nan"),
    "cumulative_volume": float("nan"),
    # PyArrow does not accept None for string columns,
    # use empty string as the default for deposit_closed_reason
    "deposit_closed_reason": "",
    # When this price row was actually written/fetched (naive UTC).
    # NaT for old data that predates this column.
    "written_at": pd.NaT,
    # Hypercore epoch marker. Set when wrangling discards a complete prior
    # wipe-out epoch and begins from recapitalised capital.
    "epoch_reset": False,
    # Source-overlap repair outcome. Empty for ordinary and non-Hypercore rows.
    "hypercore_repair_status": "",
    # Latest asynchronous vault settlement timestamp in the interval ending at
    # this price row. Merged from vault-settlements.duckdb after cleaning.
    "vault_settlement_at": pd.NaT,
}


def ensure_vault_state_columns(prices_df: pd.DataFrame) -> pd.DataFrame:
    """Ensure vault state columns are present in the DataFrame.

    - Adds missing columns with default values for backward compatibility
      with raw scan data generated before these fields were added.
    """
    for col, default in VAULT_STATE_COLUMNS.items():
        if col not in prices_df.columns:
            prices_df[col] = default
    return prices_df


def derive_deposit_closed_reason(prices_df: pd.DataFrame) -> pd.DataFrame:
    """Derive unified ``deposit_closed_reason`` from protocol-specific columns.

    For Hyperliquid vaults, ``deposit_closed_reason`` is already set by
    :py:func:`~eth_defi.hyperliquid.vault_data_export.build_raw_prices_dataframe`
    with specific reason strings.

    For ERC-4626 vaults, the ``deposits_open`` string column ("true"/"false"/"")
    is converted to a generic reason.

    :param prices_df:
        DataFrame with ``deposit_closed_reason`` and ``deposits_open`` columns.
    :return:
        DataFrame with ``deposit_closed_reason`` filled in for both vault types.
    """
    if "deposit_closed_reason" not in prices_df.columns:
        # PyArrow does not accept None for string columns, use empty string
        prices_df["deposit_closed_reason"] = ""
    else:
        # Ensure compatible dtype: convert None/NaN to empty string
        # because PyArrow string columns do not accept null assignment via .loc
        prices_df["deposit_closed_reason"] = prices_df["deposit_closed_reason"].astype(object).fillna("").astype(str)

    if "deposits_open" not in prices_df.columns:
        return prices_df

    # Fill in reason for ERC-4626 rows where deposits_open == "false"
    # but deposit_closed_reason is not yet set (Hyperliquid rows already have it).
    mask = (prices_df["deposit_closed_reason"] == "") & (prices_df["deposits_open"] == "false")
    prices_df.loc[mask, "deposit_closed_reason"] = "Vault deposits disabled"

    return prices_df


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
    # NOTE: ``returns_1h`` is a misnomer.  The column is ``pct_change()``
    # between consecutive rows regardless of their actual time delta.
    # For EVM chains scanned at 1h frequency the name is accurate, but
    # for native protocols (Hypercore, GRVT, Lighter) the rows may be
    # spaced at daily or irregular intervals — producing ~24h or
    # variable-interval returns labelled "1h".  Renaming would break
    # every downstream consumer so the name is kept for compatibility.
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

    # Kept for API compatibility with notebook and script callers.
    del display

    returns_df = prices_df

    # Hypercore's bounded PnL/NAV approximation runs before this generic
    # cleaner. Retain its audited economic return instead of replacing it with
    # a value that disagrees with the cleaned performance index.
    high_returns_mask = returns_df[returns_col] > outlier_threshold
    if "chain" in returns_df.columns:
        high_returns_mask &= returns_df["chain"] != HYPERCORE_CHAIN_ID
    outlier_returns = returns_df[high_returns_mask]

    # Sort by return value (highest first)
    outlier_returns = outlier_returns.sort_values(by=returns_col, ascending=False)

    # Show a compact summary instead of dumping sample DataFrames to logs.
    if len(outlier_returns) > 0:
        outlier_counts = outlier_returns.groupby("name").size().sort_values(ascending=False)
        top_outlier_counts = ", ".join(f"{name}={count:,}" for name, count in outlier_counts.head(3).items())
        logger(f"Found {len(outlier_returns):,} outlier returns > {outlier_threshold:%}; top vaults by count: {top_outlier_counts}")
    else:
        logger(f"Found 0 outlier returns > {outlier_threshold:%}")

    # Clean up obv too high returns
    returns_df.loc[high_returns_mask, returns_col] = 0

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

    Hypercore keeps its PnL/NAV price-derived return because rewriting the
    return alone would make profit disagree with the cleaned share price. Its
    low-TVL observations still receive ``tvl_filtering_mask=True`` so
    investment-suitability consumers can exclude them. Other protocols retain
    the existing zero-return behaviour.

    :param rows:
        Vault metadata keyed by address. Retained for compatibility with the
        existing cleaner interface.
    :param prices_df:
        Timestamp-indexed price rows containing ``id``, ``chain``,
        ``total_assets``, and the selected return column.
    :param logger:
        Notebook, console, or structured-log adapter accepting one message.
    :param tvl_threshold_min:
        Absolute minimum NAV in USD.
    :param tvl_threshold_max:
        Absolute maximum NAV in USD.
    :param tvl_threshold_min_dynamic:
        Minimum NAV as a fraction of the vault's all-time average NAV.
    :param returns_col:
        Name of the return column to clean.
    :return:
        The input frame with TVL audit columns and protocol-appropriate return
        cleaning applied.
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

    # Hypercore returns are already bounded and audited by the PnL/NAV index.
    # Keep its price-derived return internally consistent, while retaining the
    # TVL mask so investment-suitability consumers can exclude low-capital rows.
    return_cleaning_mask = mask
    if "chain" in returns_df.columns:
        return_cleaning_mask = mask & (returns_df["chain"] != HYPERCORE_CHAIN_ID)

    # Set generic protocol returns to zero where the mask is true.
    returns_df.loc[return_cleaning_mask, returns_col] = 0
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


def remove_inactive_lead_time(
    prices_df: pd.DataFrame,
    logger=print,
) -> pd.DataFrame:
    """Remove initial inactive period from each vault's price history.

    - At the beginning of a vault's lifecycle, total supply may remain constant
      while the vault is inactive (e.g., 1, 1000, etc.)
    - When the vault activates, the share price may jump, causing abnormal returns
    - This function removes the initial rows where total_supply hasn't changed
    - Uses exact equality for comparison
    - Skips initial rows with zero or NaN total_supply to find first valid value

    :param prices_df:
        Price data with 'id' and 'total_supply' columns.
        Assumes data is sorted by timestamp within each vault.

    :return:
        DataFrame with inactive lead time removed for each vault
    """

    original_row_count = len(prices_df)
    rows_removed = 0
    vaults_affected = 0

    def _find_first_supply_change(group: pd.DataFrame) -> pd.DataFrame:
        """Find the first row where total_supply changes from its initial value."""
        nonlocal rows_removed
        nonlocal vaults_affected

        if len(group) <= 1:
            return group

        # Skip initial rows with zero or NaN total_supply to find first valid value
        valid_supply_mask = (group["total_supply"] > 0) & pd.notna(group["total_supply"])
        if not valid_supply_mask.any():
            # No valid total_supply values - keep all data
            return group

        first_valid_idx = valid_supply_mask.idxmax()
        first_valid_loc = group.index.get_loc(first_valid_idx)
        initial_supply = group.iloc[first_valid_loc]["total_supply"]

        # Find the first index where total_supply differs from initial value
        # Only consider rows from first_valid_loc onwards
        remaining_group = group.iloc[first_valid_loc:]
        supply_changed_mask = remaining_group["total_supply"] != initial_supply

        if not supply_changed_mask.any():
            # Total supply never changed after initial valid value - keep from first valid
            if first_valid_loc > 0:
                vaults_affected += 1
                rows_removed += first_valid_loc
            return remaining_group

        # Get the index of the first change
        first_change_idx = supply_changed_mask.idxmax()
        first_change_loc = remaining_group.index.get_loc(first_change_idx)

        # Calculate total rows to remove (invalid initial rows + constant supply rows)
        total_lead_rows = first_valid_loc + first_change_loc

        if total_lead_rows > 0:
            vaults_affected += 1
            rows_removed += total_lead_rows

        # Return only rows from the first change onwards
        return remaining_group.iloc[first_change_loc:]

    filtered_df = prices_df.groupby("id", group_keys=True, sort=False).apply(
        _find_first_supply_change,
        include_groups=False,
    )

    # groupby() added id as a MultiIndex level, unwind this back
    # as other functions do not expect it
    if isinstance(filtered_df.index, pd.MultiIndex):
        filtered_df = filtered_df.reset_index(level="id")

    logger(f"Removed inactive lead time: {original_row_count:,} -> {len(filtered_df):,} rows ({rows_removed:,} removed from {vaults_affected} vaults)")

    return filtered_df


def approximate_hypercore_share_prices_from_pnl_nav(  # noqa: PLR0914
    prices_df: pd.DataFrame,
    logger: Callable[[str], None] = print,
    max_positive_return: Percent = 1.0,
) -> pd.DataFrame:
    """Build an actionable Hypercore economic-performance index from PnL and NAV.

    Hyperliquid does not expose historical vault share supply or an investable
    unit price through its
    `vaultDetails API <https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint#retrieve-information-about-a-vault>`__.
    Its rolling NAV and PnL windows can refresh at different timestamps, so the
    scanner-derived ERC-4626-like supply can change units between daily and HF
    reads. The July 2026 production investigation found that this left raw
    multi-hundred-percent moves in cleaned data and that a partial daily/HF
    repair itself created Order Block Hunter's ``+275.4%`` clean return.
    Ledger flows prove NAV accounting for some intervals, but their
    intra-period ordering is unavailable and therefore cannot recover exact
    time-weighted investor returns.

    The cleaned Hypercore price is consequently a conservative performance
    index. One freshest usable checkpoint is selected per UTC date. Between
    checkpoints, cumulative account-PnL change is divided by the larger of
    opening NAV, closing NAV, and one dollar; the resulting returns are
    compounded from ``1.0`` within each recapitalisation epoch. This denominator
    prevents unknown capital-flow timing and small NAV from manufacturing
    performance. Positive returns are capped at ``max_positive_return``. A
    return at or below ``-100%`` is accepted only when NAV is zero and does not
    recover in the same epoch; otherwise the price is carried because applying
    one questionable negative PnL baseline would permanently zero the index.
    Non-checkpoint rows are also carried, avoiding duplicate daily/HF returns
    without interpolating future information backwards.

    Input uses a timestamp index and requires ``id`` (string), ``chain``
    (integer), ``share_price`` (float), ``total_assets`` (float), and cumulative
    PnL as either exported ``account_pnl`` (float) or scanner-shaped
    ``cumulative_pnl`` (float). Optional ``written_at`` timestamps select the
    freshest scanner batch, ``hypercore_source`` strings break otherwise equal
    ties in favour of HF, and boolean ``epoch_reset`` values separate
    performance epochs. The output preserves scanner values in
    ``raw_share_price``, writes audit values to ``hypercore_repair_status``, and
    recalculates Hypercore ``total_supply`` as synthetic index units so that
    ``total_assets == share_price * total_supply`` remains true. Hypercore
    ``total_supply`` is not an actual token supply.

    :param prices_df:
        Timestamp-indexed cleaned-price input containing the columns described
        above. Rows must already be ordered by vault and timestamp.
    :param logger:
        Notebook, console, or structured-log adapter accepting one message.
    :param max_positive_return:
        Maximum approximate return applied at one daily checkpoint. The default
        is ``1.0`` (100%).
    :return:
        A copy with every Hypercore row expressed on its PnL/NAV performance
        index; all non-Hypercore rows are unchanged.
    """
    hypercore_mask = prices_df["chain"] == HYPERCORE_CHAIN_ID
    if not hypercore_mask.any():
        return prices_df

    if max_positive_return <= 0:
        raise ValueError(f"max_positive_return must be positive, got {max_positive_return!r}")

    pnl_column = "account_pnl" if "account_pnl" in prices_df.columns else "cumulative_pnl"
    required_columns = {"id", "share_price", "total_assets", pnl_column}
    missing_columns = required_columns.difference(prices_df.columns)
    if missing_columns:
        raise ValueError(f"Cannot approximate Hypercore share prices; missing columns: {sorted(missing_columns)}")

    prices_df = prices_df.copy()
    if "raw_share_price" not in prices_df.columns:
        prices_df["raw_share_price"] = prices_df["share_price"]
    if "hypercore_repair_status" not in prices_df.columns:
        prices_df["hypercore_repair_status"] = ""

    share_price_col = prices_df.columns.get_loc("share_price")
    hypercore_positions = np.flatnonzero(hypercore_mask.to_numpy())
    clean_share_prices = prices_df["share_price"].to_numpy(dtype=float, copy=True)
    repair_statuses = prices_df["hypercore_repair_status"].astype("string").fillna("").to_numpy(dtype=object, copy=True)
    synthetic_supplies = prices_df["total_supply"].to_numpy(dtype=float, copy=True) if "total_supply" in prices_df.columns else None
    all_timestamps = pd.DatetimeIndex(prices_df.index)
    all_nav = prices_df["total_assets"].to_numpy(dtype=float)
    all_pnl = prices_df[pnl_column].to_numpy(dtype=float)
    all_source = prices_df["hypercore_source"].astype("string").fillna("").to_numpy(dtype=str) if "hypercore_source" in prices_df.columns else np.full(len(prices_df), "", dtype=str)
    all_written_at = pd.to_datetime(prices_df["written_at"], errors="coerce").to_numpy(dtype="datetime64[ns]").astype("int64") if "written_at" in prices_df.columns else np.full(len(prices_df), pd.NaT.value, dtype=np.int64)
    all_epoch_reset = prices_df["epoch_reset"].fillna(False).to_numpy(dtype=bool) if "epoch_reset" in prices_df.columns else np.zeros(len(prices_df), dtype=bool)

    checkpoint_count = 0
    carried_count = 0
    missing_count = 0
    clipped_count = 0
    deferred_outlier_count = 0
    wipe_out_count = 0
    affected_vaults = 0

    for _vault_id, relative_positions in prices_df.loc[hypercore_mask, ["id"]].groupby("id", sort=False).indices.items():
        positions = hypercore_positions[np.asarray(relative_positions, dtype=int)]
        timestamps = all_timestamps[positions]
        timestamp_ns = timestamps.to_numpy(dtype="datetime64[ns]").astype("int64")
        day_ns = timestamps.normalize().to_numpy(dtype="datetime64[ns]").astype("int64")
        nav = all_nav[positions]
        pnl = all_pnl[positions]
        source = all_source[positions]
        hf_priority = source == "hf"
        written_at = all_written_at[positions]
        epoch_reset = all_epoch_reset[positions]
        epoch_number = np.cumsum(epoch_reset)

        clean_price = np.full(len(positions), np.nan, dtype=float)
        repair_status = np.full(len(positions), "", dtype=object)

        for epoch in np.unique(epoch_number):
            epoch_positions = np.flatnonzero(epoch_number == epoch)
            epoch_days = day_ns[epoch_positions]
            usable = np.isfinite(nav[epoch_positions]) & np.isfinite(pnl[epoch_positions])
            usable_positions = epoch_positions[usable]

            if len(usable_positions):
                checkpoint_order = np.lexsort(
                    (
                        hf_priority[usable_positions].astype(np.int8),
                        timestamp_ns[usable_positions],
                        written_at[usable_positions],
                        day_ns[usable_positions],
                    )
                )
                ordered_positions = usable_positions[checkpoint_order]
                ordered_days = day_ns[ordered_positions]
                latest_per_day = np.r_[ordered_days[1:] != ordered_days[:-1], True]
                checkpoints = ordered_positions[latest_per_day]
            else:
                checkpoints = np.asarray([], dtype=int)
            checkpoint_days = day_ns[checkpoints] if len(checkpoints) else np.asarray([], dtype=np.int64)
            day_has_checkpoint = np.isin(epoch_days, checkpoint_days)
            repair_status[epoch_positions[day_has_checkpoint]] = "approximated_pnl_nav_carried"
            repair_status[epoch_positions[~day_has_checkpoint]] = "deferred_pnl_nav"
            missing_count += int((~day_has_checkpoint).sum())

            if len(checkpoints) == 0:
                clean_price[epoch_positions] = 1.0
                continue

            checkpoint_nav = nav[checkpoints]
            checkpoint_pnl = pnl[checkpoints]
            raw_returns = np.zeros(len(checkpoints), dtype=float)
            if len(checkpoints) > 1:
                capital_base = np.maximum.reduce(
                    [
                        checkpoint_nav[:-1],
                        checkpoint_nav[1:],
                        np.ones(len(checkpoints) - 1, dtype=float),
                    ]
                )
                raw_returns[1:] = np.diff(checkpoint_pnl) / capital_base

            applied_returns = raw_returns.copy()
            positive_clipped = raw_returns > max_positive_return
            applied_returns[positive_clipped] = max_positive_return

            funded_absorbing_loss = raw_returns <= -1.0
            corroborated_wipe_out = np.zeros(len(checkpoints), dtype=bool)
            positive_nav = np.isfinite(nav[epoch_positions]) & (nav[epoch_positions] > HYPERCORE_ZERO_NAV_EPSILON)
            later_positive_nav = np.r_[np.maximum.accumulate(positive_nav[::-1])[::-1][1:], False]
            for checkpoint_number in np.flatnonzero(funded_absorbing_loss):
                checkpoint_position = checkpoints[checkpoint_number]
                epoch_position = int(np.searchsorted(epoch_positions, checkpoint_position))
                corroborated_wipe_out[checkpoint_number] = checkpoint_nav[checkpoint_number] <= HYPERCORE_ZERO_NAV_EPSILON and not later_positive_nav[epoch_position]

            deferred_absorbing_loss = funded_absorbing_loss & ~corroborated_wipe_out
            applied_returns[deferred_absorbing_loss] = 0.0
            applied_returns[corroborated_wipe_out] = -1.0
            checkpoint_prices = np.cumprod(1.0 + applied_returns)

            checkpoint_status = np.full(len(checkpoints), "approximated_pnl_nav", dtype=object)
            checkpoint_status[positive_clipped] = "approximated_pnl_nav_clipped"
            checkpoint_status[deferred_absorbing_loss] = "deferred_pnl_nav_outlier"
            checkpoint_status[corroborated_wipe_out] = "approximated_pnl_nav_wipe_out"
            repair_status[checkpoints] = checkpoint_status

            checkpoint_lookup = np.searchsorted(checkpoints, epoch_positions, side="right") - 1
            has_previous_checkpoint = checkpoint_lookup >= 0
            epoch_prices = np.ones(len(epoch_positions), dtype=float)
            epoch_prices[has_previous_checkpoint] = checkpoint_prices[checkpoint_lookup[has_previous_checkpoint]]
            clean_price[epoch_positions] = epoch_prices

            checkpoint_count += len(checkpoints)
            carried_count += int(day_has_checkpoint.sum()) - len(checkpoints)
            clipped_count += int(positive_clipped.sum())
            deferred_outlier_count += int(deferred_absorbing_loss.sum())
            wipe_out_count += int(corroborated_wipe_out.sum())

        clean_share_prices[positions] = clean_price
        repair_statuses[positions] = repair_status
        if synthetic_supplies is not None:
            valid_supply = np.isfinite(nav) & np.isfinite(clean_price) & (clean_price > 0)
            synthetic_supply = synthetic_supplies[positions].copy()
            synthetic_supply[valid_supply] = nav[valid_supply] / clean_price[valid_supply]
            zero_supply = np.isfinite(nav) & (nav <= HYPERCORE_ZERO_NAV_EPSILON) & (clean_price == 0)
            synthetic_supply[zero_supply] = 0.0
            synthetic_supplies[positions] = synthetic_supply
        affected_vaults += 1

    prices_df.iloc[:, share_price_col] = clean_share_prices
    prices_df["hypercore_repair_status"] = repair_statuses
    if synthetic_supplies is not None:
        prices_df["total_supply"] = synthetic_supplies

    logger(f"Approximated Hypercore economic share prices for {affected_vaults:,} vaults using {checkpoint_count:,} daily PnL/NAV checkpoints; carried {carried_count:,} duplicate rows, deferred {missing_count:,} missing-input rows and {deferred_outlier_count:,} uncorroborated losses, capped {clipped_count:,} gains and recorded {wipe_out_count:,} terminal wipe-outs")
    return prices_df


def discard_hypercore_pre_recapitalisation_history(  # noqa: PLR0914
    prices_df: pd.DataFrame,
    logger=print,
    min_recapitalisation_assets: float = MIN_HYPERCORE_RECAPITALISATION_ASSETS,
    min_recovery_delay: pd.Timedelta = MIN_HYPERCORE_RECAPITALISATION_RECOVERY_DELAY,
) -> pd.DataFrame:
    """Start a recapitalised Hypercore vault at its new meaningful capital base.

    A complete wipe-out followed by new deposits cannot be represented by one
    continuous share-price series. The old investors have a -100% return,
    while the new investors must not inherit the destroyed share supply. When
    a vault has meaningful NAV, reaches zero, and does not regain *any* positive
    NAV until after ``min_recovery_delay``, discard its earlier observations
    from the *cleaned* output. The raw parquet remains unchanged.

    Recovery duration and the new tracking threshold are intentionally separate.
    The delay is measured to the first value above
    :py:data:`HYPERCORE_ZERO_NAV_EPSILON`, even when that value is below
    ``min_recapitalisation_assets``. This prevents a sequence such as
    ``$2,000 -> $0 -> $900 next day -> $1,000 after seven days`` from erasing
    valid history merely because the recovery crossed the display threshold
    later. Once a durable recovery is established, the first retained
    observation must have at least ``min_recapitalisation_assets`` in NAV and
    is marked ``epoch_reset``.

    Raw scanner ``epoch_reset`` values are cleared before applying this rule.
    They mark arbitrary resets of the reconstructed synthetic supply, including
    funded vaults, and are not evidence of an economic wipe-out. Only the
    duration/NAV-qualified marker produced here may split the cleaned
    performance index.

    The July 2026 production snapshot contained four qualifying episodes across
    569 Hypercore vaults. HODL My Perps, HLP Liquidator, Rehobot LR, and Sifu all
    still qualify when measuring the delay to the first positive NAV, removing
    369 rows from cleaned output. The stricter definition was chosen because the
    same snapshot contained hundreds of transient zero observations which must
    not reset lifetime performance.

    :param prices_df:
        Vault price data indexed by timestamp, with ``id``, ``chain``, and
        ``total_assets`` columns. It must be sorted by vault and timestamp.
    :param logger:
        Notebook or console logging function.
    :param min_recapitalisation_assets:
        Minimum NAV in USD needed before tracking the new investment epoch.
    :param min_recovery_delay:
        Minimum elapsed time between zero NAV and the first later positive NAV.
    :return:
        Price data without the superseded pre-recapitalisation epochs.
    """
    hypercore_mask = prices_df["chain"] == HYPERCORE_CHAIN_ID
    if not hypercore_mask.any():
        return prices_df

    prices_df = prices_df.copy()
    if "epoch_reset" not in prices_df.columns:
        epoch_reset_values = np.zeros(len(prices_df), dtype=bool)
    else:
        # Scanner epoch resets only describe a reconstructed synthetic-supply
        # boundary. They are not evidence of a durable economic wipe-out.
        # Rebuild this marker exclusively from the duration/NAV rule below.
        epoch_reset_values = prices_df["epoch_reset"].fillna(False).to_numpy(dtype=bool, copy=True)
        epoch_reset_values[hypercore_mask.to_numpy()] = False
    if "raw_share_price" not in prices_df.columns:
        prices_df["raw_share_price"] = prices_df["share_price"]

    remove_mask = np.zeros(len(prices_df), dtype=bool)
    epoch_reset_positions: list[int] = []
    hypercore_positions = np.flatnonzero(hypercore_mask.to_numpy())
    all_total_assets = prices_df["total_assets"].to_numpy(dtype=float)
    all_timestamps = pd.DatetimeIndex(prices_df.index)

    for _vault_id, row_positions in prices_df.loc[hypercore_mask, ["id"]].groupby("id", sort=False).indices.items():
        positions = hypercore_positions[np.asarray(row_positions, dtype=int)]
        total_assets = all_total_assets[positions]
        timestamp = all_timestamps[positions]

        meaningful_assets = np.isfinite(total_assets) & (total_assets >= min_recapitalisation_assets)
        zero_assets = np.isfinite(total_assets) & (total_assets <= HYPERCORE_ZERO_NAV_EPSILON)
        zero_starts = np.flatnonzero(zero_assets & np.r_[True, ~zero_assets[:-1]])
        zero_ends = np.flatnonzero(zero_assets & np.r_[~zero_assets[1:], True])
        recapitalisation_position: int | None = None

        for zero_start, zero_end in zip(zero_starts, zero_ends):
            # A zero before a vault's first meaningful deposit is normal
            # initialisation, not a loss of an existing investment epoch.
            if not meaningful_assets[:zero_start].any():
                continue

            post_zero_positive = np.flatnonzero(np.isfinite(total_assets[zero_end + 1 :]) & (total_assets[zero_end + 1 :] > HYPERCORE_ZERO_NAV_EPSILON))
            if len(post_zero_positive) == 0:
                continue

            first_positive = zero_end + 1 + int(post_zero_positive[0])
            if timestamp[first_positive] - timestamp[zero_start] < min_recovery_delay:
                continue

            post_zero_meaningful = np.flatnonzero(meaningful_assets[zero_end + 1 :])
            if len(post_zero_meaningful) == 0:
                continue

            first_recapitalisation = zero_end + 1 + int(post_zero_meaningful[0])

            # Keep the latest valid reset if a vault has more than one
            # complete lifecycle. The output must start at its current epoch.
            recapitalisation_position = first_recapitalisation

        if recapitalisation_position is not None:
            remove_mask[positions[:recapitalisation_position]] = True
            epoch_reset_positions.append(int(positions[recapitalisation_position]))

    if not epoch_reset_positions:
        prices_df["epoch_reset"] = epoch_reset_values
        return prices_df

    epoch_reset_values[epoch_reset_positions] = True
    prices_df["epoch_reset"] = epoch_reset_values
    filtered_prices_df = prices_df.iloc[~remove_mask].copy()
    logger(f"Discarded {int(remove_mask.sum()):,} pre-recapitalisation Hypercore price rows across {len(epoch_reset_positions):,} vaults; new epochs start once NAV reaches ${min_recapitalisation_assets:,.0f} after {min_recovery_delay}")
    return filtered_prices_df


def fix_outlier_share_prices(
    prices_df: pd.DataFrame,
    logger=print,
    max_diff=0.33,
    look_back_hours=24,
    look_ahead_hours=24,
) -> pd.DataFrame:
    """Fix out rows with share price that is too high.

    - Sometimes share price jump to an outlier value and back
    - This caused abnormal returns in returns calculations, messing all volatility numbers, sharpe,
      charts, etc.
    - The root cause is bad oracles, fat fingers, MEV trades, etc.
    - The lookback window is time-based (hours), not row-based, so it works
      correctly for vaults with non-hourly polling intervals
    - See ``check-share-price`` script for inspecting individual prices

    Case Fluegel DAO:

    +---------------------+-------+-------------------------------------------+--------------+-------------+--------------+--------------+
    | timestamp           | chain | address                                   | block_number | share_price | total_assets | total_supply |
    +=====================+=======+===========================================+==============+=============+==============+==============+
    | 2024-07-16 15:02:57 | 8453  | 0x277a3c57f3236a7d458576074d7c3d7046eb26c | 17176415     | 1.60        | 373,740.21   | 232,929.92   |
    +---------------------+-------+-------------------------------------------+--------------+-------------+--------------+--------------+
    | 2024-07-16 16:02:57 | 8453  | 0x277a3c57f3236a7d458576074d7c3d7046eb26c | 17178215     | 1.63        | 379,832.59   | 232,929.92   |
    +---------------------+-------+-------------------------------------------+--------------+-------------+--------------+--------------+
    | 2024-07-16 17:02:57 | 8453  | 0x277a3c57f3236a7d458576074d7c3d7046eb26c | 17180015     | 0.33        | 75,744.97    | 232,929.92   |
    +---------------------+-------+-------------------------------------------+--------------+-------------+--------------+--------------+
    | 2024-07-16 18:02:57 | 8453  | 0x277a3c57f3236a7d458576074d7c3d7046eb26c | 17181815     | 1.64        | 382,282.78   | 232,929.92   |
    +---------------------+-------+-------------------------------------------+--------------+-------------+--------------+--------------+

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

        # Compute row-based shift from actual time spacing so that vaults
        # with non-hourly polling (daily, weekly) get a sensible window.
        if isinstance(group.index, pd.DatetimeIndex) and len(group) >= 2:
            median_interval = group.index.to_series().diff().median()
            if pd.notna(median_interval) and median_interval > pd.Timedelta(0):
                rows_per_hour = pd.Timedelta(hours=1) / median_interval
                effective_look_back = max(1, round(look_back_hours * rows_per_hour))
                effective_look_ahead = max(1, round(look_ahead_hours * rows_per_hour))
            else:
                effective_look_back = look_back_hours
                effective_look_ahead = look_ahead_hours
        else:
            effective_look_back = look_back_hours
            effective_look_ahead = look_ahead_hours

        group["next_price_candidate"] = group["share_price"].shift(-effective_look_ahead).ffill()
        group["prev_price_candidate"] = group["share_price"].shift(effective_look_back).bfill()

        # Calculate forward and backward percentage change for each vault.
        # Use symmetric max(a/b, b/a) - 1 so that spikes are detected regardless
        # of which value sits in the denominator.
        group["pct_change_prev"] = np.maximum(
            (group["prev_price_candidate"] / group["share_price"] - 1).abs(),
            (group["share_price"] / group["prev_price_candidate"] - 1).abs(),
        )
        group["pct_change_next"] = np.maximum(
            (group["next_price_candidate"] / group["share_price"] - 1).abs(),
            (group["share_price"] / group["next_price_candidate"] - 1).abs(),
        )

        # 2025-10-24 05:01:27  42161  0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9     392792321     1.046227  144437.368503  138055.399019              NaN             NaN         42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9  USDn2           60  Untangle Finance         1.046227
        # 2025-10-24 06:01:21  42161  0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9     392806721     0.487429   67292.321607  138055.399019              NaN             NaN         42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9  USDn2           60  Untangle Finance         0.487429
        # 2025-10-24 07:01:10  42161  0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9     392821121     1.047508  144614.157347  138055.399019              NaN             NaN         42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9  USDn2           60  Untangle Finance         1.047508

        # Mark rows where both changes exceed the threshold (spike and recovery)
        abnormal_mask = (group["pct_change_prev"] > max_diff) | (group["pct_change_next"] > max_diff)

        group["fixed_share_price"] = np.nan

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
            if prev_price != 0 and next_price != 0 and max(abs(next_price / prev_price - 1), abs(prev_price / next_price - 1)) < max_diff:
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

    # groupby() added id as a MultiIndex level (id, timestamp), unwind back
    if isinstance(filtered_all_df.index, pd.MultiIndex):
        filtered_all_df.reset_index(level="id", inplace=True)

    return filtered_all_df


def sort_and_index_vault_prices(
    prices_df: pd.DataFrame,
    priority_ids: list[str],
):
    """Set up the order of vaults for processing.

    - If we do debugging we want vaults we debug go first,
      as the pipeline takes several minutes to run
    """

    assert isinstance(prices_df.index, pd.DatetimeIndex) or pd.api.types.is_datetime64_any_dtype(prices_df.index), f"Expected datetime index, got: {type(prices_df.index)}, dtype: {prices_df.index.dtype}"

    # Create a priority column for sorting
    priority_set = set(priority_ids)
    prices_df["sort_priority"] = prices_df["id"].isin(priority_set).map({True: 0, False: 1})

    # Sort by priority first, then by id and timestamp
    # Use sort_index name to avoid reset_index overhead
    prices_df = prices_df.reset_index()
    prices_df.sort_values(by=["sort_priority", "id", "timestamp"], inplace=True)
    prices_df.drop("sort_priority", axis=1, inplace=True)
    prices_df.set_index("timestamp", inplace=True)
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

    prices_df = ensure_vault_state_columns(prices_df)
    prices_df = derive_deposit_closed_reason(prices_df)

    assign_unique_names(rows, prices_df, logger)

    missing_ids = check_missing_metadata(rows, prices_df["id"], prices_df, logger)
    if missing_ids:
        before_count = len(prices_df)
        prices_df = prices_df[~prices_df["id"].isin(missing_ids)]
        logger(f"Dropped {before_count - len(prices_df):,} price rows for {len(missing_ids):,} vaults without metadata")

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

    prices_df = remove_inactive_lead_time(prices_df, logger)

    # A complete Hypercore wipe-out followed by later deposits is a new
    # investment epoch, not a recoverable price movement. Begin the cleaned
    # history from the meaningful recapitalisation point.
    prices_df = discard_hypercore_pre_recapitalisation_history(prices_df, logger)

    if diagnose_vault_id:
        vault_prices_df = prices_df[prices_df["id"] == diagnose_vault_id]
        logger("After remove_inactive_lead_time():")
        display(vault_prices_df)

    # Hyperliquid does not expose an authoritative historical unit price or
    # share supply. Replace every raw Hypercore scanner unit with one
    # conservative PnL/NAV performance index before calculating returns.
    prices_df = approximate_hypercore_share_prices_from_pnl_nav(prices_df, logger)

    # The generic fixer derives one row offset from each vault's median polling
    # interval. Hypercore mixes roughly 20-minute, daily, and weekly rows, so one
    # offset cannot represent a stable time window. The economic index above
    # handles Hypercore; keep the generic fixer limited to EVM vaults.

    hypercore_mask = prices_df["chain"] == HYPERCORE_CHAIN_ID
    has_hypercore = hypercore_mask.any()
    has_evm = (~hypercore_mask).any()

    if has_hypercore and has_evm:
        # Fix outlier share prices only for EVM rows, operating in-place
        evm_df = prices_df.loc[~hypercore_mask]
        fixed_evm = fix_outlier_share_prices(evm_df, logger)
        prices_df.loc[~hypercore_mask] = fixed_evm
    elif has_evm:
        prices_df = fix_outlier_share_prices(prices_df, logger)
    else:
        logger("Skipping fix_outlier_share_prices() for Hypercore-only dataset")

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
    prices_df: pd.DataFrame,
    logger=print,
) -> set[str]:
    """Check that we have metadata for all vaults in the prices DataFrame.

    Vault id is in format: ``56-0x10c90bfcfb3d2a7ae814da1548ae3a7fc31c35a0``

    If there are vaults with price data but no metadata, they are logged
    at error level and their IDs returned so the caller can drop them.

    :param rows:
        Metadata rows from vault database

    :param prices_df:
        The full prices DataFrame, used to extract context for missing vaults.

    :return:
        Set of vault IDs that are missing from the metadata.
        These should be dropped from the price data before further processing.
    """

    assert isinstance(price_ids, pd.Series)

    unique_price_ids = sorted(list(price_ids.unique()))

    vaults_by_id = get_vaults_by_id(rows)

    logger(f"Price data has {len(unique_price_ids):,} unique vault ids, vault database has {len(vaults_by_id):,} vault ids")

    assert len(unique_price_ids) > 0, "No vault ids in price data"

    missing_ids = set()

    for vault_id in unique_price_ids:
        if vault_id not in vaults_by_id:
            missing_ids.add(vault_id)

            # Extract context from the price rows for this vault
            vault_rows = prices_df[prices_df["id"] == vault_id]
            row_count = len(vault_rows)
            chain = vault_rows["chain"].iloc[0] if row_count > 0 else "?"
            address = vault_rows["address"].iloc[0] if row_count > 0 else "?"
            chain_name = get_chain_name(chain) if isinstance(chain, int) else str(chain)
            first_ts = vault_rows["timestamp"].min() if row_count > 0 else "?"
            last_ts = vault_rows["timestamp"].max() if row_count > 0 else "?"
            logger(f"ERROR: Missing metadata for vault {vault_id} (chain={chain_name}, address={address}, {row_count:,} price rows, {first_ts} to {last_ts}), dropping from price data")

    if missing_ids:
        logger(f"ERROR: Missing vault metadata for {len(missing_ids):,} vault ids out of {len(unique_price_ids):,}, dropping their price rows. This may be caused by a case mismatch between address formats in the price data vs vault database.")

    return missing_ids


def generate_cleaned_vault_datasets(
    vault_db_path=DEFAULT_VAULT_DATABASE,
    price_df_path=DEFAULT_UNCLEANED_PRICE_DATABASE,
    cleaned_price_df_path=Path.home() / ".tradingstrategy" / "vaults" / "cleaned-vault-prices-1h.parquet",
    settlement_db_path: Path | None = None,
    logger=print,
    display=display,
    diagnose_vault_id: str | None = None,
):
    """A command line script entry point to take raw scanned vault price data and clean it up to a format that can be analysed.

    - Reads ``vault-prices-1h.parquet`` and generates ``cleaned-vault-prices-1h.parquet``
    - Calculate returns and various performance metrics to be included with prices data
    - Clean returns from abnormalities
    - Stamp the cleaned Parquet with the current Docker ``metadata.version``
      provenance, matching vault scanner JSON exports

    .. note::

        Drops non-stablecoin vaults. The cleaning is currently applicable
        for stable vaults only.
    """

    assert vault_db_path.exists()
    assert price_df_path.exists()

    logger(f"Loading vault database {vault_db_path}")
    vault_db: VaultDatabase = pickle.load(vault_db_path.open("rb"))

    logger(f"Loading prices {price_df_path}")
    prices_df = pd.read_parquet(price_df_path, dtype_backend="pyarrow")

    logger(f"We have {vault_db.get_lead_count():,} vault leads in the vault database and {len(prices_df):,} price rows in the raw prices DataFrame")

    rows = vault_db.rows

    enhanced_prices_df = process_raw_vault_scan_data(
        rows,
        prices_df,
        logger,
        display=display,
        diagnose_vault_id=diagnose_vault_id,
    )
    logger(f"We have {len(enhanced_prices_df):,} price rows in the cleaned prices DataFrame before settlement annotation")
    enhanced_prices_df = merge_vault_settlements_into_cleaned_prices(enhanced_prices_df, settlement_db_path=settlement_db_path)

    # Free the original uncleaned DataFrame to reduce peak memory
    del prices_df

    # Sort for better compression
    enhanced_prices_df.sort_values(by=["id", "timestamp"], inplace=True)

    # Write to a temp file, verify, then atomically replace the target.
    # If verification fails, the original cleaned parquet is preserved.
    temp_fd, temp_path = tempfile.mkstemp(
        suffix=".parquet",
        dir=str(cleaned_price_df_path.parent),
    )
    try:
        os.close(temp_fd)
        table = pa.Table.from_pandas(enhanced_prices_df)
        table = table.replace_schema_metadata(stamp_parquet_schema_metadata(table.schema).metadata)
        pq.write_table(table, temp_path, compression="zstd")
        verify_parquet_file(
            temp_path,
            expected_rows=len(enhanced_prices_df),
            required_columns=["id", "share_price", "raw_share_price", "returns_1h", "timestamp"],
        )
        os.replace(temp_path, str(cleaned_price_df_path))
    except BaseException:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise

    fsize = cleaned_price_df_path.stat().st_size
    logger(f"Saved cleaned vault prices to {cleaned_price_df_path}, total {len(enhanced_prices_df):,} rows, file size is {fsize / 1024 / 1024:.2f} MB")


def replace_cleaned_vault_histories(  # noqa: PLR0914
    vault_ids: set[str],
    *,
    vault_db_path: Path = DEFAULT_VAULT_DATABASE,
    raw_price_df_path: Path = DEFAULT_UNCLEANED_PRICE_DATABASE,
    cleaned_price_df_path: Path = DEFAULT_RAW_PRICE_DATABASE,
    settlement_db_path: Path | None = None,
    logger: Callable[[str], None] = print,
) -> int:
    """Rebuild and atomically replace cleaned histories for selected vaults.

    The normal cleaner is deliberately whole-dataset: it reads every raw row,
    applies its transformations, and emits a new Parquet file.  A historical
    repair only changes a small number of vaults, however, and each cleaner
    transformation is independent between vault ids.  Recompute the complete
    raw history for the selected ids, then stream-copy all other cleaned row
    groups into a replacement Parquet.  This avoids expensive pandas cleaning
    for unrelated vaults while preserving their existing cleaned rows and
    physical ``id, timestamp`` order.

    The destination remains a single Parquet file, so its bytes must still be
    rewritten before the atomic replace.  The function does not silently drop
    columns: a selected vault whose cleaned columns do not match the existing
    output raises an error before replacing the original file.

    :param vault_ids:
        Canonical lower-case ``chain_id-address`` ids to replace.
    :param vault_db_path:
        Metadata database used for denormalisation and stablecoin filtering.
    :param raw_price_df_path:
        Raw scanner Parquet containing the replacement histories.
    :param cleaned_price_df_path:
        Existing cleaned Parquet to update atomically.
    :param settlement_db_path:
        Optional settlement database applied to the selected cleaned rows.
    :param logger:
        Progress callback.
    :return:
        Number of cleaned rows written for the selected vaults.
    """

    canonical_ids = sorted(vault_id.lower() for vault_id in vault_ids)
    if not canonical_ids:
        message = "vault_ids must not be empty"
        raise ValueError(message)

    assert vault_db_path.exists(), f"Vault metadata database does not exist: {vault_db_path}"
    assert raw_price_df_path.exists(), f"Raw price database does not exist: {raw_price_df_path}"
    assert cleaned_price_df_path.exists(), f"Cleaned price database does not exist: {cleaned_price_df_path}"

    vault_specs = [VaultSpec.parse_string(vault_id) for vault_id in canonical_ids]
    logger(f"Loading raw histories for {len(canonical_ids):,} selected vaults from {raw_price_df_path}")
    raw_reader = pq.ParquetFile(raw_price_df_path)
    required_raw_columns = {"chain", "address"}
    missing_raw_columns = required_raw_columns - set(raw_reader.schema_arrow.names)
    if missing_raw_columns:
        raise ValueError(f"Raw price database is missing required columns: {sorted(missing_raw_columns)}")

    selected_raw_batches: list[pa.Table] = []
    for batch in raw_reader.iter_batches(batch_size=100_000):
        raw_table = pa.Table.from_batches([batch])
        pair_mask = pc.and_(
            pc.equal(raw_table["chain"], vault_specs[0].chain_id),
            pc.equal(raw_table["address"], vault_specs[0].vault_address),
        )
        for spec in vault_specs[1:]:
            pair_mask = pc.or_(
                pair_mask,
                pc.and_(
                    pc.equal(raw_table["chain"], spec.chain_id),
                    pc.equal(raw_table["address"], spec.vault_address),
                ),
            )
        selected_raw = raw_table.filter(pair_mask)
        if selected_raw.num_rows:
            selected_raw_batches.append(selected_raw)

    raw_prices_df = pa.concat_tables(selected_raw_batches).to_pandas(types_mapper=pd.ArrowDtype) if selected_raw_batches else pd.DataFrame()
    if raw_prices_df.empty:
        raise ValueError(f"No raw price rows found for selected vault ids: {', '.join(canonical_ids)}")

    logger(f"Loading vault metadata from {vault_db_path}")
    vault_db = VaultDatabase.read(vault_db_path)
    cleaned_selected_df = process_raw_vault_scan_data(vault_db.rows, raw_prices_df, logger=logger)
    cleaned_selected_df = merge_vault_settlements_into_cleaned_prices(cleaned_selected_df, settlement_db_path=settlement_db_path)
    if "timestamp" not in cleaned_selected_df.columns and cleaned_selected_df.index.name == "timestamp":
        cleaned_selected_df = cleaned_selected_df.reset_index()

    missing_cleaned_columns = {"id", "timestamp"} - set(cleaned_selected_df.columns)
    if missing_cleaned_columns:
        raise ValueError(f"Selected cleaned histories are missing required columns: {sorted(missing_cleaned_columns)}")

    cleaned_ids = set(cleaned_selected_df["id"].astype(str).str.lower())
    missing_cleaned_ids = set(canonical_ids) - cleaned_ids
    if missing_cleaned_ids:
        raise ValueError(f"Cleaning removed all rows for selected vault ids; refusing to replace existing histories: {', '.join(sorted(missing_cleaned_ids))}")
    cleaned_selected_df.sort_values(by=["id", "timestamp"], inplace=True)

    existing_reader = pq.ParquetFile(cleaned_price_df_path)
    output_schema = existing_reader.schema_arrow
    selected_table = pa.Table.from_pandas(cleaned_selected_df)
    unexpected_columns = set(selected_table.schema.names) - set(output_schema.names)
    if unexpected_columns:
        raise ValueError(f"Selected cleaned histories contain columns missing from {cleaned_price_df_path}: {sorted(unexpected_columns)}")

    selected_columns = []
    for field in output_schema:
        if field.name in selected_table.schema.names:
            column = selected_table[field.name]
            if column.type != field.type:
                column = column.cast(field.type)
        else:
            column = pa.nulls(len(selected_table), type=field.type)
        selected_columns.append(column)
    selected_table = pa.Table.from_arrays(selected_columns, schema=output_schema)
    selected_sort_keys = list(zip(selected_table["id"].to_pylist(), selected_table["timestamp"].to_pylist(), strict=True))

    value_set = pa.array(canonical_ids, type=pa.string())
    temp_fd, temp_path = tempfile.mkstemp(suffix=".parquet", dir=str(cleaned_price_df_path.parent))
    os.close(temp_fd)
    retained_rows = 0
    selected_row_offset = 0
    try:
        with pq.ParquetWriter(temp_path, output_schema, compression="zstd") as writer:
            for batch in existing_reader.iter_batches(batch_size=100_000):
                table = pa.Table.from_batches([batch]).replace_schema_metadata(output_schema.metadata)
                retained = table.filter(pc.invert(pc.is_in(table["id"], value_set=value_set)))
                if retained.num_rows:
                    retained_row_count = retained.num_rows
                    last_sort_key = (retained["id"][-1].as_py(), retained["timestamp"][-1].as_py())
                    selected_end = bisect_right(selected_sort_keys, last_sort_key, lo=selected_row_offset)
                    if selected_end > selected_row_offset:
                        combined = pa.concat_tables(
                            [retained, selected_table.slice(selected_row_offset, selected_end - selected_row_offset)],
                        )
                        sort_indices = pc.sort_indices(combined, sort_keys=[("id", "ascending"), ("timestamp", "ascending")])
                        retained = combined.take(sort_indices)
                        selected_row_offset = selected_end
                    writer.write_table(retained)
                    retained_rows += retained_row_count
            if selected_row_offset < selected_table.num_rows:
                writer.write_table(selected_table.slice(selected_row_offset))

        expected_rows = retained_rows + selected_table.num_rows
        verify_parquet_file(
            temp_path,
            expected_rows=expected_rows,
            expected_schema=output_schema,
        )
        os.replace(temp_path, cleaned_price_df_path)
    except BaseException:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise

    logger(f"Replaced {selected_table.num_rows:,} cleaned price rows for {len(canonical_ids):,} vaults; preserved {retained_rows:,} unrelated rows")
    return selected_table.num_rows


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
