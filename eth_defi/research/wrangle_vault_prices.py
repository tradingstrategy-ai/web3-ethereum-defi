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

import datetime
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
from eth_defi.hyperliquid.combined_analysis import rescale_share_price_rows
from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID
from eth_defi.token import is_stablecoin_like
from eth_defi.types import Percent
from eth_defi.vault.base import VaultSpec, verify_parquet_file
from eth_defi.vault.settlement_data import (
    merge_vault_settlements_into_cleaned_prices,
)
from eth_defi.vault.vaultdb import DEFAULT_RAW_PRICE_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_VAULT_DATABASE, VaultDatabase, VaultRow
from eth_defi.version_info import stamp_parquet_schema_metadata

#: At least two canonical observations are needed to bracket a daily price.
MIN_HYPERCORE_PRICE_ANCHORS = 2

#: Rows emitted by one daily refresh share practically the same write time.
HYPERCORE_DAILY_REFRESH_TOLERANCE = pd.Timedelta(minutes=1)

#: Do not estimate a price across a missing weekly Hypercore anchor.
HYPERCORE_MAX_PRICE_ANCHOR_GAP = pd.Timedelta(days=8)

#: NAV at or below this value counts as a complete Hypercore wipe-out.
HYPERCORE_ZERO_NAV_EPSILON = 0.000001

#: New capital must reach this NAV before a recapitalised vault is tracked again.
MIN_HYPERCORE_RECAPITALISATION_ASSETS = 1_000.0

#: Ignore isolated zero-NAV observations that recover before this delay.
MIN_HYPERCORE_RECAPITALISATION_RECOVERY_DELAY = pd.Timedelta(days=7)

#: Never infer a scanner-batch price unit across a longer missing-HF interval.
HYPERCORE_MAX_HF_BATCH_STITCH_GAP = pd.Timedelta(days=2)

#: A flow-reconciled PnL path must end close to its next trusted HF observation.
HYPERCORE_MAX_PNL_PATH_ENDPOINT_DEVIATION: Percent = 0.10

#: Permit cents and small API rounding differences when checking NAV accounting.
HYPERCORE_PNL_PATH_ACCOUNTING_ABSOLUTE_TOLERANCE = 1.0

#: Relative NAV accounting tolerance for the flow-reconciled price path.
HYPERCORE_PNL_PATH_ACCOUNTING_RELATIVE_TOLERANCE: Percent = 0.01


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
    #: Hypercore (native Hyperliquid vaults) does not expose a share price;
    #: it is **internally calculated** as ``total_assets / total_supply``
    #: from reconstructed equity curves and deposit/withdrawal histories.
    #: See :py:mod:`eth_defi.hyperliquid.combined_analysis` for the
    #: Hypercore share price derivation.
    #:
    #: General — present for all protocols.
    share_price: float

    #: Total assets under management (TVL) in denomination token units.
    #:
    #: General — present for all protocols.
    total_assets: float

    #: Total supply of vault share tokens.
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

    #: Outcome of the conservative Hypercore source-overlap repair.
    #:
    #: Values start with ``"repaired_"`` when the cleaned share price was
    #: changed and ``"deferred_"`` when a candidate was left unchanged because
    #: it failed the NAV, anchor-gap, or lifecycle-boundary safeguards.
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

    # Hypercore prices are synthetic. Its source-aware continuity repairs run
    # before this generic cleaner, and a genuine post-epoch trading return may
    # exceed the ERC-4626-oriented threshold. Retain it for the downstream
    # return-based suitability checks instead of replacing it with a false zero.
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


def cap_hypercore_share_prices(
    prices_df: pd.DataFrame,
    logger=print,
    max_share_price: float = 1_000_000.0,
) -> pd.DataFrame:
    """Cap share prices for Hypercore (Hyperliquid native) vaults.

    Hypercore share prices are derived synthetically from portfolio
    history in :py:func:`~eth_defi.hyperliquid.combined_analysis._calculate_share_price`.
    When ``total_supply`` approaches zero while ``total_assets`` remains nonzero
    (e.g. after most depositors withdrew from a leveraged trading vault),
    share prices can overflow to absurd values (trillions+).

    This step caps those overflow values before the standard
    :py:func:`fix_outlier_share_prices` smoothing runs.  Only applies
    to Hypercore vaults (chain == 9999).

    :param prices_df:
        Price data with ``chain`` and ``share_price`` columns.
    :param logger:
        Logging function.
    :param max_share_price:
        Maximum allowed share price for Hypercore vaults.
    :return:
        DataFrame with capped share prices.
    """
    hypercore_mask = prices_df["chain"] == HYPERCORE_CHAIN_ID
    if not hypercore_mask.any():
        return prices_df

    overflow_mask = hypercore_mask & (prices_df["share_price"] > max_share_price)
    overflow_count = overflow_mask.sum()

    if overflow_count > 0:
        logger(f"Capping {overflow_count:,} Hypercore share prices above {max_share_price:,.0f}")
        prices_df.loc[overflow_mask, "share_price"] = max_share_price

    return prices_df


