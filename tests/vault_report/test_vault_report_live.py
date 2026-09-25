"""Real integration tests for the monthly vault report data sources and Ghost.

Each test is skipped unless its credentials are available.

.. code-block:: shell

    source .local-test.env && poetry run pytest tests/vault_report/test_vault_report_live.py
"""

import datetime
import os
import uuid
from pathlib import Path

import pandas as pd
import pytest
import requests

from eth_defi.vault_report.benchmarks import BTC, ETH, fetch_crypto_prices, fetch_treasury_bill_yields
from eth_defi.vault_report.data import TOP_VAULTS_JSON_URL, VAULT_PRICES_DOWNLOAD_URL
from eth_defi.vault_report.ghost import GhostAdminClient, GhostContentClient
from eth_defi.vault_report.logos import fetch_chain_logo_uri
from eth_defi.vault_report.post import REPORT_SLUG_PREFIX, extract_section_html

GHOST_CONTENT_API_URL = os.environ.get("GHOST_CONTENT_API_URL")
GHOST_CONTENT_API_KEY = os.environ.get("GHOST_CONTENT_API_KEY")
GHOST_ADMIN_API_KEY = os.environ.get("GHOST_ADMIN_API_KEY")
VAULT_PRO_API_KEY = os.environ.get("VAULT_PRO_API_KEY")


def _read_first_bytes(url: str, params: dict | None = None, count: int = 64) -> bytes:
    """Read the first bytes of a download without fetching the whole file.

    The status is checked manually, because ``raise_for_status()`` would put
    the API key query parameter in the failure message.

    :param url:
        Download URL.

    :param params:
        Query parameters.

    :param count:
        Number of bytes to read.

    :return:
        First bytes of the response body.
    """
    with requests.get(url, params=params, stream=True, timeout=60) as resp:
        assert resp.status_code == 200, f"Downloading {url} failed: HTTP {resp.status_code}"
        return next(resp.iter_content(chunk_size=count))[:count]


def test_top_vaults_json_available():
    """The public top vaults JSON export is reachable."""
    head = _read_first_bytes(TOP_VAULTS_JSON_URL)
    assert b'"generated_at"' in head


@pytest.mark.skipif(not VAULT_PRO_API_KEY, reason="VAULT_PRO_API_KEY needed")
def test_pro_vault_prices_download():
    """The Pro vault price download serves a Parquet file for our API key."""
    head = _read_first_bytes(VAULT_PRICES_DOWNLOAD_URL, params={"api-key": VAULT_PRO_API_KEY})
    assert head.startswith(b"PAR1")


@pytest.mark.skipif(not (GHOST_CONTENT_API_URL and GHOST_CONTENT_API_KEY), reason="GHOST_CONTENT_API_URL and GHOST_CONTENT_API_KEY needed")
def test_ghost_content_api_previous_report():
    """Find the latest published monthly report and its evergreen sections."""
    client = GhostContentClient(GHOST_CONTENT_API_URL, GHOST_CONTENT_API_KEY)
    post = client.fetch_latest_post_by_slug_prefix(REPORT_SLUG_PREFIX)
    assert post is not None
    assert post.slug.startswith(REPORT_SLUG_PREFIX)
    assert post.published_at is not None
    partners = extract_section_html(post.html, "partners")
    assert partners.startswith('<h2 id="partners">')
    assert "ghost.io" not in partners


@pytest.mark.skipif(not (GHOST_CONTENT_API_URL and GHOST_ADMIN_API_KEY), reason="GHOST_CONTENT_API_URL and GHOST_ADMIN_API_KEY needed")
def test_ghost_admin_api_draft(tmp_path: Path):
    """Upload an image, create a draft post and delete it."""
    admin_url = os.environ.get("GHOST_ADMIN_API_URL") or GHOST_CONTENT_API_URL
    client = GhostAdminClient(admin_url, GHOST_ADMIN_API_KEY)

    image_path = tmp_path / "test.png"
    # 1x1 transparent PNG
    image_path.write_bytes(bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"))
    image_url = client.upload_image(image_path)
    assert image_url.startswith("http")

    slug = f"test-vault-report-draft-{uuid.uuid4().hex[:8]}"
    post = client.create_or_update_draft("Test vault report draft", slug, f'<p>Test</p><figure class="kg-card kg-image-card"><img src="{image_url}"></figure>')
    try:
        assert post.status == "draft"
        assert client.fetch_post_by_slug(slug).id == post.id
    finally:
        client.delete_post(post.id)
    assert client.fetch_post_by_slug(slug) is None


def test_treasury_bill_yields_available(tmp_path: Path):
    """FRED serves recent 3-month Treasury bill rates without an API key."""
    yields = fetch_treasury_bill_yields(tmp_path)
    assert yields is not None
    assert 0 < yields.iloc[-1] < 0.2
    assert yields.index[-1] > pd.Timestamp.now() - pd.Timedelta(days=14)


def test_chain_logo_available(tmp_path: Path):
    """The website serves chain logos, and Hypercore maps to the Hyperliquid logo."""
    for chain in ("Ethereum", "Hypercore"):
        assert fetch_chain_logo_uri(chain, tmp_path).startswith("data:image/svg+xml;base64,")


def test_crypto_benchmark_prices_available(tmp_path: Path):
    """Coinbase serves daily BTC and ETH closes without an API key."""
    end_at = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    for benchmark in (BTC, ETH):
        prices = fetch_crypto_prices(benchmark, end_at - datetime.timedelta(days=100), end_at, tmp_path)
        assert prices is not None
        assert len(prices) >= 95
        assert prices.iloc[-1] > 0
