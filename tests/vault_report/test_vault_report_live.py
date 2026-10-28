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
import pyarrow.parquet as pq
import pytest
import requests
from PIL import Image

from eth_defi.compat import native_datetime_utc_now
from eth_defi.vault_report.benchmarks import BTC, ETH, fetch_crypto_prices, fetch_treasury_bill_yields
from eth_defi.vault_report.data import TOP_VAULTS_JSON_URL, VAULT_PRICES_DOWNLOAD_URL, fetch_vault_report_data_from_r2
from eth_defi.vault_report.ghost import GhostAdminClient, GhostContentClient
from eth_defi.vault_report.logos import fetch_chain_logo_uri, load_protocol_logo_path
from eth_defi.vault_report.podcasts import fetch_latest_podcast_episodes
from eth_defi.vault_report.post import REPORT_SLUG_PREFIX, extract_section_html
from eth_defi.vault_report.theme import DARK_THEME

GHOST_CONTENT_API_URL = os.environ.get("GHOST_CONTENT_API_URL")
GHOST_CONTENT_API_KEY = os.environ.get("GHOST_CONTENT_API_KEY")
GHOST_ADMIN_API_KEY = os.environ.get("GHOST_ADMIN_API_KEY")
VAULT_PRO_API_KEY = os.environ.get("VAULT_PRO_API_KEY")


@pytest.mark.skipif(not os.environ.get("R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME"), reason="Private production R2 configuration needed")
def test_private_r2_vault_report_data(tmp_path: Path) -> None:
    """Download real production metrics and chart prices using authenticated R2.

    Exercises the generator's complete input refresh without Ghost writes.
    A second call must reuse the daily cache, preserving the price file's
    download timestamp. See the `R2 API <https://developers.cloudflare.com/r2/api/s3/>`__.

    :param tmp_path:
        Isolated cache, ensuring the first call actually contacts production.
    """
    data = fetch_vault_report_data_from_r2(tmp_path)
    assert len(data.vaults_df) > 0
    assert data.data_end_at > native_datetime_utc_now() - datetime.timedelta(days=7)
    assert {"id", "timestamp", "share_price"} <= set(pq.read_schema(data.prices_path).names)
    downloaded_at = data.prices_path.stat().st_mtime_ns
    cached = fetch_vault_report_data_from_r2(tmp_path)
    assert cached.prices_path == data.prices_path
    assert cached.prices_path.stat().st_mtime_ns == downloaded_at


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


@pytest.mark.skipif(not (GHOST_CONTENT_API_URL and GHOST_CONTENT_API_KEY), reason="GHOST_CONTENT_API_URL and GHOST_CONTENT_API_KEY needed")
def test_ghost_content_api_latest_podcasts():
    """The latest podcast episodes have promotion texts, Spotify and YouTube links and a guest logo."""
    episodes = fetch_latest_podcast_episodes(GhostContentClient(GHOST_CONTENT_API_URL, GHOST_CONTENT_API_KEY))
    assert len(episodes) == 4
    assert [episode.published_at for episode in episodes] == sorted((episode.published_at for episode in episodes), reverse=True)
    for episode in episodes:
        assert "episode" in episode.title.lower()
        assert episode.promotion
        assert episode.spotify_url.startswith("https://open.spotify.com/episode/")
        assert episode.youtube_url.startswith(("https://youtu.be/", "https://www.youtube.com/watch?v="))
        assert load_protocol_logo_path(episode.logo_slug, DARK_THEME) is not None


@pytest.mark.skipif(not (GHOST_CONTENT_API_URL and GHOST_ADMIN_API_KEY), reason="GHOST_CONTENT_API_URL and GHOST_ADMIN_API_KEY needed")
def test_ghost_admin_api_draft(tmp_path: Path):
    """Upload an image, create a draft post and delete it."""
    admin_url = os.environ.get("GHOST_ADMIN_API_URL") or GHOST_CONTENT_API_URL
    client = GhostAdminClient(admin_url, GHOST_ADMIN_API_KEY)

    image_path = tmp_path / "test.png"
    # Ghost resizes uploads and rejects a hand-made 1x1 PNG with "Unable to manipulate image"
    Image.new("RGB", (64, 64), (0, 200, 120)).save(image_path)
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
    assert yields.index[-1] > pd.Timestamp(native_datetime_utc_now()) - pd.Timedelta(days=14)


def test_chain_logo_available(tmp_path: Path):
    """The website serves chain logos, and Hypercore maps to the Hyperliquid logo."""
    for chain in ("Ethereum", "Hypercore"):
        assert fetch_chain_logo_uri(chain, tmp_path).startswith("data:image/svg+xml;base64,")


def test_crypto_benchmark_prices_available(tmp_path: Path):
    """Coinbase serves daily BTC and ETH closes without an API key."""
    end_at = native_datetime_utc_now()
    for benchmark in (BTC, ETH):
        prices = fetch_crypto_prices(benchmark, end_at - datetime.timedelta(days=100), end_at, tmp_path)
        assert prices is not None
        assert len(prices) >= 95
        assert prices.iloc[-1] > 0