def discard_hypercore_pre_recapitalisation_history(
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
        prices_df["epoch_reset"] = False
    if "raw_share_price" not in prices_df.columns:
        prices_df["raw_share_price"] = prices_df["share_price"]

    remove_mask = np.zeros(len(prices_df), dtype=bool)
    epoch_reset_positions: list[int] = []
    hypercore_positions = np.flatnonzero(hypercore_mask.to_numpy())

    for _vault_id, row_positions in prices_df.loc[hypercore_mask].groupby("id", sort=False).indices.items():
        positions = hypercore_positions[np.asarray(row_positions, dtype=int)]
        group = prices_df.iloc[positions]
        total_assets = group["total_assets"].to_numpy(dtype=float)
        timestamp = pd.DatetimeIndex(group.index)

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
        return prices_df

    epoch_reset_col = prices_df.columns.get_loc("epoch_reset")
    prices_df.iloc[epoch_reset_positions, epoch_reset_col] = True

    # A new investor epoch must start from a readable unit price. Preserve the
    # scanner value in raw_share_price, then rescale this vault's retained rows
    # so the first meaningful recapitalisation observation is exactly 1.0.
    for epoch_reset_position in epoch_reset_positions:
        vault_id = prices_df.iloc[epoch_reset_position]["id"]
        vault_positions = np.flatnonzero((prices_df["id"] == vault_id).to_numpy())
        retained_positions = vault_positions[vault_positions >= epoch_reset_position]
        recapitalisation_price = float(prices_df.iloc[epoch_reset_position]["share_price"])
        if np.isfinite(recapitalisation_price) and recapitalisation_price > 0:
            rescale_share_price_rows(prices_df, 1.0 / recapitalisation_price, retained_positions)

    filtered_prices_df = prices_df.iloc[~remove_mask].copy()
    logger(f"Discarded {int(remove_mask.sum()):,} pre-recapitalisation Hypercore price rows across {len(epoch_reset_positions):,} vaults; new epochs start once NAV reaches ${min_recapitalisation_assets:,.0f} after {min_recovery_delay}")
    return filtered_prices_df


def stitch_hypercore_high_freq_share_price_batches(
    prices_df: pd.DataFrame,
    logger=print,
    max_timestamp_gap: pd.Timedelta = HYPERCORE_MAX_HF_BATCH_STITCH_GAP,
    max_return_deviation: Percent = 0.50,
) -> pd.DataFrame:
    """Stitch incompatible Hypercore high-frequency scanner batches.

    The API supplies rolling account-value and cumulative-PnL windows, rather
    than a share price or supply. A new scan batch can therefore reconstruct
    the same vault in a different arbitrary unit. At a ``written_at`` batch
    boundary, the economically expected price change is
    ``(pnl_now - pnl_before) / nav_before``. When the observed share-price
    return differs by more than ``max_return_deviation``, rescale the boundary
    and all later rows. This retains legitimate large trading gains when PnL
    supports them, while repairing the unit changes seen in HODL My Perps.

    The narrow two-day and consecutive-HF requirements deliberately avoid
    inferring a price across sparse allTime history, lifecycle resets, or
    ordinary daily observations. More elaborate capital-flow reconstruction is
    not justified by the information Hyperliquid currently exposes.

    :param prices_df:
        Timestamp-indexed vault prices with ``id``, ``chain``,
        ``share_price``, ``total_assets``, and ``written_at`` columns. The
        cumulative PnL column is named ``account_pnl`` in exported parquet and
        ``cumulative_pnl`` in scanner-shaped DataFrames. ``hypercore_source``
        is used when available; legacy sources are inferred from midnight
        timestamps.
    :param logger:
        Notebook or console logging function.
    :param max_timestamp_gap:
        Largest permitted gap between consecutive HF observations.
    :param max_return_deviation:
        Symmetric deviation threshold used to distinguish a scale change from
        an economically supported price movement.
    :return:
        A copy with repaired price units and audit columns populated.
    """
    hypercore_mask = prices_df["chain"] == HYPERCORE_CHAIN_ID
    pnl_column = "cumulative_pnl" if "cumulative_pnl" in prices_df.columns else "account_pnl"
    required_columns = {"total_assets", pnl_column, "written_at"}
    if not hypercore_mask.any() or not required_columns.issubset(prices_df.columns):
        return prices_df

    prices_df = prices_df.copy()
    if "raw_share_price" not in prices_df.columns:
        prices_df["raw_share_price"] = prices_df["share_price"]
    if "hypercore_repair_status" not in prices_df.columns:
        prices_df["hypercore_repair_status"] = ""
    if "hypercore_source" not in prices_df.columns:
        prices_df["hypercore_source"] = pd.NA

    missing_source_mask = hypercore_mask & prices_df["hypercore_source"].isna()
    if missing_source_mask.any():
        timestamps = pd.DatetimeIndex(prices_df.index[missing_source_mask])
        prices_df.loc[missing_source_mask, "hypercore_source"] = np.where(timestamps == timestamps.normalize(), "daily", "hf")

    repair_count = 0
    hypercore_positions = np.flatnonzero(hypercore_mask.to_numpy())
    status_column = prices_df.columns.get_loc("hypercore_repair_status")
    for vault_id, row_positions in prices_df.loc[hypercore_mask].groupby("id", sort=False).indices.items():
        positions = hypercore_positions[np.asarray(row_positions, dtype=int)]
        group = prices_df.iloc[positions]
        hf_source_mask = group["hypercore_source"].astype("string").fillna("").to_numpy(dtype=str) == "hf"
        hf_positions = np.flatnonzero(hf_source_mask)
        if len(hf_positions) < 2:
            continue

        hf_written_at = pd.to_datetime(
            group["written_at"].iloc[hf_positions],
            errors="coerce",
        ).to_numpy(dtype="datetime64[ns]")
        hf_timestamp_ns = (
            pd.DatetimeIndex(group.index[hf_positions])
            .to_numpy(
                dtype="datetime64[ns]",
            )
            .astype("int64")
        )
        timestamp_gaps = np.diff(hf_timestamp_ns)
        batch_boundary_mask = np.zeros(len(hf_positions), dtype=bool)
        has_written_at = ~pd.isna(hf_written_at[:-1]) & ~pd.isna(hf_written_at[1:])
        changed_batch = hf_written_at[1:] != hf_written_at[:-1]
        short_forward_gap = (timestamp_gaps > 0) & (timestamp_gaps <= max_timestamp_gap.value)
        batch_boundary_mask[1:] = has_written_at & changed_batch & short_forward_gap
        for boundary_position in np.flatnonzero(batch_boundary_mask):
            previous_position = int(hf_positions[boundary_position - 1])
            current_position = int(hf_positions[boundary_position])
            previous_row = prices_df.iloc[positions[previous_position]]
            current_row = prices_df.iloc[positions[current_position]]
            previous_epoch_reset = previous_row.get("epoch_reset", False)
            current_epoch_reset = current_row.get("epoch_reset", False)
            crosses_epoch_boundary = (pd.notna(previous_epoch_reset) and bool(previous_epoch_reset)) or (pd.notna(current_epoch_reset) and bool(current_epoch_reset))
            if crosses_epoch_boundary:
                continue

            previous_nav = float(previous_row["total_assets"])
            previous_pnl = float(previous_row[pnl_column])
            current_pnl = float(current_row[pnl_column])
            previous_price = float(previous_row["share_price"])
            current_price = float(current_row["share_price"])
            finite_values = (previous_nav, previous_pnl, current_pnl, previous_price, current_price)
            if not all(np.isfinite(value) for value in finite_values) or previous_nav <= 0 or previous_price <= 0 or current_price <= 0:
                continue

            expected_price = previous_price * (1 + (current_pnl - previous_pnl) / previous_nav)
            if not np.isfinite(expected_price) or expected_price <= 0:
                continue
            deviation = max(current_price / expected_price, expected_price / current_price) - 1
            if deviation > max_return_deviation:
                factor = expected_price / current_price
                rescale_share_price_rows(prices_df, factor, positions[current_position:])
                prices_df.iloc[positions[current_position], status_column] = "repaired_hf_batch_scale"
                description = "large " if factor > 2 or factor < 0.5 else ""
                logger(f"Stitched {description}Hypercore HF batch scale for {vault_id} at {current_row.name}: factor {factor:.6f}")
                repair_count += 1

    if repair_count:
        logger(f"Stitched {repair_count:,} incompatible Hypercore HF scanner batch boundaries")
    return prices_df


def fix_hypercore_flow_reconciled_share_price_paths(  # noqa: PLR0914, PLR0917
    prices_df: pd.DataFrame,
    logger=print,
    max_anchor_deviation: Percent = 0.50,
    max_anchor_gap: pd.Timedelta = HYPERCORE_MAX_PRICE_ANCHOR_GAP,
    max_endpoint_deviation: Percent = HYPERCORE_MAX_PNL_PATH_ENDPOINT_DEVIATION,
    accounting_absolute_tolerance: float = HYPERCORE_PNL_PATH_ACCOUNTING_ABSOLUTE_TOLERANCE,
    accounting_relative_tolerance: Percent = HYPERCORE_PNL_PATH_ACCOUNTING_RELATIVE_TOLERANCE,
) -> pd.DataFrame:
    """Repair a conflicted Hypercore interval from ledger-reconciled PnL.

    Hyperliquid does not expose a native vault share price. Both scanners derive
    one from rolling portfolio windows, so a daily observation can be in a
    different arbitrary unit even though its NAV and cumulative PnL are real.
    In February 2026 Magixbox reported a daily price of ``26.03541`` between HF
    observations near ``1.30``. Its $7,614.65 of same-day withdrawals and
    -$1,386.74 PnL exactly reconcile the NAV change to within two cents. The
    price spike therefore cannot be an investor return: cash flows change the
    implied supply, while PnL changes the price.

    When persisted daily ledger flows prove that accounting relationship for a
    conflicted observation, reconstruct its price from the PnL path between
    adjacent HF anchors using ``1 + pnl_change / previous_nav``. A small
    log-linear endpoint correction makes the reconstructed path meet the next
    HF price without discarding the timing and direction of the observed PnL.
    This is deliberately narrower than generic smoothing: each changed daily
    row needs known flow values and must reconcile, there may be no wipe-out
    boundary, and the uncorrected PnL path must already end within
    ``max_endpoint_deviation`` of the next HF anchor. If old parquet lacks the
    ledger flow columns, this function leaves it untouched and the older,
    conservative source-overlap repair remains the fallback.

    :param prices_df:
        Timestamp-indexed vault price data. Hypercore rows need ``id``,
        ``chain``, ``share_price``, ``total_assets``, ``account_pnl`` (or
        scanner-shaped ``cumulative_pnl``), ``daily_deposit_usd``,
        ``daily_withdrawal_usd``, and ``hypercore_source``. Each repaired
        calendar date must have a known flow record, on either its daily or HF
        observation; zero is required when there was no ledger event. Matching
        daily/HF copies of the same flow are de-duplicated.
    :param logger:
        Notebook or console logging function.
    :param max_anchor_deviation:
        Minimum symmetric daily/HF price disagreement required before the
        interval is considered for this specialised repair.
    :param max_anchor_gap:
        Largest interval between consecutive HF anchors.
    :param max_endpoint_deviation:
        Largest symmetric difference permitted between the uncorrected PnL
        path and the right HF anchor.
    :param accounting_absolute_tolerance:
        Dollar tolerance for API rounding when reconciling one NAV step.
    :param accounting_relative_tolerance:
        Relative NAV tolerance for the same reconciliation.
    :return:
        A copy with qualifying daily paths marked ``repaired_hf_pnl_flow``;
        ``raw_share_price`` remains the scanner-derived value.
    """
    hypercore_mask = prices_df["chain"] == HYPERCORE_CHAIN_ID
    pnl_column = "cumulative_pnl" if "cumulative_pnl" in prices_df.columns else "account_pnl"
    required_columns = {"total_assets", pnl_column, "daily_deposit_usd", "daily_withdrawal_usd"}
    if not hypercore_mask.any() or not required_columns.issubset(prices_df.columns):
        return prices_df

    prices_df = prices_df.copy()
    if "raw_share_price" not in prices_df.columns:
        prices_df["raw_share_price"] = prices_df["share_price"]
    if "hypercore_repair_status" not in prices_df.columns:
        prices_df["hypercore_repair_status"] = ""
    if "hypercore_source" not in prices_df.columns:
        prices_df["hypercore_source"] = pd.NA

    missing_source_mask = hypercore_mask & prices_df["hypercore_source"].isna()
    if missing_source_mask.any():
        timestamps = pd.DatetimeIndex(prices_df.index[missing_source_mask])
        prices_df.loc[missing_source_mask, "hypercore_source"] = np.where(timestamps == timestamps.normalize(), "daily", "hf")

    repaired_rows = 0
    repaired_vaults: set[str] = set()
    hypercore_positions = np.flatnonzero(hypercore_mask.to_numpy())
    share_price_col = prices_df.columns.get_loc("share_price")
    total_supply_col = prices_df.columns.get_loc("total_supply") if "total_supply" in prices_df.columns else None
    status_col = prices_df.columns.get_loc("hypercore_repair_status")

    for vault_id, row_positions in prices_df.loc[hypercore_mask].groupby("id", sort=False).indices.items():
        positions = hypercore_positions[np.asarray(row_positions, dtype=int)]
        group = prices_df.iloc[positions]
        source = group["hypercore_source"].astype("string").fillna("").to_numpy(dtype=str)
        status = group["hypercore_repair_status"].astype("string").fillna("").to_numpy(dtype=str)
        timestamp = pd.DatetimeIndex(group.index)
        timestamp_ns = timestamp.to_numpy(dtype="datetime64[ns]").astype("int64")
        share_price = group["share_price"].to_numpy(dtype=float)
        total_assets = group["total_assets"].to_numpy(dtype=float)
        account_pnl = group[pnl_column].to_numpy(dtype=float)
        deposits = group["daily_deposit_usd"].to_numpy(dtype=float)
        withdrawals = group["daily_withdrawal_usd"].to_numpy(dtype=float)
        epoch_reset = group["epoch_reset"].fillna(False).astype(bool).to_numpy() if "epoch_reset" in group.columns else np.zeros(len(group), dtype=bool)
        daily_flow_by_date: dict[datetime.date, tuple[float, float]] = {}
        known_flow_mask = np.isfinite(deposits) & np.isfinite(withdrawals)
        calendar_dates = timestamp.date
        for day in np.unique(calendar_dates):
            day_positions = np.flatnonzero(calendar_dates == day)
            known_positions = day_positions[known_flow_mask[day_positions]]
            if len(known_positions) == 0:
                continue
            known_flows = np.column_stack((deposits[known_positions], withdrawals[known_positions]))
            non_zero_flows = known_flows[np.sum(np.abs(known_flows), axis=1) > 0]
            candidate_flows = non_zero_flows if len(non_zero_flows) else known_flows
            if np.allclose(candidate_flows, candidate_flows[0], rtol=0.0, atol=0.01):
                daily_flow_by_date[day] = tuple(candidate_flows[0])

        hf_positions = np.flatnonzero((source == "hf") & np.isfinite(share_price) & (share_price > 0) & np.isfinite(total_assets) & (total_assets > 0) & np.isfinite(account_pnl))
        if len(hf_positions) < MIN_HYPERCORE_PRICE_ANCHORS:
            continue

        for left_position, right_position in zip(hf_positions[:-1], hf_positions[1:]):
            if timestamp_ns[right_position] - timestamp_ns[left_position] > max_anchor_gap.value:
                continue

            inner_positions = np.arange(left_position + 1, right_position)
            if len(inner_positions) == 0 or not np.all(source[inner_positions] == "daily"):
                continue
            if np.any(epoch_reset[left_position : right_position + 1]) or np.any(total_assets[left_position : right_position + 1] <= HYPERCORE_ZERO_NAV_EPSILON):
                continue

            # Do not change a path that had already been handled by a prior
            # Hypercore repair in this wrangle run.
            if np.any(status[inner_positions] != ""):
                continue

            elapsed = (timestamp_ns[inner_positions] - timestamp_ns[left_position]) / (timestamp_ns[right_position] - timestamp_ns[left_position])
            expected_anchor_prices = np.exp(np.log(share_price[left_position]) + elapsed * (np.log(share_price[right_position]) - np.log(share_price[left_position])))
            with np.errstate(divide="ignore", invalid="ignore"):
                anchor_deviation = (
                    np.maximum(
                        share_price[inner_positions] / expected_anchor_prices,
                        expected_anchor_prices / share_price[inner_positions],
                    )
                    - 1
                )
            candidate_mask = np.isfinite(anchor_deviation) & (anchor_deviation > max_anchor_deviation)
            candidate_positions = inner_positions[candidate_mask]
            if len(candidate_positions) == 0:
                continue

            required_values = np.concatenate(
                [
                    total_assets[[left_position, right_position]],
                    account_pnl[[left_position, right_position]],
                    total_assets[inner_positions],
                    account_pnl[inner_positions],
                ]
            )
            if not np.all(np.isfinite(required_values)) or np.any(total_assets[[left_position, *inner_positions]] <= 0):
                continue

            provisional_prices: list[float] = []
            previous_assets = total_assets[left_position]
            previous_pnl = account_pnl[left_position]
            previous_price = share_price[left_position]
            pnl_path_valid = True
            for position in inner_positions:
                pnl_change = account_pnl[position] - previous_pnl
                next_price = previous_price * (1 + pnl_change / previous_assets)
                if not np.isfinite(next_price) or next_price <= 0:
                    pnl_path_valid = False
                    break
                provisional_prices.append(next_price)
                previous_assets = total_assets[position]
                previous_pnl = account_pnl[position]
                previous_price = next_price

            if not pnl_path_valid:
                continue

            endpoint_price = previous_price * (1 + (account_pnl[right_position] - previous_pnl) / previous_assets)
            if not np.isfinite(endpoint_price) or endpoint_price <= 0:
                continue
            endpoint_deviation = max(endpoint_price / share_price[right_position], share_price[right_position] / endpoint_price) - 1
            if endpoint_deviation > max_endpoint_deviation:
                continue

            endpoint_correction = np.log(share_price[right_position] / endpoint_price)
            repaired_prices = np.asarray(provisional_prices) * np.exp(endpoint_correction * elapsed)
            repaired_by_position = dict(zip(inner_positions, repaired_prices))
            repaired_candidate_count = 0
            for position in candidate_positions:
                flow_values = daily_flow_by_date.get(timestamp[position].date())
                if flow_values is None:
                    continue
                deposit, withdrawal = flow_values
                previous_position = position - 1
                expected_assets = total_assets[previous_position] + (account_pnl[position] - account_pnl[previous_position]) + deposit - withdrawal
                tolerance = max(
                    accounting_absolute_tolerance,
                    accounting_relative_tolerance * max(abs(expected_assets), abs(total_assets[position])),
                )
                if not np.isfinite(deposit) or not np.isfinite(withdrawal) or abs(total_assets[position] - expected_assets) > tolerance:
                    continue

                repaired_price = repaired_by_position[position]
                original_price = share_price[position]
                if not np.isfinite(original_price) or original_price <= 0:
                    continue
                factor = repaired_price / original_price
                prices_df.iloc[positions[position], share_price_col] = repaired_price
                if total_supply_col is not None:
                    original_supply = prices_df.iloc[positions[position], total_supply_col]
                    if pd.notna(original_supply) and original_supply > 0:
                        prices_df.iloc[positions[position], total_supply_col] = original_supply / factor
                prices_df.iloc[positions[position], status_col] = "repaired_hf_pnl_flow"
                repaired_rows += 1
                repaired_candidate_count += 1
            if repaired_candidate_count:
                repaired_vaults.add(str(vault_id))

    if repaired_rows:
        logger(f"Repaired {repaired_rows:,} flow-reconciled Hypercore daily prices across {len(repaired_vaults):,} vaults using PnL paths")
    return prices_df


def fix_hypercore_source_overlap_share_prices(  # noqa: PLR0914
    prices_df: pd.DataFrame,
    logger=print,
    max_anchor_deviation: Percent = 0.50,
    max_anchor_gap: pd.Timedelta = HYPERCORE_MAX_PRICE_ANCHOR_GAP,
) -> pd.DataFrame:
    """Repair corrupted daily Hypercore prices using canonical observations.

    Hypercore daily and high-frequency scanners both derive synthetic share
    prices from the rolling ``vaultDetails`` portfolio windows. Historical
    daily rows may have been calculated from a different rolling window than
    the later HF rows. Mixing the two sources can therefore create temporary
    multi-day price excursions that are absent from the canonical HF history.

    For periods covered by both sources, use positive HF observations as a
    time-based anchor curve. Some legacy vaults have daily history only. For
    these, use the latest batch of refreshed daily rows, identified by their
    common ``written_at`` value, as the canonical anchors. This handles stale
    rolling-window rows left between observations refreshed from a later
    ``allTime`` response.

    A daily observation becomes a repair candidate when its symmetric deviation
    from the log-linearly interpolated anchor price exceeds
    ``max_anchor_deviation``. It is changed only when all three conservative
    safeguards pass:

    - its NAV is within ``max_anchor_deviation`` of the interpolated anchor NAV;
    - the bracketing anchor gap is no longer than ``max_anchor_gap``; and
    - the anchor interval contains neither zero NAV nor ``epoch_reset``.

    A July 2026 audit of 848,333 Hypercore rows found 1,051 candidates across
    181 vaults. These rules automatically repair 747 rows and defer 304
    ambiguous rows: 260 failed NAV consistency, 60 used a gap over eight days,
    and 34 crossed a lifecycle boundary, with overlap between the counts. The
    NAV rule is deliberately applied to HF anchors too. For example, four of
    six Magixbox candidates are now deferred despite looking suspicious,
    because the raw data does not preserve enough intra-week information to
    prove a safe replacement. Avoiding a fabricated investor return takes
    priority over maximising the number of smooth chart points.

    Canonical anchors, rows outside anchor coverage, all HF rows, and all
    non-Hypercore rows are left unchanged. Deferred rows also remain unchanged
    and receive a reason in ``hypercore_repair_status``.

    ``raw_share_price`` always preserves the input value for auditability.

    :param prices_df:
        Vault prices indexed by timestamp. New Hypercore rows carry the
        ``hypercore_source`` value ``daily`` or ``hf``. For legacy rows the
        source is inferred from daily midnight normalisation versus the raw HF
        API timestamp.
    :param logger:
        Notebook or console logging function.
    :param max_anchor_deviation:
        Maximum symmetric ratio deviation from the interpolated anchor.
        ``0.50`` means either price may be at most 50% larger than the other.
    :param max_anchor_gap:
        Maximum elapsed time between the two observations used as anchors.
        Eight days permits the normal historical weekly HF cadence but rejects
        a missing weekly observation and longer interpolation.
    :return:
        Price data with conflicting daily Hypercore prices repaired.
    """
    prices_df = prices_df.copy()
    if "raw_share_price" not in prices_df.columns:
        prices_df["raw_share_price"] = prices_df["share_price"]
    if "hypercore_repair_status" not in prices_df.columns:
        prices_df["hypercore_repair_status"] = ""

    hypercore_mask = prices_df["chain"] == HYPERCORE_CHAIN_ID
    if not hypercore_mask.any():
        return prices_df

    if "hypercore_source" not in prices_df.columns:
        prices_df["hypercore_source"] = pd.NA

    missing_source_mask = hypercore_mask & prices_df["hypercore_source"].isna()
    if missing_source_mask.any():
        # Backwards compatibility for Parquet files written before explicit
        # source provenance was exported. Daily rows are normalised to midnight;
        # HF rows retain the raw API timestamp, normally including milliseconds.
        missing_timestamps = pd.DatetimeIndex(prices_df.index[missing_source_mask])
        inferred_sources = np.where(missing_timestamps == missing_timestamps.normalize(), "daily", "hf")
        prices_df.loc[missing_source_mask, "hypercore_source"] = inferred_sources
        logger(f"Inferred Hypercore source provenance for {int(missing_source_mask.sum()):,} legacy price rows")

    share_price_col = prices_df.columns.get_loc("share_price")
    repair_status_col = prices_df.columns.get_loc("hypercore_repair_status")
    hf_fixed_count = 0
    hf_affected_vaults = 0
    daily_fixed_count = 0
    daily_affected_vaults = 0
    deferred_count = 0
    nav_deferred_count = 0
    gap_deferred_count = 0
    boundary_deferred_count = 0
    deferred_vaults: set[str] = set()
    hypercore_positions = np.flatnonzero(hypercore_mask.to_numpy())

    for _vault_id, row_positions in prices_df.loc[hypercore_mask].groupby("id", sort=False).indices.items():
        # ``groupby().indices`` above is relative to the filtered frame. Map
        # these positions back to the original frame before assigning by iloc.
        positions = hypercore_positions[np.asarray(row_positions, dtype=int)]
        group = prices_df.iloc[positions]

        source = group["hypercore_source"].astype("string").fillna("").to_numpy(dtype=str)
        existing_status = group["hypercore_repair_status"].astype("string").fillna("").to_numpy(dtype=str)
        share_price = group["share_price"].to_numpy(dtype=float)
        timestamp_ns = pd.DatetimeIndex(group.index).to_numpy(dtype="datetime64[ns]").astype("int64")

        if "total_assets" not in group.columns:
            continue
        total_assets = group["total_assets"].to_numpy(dtype=float)
        positive_assets_mask = np.isfinite(total_assets) & (total_assets > 0)

        positive_price_mask = np.isfinite(share_price) & (share_price > 0)
        hf_price_mask = (source == "hf") & positive_price_mask
        daily_mask = source == "daily"
        if not daily_mask.any():
            continue

        if hf_price_mask.sum() >= MIN_HYPERCORE_PRICE_ANCHORS:
            # HF coverage selects the HF repair path even if some observations
            # have unusable NAV. Never reinterpret such a vault as daily-only.
            anchor_mask = hf_price_mask & positive_assets_mask
            candidate_mask = daily_mask & (existing_status == "")
            anchor_source = "hf"
        else:
            # Some legacy vaults were never covered by the HF scanner. A
            # later daily scan refreshes the canonical allTime observations
            # in one batch, while stale rows from older rolling windows retain
            # older or missing write times. Never modify the refresh batch
            # itself; it is the best available canonical history.
            if "written_at" not in group.columns:
                continue
            written_at = pd.to_datetime(group["written_at"], errors="coerce")
            latest_written_at = written_at.max()
            if pd.isna(latest_written_at):
                continue
            refreshed_mask = (written_at >= latest_written_at - HYPERCORE_DAILY_REFRESH_TOLERANCE).to_numpy()
            anchor_mask = daily_mask & refreshed_mask & positive_price_mask & positive_assets_mask
            candidate_mask = daily_mask & ~anchor_mask & (existing_status == "")
            anchor_source = "daily"

        anchor_timestamps = timestamp_ns[anchor_mask]
        anchor_prices = share_price[anchor_mask]
        anchor_assets = total_assets[anchor_mask]
        sort_order = np.argsort(anchor_timestamps)
        anchor_timestamps = anchor_timestamps[sort_order]
        anchor_prices = anchor_prices[sort_order]
        anchor_assets = anchor_assets[sort_order]

        # np.interp expects unique x values. Keep the last anchor value for any
        # duplicate timestamp, matching the export deduplication behaviour.
        reverse_unique_positions = np.unique(anchor_timestamps[::-1], return_index=True)[1]
        unique_positions = np.sort(len(anchor_timestamps) - 1 - reverse_unique_positions)
        anchor_timestamps = anchor_timestamps[unique_positions]
        anchor_prices = anchor_prices[unique_positions]
        anchor_assets = anchor_assets[unique_positions]
        if len(anchor_timestamps) < MIN_HYPERCORE_PRICE_ANCHORS:
            continue

        expected_log_price = np.interp(
            timestamp_ns,
            anchor_timestamps,
            np.log(anchor_prices),
            left=np.nan,
            right=np.nan,
        )
        expected_price = np.exp(expected_log_price)
        valid_expected = np.isfinite(expected_price) & (expected_price > 0)

        expected_assets = np.exp(
            np.interp(
                timestamp_ns,
                anchor_timestamps,
                np.log(anchor_assets),
                left=np.nan,
                right=np.nan,
            )
        )
        with np.errstate(divide="ignore", invalid="ignore"):
            asset_deviation = (
                np.maximum(
                    total_assets / expected_assets,
                    expected_assets / total_assets,
                )
                - 1
            )
        nav_safe_mask = positive_assets_mask & np.isfinite(expected_assets) & (asset_deviation <= max_anchor_deviation)

        right_anchor = np.searchsorted(anchor_timestamps, timestamp_ns, side="left")
        bracketed_mask = (right_anchor > 0) & (right_anchor < len(anchor_timestamps))
        left_anchor = np.maximum(right_anchor - 1, 0)
        clipped_right_anchor = np.minimum(right_anchor, len(anchor_timestamps) - 1)
        anchor_gap_ns = anchor_timestamps[clipped_right_anchor] - anchor_timestamps[left_anchor]
        gap_safe_mask = bracketed_mask & (anchor_gap_ns <= max_anchor_gap.value)

        epoch_reset = group["epoch_reset"].fillna(False).astype(bool).to_numpy() if "epoch_reset" in group.columns else np.zeros(len(group), dtype=bool)
        boundary_mask = epoch_reset | (np.isfinite(total_assets) & (total_assets <= HYPERCORE_ZERO_NAV_EPSILON))
        boundary_timestamps = np.sort(timestamp_ns[boundary_mask])
        crosses_boundary = np.zeros(len(group), dtype=bool)
        if len(boundary_timestamps):
            left_timestamps = anchor_timestamps[left_anchor]
            right_timestamps = anchor_timestamps[clipped_right_anchor]
            boundary_start = np.searchsorted(boundary_timestamps, left_timestamps, side="left")
            boundary_end = np.searchsorted(boundary_timestamps, right_timestamps, side="right")
            crosses_boundary = bracketed_mask & (boundary_end > boundary_start)
        boundary_safe_mask = ~crosses_boundary

        with np.errstate(divide="ignore", invalid="ignore"):
            symmetric_deviation = (
                np.maximum(
                    share_price / expected_price,
                    expected_price / share_price,
                )
                - 1
            )
        repair_candidate_mask = candidate_mask & valid_expected & positive_price_mask & (symmetric_deviation > max_anchor_deviation)
        repair_mask = repair_candidate_mask & nav_safe_mask & gap_safe_mask & boundary_safe_mask
        deferred_mask = repair_candidate_mask & ~repair_mask

        status_values = np.full(len(group), "", dtype=object)
        status_values[repair_mask] = f"repaired_{anchor_source}"
        if deferred_mask.any():
            failure_reason = np.select(
                [
                    ~boundary_safe_mask & ~gap_safe_mask & ~nav_safe_mask,
                    ~boundary_safe_mask & ~gap_safe_mask,
                    ~boundary_safe_mask & ~nav_safe_mask,
                    ~gap_safe_mask & ~nav_safe_mask,
                    ~boundary_safe_mask,
                    ~gap_safe_mask,
                    ~nav_safe_mask,
                ],
                ["boundary_gap_nav", "boundary_gap", "boundary_nav", "gap_nav", "boundary", "gap", "nav"],
                default="unknown",
            )
            status_values[deferred_mask] = np.asarray([f"deferred_{anchor_source}_{reason}" for reason in failure_reason[deferred_mask]], dtype=object)
            deferred_count += int(deferred_mask.sum())
            nav_deferred_count += int((deferred_mask & ~nav_safe_mask).sum())
            gap_deferred_count += int((deferred_mask & ~gap_safe_mask).sum())
            boundary_deferred_count += int((deferred_mask & ~boundary_safe_mask).sum())
            deferred_vaults.add(str(_vault_id))

        status_mask = repair_mask | deferred_mask
        prices_df.iloc[positions[status_mask], repair_status_col] = status_values[status_mask]

        if repair_mask.any():
            repair_positions = positions[repair_mask]
            prices_df.iloc[repair_positions, share_price_col] = expected_price[repair_mask]
            if anchor_source == "hf":
                hf_fixed_count += int(repair_mask.sum())
                hf_affected_vaults += 1
            else:
                daily_fixed_count += int(repair_mask.sum())
                daily_affected_vaults += 1

    if hf_fixed_count:
        logger(f"Repaired {hf_fixed_count:,} conflicting daily Hypercore share prices across {hf_affected_vaults:,} vaults using HF anchors")
    if daily_fixed_count:
        logger(f"Repaired {daily_fixed_count:,} stale daily Hypercore share prices across {daily_affected_vaults:,} vaults using refreshed daily anchors")
    if deferred_count:
        logger(f"Deferred {deferred_count:,} ambiguous Hypercore share-price repairs across {len(deferred_vaults):,} vaults: {nav_deferred_count:,} failed NAV consistency, {gap_deferred_count:,} exceeded the anchor-gap limit, and {boundary_deferred_count:,} crossed a lifecycle boundary (counts overlap)")

    return prices_df


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

    # Correct scanner-batch unit changes before using HF observations as
    # canonical anchors for the daily/HF source-overlap repair.
    prices_df = stitch_hypercore_high_freq_share_price_batches(prices_df, logger)

    # Cap Hypercore share prices before the standard outlier smoothing.
    # Hypercore share prices can overflow when total_supply → 0;
    # this prevents the smoothing algorithm from being confused by absurd values.
    prices_df = cap_hypercore_share_prices(prices_df, logger)

    # Some historic daily/HF overlap conflicts are real capital flows paired
    # with a synthetic daily price unit. Where persisted ledger flows prove the
    # NAV accounting, retain the observed PnL path rather than interpolating it.
    prices_df = fix_hypercore_flow_reconciled_share_price_paths(prices_df, logger)

    # Hypercore scans may overlap or refresh only a sparse subset of historical
    # rows. Repair stale daily values that conflict sharply with canonical HF
    # observations or the latest daily refresh batch.
    prices_df = fix_hypercore_source_overlap_share_prices(prices_df, logger)

    # The generic fixer derives one row offset from each vault's median polling
    # interval. Hypercore mixes roughly 20-minute, daily, and weekly rows, so one
    # offset cannot represent a stable time window. The source-aware repair above
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
