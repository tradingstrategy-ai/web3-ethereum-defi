"""Export vault protocol, stablecoin, and curator metadata to Cloudflare R2.

Reads all vault protocol metadata YAML files, converts them to JSON
with logo URLs, and uploads metadata JSON and formatted logo files to R2.
Also uploads stablecoin and curator metadata with aggregate indices.

Example:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/erc-4626/export-protocol-metadata.py

Environment variables:
    - R2_VAULT_METADATA_BUCKET_NAME: R2 bucket for protocol/stablecoin metadata and logos (required)
    - R2_VAULT_METADATA_ACCESS_KEY_ID: R2 access key ID for metadata bucket (required)
    - R2_VAULT_METADATA_SECRET_ACCESS_KEY: R2 secret access key for metadata bucket (required)
    - R2_VAULT_METADATA_ENDPOINT_URL: R2 API endpoint URL for metadata bucket (required)
    - R2_VAULT_METADATA_PUBLIC_URL: Public base URL for logo URLs in metadata (required)
    - R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME: Alternative R2 bucket for the upcoming private
      commercial professional vault data bucket (optional, uses same credentials as primary)
    - MAX_WORKERS: Number of parallel upload workers (default: 20)
    - REFRESH_STABLECOIN_RATES: Refresh stablecoin rates before uploading the stablecoin JSON bundle (default: true)
    - FORCE_STABLECOIN_RATE_REFRESH: Bypass the per-entry same-day stablecoin rate gate (default: false)
    - STABLECOIN_RATE_TIMEOUT: CoinGecko timeout in seconds (default: 20)
    - COINGECKO_DEMO_API_KEY: Optional CoinGecko demo API key used by the rate module
"""

import logging
import os
from pathlib import Path

from joblib import Parallel, delayed
from strictyaml import YAMLError
from tabulate import tabulate
from tqdm_loggable.auto import tqdm

from eth_defi.feed.stablecoin_rate import StablecoinRateRefreshSummary, refresh_stablecoin_rates
from eth_defi.stablecoin_metadata import STABLECOINS_DATA_DIR, process_and_upload_stablecoin_metadata, upload_stablecoin_index
from eth_defi.utils import setup_console_logging
from eth_defi.vault.curator import CURATORS_DATA_DIR, process_and_upload_curator_metadata, upload_curator_index, upload_protocol_curator_metadata
from eth_defi.vault.protocol_metadata import METADATA_DIR, get_available_logos, process_and_upload_protocol_metadata

logger = logging.getLogger(__name__)

_STABLECOIN_RATE_EXPORT_ERROR_TYPES = (
    YAMLError,
    OSError,
    RuntimeError,
    ValueError,
    TypeError,
    KeyError,
    AttributeError,
    IndexError,
)


def _env_flag(name: str, *, default: bool) -> bool:
    """Parse a boolean environment flag.

    :param name:
        Environment variable name.

    :param default:
        Value used when the environment variable is missing.

    :return:
        ``True`` when the variable value is one of the accepted truthy strings.
    """
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, *, default: float) -> float:
    """Parse a floating-point environment value.

    :param name:
        Environment variable name.

    :param default:
        Value used when the environment variable is missing or malformed.

    :return:
        Parsed float value.
    """
    raw_value = os.environ.get(name)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        return float(raw_value)
    except ValueError:
        logger.warning("Invalid %s=%r, using default %s", name, raw_value, default)
        return default


def refresh_stablecoin_rates_for_metadata_export() -> StablecoinRateRefreshSummary | None:
    """Refresh stablecoin rates before uploading stablecoin metadata.

    The stablecoin JSON bundle contains mutable exchange-rate fields sourced
    from CoinGecko. Refreshing them inside the metadata exporter ensures the
    bundle upload does not depend on a separate post-scanner container having
    persisted YAML changes to the host filesystem.

    :return:
        Refresh counters, or ``None`` when disabled with
        ``REFRESH_STABLECOIN_RATES=false``.
    """
    if not _env_flag("REFRESH_STABLECOIN_RATES", default=True):
        logger.info("Skipping stablecoin rate refresh (REFRESH_STABLECOIN_RATES=false)")
        return None

    timeout = _env_float("STABLECOIN_RATE_TIMEOUT", default=20.0)
    force = _env_flag("FORCE_STABLECOIN_RATE_REFRESH", default=False)
    try:
        summary = refresh_stablecoin_rates(
            data_dir=STABLECOINS_DATA_DIR,
            force=force,
            timeout=timeout,
            progress_bar=True,
        )
    except _STABLECOIN_RATE_EXPORT_ERROR_TYPES as e:
        logger.warning("Stablecoin rate refresh failed before metadata export, continuing with existing metadata: %s", e)
        return None

    logger.info(
        "Stablecoin rate refresh complete: files_scanned=%d entries_seen=%d due=%d fetched=%d files_updated=%d failed=%d depegged=%d",
        summary.files_scanned,
        summary.entries_seen,
        summary.due_count,
        summary.rates_fetched,
        summary.files_updated,
        summary.failed_count,
        summary.depegged_count,
    )
    return summary


