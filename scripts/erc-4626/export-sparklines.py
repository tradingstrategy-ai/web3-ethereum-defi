"""Export eligible vault share-price sparklines to Cloudflare R2.

Run after ``cleaned-vault-prices-1h.parquet`` has been generated.

Example:

.. code-block:: shell

    poetry run python scripts/erc-4626/export-sparklines.py

"""

import gzip
import logging
import os
from pathlib import Path
from typing import Any, TypedDict

import pandas as pd
from joblib import Parallel, delayed
from tqdm_loggable.auto import tqdm

from eth_defi.cloudflare_r2 import calculate_bytes_digest, create_r2_client, upload_bytes_to_r2
from eth_defi.research.sparkline import MIN_SPARKLINE_HISTORY, SparklineData, export_sparkline_as_png, export_sparkline_as_svg, prepare_sparkline_data, render_sparkline_gradient
from eth_defi.token import is_stablecoin_like
from eth_defi.utils import setup_console_logging
from eth_defi.vault.vaultdb import VaultDatabase, get_pipeline_data_dir

logger = logging.getLogger(__name__)

#: Minimum historical peak TVL in USD for general vault sparkline publication.
#:
#: Keep aligned with ``scripts/erc-4626/vault-analysis-json.py``.
MIN_PEAK_TVL = 5000

#: Temporary rollout threshold for ApeX native vaults.
#:
#: ApeX is a new protocol with very little TVL, so its otherwise useful vault
#: history would not receive sparklines under the general USD 5,000 threshold.
#: Keep this exemption scoped to ApeX until the protocol has established TVL.
APEX_MIN_PEAK_TVL = 500

#: Display name emitted by the ApeX native vault export.
APEX_PROTOCOL_NAME = "ApeX"


def resolve_sparkline_peak_tvl_threshold(vault_row: dict) -> int:
    """Resolve the peak-TVL requirement for one vault's sparkline.

    Most vaults use the shared USD 5,000 threshold. ApeX receives a temporary
    USD 500 exemption because it is a new protocol whose small native vaults
    would otherwise have no sparklines despite having useful history.

    :param vault_row:
        Vault metadata row from :class:`eth_defi.vault.vaultdb.VaultDatabase`.
    :return:
        Minimum historical USD total assets needed to publish a sparkline.
    """
    if vault_row.get("Protocol") == APEX_PROTOCOL_NAME:
        return APEX_MIN_PEAK_TVL
    return MIN_PEAK_TVL


class RenderData(TypedDict):
    """Plain worker-process result for one rendered image."""

    vault_id: str
    payload: bytes
    content_type: str
    extension: str


def get_included_vault_ids(
    vault_db: VaultDatabase,
    prices_df: pd.DataFrame,
) -> set[str]:
    """Pre-compute which vault IDs pass the inclusion filter.

    A vault must be denominated in a stablecoin-like asset and have crossed its
    protocol-specific historical peak-TVL threshold. The aggregation is done
    once to avoid repeated PyArrow ``ChunkedArray.take()`` calls.

    :param vault_db:
        Vault metadata used for denomination, protocol and identity.
    :param prices_df:
        All cleaned prices with ``id`` and ``total_assets`` columns.
    :return:
        Vault string IDs eligible for history preparation.
    """
    peak_tvl = prices_df.groupby("id")["total_assets"].max()

    included: set[str] = set()
    for row in vault_db.rows.values():
        vault_id = row["_detection_data"].get_spec().as_string_id()
        denomination = row.get("Denomination") or ""
        if not is_stablecoin_like(denomination):
            continue
        threshold = resolve_sparkline_peak_tvl_threshold(row)
        if peak_tvl.get(vault_id, 0) >= threshold:
            included.add(vault_id)
    return included


def prepare_vault_sparklines(
    prices_df: pd.DataFrame,
    included_ids: set[str],
) -> tuple[list[tuple[str, SparklineData]], int]:
    """Prepare all eligible vault histories for process workers.

    Full per-vault histories are retained until each vault has established its
    own latest finite observation. This is necessary for inactive vaults whose
    valid history ends before the dataset-wide 90-day period.

    :param prices_df:
        Cleaned prices indexed by ``timestamp`` with ``id``, ``share_price``
        and ``total_assets`` columns.
    :param included_ids:
        Vault IDs that passed denomination and peak-TVL policy.
    :return:
        Prepared worker inputs and the number skipped for insufficient finite
        share-price history.
    """
    selected_prices_df = prices_df.loc[prices_df["id"].isin(included_ids), ["id", "share_price", "total_assets"]]
    selected_prices_df = selected_prices_df.reset_index().set_index(["id", "timestamp"]).sort_index()

    prepared: list[tuple[str, SparklineData]] = []
    skipped = 0
    for vault_id in sorted(included_ids):
        sparkline_data = prepare_sparkline_data(selected_prices_df.loc[vault_id])
        if sparkline_data is None:
            skipped += 1
            logger.debug("Skipping sparkline for vault %s: less than %s of finite share-price history", vault_id, MIN_SPARKLINE_HISTORY)
            continue
        prepared.append((vault_id, sparkline_data))

    return prepared, skipped


