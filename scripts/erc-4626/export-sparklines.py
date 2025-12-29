"""Export all sparklines to Cloudflare R2.

- Run after cleaned prices 1h is generated

Example:

.. code-block:: shell

    python scripts/erc-4626/export-sparklines.py

"""

import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from tqdm_loggable.auto import tqdm

from joblib import Parallel, delayed

from eth_defi.token import is_stablecoin_like
from eth_defi.research.sparkline import render_sparkline_simple, export_sparkline_as_svg, render_sparkline_gradient, export_sparkline_as_png
from eth_defi.utils import setup_console_logging
from eth_defi.vault.vaultdb import VaultDatabase, read_default_vault_prices, VaultRow
from eth_defi.research.sparkline import upload_to_r2_compressed


#: What's the threshold to render the spark line for the vault
#:
#: Must match the valut in vault-analysis-json.py
MIN_PEAK_TVL = 5000


@dataclass(slots=True)
class RenderData:
    vault_id: str
    svg_bytes: bytes
    content_type: str
    extension: str


def is_vault_included(row: VaultRow):
    nav = row.get("NAV") or 0
    denomination = row.get("Denomination") or ""
    return nav > MIN_PEAK_TVL and is_stablecoin_like(denomination)


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

    # Select entries with peak TVL 50k USD

    vault_rows = [r for r in vault_db.rows.values() if is_vault_included(r)]

    logger.info(f"Exporting sparklines for {len(vault_rows)} vaults to R2 bucket '{bucket_name}'")

    # Export last 90 days

    last_day = prices_df.index.max()

    prices_df = prices_df[prices_df.index >= (last_day - pd.Timedelta(days=90))]
    prices_df = prices_df.reset_index().set_index(["id", "timestamp"]).sort_index()

    def _render_row_simple_svg(row: VaultRow) -> RenderData:
        detection_data = row["_detection_data"]
        spec = detection_data.get_spec()
        vault_id = spec.as_string_id()

        nav = row.get("NAV") or 0
        denomination = row.get("Denomination") or ""

        logger.info(
            "Exporting sparkline for vault %s: %s, NAV: %s, denomination: %s",
            vault_id,
            row.get("Name", "<unknown>"),
            nav,
            denomination,
        )

        try:
            vault_prices_df = prices_df.loc[vault_id]
        except KeyError:
            # print(f"Skipping vault {vault_id}, no price data")
            logger.info("Skipping vault %s, no price data", vault_id)
            return None

        # Do daily data points
        vault_prices_df = vault_prices_df.resample("D").last()[["share_price", "total_assets"]]

        fig = render_sparkline_simple(
            vault_prices_df,
            width=100,
            height=25,
        )

        svg_bytes = export_sparkline_as_svg(
            fig,
        )
        return RenderData(
            vault_id=vault_id,
            svg_bytes=svg_bytes,
            content_type="image/svg+xml",
            extension="svg",
        )

    def _render_row_gradient_png(row: VaultRow) -> RenderData:
        detection_data = row["_detection_data"]
        spec = detection_data.get_spec()
        vault_id = spec.as_string_id()

        nav = row.get("NAV") or 0
        denomination = row.get("Denomination") or ""

        logger.info(
            "Exporting sparkline for vault %s: %s, NAV: %s, denomination: %s",
            vault_id,
            row.get("Name", "<unknown>"),
            nav,
            denomination,
        )

        try:
            vault_prices_df = prices_df.loc[vault_id]
        except KeyError:
            # print(f"Skipping vault {vault_id}, no price data")
            logger.info("Skipping vault %s, no price data", vault_id)
            return None

        # Do daily data points
        vault_prices_df = vault_prices_df.resample("D").last()[["share_price", "total_assets"]]

        # Use Twitter Summary Card size
        fig = render_sparkline_gradient(
            vault_prices_df,
            width=300,
            height=300,
        )

        png_bytes = export_sparkline_as_png(
            fig,
        )
        return RenderData(
            vault_id=vault_id,
            svg_bytes=png_bytes,
            content_type="image/png",
            extension="png",
        )

    def _upload_row(render_data: RenderData):
        vault_id = render_data.vault_id
        svg_bytes = render_data.svg_bytes
        svg_bytes = render_data.svg_bytes
        object_name = f"sparkline-90d-{vault_id}.{render_data.extension}"

        logger.info(
            "Uploading vault %s, filename %s",
            vault_id,
            object_name,
        )

        upload_to_r2_compressed(
            payload=svg_bytes,
            bucket_name=bucket_name,
            object_name=object_name,
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            content_type=render_data.content_type,
        )
        # print(f"Uploaded sparkline to R2 bucket '{bucket_name}' as '{object_name}'")

    # Matplotlib segfaults if run outside main thread, so we first render all in main thread
    # NSWindow should only be instantiated on the main thread!
    render_data = []
    for row in tqdm(vault_rows, desc="Rendering sparklines"):
        data = _render_row_simple_svg(row)
        if data is not None:
            render_data.append(data)

        data = _render_row_gradient_png(row)
        if data is not None:
            render_data.append(data)

    # Use joblib to run uploads in parallel
    tasks = (delayed(_upload_row)(row) for row in render_data)
    Parallel(n_jobs=max_workers, prefer="threads")(tqdm(tasks, total=len(render_data), desc="Uploading sparklines to R2"))

    print("Sparkline export complete")


if __name__ == "__main__":
    main()
