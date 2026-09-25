"""Brand, protocol and chain logos for report charts.

- Brand logos are bundled in ``eth_defi/vault_report/assets``, copied from the
  website frontend (``src/lib/assets``).
- Benchmark logos (BTC, ETH, US Treasury) are bundled in
  ``eth_defi/vault_report/assets/benchmarks``, copied from the website frontend
  (``src/lib/assets/logos/tokens``), the same logos as its vault comparison chart.
- Protocol logos come from this repository's vault metadata,
  ``eth_defi/data/vaults/formatted_logos/{slug}/{light,dark}.png``. ``light.png``
  is for dark backgrounds and ``dark.png`` for light backgrounds; either may be missing.
- Chain logos are downloaded from the website, ``https://tradingstrategy.ai/logos/blockchains/{slug}``,
  and cached on disk.

Missing logos are never an error: callers get ``None`` and draw the chart without the logo.
"""

import base64
import logging
from pathlib import Path

import requests

from eth_defi.research.vault_metrics import _get_chain_slug
from eth_defi.vault_report.benchmarks import BTC, ETH, TREASURY_BILL
from eth_defi.vault_report.theme import ASSETS_DIR, ChartTheme

logger = logging.getLogger(__name__)

#: Protocol logos maintained in the vault metadata
PROTOCOL_LOGO_DIR = Path(__file__).parents[1] / "data" / "vaults" / "formatted_logos"

#: Benchmark name -> bundled logo file
BENCHMARK_LOGO_FILES = {TREASURY_BILL: "us-treasury.svg", BTC: "btc.svg", ETH: "eth.svg"}

#: Chain logo endpoint of the website
CHAIN_LOGO_URL = "https://tradingstrategy.ai/logos/blockchains/{slug}"


def to_data_uri(data: bytes, mime_type: str) -> str:
    """Encode an image as a data URI for Plotly layout images.

    :param data:
        Image bytes.

    :param mime_type:
        E.g. ``image/png`` or ``image/svg+xml``.

    :return:
        ``data:`` URI.
    """
    return f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"


def load_protocol_logo_path(protocol_slug: str | None, theme: ChartTheme) -> Path | None:
    """Find the protocol logo that suits the theme background.

    :param protocol_slug:
        Protocol slug from the vault metadata.

    :param theme:
        Chart theme; dark themes use ``light.png``.

    :return:
        PNG path, or ``None`` if the protocol has no logo.
    """
    if not protocol_slug:
        return None
    variants = ("light.png", "dark.png") if theme.name == "dark" else ("dark.png", "light.png")
    return next((path for variant in variants if (path := PROTOCOL_LOGO_DIR / protocol_slug / variant).exists()), None)


def load_protocol_logo_uri(protocol_slug: str | None, theme: ChartTheme) -> str | None:
    """Load a protocol logo as a data URI.

    :param protocol_slug:
        Protocol slug.

    :param theme:
        Chart theme.

    :return:
        Data URI, or ``None`` if the protocol has no logo.
    """
    path = load_protocol_logo_path(protocol_slug, theme)
    return to_data_uri(path.read_bytes(), "image/png") if path else None


def fetch_chain_logo_uri(chain_name: str, cache_dir: Path, timeout: float = 20.0) -> str | None:
    """Download a chain logo from the website, caching it on disk.

    :param chain_name:
        Chain name as in the vault metadata, e.g. ``Hypercore``.

    :param cache_dir:
        Logo cache directory.

    :param timeout:
        HTTP timeout in seconds.

    :return:
        SVG data URI, or ``None`` if the website has no logo for the chain or the download fails.
    """
    slug = _get_chain_slug(chain_name)
    path = cache_dir / f"chain-{slug}.svg"
    if not path.exists():
        url = CHAIN_LOGO_URL.format(slug=slug)
        try:
            resp = requests.get(url, timeout=timeout)
        except requests.RequestException as e:
            logger.warning("Could not download chain logo %s: %s", url, e)
            return None
        if resp.status_code != 200 or "svg" not in resp.headers.get("content-type", ""):
            logger.info("No chain logo for %s at %s: HTTP %d", chain_name, url, resp.status_code)
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(resp.content)
    return to_data_uri(path.read_bytes(), "image/svg+xml")


def load_benchmark_logo_uri(benchmark: str) -> str | None:
    """Load a benchmark logo as a data URI.

    :param benchmark:
        Benchmark name, see :py:mod:`eth_defi.vault_report.benchmarks`.

    :return:
        SVG data URI, or ``None`` for a benchmark without a logo.
    """
    filename = BENCHMARK_LOGO_FILES.get(benchmark)
    return to_data_uri((ASSETS_DIR / "benchmarks" / filename).read_bytes(), "image/svg+xml") if filename else None
