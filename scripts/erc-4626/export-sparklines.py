"""Export all sparklines to Cloudflare R2.

- Run after cleaned prices 1h is generated

"""

import os
from dataclasses import dataclass

import pandas as pd

from tqdm_loggable.auto import tqdm

from joblib import Parallel, delayed

from eth_defi.token import is_stablecoin_like
from eth_defi.research.sparkline import render_sparkline, export_sparkline_as_svg
from eth_defi.vault.vaultdb import VaultDatabase, read_default_vault_prices, VaultRow
from eth_defi.research.sparkline import upload_to_r2


@dataclass(slots=True)
class RenderData:
    vault_id: str
    svg_bytes: bytes


def is_vault_included(row: VaultRow):
    nav = row.get("NAV") or 0
    denomination = row.get("Denomination") or ""
    return nav > 40_000 and is_stablecoin_like(denomination)


def main():
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

    print(f"Exporting sparklines for {len(vault_rows)} vaults to R2 bucket '{bucket_name}'")

    # Export last 90 days

    last_day = prices_df.index.max()

    prices_df = prices_df[prices_df.index >= (last_day - pd.Timedelta(days=90))]
    prices_df = prices_df.reset_index().set_index(["id", "timestamp"]).sort_index()

    def _render_row(row: VaultRow) -> RenderData:
        detection_data = row["_detection_data"]
        spec = detection_data.get_spec()
        vault_id = spec.as_string_id()

        try:
            vault_prices_df = prices_df.loc[vault_id]
        except KeyError:
            # print(f"Skipping vault {vault_id}, no price data")
            return None

        fig = render_sparkline(
            vault_prices_df,
            width=128,
            height=32,
        )

        svg_bytes = export_sparkline_as_svg(
            fig,
        )
        return RenderData(
            vault_id=vault_id,
            svg_bytes=svg_bytes,
        )

    def _upload_row(render_data: RenderData):
        vault_id = render_data.vault_id
        svg_bytes = render_data.svg_bytes
        object_name = f"sparkline-90d-{vault_id}.svg"
        upload_to_r2(
            payload=svg_bytes,
            bucket_name=bucket_name,
            object_name=object_name,
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            content_type="image/svg+xml",
        )
        # print(f"Uploaded sparkline to R2 bucket '{bucket_name}' as '{object_name}'")

    render_data = []
    for row in tqdm(vault_rows, desc="Rendering sparklines"):
        data = _render_row(row)
        if data is not None:
            render_data.append(data)

    # Use joblib to run uploads in parallel
    tasks = (delayed(_upload_row)(row) for row in render_data)
    Parallel(n_jobs=max_workers, prefer="threads")(tqdm(tasks, total=len(render_data), desc="Uploading sparklines to R2"))


if __name__ == "__main__":
    main()