def render_vault_sparklines(vault_id: str, sparkline_data: SparklineData) -> list[RenderData]:
    """Render listing SVG and social-card PNG images for one vault.

    Plain dictionaries cross Joblib's standalone-script process boundary
    without requiring a custom class to be reconstructed from ``__main__``.

    :param vault_id:
        Vault string ID used in published filenames.
    :param sparkline_data:
        Prepared daily observations and fixed chart bounds.
    :return:
        Two rendered image dictionaries, or an empty list if rendering rejects
        the data.
    """
    try:
        fig_svg = render_sparkline_gradient(
            sparkline_data.prices_df,
            width=100,
            height=25,
            line_width=1,
            margin_ratio=4,
            x_axis_range=(sparkline_data.start_at, sparkline_data.end_at),
        )
        fig_png = render_sparkline_gradient(
            sparkline_data.prices_df,
            width=300,
            height=300,
            x_axis_range=(sparkline_data.start_at, sparkline_data.end_at),
        )
        return [
            {"vault_id": vault_id, "payload": export_sparkline_as_svg(fig_svg), "content_type": "image/svg+xml", "extension": "svg"},
            {"vault_id": vault_id, "payload": export_sparkline_as_png(fig_png), "content_type": "image/png", "extension": "png"},
        ]
    except ValueError as exc:
        logger.warning("Skipping sparkline for vault %s: %s", vault_id, exc)
        return []


def render_sparklines(
    vault_data: list[tuple[str, SparklineData]],
    max_workers: int,
) -> list[RenderData]:
    """Render prepared vaults in worker processes.

    :param vault_data:
        Prepared vault IDs and chart data.
    :param max_workers:
        Number of Joblib worker processes.
    :return:
        Flattened SVG and PNG render results.
    """
    tasks = (delayed(render_vault_sparklines)(vault_id, sparkline_data) for vault_id, sparkline_data in vault_data)
    results = Parallel(n_jobs=max_workers, prefer="processes")(tqdm(tasks, total=len(vault_data), desc="Rendering sparklines"))
    return [image for vault_images in results for image in vault_images]


def upload_sparkline(
    s3_client: Any,
    bucket_name: str,
    render_data: RenderData,
) -> bool:
    """Upload one changed image to R2.

    :param s3_client:
        Authenticated boto3 S3 client.
    :param bucket_name:
        Destination R2 bucket.
    :param render_data:
        Uncompressed rendered image and its publication metadata.
    :return:
        ``True`` when uploaded, ``False`` when the remote source checksum
        already matches.
    """
    payload = render_data["payload"]
    object_name = f"sparkline-90d-{render_data['vault_id']}.{render_data['extension']}"
    return upload_bytes_to_r2(
        s3_client=s3_client,
        payload=gzip.compress(payload, mtime=0),
        bucket_name=bucket_name,
        object_name=object_name,
        content_type=render_data["content_type"],
        content_encoding="gzip",
        skip_if_current=True,
        source_digest=calculate_bytes_digest(payload),
    )


def upload_sparklines(
    s3_client: Any,
    bucket_name: str,
    render_data: list[RenderData],
    max_workers: int,
) -> tuple[int, int]:
    """Upload rendered images concurrently.

    :param s3_client:
        Authenticated boto3 S3 client shared by upload threads.
    :param bucket_name:
        Destination R2 bucket.
    :param render_data:
        Rendered SVG and PNG images.
    :param max_workers:
        Number of Joblib upload threads.
    :return:
        Uploaded and unchanged image counts.
    """
    tasks = (delayed(upload_sparkline)(s3_client, bucket_name, image) for image in render_data)
    results = Parallel(n_jobs=max_workers, prefer="threads")(tqdm(tasks, total=len(render_data), desc=f"Uploading sparklines to R2 bucket {bucket_name}"))
    uploaded = sum(results)
    return uploaded, len(results) - uploaded


def main() -> None:
    """Render and publish all eligible vault sparklines."""
    setup_console_logging(
        log_file=Path("logs/export-spark-lines.log"),
        only_log_file=False,
        clear_log_file=False,
    )

    bucket_name = os.environ.get("R2_SPARKLINE_BUCKET_NAME")
    access_key_id = os.environ.get("R2_SPARKLINE_ACCESS_KEY_ID")
    secret_access_key = os.environ.get("R2_SPARKLINE_SECRET_ACCESS_KEY")
    endpoint_url = os.environ.get("R2_SPARKLINE_ENDPOINT_URL")
    max_workers = int(os.environ.get("MAX_WORKERS", "20"))

    assert bucket_name, "R2_SPARKLINE_BUCKET_NAME environment variable is required"
    assert endpoint_url, "R2_SPARKLINE_ENDPOINT_URL environment variable is required"
    assert access_key_id, "R2_SPARKLINE_ACCESS_KEY_ID environment variable is required"
    assert secret_access_key, "R2_SPARKLINE_SECRET_ACCESS_KEY environment variable is required"

    data_dir = get_pipeline_data_dir()
    vault_db = VaultDatabase.read(data_dir / "vault-metadata-db.pickle")
    prices_df = pd.read_parquet(data_dir / "cleaned-vault-prices-1h.parquet", columns=["id", "share_price", "total_assets"])

    included_ids = get_included_vault_ids(vault_db, prices_df)
    logger.info("Preparing sparklines for %s vaults for R2 bucket '%s'", len(included_ids), bucket_name)
    vault_data, skipped = prepare_vault_sparklines(prices_df, included_ids)
    logger.info("Skipped %s vaults without at least %s of finite share-price history", skipped, MIN_SPARKLINE_HISTORY)
    logger.info("Rendering sparklines for %s vaults with %s workers", len(vault_data), max_workers)
    render_data = render_sparklines(vault_data, max_workers)

    logger.info("Uploading %s sparkline images to R2", len(render_data))
    s3_client = create_r2_client(
        endpoint_url=endpoint_url,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        max_pool_connections=max_workers,
    )
    uploaded, unchanged = upload_sparklines(s3_client, bucket_name, render_data, max_workers)
    logger.info("Uploaded %s changed sparkline images and skipped %s unchanged images", uploaded, unchanged)


if __name__ == "__main__":
    main()
