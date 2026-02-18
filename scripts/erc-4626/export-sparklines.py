"""Export all sparklines to Cloudflare R2.

- Run after cleaned prices 1h is generated

Example:

.. code-block:: shell

    python scripts/erc-4626/export-sparklines.py

"""

import gzip
import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from tqdm_loggable.auto import tqdm

from joblib import Parallel, delayed

from eth_defi.token import is_stablecoin_like
from eth_defi.research.sparkline import export_sparkline_as_svg, render_sparkline_gradient, export_sparkline_as_png
from eth_defi.utils import setup_console_logging
from eth_defi.vault.vaultdb import VaultDatabase, read_default_vault_prices


#: What's the threshold to render the spark line for the vault
#:
#: Must match the valut in vault-analysis-json.py
MIN_PEAK_TVL = 5000


@dataclass
class RenderData:
    vault_id: str
    svg_bytes: bytes
    content_type: str
    extension: str


def get_included_vault_ids(
    vault_db: VaultDatabase,
    prices_df: pd.DataFrame,
) -> set[str]:
    """Pre-compute which vault IDs pass the inclusion filter.

    Uses a single groupby aggregation instead of per-vault get_group() calls,
    which avoids expensive pyarrow ChunkedArray.take() on every iteration.
    """
    # Compute peak TVL per vault in one pass
    peak_tvl = prices_df.groupby("id")["total_assets"].max()
    eligible_ids = set(peak_tvl[peak_tvl >= MIN_PEAK_TVL].index)

    included = set()
    for row in vault_db.rows.values():
        vault_id = row["_detection_data"].get_spec().as_string_id()
        denomination = row.get("Denomination") or ""
        if not is_stablecoin_like(denomination):
            continue
        if vault_id in eligible_ids:
            included.add(vault_id)
    return included


def main():
    logger = setup_console_logging(
        log_file=Path(f"logs/export-spark-lines.log"),
        only_log_file=False,
        clear_log_file=False,
    )

    bucket_name = os.environ.get("R2_SPARKLINE_BUCKET_NAME")
    access_key_id = os.environ.get("R2_SPARKLINE_ACCESS_KEY_ID")
    secret_access_key = os.environ.get("R2_SPARKLINE_SECRET_ACCESS_KEY")
    endpoint_url = os.environ.get("R2_SPARKLINE_ENDPOINT_URL")
    max_workers = int(os.environ.get("MAX_WORKERS", "20"))

    assert bucket_name, "R2_SPARKLINE_BUCKET_NAME environment variable is required"

    vault_db = VaultDatabase.read()
    prices_df = read_default_vault_prices()

    # Select entries with peak TVL threshold - uses single aggregation pass
    included_ids = get_included_vault_ids(vault_db, prices_df)
    vault_rows = [r for r in vault_db.rows.values() if r["_detection_data"].get_spec().as_string_id() in included_ids]

    logger.info(f"Exporting sparklines for {len(vault_rows)} vaults to R2 bucket '{bucket_name}'")

    # Export last 90 days

    last_day = prices_df.index.max()

    prices_df = prices_df[prices_df.index >= (last_day - pd.Timedelta(days=90))]
    prices_df = prices_df.reset_index().set_index(["id", "timestamp"]).sort_index()

    # Pre-extract per-vault DataFrames so workers receive small data, not the full prices_df
    vault_data_items = []
    for row in vault_rows:
        detection_data = row["_detection_data"]
        spec = detection_data.get_spec()
        vault_id = spec.as_string_id()
        try:
            vault_prices_df = prices_df.loc[vault_id]
        except KeyError:
            continue
        # Resample to daily data points once
        vault_prices_df = vault_prices_df.resample("D").last()[["share_price", "total_assets"]]
        vault_data_items.append((vault_id, vault_prices_df))

    logger.info("Rendering sparklines for %s vaults with %s workers", len(vault_data_items), max_workers)

    # Render both SVG and PNG for a single vault - uses Agg backend, safe in worker processes
    def _render_vault(vault_id: str, vault_prices_df: pd.DataFrame) -> list[RenderData]:
        results = []

        # Small SVG sparkline for listings
        fig_svg = render_sparkline_gradient(
            vault_prices_df,
            width=100,
            height=25,
            line_width=1,
            margin_ratio=4,
        )
        svg_bytes = export_sparkline_as_svg(fig_svg)
        results.append(
            RenderData(
                vault_id=vault_id,
                svg_bytes=svg_bytes,
                content_type="image/svg+xml",
                extension="svg",
            )
        )

        # Large PNG sparkline for Twitter Summary Cards
        fig_png = render_sparkline_gradient(
            vault_prices_df,
            width=300,
            height=300,
        )
        png_bytes = export_sparkline_as_png(fig_png)
        results.append(
            RenderData(
                vault_id=vault_id,
                svg_bytes=png_bytes,
                content_type="image/png",
                extension="png",
            )
        )

        return results

    # Parallelise rendering across processes - each gets its own matplotlib instance
    render_tasks = (delayed(_render_vault)(vid, df) for vid, df in vault_data_items)
    render_results = Parallel(n_jobs=max_workers, prefer="processes")(tqdm(render_tasks, total=len(vault_data_items), desc="Rendering sparklines"))
    render_data = [item for sublist in render_results for item in sublist]

    logger.info("Uploading %s sparkline images to R2", len(render_data))

    # Create boto3 client once, reuse for all uploads
    import boto3

    s3_client = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name="auto",
    )

    def _upload_row(render_data: RenderData):
        object_name = f"sparkline-90d-{render_data.vault_id}.{render_data.extension}"
        s3_client.put_object(
            Bucket=bucket_name,
            Key=object_name,
            Body=gzip.compress(render_data.svg_bytes),
            ContentType=render_data.content_type,
            ContentEncoding="gzip",
        )

    # Upload in parallel using threads (I/O bound)
    upload_tasks = (delayed(_upload_row)(row) for row in render_data)
    Parallel(n_jobs=max_workers, prefer="threads")(tqdm(upload_tasks, total=len(render_data), desc="Uploading sparklines to R2"))

    print("Sparkline export complete")


if __name__ == "__main__":
    main()
