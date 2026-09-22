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
import tempfile
import time
import warnings
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Callable, TypedDict

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq
from IPython.display import display
from tqdm_loggable.auto import tqdm

from eth_defi.chain import get_chain_name
from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID
from eth_defi.perp_dex.adapter import embed_perp_capability_registry, load_perp_capability_registry
from eth_defi.perp_dex.parquet import PERP_DEX_NATIVE_CHAIN_IDS, build_registered_perp_vault_index, finalise_perp_metric_columns
from eth_defi.types import Percent
from eth_defi.vault.base import VaultSpec, verify_parquet_file
from eth_defi.vault.denomination import (
    DenominationFamily,
    classify_denomination,
    convert_usd_threshold_to_denomination,
)
from eth_defi.vault.settlement_data import (
    merge_vault_settlements_into_cleaned_prices,
)
from eth_defi.vault.vaultdb import DEFAULT_RAW_PRICE_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_VAULT_DATABASE, VaultDatabase, VaultRow
from eth_defi.version_info import stamp_parquet_schema_metadata

#: NAV at or below this value counts as a complete Hypercore wipe-out.
HYPERCORE_ZERO_NAV_EPSILON = 0.000001

#: Fixed UTC interval used to select publishable Hypercore economic checkpoints.
HYPERCORE_ECONOMIC_CHECKPOINT_INTERVAL = pd.Timedelta(hours=4)

#: Maximum elapsed time accepted around a delayed Hypercore NAV confirmation.
MAX_HYPERCORE_PNL_NAV_CONFIRMATION_DELAY = pd.Timedelta(hours=26)

#: Maximum absolute mismatch when recognising a delayed PnL/NAV API update.
HYPERCORE_PNL_NAV_LAG_ABSOLUTE_TOLERANCE = 1.0

#: Maximum relative mismatch when recognising a delayed PnL/NAV API update.
HYPERCORE_PNL_NAV_LAG_RELATIVE_TOLERANCE = 0.001

#: PnL-first, NAV-confirming API lag needs a baseline and two later checkpoints.
MIN_HYPERCORE_PNL_NAV_LAG_CHECKPOINTS = 3

#: New capital must reach this NAV before a recapitalised vault is tracked again.
MIN_HYPERCORE_RECAPITALISATION_ASSETS = 1_000.0

#: A new Hypercore vault needs this NAV before its performance history is published.
#:
#: Use the same threshold as a post-wipe-out epoch, so a vault starts its
#: initial and recovery curves from the first $1,000 NAV observation.
MIN_HYPERCORE_INITIAL_TRACKING_ASSETS = MIN_HYPERCORE_RECAPITALISATION_ASSETS

#: Ignore isolated zero-NAV observations that recover before this delay.
MIN_HYPERCORE_RECAPITALISATION_RECOVERY_DELAY = pd.Timedelta(days=7)

#: Minimum observations needed to infer the median sampling interval.
MIN_ROWS_FOR_INTERVAL_ESTIMATE = 2

#: Internal integer grouping key reused across the large cleaning stages.
#:
#: The column is removed before a cleaned frame is returned. Keeping one
#: first-seen factorisation avoids repeatedly hashing millions of string IDs.
INTERNAL_VAULT_GROUP_COLUMN = "_vault_group_code"


