"""Fetch and render AMLP/AHLP equity curves and reported TVL from Hypersync.

Requires JSON_RPC_ARBITRUM and HYPERSYNC_API_KEY. OUTPUT_DIR defaults to
``~/.cache/antarctic-charts`` and contains isolated context/token/timestamp caches,
PNG/interactive HTML charts, observed CSV rows and a provenance summary.
HYPERSYNC_RPM configures the token quota. Optional END_BLOCK selects an
exclusive historical cutoff; otherwise the confirmed RPC head is used.

This command does not mutate the scanner database or reader state. Install the
``data`` and ``hypersync`` extras; Kaleido image export also needs Chrome
(``BROWSER_PATH`` can point to an existing installation).
"""

import json
import logging
import os
from pathlib import Path

import pandas as pd
from tabulate import tabulate

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.vault_protocol.antarctic.charts import create_antarctic_figure, fetch_antarctic_chart_series
from eth_defi.erc_4626.vault_protocol.antarctic.constants import ANTARCTIC_CHAIN_ID, ANTARCTIC_DEPLOYMENTS
from eth_defi.erc_4626.vault_protocol.antarctic.historical_context import fetch_and_store_antarctic_history
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.vaultdb import get_pipeline_data_dir

logger = logging.getLogger(__name__)


def main() -> None:
    """Render full observed histories into an isolated, reusable output directory.

    Source coverage is completed before plotting. Repeated runs reuse the
    private source cursor; use a fresh output directory for a new full audit.
    Summary and CSV files make the plotted sample count and cutoff inspectable.

    :return: None.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    output = Path(os.environ.get("OUTPUT_DIR", str(Path.home() / ".cache" / "antarctic-charts"))).expanduser().resolve()
    pipeline = get_pipeline_data_dir().resolve()
    if output == pipeline or pipeline in output.parents:
        message = "Chart OUTPUT_DIR must be outside the shared pipeline directory"
        raise ValueError(message)
    output.mkdir(parents=True, exist_ok=True)
    with wait_other_writers(output / "chart-run", timeout=60):
        web3 = create_multi_provider_web3(read_json_rpc_url(ANTARCTIC_CHAIN_ID))
        client = configure_hypersync_from_env(web3).hypersync_client
        if client is None:
            message = "Antarctic charts require a configured Hypersync client"
            raise RuntimeError(message)
        safe_head = get_almost_latest_block_number(web3)
        end = int(os.environ.get("END_BLOCK", safe_head))
        if end > safe_head or end <= max(d.manager_deployment_block for d in ANTARCTIC_DEPLOYMENTS):
            message = "END_BLOCK must cover both deployments and not exceed the confirmed head"
            raise ValueError(message)
        context = output / "vault-historical-context.duckdb"
        fetch_and_store_antarctic_history(web3=web3, hypersync_client=client, pool_start_blocks={d.address: d.manager_deployment_block for d in ANTARCTIC_DEPLOYMENTS}, end_block=end, context_path=context, timestamp_cache_path=output / "block-timestamp")
        cache = TokenDiskCache(output / "tokens.sqlite")
        summary = {"generated_at": native_datetime_utc_now().isoformat(), "chain_id": ANTARCTIC_CHAIN_ID, "end_block_exclusive": end, "source": "Hypersync AddLiquidity; last canonical log per block", "equity_basis": "first observed subscription share price = 100; staking excluded", "tvl_basis": "handler-reported pre-batch TVL in USDT; bootstrap zero is unknown", "vaults": []}
        try:
            for deployment, frame in fetch_antarctic_chart_series(web3, context, cache, end):
                product = deployment.product
                figure = create_antarctic_figure(frame, product)
                frame.to_csv(output / f"{product}-observations.csv", index=False)
                figure.write_html(output / f"{product}-equity-tvl.html", include_plotlyjs=True)
                logger.info("Rendering %s PNG (%d actual subscription blocks)", product.upper(), len(frame))
                figure.write_image(output / f"{product}-equity-tvl.png", scale=1.5)
                last = frame.iloc[-1]
                summary["vaults"].append({"product": product.upper(), "address": deployment.address, "manager": deployment.manager, "observations": len(frame), "first_observed_at": frame["timestamp"].iloc[0].isoformat(), "last_observed_at": last["timestamp"].isoformat(), "last_observed_block": int(last["block_number"]), "last_share_price_usdt": float(last["share_price"]), "share_price_return_percent": float(last["equity_index"] - 100), "last_reported_tvl_usdt": float(last["total_assets"]) if pd.notna(last["total_assets"]) else None})
            cache.commit()
        finally:
            cache.close()
        (output / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
        print(tabulate([(r["product"], r["observations"], r["last_share_price_usdt"], r["last_reported_tvl_usdt"], r["last_observed_at"]) for r in summary["vaults"]], headers=("Product", "Samples", "Last share price (USDT)", "Reported TVL (USDT)", "Last observed UTC")))
    logger.info("Charts and observed data written to %s", output)


if __name__ == "__main__":
    main()
