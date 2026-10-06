"""Check a generated report's table markup and real public CDN images.

Run with ``REPORT_DIR`` pointing at a complete local report bundle and optionally
``MAX_WORKERS`` (default 8). Requires the ``data`` extra; no API token is needed.
This read-only integration check downloads and decodes each unique table PNG
from both ``post.html`` and ``preview.html``. HTTP 200 alone does not establish
that a native 4:1 image was served.

Example::

    source .local-test.env && REPORT_DIR=/tmp/vault-report poetry run python scripts/erc-4626/check-report-sparklines.py
"""

import logging
import os
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path

import requests
from joblib import Parallel, delayed
from PIL import Image
from tqdm_loggable.auto import tqdm

from eth_defi.research.sparkline import SPARKLINE_TABLE_PNG_HEIGHT, SPARKLINE_TABLE_PNG_WIDTH
from eth_defi.utils import setup_console_logging
from eth_defi.vault_report.sections import SPARKLINE_URL

logger = logging.getLogger(__name__)


class SparklineMarkupParser(HTMLParser):
    """Collect and validate all sparkline image tags in a report document."""

    def __init__(self) -> None:
        """Initialise a fresh set of CDN image URLs for one HTML document."""
        super().__init__()
        self.urls: set[str] = set()
        self.image_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        source = values.get("src") or ""
        if tag != "img" or "vault-sparklines.tradingstrategy.ai" not in source:
            return
        assert source.startswith(SPARKLINE_URL.split("{vault_id}")[0]) and source.endswith(".png"), f"Unexpected sparkline asset: {source}"
        assert values.get("width") == "72" and values.get("height") == "18", f"Wrong sparkline dimensions: {source}"
        assert values.get("style") == "width:72px;max-width:none;height:18px;vertical-align:middle", f"Wrong sparkline sizing style: {source}"
        self.urls.add(source)
        self.image_count += 1


def fetch_table_sparkline(url: str) -> None:
    """Download and decode a native table PNG from the public CDN.

    Verify the actual file format and intrinsic dimensions, including gzip
    transport decoding performed by requests. A missing or malformed image
    aborts the check with the URL that needs attention.

    :param url:
        Public table PNG URL taken from the generated HTML.
    """
    with requests.get(url, timeout=30) as response:
        response.raise_for_status()
        with Image.open(BytesIO(response.content)) as image:
            image.load()
            assert image.format == "PNG", f"Expected PNG from {url}, got {image.format}"
            assert image.size == (SPARKLINE_TABLE_PNG_WIDTH, SPARKLINE_TABLE_PNG_HEIGHT), f"Wrong intrinsic dimensions from {url}: {image.size}"


def main() -> None:
    """Validate both bundle documents and every unique published sparkline.

    ``REPORT_DIR`` is required to avoid accidentally validating another bundle.
    ``MAX_WORKERS`` controls the public CDN request concurrency.
    """
    setup_console_logging(default_log_level="info")
    assert os.environ.get("REPORT_DIR"), "Set REPORT_DIR to the generated report bundle"
    report_dir = Path(os.environ["REPORT_DIR"]).expanduser()
    max_workers = int(os.environ.get("MAX_WORKERS", "8"))
    assert max_workers > 0, "MAX_WORKERS must be positive"
    sources: list[set[str]] = []
    for name in ("post.html", "preview.html"):
        parser = SparklineMarkupParser()
        parser.feed((report_dir / name).read_text())
        assert parser.urls, f"No table sparklines in {name}; publish the variants and enable CHECK_SPARKLINES"
        sources.append(parser.urls)
        logger.info("%s: %d table images, %d unique URLs, all using native table PNG markup", name, parser.image_count, len(parser.urls))
    assert sources[0] == sources[1], "Post and preview contain different sparkline assets"
    urls = sorted(sources[0])
    Parallel(n_jobs=max_workers, backend="threading")(delayed(fetch_table_sparkline)(url) for url in tqdm(urls, desc="Decoding public table PNGs"))
    logger.info("Passed: %d public CDN PNGs decoded at %d x %d, with 72 x 18 HTML markup", len(urls), SPARKLINE_TABLE_PNG_WIDTH, SPARKLINE_TABLE_PNG_HEIGHT)


if __name__ == "__main__":
    main()