def main():  # noqa: PLR0914
    setup_console_logging(
        log_file=Path("logs/export-protocol-metadata.log"),
        only_log_file=False,
        clear_log_file=False,
    )

    bucket_name = os.environ.get("R2_VAULT_METADATA_BUCKET_NAME")
    access_key_id = os.environ.get("R2_VAULT_METADATA_ACCESS_KEY_ID")
    secret_access_key = os.environ.get("R2_VAULT_METADATA_SECRET_ACCESS_KEY")
    endpoint_url = os.environ.get("R2_VAULT_METADATA_ENDPOINT_URL")
    public_url = os.environ.get("R2_VAULT_METADATA_PUBLIC_URL")
    max_workers = int(os.environ.get("MAX_WORKERS", "20"))

    assert bucket_name, "R2_VAULT_METADATA_BUCKET_NAME environment variable is required"
    assert public_url, "R2_VAULT_METADATA_PUBLIC_URL environment variable is required"

    # Build list of target buckets.
    # The alternative bucket is for the upcoming private commercial professional vault data bucket.
    # When set, all uploads go to both the primary and alternative buckets using the same credentials.
    bucket_names = [bucket_name]
    alt_bucket_name = os.environ.get("R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME")
    if alt_bucket_name:
        bucket_names.append(alt_bucket_name)
        logger.info("Alternative bucket configured: %s", alt_bucket_name)

    yaml_files = list(METADATA_DIR.glob("*.yaml"))
    slugs = [f.stem for f in yaml_files]
    logger.info("Found %d protocol metadata files", len(slugs))

    stablecoin_files = list(STABLECOINS_DATA_DIR.glob("*.yaml"))
    logger.info("Found %d stablecoin metadata files", len(stablecoin_files))

    # Keep mutable stablecoin exchange rates fresh in the exact process that
    # builds and uploads the JSON bundle.
    refresh_stablecoin_rates_for_metadata_export()

    for current_bucket in bucket_names:
        if len(bucket_names) > 1:
            logger.info("Processing metadata export for bucket: %s", current_bucket)

        # Upload protocol metadata
        def _process_slug(slug: str, _bucket: str = current_bucket):
            process_and_upload_protocol_metadata(
                slug=slug,
                bucket_name=_bucket,
                endpoint_url=endpoint_url,
                access_key_id=access_key_id,
                secret_access_key=secret_access_key,
                public_url=public_url,
            )

        tasks = (delayed(_process_slug)(slug) for slug in slugs)
        Parallel(n_jobs=max_workers, prefer="threads")(tqdm(tasks, total=len(slugs), desc="Checking protocol metadata"))

        # Upload stablecoin metadata
        def _process_stablecoin(yaml_path: Path, _bucket: str = current_bucket):
            process_and_upload_stablecoin_metadata(
                yaml_path=yaml_path,
                bucket_name=_bucket,
                endpoint_url=endpoint_url,
                access_key_id=access_key_id,
                secret_access_key=secret_access_key,
                public_url=public_url,
            )

        stablecoin_tasks = (delayed(_process_stablecoin)(f) for f in stablecoin_files)
        Parallel(n_jobs=max_workers, prefer="threads")(tqdm(stablecoin_tasks, total=len(stablecoin_files), desc="Checking stablecoin metadata"))

        # Upload aggregate stablecoin index
        index = upload_stablecoin_index(
            bucket_name=current_bucket,
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            public_url=public_url,
        )

        # Upload curator metadata
        curator_files = list(CURATORS_DATA_DIR.glob("*.yaml"))
        logger.info("Found %d curator metadata files", len(curator_files))

        def _process_curator(yaml_path: Path, _bucket: str = current_bucket):
            process_and_upload_curator_metadata(
                yaml_path=yaml_path,
                bucket_name=_bucket,
                endpoint_url=endpoint_url,
                access_key_id=access_key_id,
                secret_access_key=secret_access_key,
                public_url=public_url,
            )

        curator_tasks = (delayed(_process_curator)(f) for f in curator_files)
        Parallel(n_jobs=max_workers, prefer="threads")(tqdm(curator_tasks, total=len(curator_files), desc="Checking curator metadata"))

        # Upload protocol-curator entries (Ostium, gTrade, Hyperliquid, Lighter)
        upload_protocol_curator_metadata(
            bucket_name=current_bucket,
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            public_url=public_url,
        )

        # Upload aggregate curator index
        curator_index = upload_curator_index(
            bucket_name=current_bucket,
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            public_url=public_url,
        )

    # Build summary table of exported protocols and logo availability
    table_data = []
    for slug in sorted(slugs):
        logos = get_available_logos(slug)
        table_data.append(
            [
                slug,
                "Yes" if logos["generic"] else "No",
                "Yes" if logos["light"] else "No",
                "Yes" if logos["dark"] else "No",
            ]
        )

    print("\nProtocol metadata export complete\n")
    print(
        tabulate(
            table_data,
            headers=["Protocol", "Generic logo", "Light logo", "Dark logo"],
            tablefmt="simple",
        )
    )

    print(f"\nStablecoin metadata export complete: {len(stablecoin_files)} stablecoins, {len(index)} index entries\n")

    print(f"\nCurator metadata export complete: {len(curator_files)} curators, {len(curator_index)} index entries\n")


if __name__ == "__main__":
    main()