class CleanedVaultPriceRow(TypedDict, total=False):
    """Schema for a single row in the cleaned vault price DataFrame.

    This is the enriched format produced by the cleaning pipeline in this module
    and consumed by
    :py:func:`~eth_defi.research.vault_metrics.calculate_lifetime_metrics`.

    It extends :py:class:`~eth_defi.vault.base.RawVaultPriceRow` with
    denormalised metadata columns (``id``, ``name``, ``event_count``,
    ``protocol``) and computed columns (``returns_1h``). ``returns_1h`` is a
    legacy name: it is the return between consecutive observations and does
    not guarantee a one-hour interval.
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
    - **Investor-flow** amount columns contain direct daily observations for
      Hypercore and Lighter. Event counts are available for Hypercore only.
      These columns remain NaN in cleaned ERC-4626 price data; the metrics
      exporter can estimate signed stablecoin-vault flow later from consecutive
      daily total-assets, total-supply and share-price states.
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
    #: - ``9995`` — ApeX native vaults,
    #:   see :py:data:`~eth_defi.apex.constants.APEX_CHAIN_ID`
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
    #: - ApeX: synthetic id (e.g. ``"apex-vault-2044287989957394432"``)
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

    #: Utilisation ratio (0.0-1.0) for lending vaults. NaN if not applicable.
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

    # -- Native perp DEX account columns --

    #: Sum of current positive position notionals in ``perp_quote_asset``.
    perp_long_notional: float

    #: Sum of absolute current negative position notionals.
    perp_short_notional: float

    #: Count of current non-zero source-market positions.
    perp_open_position_count: int

    #: Largest absolute current position notional.
    perp_largest_position_notional: float

    #: Exact source denomination for all materialised position notionals.
    perp_quote_asset: str

    #: Position availability or freshness state, such as ``available`` or
    #: ``stale``. Stale numeric values remain present for auditability.
    perp_position_data_status: str

    #: Actual source measurement time at one-second resolution.
    #: This remains attached to stale values and observations forward-aligned
    #: to the latest row of a delayed native price feed.
    perp_metrics_observed_at: "pd.Timestamp"

    #: Latest asynchronous vault settlement timestamp in the interval ending at this price row.
    #:
    #: General — populated by merging ``vault-settlements.duckdb`` after
    #: cleaning. ``NaT`` means no known settlement occurred since the previous
    #: cleaned price row.
    vault_settlement_at: "pd.Timestamp"

    # -- Hypercore only columns --
    # Populated for native Hyperliquid vaults (chain 9999). NaN for all other protocols.

    #: Fraction of vault assets controlled by the leader (0.0-1.0).
    #:
    #: Hypercore only — NaN for all other protocols.
    leader_fraction: float

    #: Commission rate charged by the vault leader (0.0-1.0).
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
    #: ``approximated_pnl_nav`` marks a four-hour economic checkpoint,
    #: ``approximated_pnl_nav_clipped`` a positive checkpoint capped at 100%,
    #: ``approximated_pnl_nav_lag_repaired`` a gain whose NAV confirmation
    #: arrived within the bounded confirmation window,
    #: ``approximated_pnl_nav_wipe_out`` a terminal NAV-corroborated loss, and
    #: ``approximated_pnl_nav_carried`` a row that adds no performance, either
    #: because it is not the selected checkpoint for its four-hour UTC bucket,
    #: because its PnL awaits NAV confirmation, or because the epoch is already
    #: at zero after a terminal loss. A later NAV confirmation can revise the
    #: recent provisional PnL-only checkpoint and subsequently compounded
    #: prices.
    #: ``deferred_pnl_nav`` means inputs were missing and
    #: ``deferred_pnl_nav_outlier`` means an uncorroborated negative PnL step
    #: was not allowed to zero a funded vault.
    #: Hypercore only — empty for ordinary observations and other protocols.
    hypercore_repair_status: str

    # -- Direct protocol flow columns --
    # Amounts are populated for Hypercore and Lighter; event counts are
    # populated for Hypercore only. All are NaN in cleaned ERC-4626 rows.

    #: Number of deposit events in the latest day.
    #:
    #: Direct protocol flow — Hypercore only. NaN for other protocols.
    daily_deposit_count: float

    #: Number of withdrawal events in the latest day.
    #:
    #: Direct protocol flow — Hypercore only. NaN for other protocols.
    daily_withdrawal_count: float

    #: Total USD deposited in the latest day.
    #:
    #: Direct protocol flow — Hypercore and Lighter. NaN for other protocols.
    daily_deposit_usd: float

    #: Total USD withdrawn in the latest day.
    #:
    #: Direct protocol flow — Hypercore and Lighter. NaN for other protocols.
    daily_withdrawal_usd: float


#: For manual debugging, we process these vaults first
PRIORITY_SORT_IDS = [
    "8453-0x0d877dc7c8fa3ad980dfdb18b48ec9f8768359c4",
]


def get_vaults_by_id(rows: Mapping[VaultSpec, VaultRow]) -> dict[str, VaultRow]:
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
    "perp_long_notional": float("nan"),
    "perp_short_notional": float("nan"),
    "perp_open_position_count": pd.NA,
    "perp_largest_position_notional": float("nan"),
    "perp_quote_asset": "",
    "perp_position_data_status": "",
    "perp_metrics_observed_at": pd.NaT,
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
    logger: Callable[[str], None] = print,
    duplicate_nav_threshold: float = 1000,
    *,
    assign_names: bool = True,
) -> pd.DataFrame:
    """Ensure all vaults have unique human-readable name.

    - Rerwrite metadata rows
    - Find duplicate vault names
    - Add a running counter to the name to make it unique

    The raw price frame can contain many more rows than the selected
    denomination family.  Callers may therefore request only the canonical
    ``id`` column first, filter the frame, and add names afterwards.  The
    default remains ``True`` for notebook and script callers that expect the
    historical behaviour.

    :param rows:
        Vault metadata keyed by :class:`VaultSpec`.
    :param prices_df:
        Raw price rows to receive ``id`` and optionally ``name``.
    :param logger:
        Logging callback for metadata repair diagnostics.
    :param duplicate_nav_threshold:
        NAV threshold used by the duplicate-name diagnostic.
    :param assign_names:
        Whether to materialise the human-readable name column.  Set to
        ``False`` when the caller will filter by denomination before mapping
        names across the large raw frame.
    :return:
        The input frame with canonical identifiers and, by default, names.
    """
    vaults_by_id = get_vaults_by_id(rows)

    # We use name later as DF index, so we need to make sure they are unique
    counter = 1
    duplicate_names_with_nav = 0
    used_names = set()
    empty_names = set()

    for vault_id, vault in vaults_by_id.items():
        # 40acres historically exposed the generic ERC-4626 name ``Vault``.
        # Restrict this compatibility repair to the detected protocol so other
        # vaults with the same generic name retain their own identity.
        if vault["Name"] == "Vault" and vault.get("Protocol") == "40acres":
            vault["Name"] = "40acres"

        if vault["Name"] in {None, ""}:
            empty_names.add(vault_id)

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
        example_id = next(iter(empty_names))
        example = vaults_by_id[example_id]
        logger(f"Example vault with empty name: {example}")

    # Vaults are identified by their chain and address tuple, make this one human-readable column
    # to make DataFrame wrangling easier
    prices_df["id"] = prices_df["chain"].astype(str) + "-" + prices_df["address"].astype(str)
    if assign_names:
        # ``Series.map`` performs one vectorised dictionary lookup per row and
        # avoids a Python callback for every raw observation.  Missing metadata
        # is deliberately retained as ``<unknown>`` until the caller's
        # explicit metadata check decides whether those rows are dropped.
        name_by_id = {vault_id: vault["Name"] for vault_id, vault in vaults_by_id.items()}
        prices_df["name"] = prices_df["id"].map(name_by_id).fillna("<unknown>")

    return prices_df


def assign_vault_names(
    rows: Mapping[VaultSpec, VaultRow],
    prices_df: pd.DataFrame,
) -> pd.DataFrame:
    """Map repaired vault names onto an already filtered price frame.

    Name assignment is intentionally separate from :func:`assign_unique_names`
    so denomination filtering can happen before the large row-wise mapping.
    The metadata repair loop still runs once per vault, while this operation is
    one vectorised lookup over the selected rows.

    Performance history
    -------------------

    Baseline (2026-09-22 production run): name and denormalised metadata were
    mapped before filtering 22,552,978 raw rows as part of the approximately
    2m14s metadata/selection/sort segment.  In the production-shaped rerun,
    the complete post-read metadata/filter stage (including this mapping,
    denomination membership and default-column expansion) took 12.54 seconds
    on 10,338,606 selected rows; the subsequent sort took 6.56 seconds.  The
    name-only operation was not isolated, so no separate speed-up or stage RSS
    is claimed.  The combined metadata/filter/sort segment, including the
    compact identity scan and Arrow read, was 24.58 seconds with 20.4 GiB
    peak process RSS.

    :param rows:
        Vault metadata keyed by :class:`VaultSpec`.
    :param prices_df:
        Filtered rows containing the canonical ``id`` column.
    :return:
        The input frame with a filled ``name`` column.
    """
    vaults_by_id = get_vaults_by_id(rows)
    name_by_id = {vault_id: vault["Name"] for vault_id, vault in vaults_by_id.items()}
    prices_df["name"] = prices_df["id"].map(name_by_id).fillna("<unknown>")
    return prices_df


def add_denormalised_vault_data(
    rows: dict[VaultSpec, VaultRow],
    prices_df: pd.DataFrame,
    logger: Callable[[str], None] = print,
) -> pd.DataFrame:
    """Add denormalised data to the prices DataFrame.

    Protocol names and event counts are mapped from the metadata database onto
    every selected observation. Unknown identifiers remain a hard error because
    silently exporting partially enriched rows would corrupt downstream reports.

    :param rows:
        Vault metadata keyed by vault specification.
    :param prices_df:
        Selected price rows containing the canonical ``id`` column.
    :param logger:
        Diagnostic callback accepting one message.
    :return:
        The input frame with ``event_count`` and ``protocol`` columns.
    """

    vaults_by_id = get_vaults_by_id(rows)
    try:
        # Keep the old hard failure for an unknown id, but do the large lookup
        # with vectorised maps rather than invoking two Python lambdas for every
        # selected price observation.
        known_ids = prices_df["id"].isin(vaults_by_id)
        if not bool(known_ids.all()):
            missing_id = prices_df.loc[~known_ids, "id"].iloc[0]
            raise KeyError(missing_id)
        event_counts = {vault_id: vault["_detection_data"].deposit_count + vault["_detection_data"].redeem_count for vault_id, vault in vaults_by_id.items()}
        protocols = {vault_id: vault["Protocol"] for vault_id, vault in vaults_by_id.items()}
        prices_df["event_count"] = prices_df["id"].map(event_counts)
        prices_df["protocol"] = prices_df["id"].map(protocols)
    except KeyError as e:
        logger(f"Likely metadata issue: {e}")
        raise

    return prices_df


def filter_vaults_by_denomination_families(
    rows: Mapping[VaultSpec, VaultRow],
    prices_df: pd.DataFrame,
    denomination_families: Iterable[DenominationFamily],
    *,
    logger: Callable[[str], None] = print,
) -> pd.DataFrame:
    """Reduce vault price rows to selected denomination families.

    Classification is based only on the denomination symbol stored in vault
    metadata.  This preserves the legacy stablecoin filter while allowing the
    isolated crypto bundle to select reviewed ETH/BTC wrappers.

    :param rows:
        Vault metadata rows keyed by vault specification.
    :param prices_df:
        Raw/enriched price rows containing the canonical ``id`` column.
    :param denomination_families:
        Families to retain.
    :param logger:
        Log adapter.
    :return:
        Input rows whose vault metadata belongs to a requested family.
    """
    families = frozenset(denomination_families)
    assert families, "At least one denomination family must be selected"
    selected_specs = {spec for spec, vault in rows.items() if classify_denomination(vault.get("Denomination")) in families}
    allowed_vault_ids = {spec.as_string_id() for spec in selected_specs}
    filtered = prices_df.loc[prices_df["id"].isin(allowed_vault_ids)].copy()
    logger(f"Selected {len(selected_specs):,} vaults and {len(filtered):,} price rows for denomination families {sorted(f.value for f in families)}")
    return filtered


def filter_vaults_by_stablecoin(
    rows: Mapping[VaultSpec, VaultRow],
    prices_df: pd.DataFrame,
    logger: Callable[[str], None] = print,
) -> pd.DataFrame:
    """Reduce vaults to stablecoin vaults using the compatibility selection.

    :param rows:
        Vault metadata rows keyed by vault specification.
    :param prices_df:
        Raw/enriched price rows containing the canonical ``id`` column.
    :param logger:
        Log adapter.
    :return:
        Stablecoin-denominated price rows.
    """
    return filter_vaults_by_denomination_families(rows, prices_df, {DenominationFamily.stablecoin}, logger=logger)


def calculate_vault_returns(
    prices_df: pd.DataFrame,
    logger: Callable[[str], None] = print,
) -> pd.DataFrame:
    """Calculate returns for each vault.

    Discard invalid share-price observations and add the compatibility
    ``returns_1h`` column. Consecutive zero prices after a complete loss carry
    a zero return: the loss was already recorded by the first transition to
    zero, and leaving later ``0 / 0`` changes as NaN makes the terminal curve
    unnecessarily difficult for downstream consumers to use.

    Example of input data:

    .. code-block:: none

             chain                                     address  block_number           timestamp  share_price  ...  errors                                                id  name  event_count            protocol
        207  42161  0x487cdc7d21ac8765eff6c0e681aea36ae1594471      13294721 2022-05-30 19:59:22          1.0  ...          42161-0x487cdc7d21ac8765eff6c0e681aea36ae1594471  LDAI           17  <unknown ERC-4626>

    :param prices_df:
        Price rows containing string ``id`` and numeric ``share_price``
        columns, ordered by vault and timestamp.
    :param logger:
        Notebook, console, or structured-log adapter accepting one message.
    :return:
        Price rows with ``returns_1h`` calculated between consecutive rows.

    Performance history
    -------------------

    Baseline (2026-09-22 production run): the implementation performed two
    grouped passes over the cleaned frame (`shift` and `pct_change`), but the
    source log did not isolate their elapsed time.  The optimised one-shift
    implementation completed the complete return-calculation stage in 5.30
    seconds for 10,129,623 rows; stage-only speed-up and RSS are therefore not
    available.  The surrounding production-shaped cleaner run recorded 20.4
    GiB peak process RSS.
    """
    assert isinstance(prices_df, pd.DataFrame), "prices_df must be a pandas DataFrame"

    share_prices = sanitise_share_price_observations(prices_df["share_price"])
    invalid_share_price_mask = share_prices.isna()
    invalid_share_price_count = invalid_share_price_mask.sum()
    if invalid_share_price_count:
        logger(f"Dropping {invalid_share_price_count:,} invalid share-price observations")
        prices_df = prices_df.loc[~invalid_share_price_mask].copy()
        share_prices = share_prices.loc[~invalid_share_price_mask]
    prices_df["share_price"] = share_prices
    # NOTE: ``returns_1h`` is a misnomer.  The column is ``pct_change()``
    # between consecutive rows regardless of their actual time delta.
    # For EVM chains scanned at 1h frequency the name is accurate, but
    # for native protocols (Hypercore, GRVT, Lighter) the rows may be
    # spaced at daily or irregular intervals — producing ~24h or
    # variable-interval returns labelled "1h".  Renaming would break
    # every downstream consumer so the name is kept for compatibility.
    group_column = INTERNAL_VAULT_GROUP_COLUMN if INTERNAL_VAULT_GROUP_COLUMN in prices_df.columns else "id"
    previous_share_price = prices_df.groupby(group_column, sort=False)["share_price"].shift(1)
    # ``share_prices`` contains no missing values after sanitisation, so the
    # compatibility pct-change is exactly current / previous - 1.  Reusing the
    # one grouped shift avoids a second hash-grouping and preserves infinities
    # for transitions from zero, which the later cleaners already handle.
    prices_df["returns_1h"] = prices_df["share_price"].div(previous_share_price).sub(1.0)
    repeated_zero_price = (prices_df["share_price"] == 0) & (previous_share_price == 0)
    prices_df.loc[repeated_zero_price, "returns_1h"] = 0.0
    return prices_df


def sanitise_share_price_observations(share_price_observations: pd.Series) -> pd.Series:
    """Normalise share prices and mark unusable observations as missing.

    Scanner failures may be persisted as IEEE ``NaN`` values in a PyArrow
    ``double`` column. Unlike Arrow nulls, these values are not consistently
    removed by :meth:`pandas.Series.dropna` until they are converted to a
    NumPy-backed floating-point series. Negative share prices are invalid as
    well, whereas zero is a valid complete-loss value.

    :param share_price_observations:
        Sparse share prices indexed by naive UTC timestamps. Values are
        expected to be non-negative denomination-token amounts.
    :return:
        Float64 series preserving the original index and order. Invalid,
        non-finite, and negative values are represented as ``NaN``.
    """
    prices = pd.to_numeric(share_price_observations, errors="coerce").astype("float64")
    valid = np.isfinite(prices.to_numpy()) & (prices >= 0)
    return prices.where(valid)


def clean_returns(  # noqa: PLR0917 - stable cleaner API used by scripts and notebooks
    rows: dict[VaultSpec, VaultRow],
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
    del rows, display

    returns_df = prices_df

    # Hypercore's bounded PnL/NAV approximation runs before this generic
    # cleaner. Retain its audited economic return instead of replacing it with
    # a value that disagrees with the cleaned performance index.
    high_returns_mask = returns_df[returns_col] > outlier_threshold
    if "chain" in returns_df.columns:
        high_returns_mask &= returns_df["chain"] != HYPERCORE_CHAIN_ID
    outlier_returns = returns_df[high_returns_mask]

    # Show a compact summary instead of dumping sample DataFrames to logs.  The
    # group counts do not depend on return ordering, so sorting this potentially
    # wide frame only for logging needlessly copies millions of rows.
    if len(outlier_returns) > 0:
        outlier_counts = outlier_returns.groupby("name").size().sort_values(ascending=False)
        top_outlier_counts = ", ".join(f"{name}={count:,}" for name, count in outlier_counts.head(3).items())
        logger(f"Found {len(outlier_returns):,} outlier returns > {outlier_threshold:%}; top vaults by count: {top_outlier_counts}")
    else:
        logger(f"Found 0 outlier returns > {outlier_threshold:%}")

    # Clean up obv too high returns
    returns_df.loc[high_returns_mask, returns_col] = 0

    return returns_df


def clean_by_tvl(  # noqa: PLR0917 - stable cleaner API used by scripts and notebooks
    rows: dict[VaultSpec, VaultRow],
    prices_df: pd.DataFrame,
    logger=print,
    tvl_threshold_min: float | Mapping[str, float] | Callable[[str], float] = 1000.00,
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
        Absolute minimum NAV, vault-id mapping, or vault-id callback for a
        denomination-aware threshold. Legacy callers retain the USD scalar
        default.
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

    # Kept for API compatibility with notebook and script callers.
    del rows

    returns_df = prices_df

    # TVL based cleaning.
    # Create a mask based on TVL conditions.
    # Clean up returns during low TVL periods
    # pd.Timestamp("2024-02-10")
    if isinstance(tvl_threshold_min, Mapping):
        minimum_thresholds = returns_df["id"].map(tvl_threshold_min)
    elif callable(tvl_threshold_min):
        minimum_thresholds = returns_df["id"].map(tvl_threshold_min)
    else:
        minimum_thresholds = float(tvl_threshold_min)
    mask = returns_df["total_assets"] < minimum_thresholds
    mask |= returns_df["total_assets"] > tvl_threshold_max

    # Clean up by dynamic TVL threshold filtering
    #
    # Morpho Steakhouse USDT Compounder by Yearn case, and similars
    # https://x.com/moo9000/status/1914746350216077544

    # Calculate all-time average of total_assets for each vault
    group_column = INTERNAL_VAULT_GROUP_COLUMN if INTERNAL_VAULT_GROUP_COLUMN in returns_df.columns else "id"
    avg_assets_by_vault = returns_df.groupby(group_column, sort=False)["total_assets"].mean()
    if group_column == "id":
        average_assets = returns_df["id"].map(avg_assets_by_vault)
    else:
        # Group codes remain attached after row-dropping stages, so their
        # values can have gaps even though the original factorisation was
        # contiguous.  Scatter means into a code-sized array before taking;
        # indexing the shorter grouped result directly would mis-map or fail.
        codes = returns_df[group_column].to_numpy(dtype=np.int64)
        if len(codes) == 0:
            average_assets = pd.Series(index=returns_df.index, dtype="float64")
        else:
            average_assets_by_code = np.full(int(codes.max()) + 1, np.nan, dtype="float64")
            grouped_codes = avg_assets_by_vault.index.to_numpy(dtype=np.int64)
            average_assets_by_code[grouped_codes] = avg_assets_by_vault.to_numpy(dtype="float64")
            average_assets = pd.Series(
                average_assets_by_code[codes],
                index=returns_df.index,
            )
    returns_df["avg_assets_by_vault"] = average_assets
    returns_df["dynamic_tvl_threshold"] = returns_df["avg_assets_by_vault"] * tvl_threshold_min_dynamic

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
        mask |= mask.groupby(returns_df[group_column], sort=False).shift(1).fillna(False)

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
    epsilon=0.0025,
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


def remove_inactive_lead_time(  # noqa: PLR0914 - positional mask stages are deliberately explicit
    prices_df: pd.DataFrame,
    logger: Callable[[str], None] = print,
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
        Assumes data is sorted chronologically within each vault. Timestamp
        labels may repeat because modern chains such as Monad can create
        multiple blocks per second. One-second accuracy is sufficient for
        price cleaning, so equal timestamp rows are retained and evaluated by
        their row positions instead of being deduplicated.
    :param logger:
        Progress callback accepting one message.
    :return:
        DataFrame with inactive lead time removed for each vault

    Performance history
    -------------------

    Baseline (2026-09-22 production run): 10,338,606 stablecoin rows took 42
    seconds in a DataFrame-returning ``groupby().apply()``.  The positional
    mask implementation took 6.87 seconds for the same row count (6.1x),
    removing 203,103 rows.  Stage-only RSS was not sampled; the complete
    production-shaped cleaner peaked at 20.4 GiB.
    """

    original_row_count = len(prices_df)
    if original_row_count <= 1:
        logger(f"Removed inactive lead time: {original_row_count:,} -> {original_row_count:,} rows (0 removed from 0 vaults)")
        return prices_df

    # The frame is already sorted by id and timestamp.  Work with contiguous
    # integer ranges instead of constructing one DataFrame per vault through
    # groupby.apply(); duplicate timestamp labels therefore remain harmless.
    group_column = INTERNAL_VAULT_GROUP_COLUMN if INTERNAL_VAULT_GROUP_COLUMN in prices_df.columns else "id"
    ids = prices_df[group_column].to_numpy()
    group_starts = np.r_[0, np.flatnonzero(ids[1:] != ids[:-1]) + 1]
    assert len(group_starts) == prices_df[group_column].nunique(dropna=False), "Vault rows must be contiguous before inactive lead-time removal"
    timestamp_values = pd.to_datetime(prices_df.index).to_numpy(dtype="datetime64[ns]")
    same_group = ids[1:] == ids[:-1]
    assert not np.any(same_group & (timestamp_values[1:] < timestamp_values[:-1])), "Vault rows must be chronological before inactive lead-time removal"
    group_ends = np.r_[group_starts[1:], original_row_count]
    total_supply = prices_df["total_supply"]
    valid_supply = total_supply.gt(0).fillna(False).to_numpy(dtype=bool, na_value=False)
    supply_values = total_supply.to_numpy(dtype=object, na_value=np.nan)
    missing_supply = pd.isna(supply_values)
    safe_supply_values = np.asarray(supply_values, dtype=object).copy()
    safe_supply_values[missing_supply] = 0

    keep_mask = np.ones(original_row_count, dtype=bool)
    rows_removed = 0
    vaults_affected = 0
    for group_start, group_end in zip(group_starts, group_ends, strict=True):
        group_valid_positions = np.flatnonzero(valid_supply[group_start:group_end])
        if group_valid_positions.size == 0:
            continue

        first_valid = group_start + int(group_valid_positions[0])
        initial_supply = safe_supply_values[first_valid]
        changed = safe_supply_values[group_start:group_end] != initial_supply
        # TODO (PR #1586 data-correctness review): ``pd.isna()`` collapses an
        # IEEE NaN stored inside ``double[pyarrow]`` and a true Arrow null into
        # the same mask.  The pre-optimisation Pandas path treated IEEE NaN as
        # a supply change but ignored Arrow nulls.  Keep the current optimised
        # behaviour for now, but distinguish the Arrow validity bitmap here if
        # exact legacy inactive-lead boundaries become important.
        if pd.api.types.is_extension_array_dtype(total_supply.dtype):
            changed &= ~missing_supply[group_start:group_end]
        changed[: first_valid - group_start] = False
        changed_positions = np.flatnonzero(changed)
        keep_start = int(changed_positions[0] + group_start) if changed_positions.size else first_valid

        if keep_start > group_start:
            keep_mask[group_start:keep_start] = False
            rows_removed += keep_start - group_start
            vaults_affected += 1

    filtered_df = prices_df.iloc[keep_mask]

    logger(f"Removed inactive lead time: {original_row_count:,} -> {len(filtered_df):,} rows ({rows_removed:,} removed from {vaults_affected} vaults)")

    return filtered_df


def _repair_hypercore_delayed_nav_returns(  # noqa: PLR0914 - array repair keeps correlated state local
    checkpoint_timestamp_ns: np.ndarray,
    checkpoint_nav: np.ndarray,
    checkpoint_pnl: np.ndarray,
    raw_returns: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Move a positive PnL return to its delayed NAV confirmation.

    Hyperliquid's
    `vaultDetails API <https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint#retrieve-information-about-a-vault>`__
    can update cumulative PnL before the matching NAV. Fish Market's PnL-only
    checkpoint arrived 24 hours 20 minutes after its preceding HF observation;
    the merged daily source supplied the matching NAV 28 minutes later, and a
    later HF observation repeated it. Applying the provisional observation
    produced an economically impossible clipped gain. A 26-hour evidence
    window on both sides admits normal timestamp jitter while avoiding joins
    across the API's weekly all-time observations. Flat intermediate
    checkpoints are skipped, but any other economic change disqualifies the
    repair.

    A later confirming observation can therefore revise the recent
    provisional checkpoint and prices compounded after it. Confirmations more
    than 26 hours away are deliberately ignored even when a scanner gap may be
    responsible, because joining unrelated coarse observations would be less
    defensible.

    :param checkpoint_timestamp_ns:
        Ascending checkpoint timestamps as integer nanoseconds.
    :param checkpoint_nav:
        Finite NAV values as a float array aligned with the timestamps.
    :param checkpoint_pnl:
        Finite cumulative-PnL values as a float array aligned with the
        timestamps.
    :param raw_returns:
        PnL/NAV returns as a float array aligned with the timestamps.
    :return:
        Repaired returns, a boolean mask of provisional PnL checkpoints that
        were carried, and a boolean mask of their NAV confirmations.
    """
    repaired_returns = raw_returns.copy()
    lagged_pnl_checkpoint = np.zeros(len(raw_returns), dtype=bool)
    lag_confirmed_checkpoint = np.zeros(len(raw_returns), dtype=bool)
    if len(raw_returns) < MIN_HYPERCORE_PNL_NAV_LAG_CHECKPOINTS:
        return repaired_returns, lagged_pnl_checkpoint, lag_confirmed_checkpoint

    pnl_changes = np.diff(checkpoint_pnl)
    nav_changes = np.diff(checkpoint_nav)
    elapsed_ns = np.diff(checkpoint_timestamp_ns)
    tolerances = np.maximum(
        HYPERCORE_PNL_NAV_LAG_ABSOLUTE_TOLERANCE,
        np.abs(pnl_changes) * HYPERCORE_PNL_NAV_LAG_RELATIVE_TOLERANCE,
    )
    maximum_delay_ns = MAX_HYPERCORE_PNL_NAV_CONFIRMATION_DELAY.value
    pnl_first_candidate = (pnl_changes > HYPERCORE_PNL_NAV_LAG_ABSOLUTE_TOLERANCE) & (np.abs(nav_changes) <= tolerances) & (elapsed_ns > 0) & (elapsed_ns <= maximum_delay_ns)
    candidate_middle_numbers = np.flatnonzero(pnl_first_candidate) + 1

    for middle_number in candidate_middle_numbers:
        pnl_move = pnl_changes[middle_number - 1]
        tolerance = tolerances[middle_number - 1]
        for confirmation_number in range(middle_number + 1, len(raw_returns)):
            confirmation_delay_ns = checkpoint_timestamp_ns[confirmation_number] - checkpoint_timestamp_ns[middle_number]
            if confirmation_delay_ns > maximum_delay_ns:
                break

            confirmation_pnl_change = checkpoint_pnl[confirmation_number] - checkpoint_pnl[middle_number]
            confirmation_nav_change = checkpoint_nav[confirmation_number] - checkpoint_nav[middle_number]
            if abs(confirmation_pnl_change) <= tolerance and abs(confirmation_nav_change) <= tolerance:
                continue

            if abs(confirmation_pnl_change) <= tolerance and abs(confirmation_nav_change - pnl_move) <= tolerance:
                lagged_pnl_checkpoint[middle_number] = True
                lag_confirmed_checkpoint[confirmation_number] = True
                repaired_returns[middle_number] = 0.0
                confirmed_capital_base = max(checkpoint_nav[middle_number - 1], checkpoint_nav[confirmation_number], 1.0)
                repaired_returns[confirmation_number] = pnl_move / confirmed_capital_base
            break

    return repaired_returns, lagged_pnl_checkpoint, lag_confirmed_checkpoint


def approximate_hypercore_share_prices_from_pnl_nav(  # noqa: PLR0914
    prices_df: pd.DataFrame,
    logger: Callable[[str], None] = print,
    max_positive_return: Percent = 1.0,
) -> pd.DataFrame:
    """Build an approximate Hypercore economic-performance index from PnL and NAV.

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
    index. One freshest usable checkpoint is selected per fixed four-hour UTC
    bucket while retaining its original API timestamp. Missing buckets are not
    manufactured, and older history remains daily or weekly where Hyperliquid
    has already downsampled it. Between checkpoints, cumulative account-PnL
    change is divided by the larger of opening NAV, closing NAV, and one dollar;
    the resulting returns are compounded from ``1.0`` within each
    recapitalisation epoch. This denominator prevents unknown capital-flow
    timing and small NAV from manufacturing performance. Positive returns are
    capped at ``max_positive_return`` per selected checkpoint. A return at or
    below ``-100%`` is accepted only when NAV is zero and does not recover in
    the same epoch; otherwise the price is carried because applying one
    questionable negative PnL baseline would permanently zero the index.
    Non-checkpoint rows are also carried, avoiding duplicate daily/HF returns
    without interpolating future information backwards.

    A July 2026 follow-up found a narrower timestamp problem in Fish Market.
    Cumulative PnL increased by ``$2,169.43`` on 17 March while NAV remained
    unchanged; the same ``$2,169.43`` appeared in NAV on 18 March with no
    additional PnL or capital flow. Treating the first half of this staggered
    API update as a complete checkpoint produced a clipped ``+100%`` return.
    When a checkpoint within 26 hours exhibits this exact PnL-then-NAV pattern,
    within a small numerical tolerance, this function carries the premature
    checkpoint and applies the return at the confirming checkpoint using the
    larger opening/confirmed NAV. Flat intermediate checkpoints may occur; any
    other economic change disqualifies the repair. This gives approximately
    ``+54.5%`` for Fish Market. The bounded, positive-only and value-matching
    conditions avoid suppressing large but reconciled gains in Magixbox, IKAGI
    and Satori Quantum HF Vault. Because confirmation arrives later, the most
    recent provisional checkpoint and prices compounded after it may be revised
    on the next cleaning run.

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
        Maximum approximate return applied at one four-hour checkpoint. The
        default is ``1.0`` (100%).
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
    lag_repaired_count = 0
    affected_vaults = 0

    for _vault_id, relative_positions in prices_df.loc[hypercore_mask, ["id"]].groupby("id", sort=False).indices.items():
        positions = hypercore_positions[np.asarray(relative_positions, dtype=int)]
        timestamps = all_timestamps[positions]
        timestamp_ns = timestamps.to_numpy(dtype="datetime64[ns]").astype("int64")
        bucket_ns = timestamps.floor(HYPERCORE_ECONOMIC_CHECKPOINT_INTERVAL).to_numpy(dtype="datetime64[ns]").astype("int64")
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
            epoch_buckets = bucket_ns[epoch_positions]
            usable = np.isfinite(nav[epoch_positions]) & np.isfinite(pnl[epoch_positions])
            usable_positions = epoch_positions[usable]

            if len(usable_positions):
                checkpoint_order = np.lexsort(
                    (
                        hf_priority[usable_positions].astype(np.int8),
                        timestamp_ns[usable_positions],
                        written_at[usable_positions],
                        bucket_ns[usable_positions],
                    )
                )
                ordered_positions = usable_positions[checkpoint_order]
                ordered_buckets = bucket_ns[ordered_positions]
                latest_per_bucket = np.r_[ordered_buckets[1:] != ordered_buckets[:-1], True]
                checkpoints = ordered_positions[latest_per_bucket]
            else:
                checkpoints = np.asarray([], dtype=int)
            checkpoint_buckets = bucket_ns[checkpoints] if len(checkpoints) else np.asarray([], dtype=np.int64)
            bucket_has_checkpoint = np.isin(epoch_buckets, checkpoint_buckets)
            repair_status[epoch_positions[bucket_has_checkpoint]] = "approximated_pnl_nav_carried"
            repair_status[epoch_positions[~bucket_has_checkpoint]] = "deferred_pnl_nav"
            missing_count += int((~bucket_has_checkpoint).sum())

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

            raw_returns, lagged_pnl_checkpoint, lag_confirmed_checkpoint = _repair_hypercore_delayed_nav_returns(
                checkpoint_timestamp_ns=timestamp_ns[checkpoints],
                checkpoint_nav=checkpoint_nav,
                checkpoint_pnl=checkpoint_pnl,
                raw_returns=raw_returns,
            )

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

            # A complete loss is absorbing within one capital epoch. Later API
            # baseline changes cannot create performance or another wipe-out
            # after the clean index has reached zero.
            wipe_out_numbers = np.flatnonzero(corroborated_wipe_out)
            post_wipe_out = np.zeros(len(checkpoints), dtype=bool)
            if len(wipe_out_numbers):
                post_wipe_out[int(wipe_out_numbers[0]) + 1 :] = True
                applied_returns[post_wipe_out] = 0.0
                positive_clipped[post_wipe_out] = False
                deferred_absorbing_loss[post_wipe_out] = False
                corroborated_wipe_out[post_wipe_out] = False

            checkpoint_prices = np.cumprod(1.0 + applied_returns)

            checkpoint_status = np.full(len(checkpoints), "approximated_pnl_nav", dtype=object)
            checkpoint_status[lagged_pnl_checkpoint] = "approximated_pnl_nav_carried"
            checkpoint_status[lag_confirmed_checkpoint] = "approximated_pnl_nav_lag_repaired"
            checkpoint_status[positive_clipped] = "approximated_pnl_nav_clipped"
            checkpoint_status[deferred_absorbing_loss] = "deferred_pnl_nav_outlier"
            checkpoint_status[corroborated_wipe_out] = "approximated_pnl_nav_wipe_out"
            checkpoint_status[post_wipe_out] = "approximated_pnl_nav_carried"
            repair_status[checkpoints] = checkpoint_status

            checkpoint_lookup = np.searchsorted(checkpoints, epoch_positions, side="right") - 1
            has_previous_checkpoint = checkpoint_lookup >= 0
            epoch_prices = np.ones(len(epoch_positions), dtype=float)
            epoch_prices[has_previous_checkpoint] = checkpoint_prices[checkpoint_lookup[has_previous_checkpoint]]
            clean_price[epoch_positions] = epoch_prices

            checkpoint_count += len(checkpoints)
            carried_count += int(bucket_has_checkpoint.sum()) - len(checkpoints) + int(post_wipe_out.sum())
            carried_count += int(lagged_pnl_checkpoint.sum())
            clipped_count += int(positive_clipped.sum())
            lag_repaired_count += int(lag_confirmed_checkpoint.sum())
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

    logger(f"Approximated Hypercore economic share prices for {affected_vaults:,} vaults using {checkpoint_count:,} four-hour PnL/NAV checkpoints; carried {carried_count:,} non-performance rows, repaired {lag_repaired_count:,} delayed NAV confirmations, deferred {missing_count:,} missing-input rows and {deferred_outlier_count:,} uncorroborated losses, capped {clipped_count:,} gains and recorded {wipe_out_count:,} terminal wipe-outs")
    return prices_df


def discard_hypercore_initial_low_tvl_history(
    prices_df: pd.DataFrame,
    logger: Callable[[str], None] = print,
    min_tracking_assets: float = MIN_HYPERCORE_INITIAL_TRACKING_ASSETS,
) -> pd.DataFrame:
    """Discard an unfunded Hypercore vault's leading price observations.

    Hypercore has no authoritative historical share price. Its cleaned share
    price is instead a reconstructed PnL/NAV performance index, normally
    rebased to one at the first retained observation. A few dollars of
    bootstrap capital can therefore become the lifetime high-water mark for a
    vault that later manages meaningful capital, producing an irrelevant
    near-100% drawdown. Start the published performance history only once the
    vault reaches a meaningful initial capital base.

    This is deliberately an initial-history rule. A funded vault that later
    falls below the threshold keeps its observations, because that decline is
    material investor performance information. A vault that has not yet
    reached the threshold has no cleaned price history; its raw observations
    remain available for audit and will be included automatically once a
    future cleaner run sees sufficient capital.

    :param prices_df:
        Vault price data indexed by timestamp, with ``id``, ``chain``, and
        ``total_assets`` columns. It must be sorted by vault and timestamp.
    :param logger:
        Notebook or console logging function.
    :param min_tracking_assets:
        Minimum USD NAV needed before publishing the initial Hypercore
        performance history.
    :return:
        Price data without the unfunded initial Hypercore observations.
    """
    hypercore_mask = prices_df["chain"] == HYPERCORE_CHAIN_ID
    if not hypercore_mask.any():
        return prices_df

    remove_mask = np.zeros(len(prices_df), dtype=bool)
    affected_vaults = 0
    unfunded_vaults = 0
    hypercore_positions = np.flatnonzero(hypercore_mask.to_numpy())
    all_total_assets = prices_df["total_assets"].to_numpy(dtype=float)

    for _vault_id, row_positions in prices_df.loc[hypercore_mask, ["id"]].groupby("id", sort=False).indices.items():
        positions = hypercore_positions[np.asarray(row_positions, dtype=int)]
        total_assets = all_total_assets[positions]
        tracking_positions = np.flatnonzero(np.isfinite(total_assets) & (total_assets >= min_tracking_assets))

        if len(tracking_positions) == 0:
            remove_mask[positions] = True
            affected_vaults += 1
            unfunded_vaults += 1
            continue

        first_tracking_position = int(tracking_positions[0])
        if first_tracking_position > 0:
            remove_mask[positions[:first_tracking_position]] = True
            affected_vaults += 1

    if not remove_mask.any():
        return prices_df

    filtered_prices_df = prices_df.iloc[~remove_mask].copy()
    logger(f"Discarded {int(remove_mask.sum()):,} initial low-TVL Hypercore price rows across {affected_vaults:,} vaults; tracking starts at ${min_tracking_assets:,.0f} NAV and {unfunded_vaults:,} vaults have not reached it")
    return filtered_prices_df


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


def _fix_outlier_share_prices(  # noqa: PLR0914 - array repair keeps correlated state local
    prices_df: pd.DataFrame,
    logger: Callable[[str], None],
    max_diff: float,
    look_back_hours: int,
    look_ahead_hours: int,
) -> pd.DataFrame:
    """Repair share-price spikes with positional NumPy gathers.

    The previous implementation paid for a full DataFrame ``groupby().apply()``
    callback, forward-filled every column once per vault and indexed each
    abnormal row with ``iloc``/``iat``.  This implementation keeps the same
    group-local fill and boundary semantics, but performs candidate lookup and
    repair decisions on contiguous numerical arrays.  The complete legacy
    frame is still returned, including its forward-filled state columns and
    untouched interval-flow observations.

    Performance history
    -------------------

    Baseline (2026-09-22 production run): 8,535,236 EVM rows took 3 minutes
    57 seconds.  The optimised production-shaped EVM repair stage, including
    mixed Hypercore/EVM positional reconstruction, took 55.71 seconds for the
    same 8,535,236 rows (4.3x).  Its forward-fill substage took 4.14 seconds
    and the numerical gather/mask kernel 1.17 seconds.  Stage-only RSS was not
    sampled; the complete cleaner peaked at 20.4 GiB.

    :param prices_df:
        Price rows sorted contiguously by ``id`` and timestamp.
    :param logger:
        Logging callback accepting one message.
    :param max_diff:
        Symmetric relative change threshold for an outlier.
    :param look_back_hours:
        Nominal look-back window in hours.
    :param look_ahead_hours:
        Nominal look-ahead window in hours.
    :return:
        Cleaned frame with the original index and columns.
    """
    original_index = prices_df.index.copy()
    working = prices_df.reset_index(drop=True).copy()
    working["raw_share_price"] = working["share_price"]
    if working.empty:
        working.index = original_index
        working.index.name = prices_df.index.name
        return working

    flow_columns = [
        column
        for column in (
            "daily_deposit_count",
            "daily_withdrawal_count",
            "daily_deposit_usd",
            "daily_withdrawal_usd",
        )
        if column in working.columns
    ]
    source_flows = working[flow_columns].copy()
    group_column = INTERNAL_VAULT_GROUP_COLUMN if INTERNAL_VAULT_GROUP_COLUMN in working.columns else "id"
    state_columns = [column for column in working.columns if column not in {"id", group_column, *flow_columns}]

    # Preserve the old output side effect (all sparse state columns are filled
    # per vault) with one grouped operation rather than one DataFrame callback
    # and temporary frame allocation per vault. Flow observations are interval
    # facts, so restore their original unknown markers afterwards.
    fill_started_at = time.perf_counter()
    working[state_columns] = working.groupby(group_column, sort=False, observed=True)[state_columns].ffill()
    if flow_columns:
        working[flow_columns] = source_flows
    logger(f"Share-price outlier state forward-fill: {len(working):,} rows in {time.perf_counter() - fill_started_at:.2f}s")

    ids = working[group_column].to_numpy()
    row_count = len(working)
    group_starts = np.r_[0, np.flatnonzero(ids[1:] != ids[:-1]) + 1]
    assert len(group_starts) == working[group_column].nunique(dropna=False), "Vault rows must be contiguous before outlier repair"
    timestamp_values = pd.to_datetime(original_index).to_numpy(dtype="datetime64[ns]")
    same_group = ids[1:] == ids[:-1]
    assert not np.any(same_group & (timestamp_values[1:] < timestamp_values[:-1])), "Vault rows must be chronological before outlier repair"
    group_ends = np.r_[group_starts[1:], row_count]
    share_values = pd.to_numeric(working["share_price"], errors="coerce").to_numpy(dtype="float64", na_value=np.nan)
    raw_values = pd.to_numeric(working["raw_share_price"], errors="coerce").to_numpy(dtype="float64", na_value=np.nan)
    group_sizes = group_ends - group_starts
    group_count = len(group_starts)
    effective_look_backs = np.full(group_count, look_back_hours, dtype=np.int64)
    effective_look_aheads = np.full(group_count, look_ahead_hours, dtype=np.int64)
    if isinstance(original_index, pd.DatetimeIndex):
        # Interval inference remains one small loop per vault.  The expensive
        # candidate gathers below operate on one contiguous array instead of
        # allocating and filling two arrays for every group.
        for group_number, (group_start, group_end) in enumerate(zip(group_starts, group_ends, strict=True)):
            group_size = int(group_end - group_start)
            if group_size < MIN_ROWS_FOR_INTERVAL_ESTIMATE:
                continue
            median_interval_ns = np.median(np.diff(timestamp_values[group_start:group_end].astype("int64")))
            if np.isfinite(median_interval_ns) and median_interval_ns > 0:
                rows_per_hour = pd.Timedelta(hours=1).value / median_interval_ns
                effective_look_backs[group_number] = max(1, round(look_back_hours * rows_per_hour))
                effective_look_aheads[group_number] = max(1, round(look_ahead_hours * rows_per_hour))

    row_positions = np.arange(row_count, dtype=np.int64)
    group_starts_per_row = np.repeat(group_starts, group_sizes)
    group_ends_per_row = np.repeat(group_ends, group_sizes)
    local_positions = row_positions - group_starts_per_row
    group_sizes_per_row = np.repeat(group_sizes, group_sizes)
    look_backs_per_row = np.repeat(effective_look_backs, group_sizes)
    look_aheads_per_row = np.repeat(effective_look_aheads, group_sizes)

    kernel_started_at = time.perf_counter()
    next_shift = np.full(row_count, np.nan, dtype="float64")
    next_positions = row_positions + look_aheads_per_row
    valid_next = local_positions + look_aheads_per_row < group_sizes_per_row
    next_shift[valid_next] = share_values[next_positions[valid_next]]
    prev_shift = np.full(row_count, np.nan, dtype="float64")
    prev_positions = row_positions - look_backs_per_row
    valid_previous = local_positions >= look_backs_per_row
    prev_shift[valid_previous] = share_values[prev_positions[valid_previous]]

    # Fill candidate NaNs without crossing a vault boundary.  A global
    # cumulative gather is equivalent to per-group ffill/bfill once positions
    # before the current group are invalidated by its start/end boundary.
    # TODO (PR #1586 data-correctness review): NumPy treats IEEE NaN as a
    # missing candidate and searches past it.  The legacy Arrow-backed
    # ffill/bfill path did not fill IEEE NaN, so an EVM-only clean can now
    # repair a spike that the old implementation left untouched.  This is
    # accepted for now; preserve this note until the intended NaN policy is
    # covered by an explicit regression test.
    candidate_positions = np.arange(row_count, dtype=np.int64)
    valid_next_shift = ~np.isnan(next_shift)
    last_valid = np.maximum.accumulate(np.where(valid_next_shift, candidate_positions, -1))
    last_valid[last_valid < group_starts_per_row] = -1
    next_candidate = np.full(row_count, np.nan, dtype="float64")
    has_next_candidate = last_valid >= 0
    next_candidate[has_next_candidate] = next_shift[last_valid[has_next_candidate]]

    valid_previous_shift = ~np.isnan(prev_shift)
    next_valid = np.minimum.accumulate(np.where(valid_previous_shift, candidate_positions, row_count)[::-1])[::-1]
    next_valid[next_valid >= group_ends_per_row] = row_count
    prev_candidate = np.full(row_count, np.nan, dtype="float64")
    has_previous_candidate = next_valid < row_count
    prev_candidate[has_previous_candidate] = prev_shift[next_valid[has_previous_candidate]]

    with np.errstate(divide="ignore", invalid="ignore"):
        pct_change_prev = np.maximum(
            np.abs(prev_candidate / share_values - 1),
            np.abs(share_values / prev_candidate - 1),
        )
        pct_change_next = np.maximum(
            np.abs(next_candidate / share_values - 1),
            np.abs(share_values / next_candidate - 1),
        )
        abnormal = (pct_change_prev > max_diff) | (pct_change_next > max_diff)
        candidate_pair_change = np.maximum(
            np.abs(next_candidate / prev_candidate - 1),
            np.abs(prev_candidate / next_candidate - 1),
        )

    repairable = abnormal & ~np.isnan(next_candidate) & ~np.isnan(prev_candidate) & (prev_candidate != 0) & (next_candidate != 0) & (candidate_pair_change < max_diff)
    repaired_values = share_values.copy()
    repaired_values[repairable] = (next_candidate[repairable] + prev_candidate[repairable]) / 2
    share_prices_fixed = int(repairable.sum())
    logger(f"Share-price outlier array kernel: {row_count:,} rows in {time.perf_counter() - kernel_started_at:.2f}s")

    working["share_price"] = repaired_values
    changed_share_values = working["share_price"].to_numpy(dtype="float64", na_value=np.nan)
    change_mask = (changed_share_values != raw_values) & ~np.isnan(raw_values)
    change_count = int(change_mask.sum())
    logger(f"Share prices fix count {share_prices_fixed}, updated {change_count:,} / {len(working):,} rows with abnormal share_price spikes (> {max_diff:.2%})")

    working.index = original_index
    working.index.name = prices_df.index.name
    return working


def fix_outlier_share_prices(
    prices_df: pd.DataFrame,
    logger: Callable[[str], None] = print,
    max_diff: Percent = 0.33,
    look_back_hours: int = 24,
    look_ahead_hours: int = 24,
) -> pd.DataFrame:
    """Repair isolated share-price observations that disagree with their neighbours.

    Scanner inputs can briefly jump because of oracle errors, bad transactions
    or other source anomalies and then return to the surrounding level. The
    cleaner replaces an observation only when both time-scaled neighbours
    corroborate one another. The original value remains available for audit in
    ``raw_share_price``. See the ``check-share-price`` script for individual
    investigations.

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

    :param prices_df:
        Price rows sorted by vault identifier and timestamp. The frame must
        contain ``id`` and numeric ``share_price`` columns and use a datetime
        index for time-based window scaling.
    :param logger:
        Progress callback accepting one message.
    :param max_diff:
        Symmetric relative-change threshold used to identify an outlier.
    :param look_back_hours:
        Nominal look-back window in hours.
    :param look_ahead_hours:
        Nominal look-ahead window in hours.
    :return:
        A frame with repaired ``share_price`` values and the original values
        retained in ``raw_share_price``.
    """
    return _fix_outlier_share_prices(prices_df, logger, max_diff, look_back_hours, look_ahead_hours)


def sort_and_index_vault_prices(
    prices_df: pd.DataFrame,
    priority_ids: list[str],
) -> pd.DataFrame:
    """Sort observations into the stable order expected by cleaning stages.

    Priority vaults are placed first for interactive diagnostics; all rows are
    then ordered by identifier and timestamp. Pandas' multi-key
    lexicographical sorter retains the input order of identical keys, including
    duplicate timestamps; later positional kernels treat that order as
    authoritative. The single-key ``kind`` option is deliberately not cited as
    the guarantee because Pandas ignores it for multi-key sorts.

    :param prices_df:
        Price rows with a datetime index and canonical ``id`` column.
    :param priority_ids:
        Vault identifiers that should be processed before the remaining rows.
    :return:
        Sorted rows with ``timestamp`` restored as the index.
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


def _copy_columns_by_position(
    target: pd.DataFrame,
    source: pd.DataFrame,
    row_positions: np.ndarray,
    excluded_columns: Collection[str] = frozenset(),
) -> None:
    """Copy a cleaned subset back without duplicate-index label alignment.

    The source frame must have been selected from ``target`` with
    ``target.iloc[row_positions]`` and must retain that row order.  Copying one
    column at a time preserves extension dtypes and avoids materialising the
    full mixed-protocol frame as an object array.  Integer positions are
    essential because several observations may share the same timestamp.

    :param target:
        Full destination DataFrame modified in place.
    :param source:
        Cleaned subset with the same number and ordering as ``row_positions``.
    :param row_positions:
        One-dimensional integer positions identifying destination rows.
    :param excluded_columns:
        Identity or helper columns which the cleaner must not rewrite.
    :return:
        ``None``.
    """
    assert row_positions.ndim == 1, "Row positions must be one-dimensional"
    assert len(source) == len(row_positions), "Source rows and destination positions must match"
    missing_columns = set(source.columns) - set(target.columns)
    assert not missing_columns, f"Source columns missing from target: {sorted(missing_columns)}"
    for column in source.columns:
        if column in excluded_columns:
            continue
        target.iloc[row_positions, target.columns.get_loc(column)] = source[column].array


def process_raw_vault_scan_data(  # noqa: PLR0914 - established cleaner orchestration API
    rows: dict[VaultSpec, VaultRow] | VaultDatabase,
    prices_df: pd.DataFrame,
    logger: Callable[[str], None] = print,
    display: Callable[[pd.DataFrame], None] = lambda _: None,
    diagnose_vault_id: str | None = None,
    *,
    denomination_families: Iterable[DenominationFamily] | None = None,
    crypto_min_tvl_usd: Decimal | None = None,
) -> pd.DataFrame:
    """Preprocess vault data for further analysis.

    - Assign unique names to vaults
    - Add denormalised vault data to prices DataFrame
    - Filter out non-stablecoin vaults
    - Calculate returns, rolling metrics

    :param rows:
        Metadata rows from the vault database, or a loaded database instance.
    :param prices_df:
        Raw price observations containing at least chain, address, timestamp,
        share-price, total-assets and total-supply columns.
    :param logger:
        Notebook, console or structured-log callback accepting one message.
    :param display:
        Optional DataFrame display callback used for one-vault diagnostics.
    :param diagnose_vault_id:
        Optional canonical vault identifier to show between cleaning stages.
    :param denomination_families:
        Denomination families to retain; defaults to stablecoins.
    :param crypto_min_tvl_usd:
        Optional USD TVL threshold converted into ETH/BTC denomination units.
    :return:
        Cleaned, denormalised price observations ordered by vault and time.

    Performance history
    -------------------

    Baseline (2026-09-22 production run): stablecoin cleaning processed
    22,552,978 raw rows and took about 11 minutes 25 seconds.  The first
    optimisation pass filters before wide enrichment, reuses integer group
    codes and replaces the inactive-lead and outlier callbacks with positional
    array masks.  The production-shaped rerun processed 22,552,978 raw rows,
    selected 10,338,606 stablecoin rows and produced 10,116,623 cleaned rows
    in 146.02 seconds, versus 11m25s in the source log (4.7x).  Peak process
    RSS was 20.4 GiB versus no RSS measurement in the original log.  The
    stage breakdown is emitted by this function's timer logs; the exact input
    was the 333,430,642-byte, 38-column Parquet copy from 2026-09-22.
    """

    stage_started_at = time.perf_counter()
    raw_row_count = len(prices_df)

    # Only the canonical id is needed for metadata validation and denomination
    # selection.  State defaults, protocol names and event counts are wide
    # columns; materialising them before this filter made the full 22-million
    # row sort pay for data that was immediately discarded.
    assign_unique_names(rows, prices_df, logger, assign_names=False)

    missing_ids = check_missing_metadata(rows, prices_df["id"], prices_df, logger)
    if missing_ids:
        before_count = len(prices_df)
        prices_df = prices_df[~prices_df["id"].isin(missing_ids)]
        logger(f"Dropped {before_count - len(prices_df):,} price rows for {len(missing_ids):,} vaults without metadata")

    families = frozenset(denomination_families or {DenominationFamily.stablecoin})
    prices_df = filter_vaults_by_denomination_families(
        rows,
        prices_df,
        families,
        logger=logger,
    )

    # Add the wide compatibility/state columns only after the selected family
    # has been reduced.  This preserves every output default while shrinking
    # all later groupby, sort and cleaner allocations.
    prices_df = ensure_vault_state_columns(prices_df)
    prices_df = derive_deposit_closed_reason(prices_df)
    assign_vault_names(rows, prices_df)
    prices_df = add_denormalised_vault_data(rows, prices_df, logger)

    logger(f"Vault cleaning stage metadata/filter: {raw_row_count:,} -> {len(prices_df):,} rows in {time.perf_counter() - stage_started_at:.2f}s")

    if diagnose_vault_id:
        vault_prices_df = prices_df[prices_df["id"] == diagnose_vault_id]
        logger("After add_denormalised_vault_data():")
        display(vault_prices_df)

    # ``read_parquet(dtype_backend="pyarrow")`` may return a
    # ``timestamp[ms][pyarrow]`` Series. Pandas then creates a generic Index,
    # even though the values are datetimes, and the chronological processing
    # below correctly rejects it. Materialise the canonical DatetimeIndex
    # representation before indexing. This was surfaced by the initial shared
    # chain scan for Lighter Ethereum and Lighter Robinhood.
    prices_df["timestamp"] = pd.to_datetime(prices_df["timestamp"])
    prices_df = prices_df.set_index("timestamp")

    sort_started_at = time.perf_counter()
    prices_df = sort_and_index_vault_prices(prices_df, PRIORITY_SORT_IDS)
    logger(f"Vault cleaning stage sort: {len(prices_df):,} rows in {time.perf_counter() - sort_started_at:.2f}s")
    if prices_df.empty:
        logger("No selected denomination price rows remain; skipping return and TVL cleaning")
        # Preserve the public cleaned schema even when a valid denomination
        # selection has no rows.  Downstream Parquet verification requires
        # the audit and return columns, and finalisation supplies typed perp
        # defaults without inventing an observation.
        prices_df["raw_share_price"] = prices_df["share_price"].astype("float64")
        prices_df["returns_1h"] = pd.Series(index=prices_df.index, dtype="float64")
        return finalise_perp_metric_columns(prices_df, ())

    # All later stages consume the same stable ``id`` order.  Factorise it once
    # after the only required sort and carry the integer code through row masks;
    # the helper is dropped before returning so it never becomes part of the
    # public Parquet schema.
    prices_df[INTERNAL_VAULT_GROUP_COLUMN] = pd.factorize(prices_df["id"], sort=False)[0].astype("int32")
    # Disabled as low and does not result to any savings
    # prices_df = filter_unneeded_row(prices_df, logger)

    stage_input_rows = len(prices_df)
    stage_started_at = time.perf_counter()
    prices_df = remove_inactive_lead_time(prices_df, logger)
    logger(f"Vault cleaning stage inactive lead time: {stage_input_rows:,} -> {len(prices_df):,} rows in {time.perf_counter() - stage_started_at:.2f}s")

    # Hypercore's share price is a reconstructed PnL/NAV performance index.
    # Do not let an insignificant bootstrap deposit become its lifetime basis.
    stage_input_rows = len(prices_df)
    stage_started_at = time.perf_counter()
    prices_df = discard_hypercore_initial_low_tvl_history(prices_df, logger)
    logger(f"Vault cleaning stage Hypercore initial low-TVL discard: {stage_input_rows:,} -> {len(prices_df):,} rows in {time.perf_counter() - stage_started_at:.2f}s")

    # A complete Hypercore wipe-out followed by later deposits is a new
    # investment epoch, not a recoverable price movement. Begin the cleaned
    # history from the meaningful recapitalisation point.
    stage_input_rows = len(prices_df)
    stage_started_at = time.perf_counter()
    prices_df = discard_hypercore_pre_recapitalisation_history(prices_df, logger)
    logger(f"Vault cleaning stage Hypercore recapitalisation discard: {stage_input_rows:,} -> {len(prices_df):,} rows in {time.perf_counter() - stage_started_at:.2f}s")

    if diagnose_vault_id:
        vault_prices_df = prices_df[prices_df["id"] == diagnose_vault_id]
        logger("After discard_hypercore_pre_recapitalisation_history():")
        display(vault_prices_df)

    # Hyperliquid does not expose an authoritative historical unit price or
    # share supply. Replace every raw Hypercore scanner unit with one
    # conservative PnL/NAV performance index before calculating returns.
    stage_input_rows = len(prices_df)
    stage_started_at = time.perf_counter()
    prices_df = approximate_hypercore_share_prices_from_pnl_nav(prices_df, logger)
    logger(f"Vault cleaning stage Hypercore PnL/NAV approximation: {stage_input_rows:,} -> {len(prices_df):,} rows in {time.perf_counter() - stage_started_at:.2f}s")

    # The generic fixer derives one row offset from each vault's median polling
    # interval. Hypercore mixes roughly 20-minute, daily, and weekly rows, so one
    # offset cannot represent a stable time window. The economic index above
    # handles Hypercore; keep the generic fixer limited to EVM vaults.

    hypercore_mask = prices_df["chain"] == HYPERCORE_CHAIN_ID
    has_hypercore = hypercore_mask.any()
    has_evm = (~hypercore_mask).any()

    if has_hypercore and has_evm:
        # Fix outlier share prices only for EVM rows, operating in-place
        stage_input_rows = int((~hypercore_mask).sum())
        stage_started_at = time.perf_counter()
        evm_positions = np.flatnonzero((~hypercore_mask).to_numpy(dtype=bool, na_value=False))
        # Use integer row positions for both extraction and reconstruction. A
        # timestamp label can repeat, and DataFrame ``.loc`` assignment then
        # spends tens of seconds aligning a wide duplicate-index frame even
        # though the cleaner's row order is already authoritative.
        evm_df = prices_df.iloc[evm_positions]
        fixed_evm = fix_outlier_share_prices(evm_df, logger)
        # Assign one extension-array column at a time. Converting the complete
        # mixed Arrow-backed frame to one object ``ndarray`` both spikes RSS and
        # makes PyArrow string blocks attempt to box millions of values. The
        # per-column positional writes retain each destination dtype and avoid
        # duplicate-index alignment.
        _copy_columns_by_position(
            prices_df,
            fixed_evm,
            evm_positions,
            excluded_columns={"id", INTERNAL_VAULT_GROUP_COLUMN},
        )
        del evm_df, fixed_evm, evm_positions
        logger(f"Vault cleaning stage EVM outlier repair: {stage_input_rows:,} -> {stage_input_rows:,} rows in {time.perf_counter() - stage_started_at:.2f}s")
    elif has_evm:
        stage_input_rows = len(prices_df)
        stage_started_at = time.perf_counter()
        prices_df = fix_outlier_share_prices(prices_df, logger)
        logger(f"Vault cleaning stage EVM outlier repair: {stage_input_rows:,} -> {len(prices_df):,} rows in {time.perf_counter() - stage_started_at:.2f}s")
    else:
        logger("Skipping fix_outlier_share_prices() for Hypercore-only dataset")

    if diagnose_vault_id:
        vault_prices_df = prices_df[prices_df["id"] == diagnose_vault_id]
        logger("After fix_outlier_share_prices():")
        display(vault_prices_df)

    stage_input_rows = len(prices_df)
    stage_started_at = time.perf_counter()
    prices_df = calculate_vault_returns(prices_df, logger=logger)
    logger(f"Vault cleaning stage return calculation: {stage_input_rows:,} -> {len(prices_df):,} rows in {time.perf_counter() - stage_started_at:.2f}s")

    stage_input_rows = len(prices_df)
    stage_started_at = time.perf_counter()
    prices_df = clean_returns(
        rows,
        prices_df,
        logger=logger,
        display=display,
    )
    logger(f"Vault cleaning stage return cleaning: {stage_input_rows:,} -> {len(prices_df):,} rows in {time.perf_counter() - stage_started_at:.2f}s")

    if diagnose_vault_id:
        vault_prices_df = prices_df[prices_df["id"] == diagnose_vault_id]
        logger("After clean_returns():")
        display(vault_prices_df)

    tvl_threshold: float | Mapping[str, float] | Callable[[str], float] = 1000.0
    if families != {DenominationFamily.stablecoin}:
        base_guideline = crypto_min_tvl_usd or Decimal(os.environ.get("CRYPTO_VAULTS_MIN_TVL_USD", "5000"))
        tvl_threshold = {str(v["_detection_data"].chain) + "-" + v["_detection_data"].address: float(convert_usd_threshold_to_denomination(base_guideline, v["Denomination"])) for v in rows.values() if classify_denomination(v["Denomination"]) in families}

    stage_input_rows = len(prices_df)
    stage_started_at = time.perf_counter()
    prices_df = clean_by_tvl(
        rows,
        prices_df,
        logger,
        tvl_threshold_min=tvl_threshold,
    )
    logger(f"Vault cleaning stage TVL cleaning: {stage_input_rows:,} -> {len(prices_df):,} rows in {time.perf_counter() - stage_started_at:.2f}s")
    registered_perp_vaults = build_registered_perp_vault_index(prices_df)
    stage_input_rows = len(prices_df)
    stage_started_at = time.perf_counter()
    prices_df = finalise_perp_metric_columns(prices_df, registered_perp_vaults)
    logger(f"Vault cleaning stage perpetual metric finalisation: {stage_input_rows:,} -> {len(prices_df):,} rows in {time.perf_counter() - stage_started_at:.2f}s")
    prices_df.drop(columns=[INTERNAL_VAULT_GROUP_COLUMN], inplace=True, errors="ignore")
    return prices_df


def materialise_daily_crypto_prices(prices_df: pd.DataFrame) -> pd.DataFrame:
    """Select one real end-of-day observation per vault.

    The exported parquet deliberately preserves only real observations.  Metric
    calculations subsequently build their own forward-filled daily view using
    :func:`eth_defi.research.vault_metrics.prepare_daily_share_price_series`.

    :param prices_df:
        Cleaned selected-family price rows with timestamp/index, ``id`` and
        ``share_price`` columns.
    :return:
        Chronologically sorted, observation-preserving daily price rows using
        the existing cleaned-price schema.
    """
    frame = prices_df.reset_index() if "timestamp" not in prices_df.columns else prices_df.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    frame["_utc_date"] = frame["timestamp"].dt.floor("D")
    sort_columns = ["id", "timestamp"]
    if "block_number" in frame.columns:
        sort_columns.append("block_number")
    frame = frame.sort_values(sort_columns, kind="stable")
    daily = frame.groupby(["id", "_utc_date"], sort=False, as_index=False).tail(1).copy()
    daily = daily.sort_values(["id", "timestamp"], kind="stable")
    if "returns_1h" in daily.columns:
        # ``returns_1h`` is a legacy name. Crypto Parquet is sparse daily, so
        # this is the return since the preceding exported observation.
        daily["returns_1h"] = daily.groupby("id", sort=False)["share_price"].pct_change(fill_method=None).fillna(0.0)
        if "tvl_filtering_mask" in daily.columns:
            daily.loc[daily["tvl_filtering_mask"].fillna(False).astype(bool), "returns_1h"] = 0.0
    daily.drop(columns=["_utc_date"], inplace=True)
    daily.set_index("timestamp", inplace=True)
    return daily


def write_daily_crypto_prices_sidecar(
    prices_df: pd.DataFrame,
    destination_path: Path,
    logger: Callable[[str], None] = print,
) -> int:
    """Write an atomic daily sidecar from an already-cleaned price frame.

    The scheduled scanner otherwise writes the hourly stablecoin Parquet and
    immediately reads all of its rows again when building the private crypto
    bundle.  Materialising this small observation-preserving sidecar while the
    cleaned frame is already resident removes that second wide read.  The
    derivative is written to a temporary sibling and verified before replace,
    so an interrupted scan cannot replace a valid previous sidecar with a
    partial file.

    :param prices_df:
        Settlement-annotated cleaned hourly rows with a timestamp index.
    :param destination_path:
        Atomic daily Parquet destination.
    :param logger:
        Progress callback.
    :return:
        Number of daily rows written.

    Performance history
    -------------------

    Baseline (2026-09-22 production run): crypto construction reread
    10,116,623 hourly stablecoin rows before daily materialisation.  In the
    2026-09-22 production-shaped run, the sidecar contained 1,945,631
    rows and its atomic write took 5.91 seconds.  The old path reread and
    materialised 10,116,623 hourly rows; that wide reread was not isolated in
    the source log, so no standalone speed-up or sidecar RSS is claimed.  The
    complete cleaner's peak process RSS was 20.4 GiB.
    """
    started_at = time.perf_counter()
    daily_prices = materialise_daily_crypto_prices(prices_df)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_fd, temporary_path_text = tempfile.mkstemp(suffix=".parquet", dir=destination_path.parent)
    temporary_path = Path(temporary_path_text)
    try:
        os.close(temporary_fd)
        table = pa.Table.from_pandas(daily_prices)
        table = table.replace_schema_metadata(stamp_parquet_schema_metadata(table.schema).metadata)
        pq.write_table(table, temporary_path, compression="zstd")
        verify_parquet_file(
            temporary_path,
            expected_rows=len(daily_prices),
            required_columns=["id", "share_price", "timestamp", "returns_1h"],
        )
        os.replace(temporary_path, destination_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    logger(f"Vault cleaning stage daily sidecar write: {len(daily_prices):,} rows to {destination_path} in {time.perf_counter() - started_at:.2f}s")
    return len(daily_prices)


def check_missing_metadata(
    rows: Mapping[VaultSpec, VaultRow],
    price_ids: pd.Series,
    prices_df: pd.DataFrame,
    logger: Callable[[str], None] = print,
) -> set[str]:
    """Check that we have metadata for all vaults in the prices DataFrame.

    Vault id is in format: ``56-0x10c90bfcfb3d2a7ae814da1548ae3a7fc31c35a0``

    If there are vaults with price data but no metadata, they are logged
    at error level and their IDs returned so the caller can drop them.

    :param rows:
        Metadata rows keyed by vault specification.
    :param price_ids:
        Canonical identifier series corresponding to ``prices_df``.
    :param prices_df:
        The full prices DataFrame, used to extract context for missing vaults.
    :param logger:
        Diagnostic callback accepting one message.
    :return:
        Set of vault IDs that are missing from the metadata.
        These should be dropped from the price data before further processing.

    Performance history
    -------------------

    The previous implementation rescanned the full price frame once per
    missing identifier to build an error message.  Build one grouped context
    table for the missing subset instead; normal runs with no missing IDs take
    the same cheap path.
    """

    assert isinstance(price_ids, pd.Series)

    unique_price_ids = sorted(price_ids.unique())

    vaults_by_id = get_vaults_by_id(rows)

    logger(f"Price data has {len(unique_price_ids):,} unique vault ids, vault database has {len(vaults_by_id):,} vault ids")

    if not unique_price_ids:
        return set()

    missing_ids = set(unique_price_ids) - set(vaults_by_id)

    if missing_ids:
        # Restrict the groupby to missing rows and aggregate all diagnostic
        # fields once.  The old per-ID boolean scan multiplied its cost by the
        # number of missing vaults and competed with the main denomination
        # filter on large raw files.
        missing_rows = prices_df.loc[prices_df["id"].isin(missing_ids)]
        contexts = missing_rows.groupby("id", sort=False).agg(
            row_count=("id", "size"),
            chain=("chain", "first"),
            address=("address", "first"),
            first_timestamp=("timestamp", "min"),
            last_timestamp=("timestamp", "max"),
        )
        for vault_id in sorted(missing_ids):
            if vault_id in contexts.index:
                context = contexts.loc[vault_id]
                chain = context["chain"]
                chain_name = get_chain_name(chain) if isinstance(chain, int) else str(chain)
                logger(f"ERROR: Missing metadata for vault {vault_id} (chain={chain_name}, address={context['address']}, {int(context['row_count']):,} price rows, {context['first_timestamp']} to {context['last_timestamp']}), dropping from price data")
            else:
                # ``price_ids`` is an explicit API input and older callers may
                # validate IDs which are absent from the contextual frame.
                # Retain the previous diagnostic fallback instead of raising a
                # secondary KeyError that hides the actual missing metadata.
                logger(f"ERROR: Missing metadata for vault {vault_id} (chain=?, address=?, 0 price rows, ? to ?), dropping from price data")

    if missing_ids:
        logger(f"ERROR: Missing vault metadata for {len(missing_ids):,} vault ids out of {len(unique_price_ids):,}, dropping their price rows. This may be caused by a case mismatch between address formats in the price data vs vault database.")

    return missing_ids


def _build_vault_price_dataset_filter(vault_specs: Collection[VaultSpec]) -> ds.Expression:
    """Build an exact Arrow predicate for a set of chain/address identities.

    Raw Parquet stores the two identity columns separately, while the cleaner
    derives its Pandas ``id`` later.  Per-chain ``isin`` predicates preserve
    that exact pair relationship and let the Arrow scanner discard unrelated
    rows before any object-backed Pandas columns are materialised.

    :param vault_specs:
        Metadata identities to retain.
    :return:
        Arrow dataset filter expression.
    """
    addresses_by_chain: dict[int, set[str]] = defaultdict(set)
    for spec in vault_specs:
        # ``VaultSpec`` normalises addresses to lowercase, but keep the
        # normalisation explicit here because raw scanner Parquet is written
        # with lowercase address strings and Arrow comparisons are exact.
        addresses_by_chain[spec.chain_id].add(spec.vault_address.lower())
    if not addresses_by_chain:
        # ``isin([])`` is a valid false predicate and keeps an empty metadata
        # selection from accidentally falling back to a full raw-file read.
        return ds.field("chain").isin([])

    predicates = [(ds.field("chain") == chain_id) & ds.field("address").isin(sorted(addresses)) for chain_id, addresses in addresses_by_chain.items()]
    predicate = predicates[0]
    for additional_predicate in predicates[1:]:
        predicate |= additional_predicate
    return predicate


def generate_cleaned_vault_datasets(  # noqa: PLR0914,PLR0917 - stable cleaner orchestration API
    vault_db_path: Path = DEFAULT_VAULT_DATABASE,
    price_df_path: Path = DEFAULT_UNCLEANED_PRICE_DATABASE,
    cleaned_price_df_path: Path = DEFAULT_RAW_PRICE_DATABASE,
    settlement_db_path: Path | None = None,
    logger: Callable[[str], None] = print,
    warning_logger: Callable[[str], None] | None = None,
    display: Callable[[pd.DataFrame], None] = display,
    diagnose_vault_id: str | None = None,
    vault_db: VaultDatabase | None = None,
    *,
    denomination_families: Iterable[DenominationFamily] | None = None,
    daily_materialisation: bool = False,
    daily_price_df_path: Path | None = None,
    crypto_min_tvl_usd: Decimal | None = None,
    raw_vault_specs: Collection[VaultSpec] | None = None,
) -> None:
    """Clean raw scanned vault prices and atomically write an analysis dataset.

    - Reads ``vault-prices-1h.parquet`` and generates a cleaned Parquet dataset
    - Calculates returns and related fields included with price data
    - Cleans abnormal returns
    - Stamp the cleaned Parquet with the current Docker ``metadata.version``
      provenance, matching vault scanner JSON exports

    .. note::

        Defaults to stablecoin-only selection for compatibility.  The isolated
        crypto bundle passes explicitly reviewed stablecoin, ETH and BTC families.

    :param vault_db_path:
        Vault metadata pickle path.
    :param price_df_path:
        Raw scanner Parquet source.
    :param cleaned_price_df_path:
        Atomic cleaned Parquet destination.
    :param settlement_db_path:
        Optional settlement database used to annotate cleaned observations.
    :param logger:
        Progress callback accepting one message.
    :param warning_logger:
        Optional warning callback for recoverable sidecar failures. Defaults
        to ``logger`` for notebook callers without structured log levels.
    :param display:
        Optional DataFrame display callback for diagnostics.
    :param diagnose_vault_id:
        Optional canonical vault identifier to show between cleaning stages.
    :param vault_db:
        Optional preloaded vault metadata database. Supplying it avoids a
        second pickle read when callers derive ``raw_vault_specs`` from the same
        metadata.
    :param denomination_families:
        Denomination families to retain; defaults to stablecoins.
    :param daily_materialisation:
        Whether the primary output itself should contain one row per UTC day.
    :param daily_price_df_path:
        Optional atomic daily sidecar destination. When supplied, the sidecar
        is materialised from the settlement-annotated hourly frame before the
        frame is released, allowing the crypto bundle to avoid rereading the
        full stablecoin Parquet.
    :param crypto_min_tvl_usd:
        Optional USD TVL threshold for crypto-denominated vaults.
    :param raw_vault_specs:
        Optional source vault identities to push down to the Parquet reader.
        Raw Parquet stores these as ``chain`` and ``address`` rather than the
        derived ``id`` column. The crypto-only cleaner uses this to avoid
        materialising unrelated raw histories before denomination selection.
        A targeted clean may produce a typed zero-row output. When omitted,
        a zero-row result is rejected to protect the last valid public output.

    :return:
        ``None``. The verified output is atomically installed at
        ``cleaned_price_df_path``.

    Performance history
    -------------------

    Baseline (2026-09-22 production log): the default stablecoin cleaner
    processed 22,552,978 raw rows to 10,116,623 cleaned rows in about 11m25s.
    The production-shaped rerun processed the same row counts in 146.02s
    (4.7x), with 20.4 GiB peak process RSS.  The measured input fingerprint
    was the 333,430,642-byte, 38-column raw Parquet copy from 2026-09-22;
    output had 49 columns and 10,116,623 rows.  This timer covers metadata
    filtering, all Python transforms, settlement annotation, Arrow conversion,
    write, verification and the optional sidecar when requested.
    """

    assert vault_db_path.exists()
    assert price_df_path.exists()
    warning_logger = warning_logger or logger
    pipeline_started_at = time.perf_counter()

    if vault_db is None:
        logger(f"Loading vault database {vault_db_path}")
        vault_db = VaultDatabase.read(vault_db_path)
    else:
        logger(f"Using preloaded vault database {vault_db_path}")

    rows = vault_db.rows
    selected_families = frozenset(denomination_families or {DenominationFamily.stablecoin})
    filter_specs = set(raw_vault_specs) if raw_vault_specs is not None else {spec for spec, row in rows.items() if classify_denomination(row.get("Denomination")) in selected_families}
    if raw_vault_specs is not None and not filter_specs:
        message = "raw_vault_specs must not be empty when Parquet push-down is requested"
        raise ValueError(message)

    logger(f"Loading prices {price_df_path}")
    raw_schema = pq.read_schema(price_df_path)
    dataset = ds.dataset(price_df_path, format="parquet")
    dataset_filter = _build_vault_price_dataset_filter(filter_specs)
    if raw_vault_specs is None:
        # Keep the legacy missing-metadata diagnostic without materialising all
        # 38 raw columns. The compact identity scan is released before the
        # selected wide table is converted to Pandas.
        identity_started_at = time.perf_counter()
        identity_table = dataset.to_table(columns=["chain", "address", "timestamp"])
        identity_frame = identity_table.to_pandas(types_mapper=pd.ArrowDtype)
        identity_frame["id"] = identity_frame["chain"].astype(str) + "-" + identity_frame["address"].astype(str)
        check_missing_metadata(rows, identity_frame["id"], identity_frame, logger)
        logger(f"Vault cleaning stage metadata identity scan: {len(identity_frame):,} rows in {time.perf_counter() - identity_started_at:.2f}s")
        del identity_frame, identity_table
    read_started_at = time.perf_counter()
    selected_table = dataset.to_table(filter=dataset_filter)
    prices_df = selected_table.to_pandas(types_mapper=pd.ArrowDtype)
    del selected_table, dataset
    logger(f"Vault cleaning stage raw Parquet read: {len(prices_df):,} rows in {time.perf_counter() - read_started_at:.2f}s")

    # A registry is mandatory once an artefact contains collected perp DEX
    # metrics. This deliberately fails rather than silently consulting a
    # mutable process-global registry: re-cleaning must retain the historical
    # capability declaration used to collect the raw data. Old native price
    # histories without these columns remain readable.
    metric_status = prices_df.get("perp_position_data_status")
    metric_observed_at = prices_df.get("perp_metrics_observed_at")
    contains_collected_perp_metrics = "chain" in prices_df.columns and prices_df["chain"].isin(PERP_DEX_NATIVE_CHAIN_IDS).any() and ((metric_status is not None and metric_status.fillna("").ne("").any()) or (metric_observed_at is not None and metric_observed_at.notna().any()))
    perp_capability_registry = load_perp_capability_registry(raw_schema) if contains_collected_perp_metrics else None

    logger(f"We have {vault_db.get_lead_count():,} vault leads in the vault database and {len(prices_df):,} price rows in the raw prices DataFrame")

    process_started_at = time.perf_counter()
    enhanced_prices_df = process_raw_vault_scan_data(
        rows,
        prices_df,
        logger,
        display=display,
        diagnose_vault_id=diagnose_vault_id,
        denomination_families=selected_families,
        crypto_min_tvl_usd=crypto_min_tvl_usd,
    )
    logger(f"Vault cleaning stage Python transforms: {len(prices_df):,} -> {len(enhanced_prices_df):,} rows in {time.perf_counter() - process_started_at:.2f}s")

    # A full clean writes the public stablecoin history.  Zero selected rows
    # indicate an empty raw source or a metadata-classification regression,
    # not a valid publication.  Fail before settlement and temporary-file
    # creation so the last known-good public Parquet and sidecar stay intact.
    # Targeted crypto cleans pass ``raw_vault_specs`` and may legitimately
    # materialise a typed empty result for their isolated temporary output.
    if raw_vault_specs is None and enhanced_prices_df.empty:
        message = "Refusing to replace the public cleaned vault price dataset with zero rows"
        raise ValueError(message)

    logger(f"We have {len(enhanced_prices_df):,} price rows in the cleaned prices DataFrame before settlement annotation")
    settlement_started_at = time.perf_counter()
    enhanced_prices_df = merge_vault_settlements_into_cleaned_prices(enhanced_prices_df, settlement_db_path=settlement_db_path)
    logger(f"Vault cleaning stage settlement annotation: {len(enhanced_prices_df):,} rows in {time.perf_counter() - settlement_started_at:.2f}s")

    if daily_materialisation:
        daily_started_at = time.perf_counter()
        before_daily_rows = len(enhanced_prices_df)
        enhanced_prices_df = materialise_daily_crypto_prices(enhanced_prices_df)
        logger(f"Vault cleaning stage daily materialisation: {before_daily_rows:,} -> {len(enhanced_prices_df):,} rows in {time.perf_counter() - daily_started_at:.2f}s")

    # Free the original uncleaned DataFrame to reduce peak memory
    del prices_df

    # Sort for better compression
    sort_started_at = time.perf_counter()
    enhanced_prices_df.sort_values(by=["id", "timestamp"], inplace=True)
    logger(f"Vault cleaning stage output sort: {len(enhanced_prices_df):,} rows in {time.perf_counter() - sort_started_at:.2f}s")

    # Write to a temp file, verify, then atomically replace the target.
    # If verification fails, the original cleaned parquet is preserved.
    temp_fd, temp_path = tempfile.mkstemp(
        suffix=".parquet",
        dir=str(cleaned_price_df_path.parent),
    )
    try:
        os.close(temp_fd)
        arrow_started_at = time.perf_counter()
        table = pa.Table.from_pandas(enhanced_prices_df)
        if perp_capability_registry is not None:
            table = table.replace_schema_metadata(embed_perp_capability_registry(table.schema, perp_capability_registry).metadata)
        table = table.replace_schema_metadata(stamp_parquet_schema_metadata(table.schema).metadata)
        logger(f"Vault cleaning stage Arrow conversion: {len(enhanced_prices_df):,} rows in {time.perf_counter() - arrow_started_at:.2f}s")
        write_started_at = time.perf_counter()
        pq.write_table(table, temp_path, compression="zstd")
        logger(f"Vault cleaning stage Parquet write: {len(enhanced_prices_df):,} rows in {time.perf_counter() - write_started_at:.2f}s")
        verify_started_at = time.perf_counter()
        verify_parquet_file(
            temp_path,
            expected_rows=len(enhanced_prices_df),
            required_columns=[
                "id",
                "share_price",
                "raw_share_price",
                "returns_1h",
                "timestamp",
            ],
        )
        logger(f"Vault cleaning stage Parquet verification: {len(enhanced_prices_df):,} rows in {time.perf_counter() - verify_started_at:.2f}s")
        os.replace(temp_path, str(cleaned_price_df_path))
    except BaseException:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise

    if daily_price_df_path is not None:
        try:
            # Write after the hourly replace so normal scanner runs give the
            # sidecar a newer mtime. The reader uses that order as a cheap
            # stale-file guard, while still treating the hourly file as the
            # authority when copied or restored mtimes are inconclusive.
            write_daily_crypto_prices_sidecar(enhanced_prices_df, daily_price_df_path, logger=logger)
        except (KeyError, OSError, ValueError, pa.ArrowException) as exc:
            # The hourly public output remains authoritative. Crypto bundle
            # construction detects a missing sidecar and safely falls back to
            # the existing hourly reread, while the failure remains visible.
            warning_logger(f"Daily crypto sidecar unavailable ({exc}); crypto bundle will fall back to hourly stablecoin data")

    fsize = cleaned_price_df_path.stat().st_size
    logger(f"Saved cleaned vault prices to {cleaned_price_df_path}, total {len(enhanced_prices_df):,} rows, file size is {fsize / 1024 / 1024:.2f} MB; total elapsed {time.perf_counter() - pipeline_started_at:.2f}s")


def replace_cleaned_vault_histories(  # noqa: PLR0914
    vault_ids: set[str],
    *,
    vault_db_path: Path = DEFAULT_VAULT_DATABASE,
    raw_price_df_path: Path = DEFAULT_UNCLEANED_PRICE_DATABASE,
    cleaned_price_df_path: Path = DEFAULT_RAW_PRICE_DATABASE,
    settlement_db_path: Path | None = None,
    logger: Callable[[str], None] = print,
    require_all_cleaned: bool = True,
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
    :param require_all_cleaned:
        Keep the default safety check that rejects a replacement when cleaning
        removes any selected id. Set to ``False`` only for an exhaustive
        discovery migration where valid but inactive vaults are expected to
        have no cleanable history. In that mode an existing selected cleaned
        history is deliberately replaced by no rows when its raw history is
        no longer suitable for publication.
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
    if missing_cleaned_ids and require_all_cleaned:
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
    if "chain" in resampled.columns and "address" in resampled.columns:
        registered_perp_vaults = build_registered_perp_vault_index(resampled)
        resampled = finalise_perp_metric_columns(resampled, registered_perp_vaults)
    return resampled
