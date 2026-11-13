"""Wildcat app API metadata parsing and caching."""

import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from eth_defi.erc_4626.vault_protocol.wildcat import offchain_metadata
from eth_defi.erc_4626.vault_protocol.wildcat.offchain_metadata import extract_summary_line, fetch_wildcat_market_metadata, strip_markdown

#: Wintermute's Wildcat market and borrower on Ethereum.
MARKET = "0xc9499006a149c553d18171747ed19aa7c6dd19e2"
BORROWER = "0x5b15f94efc3a6e2c82d7db8efd8d5ba55da11feb"

NOW = datetime.datetime(2026, 11, 10)


class FakeResponse:
    """Minimal ``requests`` response for recorded API documents."""

    def __init__(self, status_code: int, document: object = None) -> None:
        self.status_code = status_code
        self.document = document

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> object:
        return self.document


@pytest.fixture
def web3() -> SimpleNamespace:
    """Chain-only Web3 stand-in; the metadata module reads only ``chain_id``."""
    return SimpleNamespace(eth=SimpleNamespace(chain_id=1))


def install_responses(monkeypatch: pytest.MonkeyPatch, responses: dict[str, FakeResponse | Exception]) -> list[str]:
    """Serve recorded responses by URL path and record requested URLs."""
    requested = []

    def fake_get(url: str, timeout: int) -> FakeResponse:
        requested.append(url)
        response = next(value for key, value in responses.items() if key in url)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(offchain_metadata.requests, "get", fake_get)
    return requested


def test_strip_markdown() -> None:
    """Headings, emphasis and bullets are removed for plain-text summaries."""
    text = "**Facility Purpose: Revolving Credit**\n\n## Terms\n- 10% APR\n* weekly __repayment__ and *The Facility*"
    assert strip_markdown(text) == "Facility Purpose: Revolving Credit\n\nTerms\n10% APR\nweekly repayment and The Facility"
    assert strip_markdown("") is None
    assert strip_markdown(None) is None


@pytest.mark.parametrize(
    "text, summary",
    [
        # Real borrower texts whose first line is a heading, a date or a link
        ("Loan market info\nhttps://x.com/Gasp_xyz/status/1909658236665651409\n\nGasp is the purpose-built blockchain for stablecoin trading. Secured by \n@eigenlayer", "Gasp is the purpose-built blockchain for stablecoin trading. Secured by"),
        ("March 25, 2026\n\nMaseer Finance | USDT | 20% APR\n\n*The Facility*\nRevolving credit to guarantee CANA token redemptions (Q2 2026).", "Revolving credit to guarantee CANA token redemptions (Q2 2026)."),
        ("**Facility Purpose: Revolving Credit for Redemption Liquidity**\n\nCANA is preparing for centralized exchange listings.", "CANA is preparing for centralized exchange listings."),
        ("CREDI is redefining private credit by unlocking Receivables\nfinance opportunities through blockchain technology.", "CREDI is redefining private credit by unlocking Receivables finance opportunities through blockchain technology."),
        ("Delta neutral arbitrage", "Delta neutral arbitrage"),
        (None, None),
    ],
)
def test_extract_summary_line(text: str | None, summary: str | None) -> None:
    """A summary starts from the first line that ends a sentence."""
    assert extract_summary_line(text) == summary


def test_wildcat_metadata_cache_and_failures(web3: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fetched entries are cached, a 404 is stored and failures keep old values."""
    requested = install_responses(
        monkeypatch,
        {
            "/market-summary/": FakeResponse(404, {"error": "Market description not found"}),
            "/profiles/": FakeResponse(200, {"profile": {"name": "Wintermute Trading Ltd", "alias": "Wintermute", "description": "**Wintermute** is a trading firm."}}),
        },
    )
    metadata = fetch_wildcat_market_metadata(web3, MARKET, BORROWER, now_=NOW, cache_path=tmp_path)
    assert metadata == {"market_description": None, "borrower_name": "Wintermute Trading Ltd", "borrower_description": "**Wintermute** is a trading firm."}
    assert len(requested) == 2

    # A fresh cache makes no requests
    assert fetch_wildcat_market_metadata(web3, MARKET, BORROWER, now_=NOW + datetime.timedelta(days=1), cache_path=tmp_path) == metadata
    assert len(requested) == 2

    # After expiry, failures and API omissions keep the published profile
    install_responses(monkeypatch, {"/market-summary/": requests.ConnectionError("down"), "/profiles/": FakeResponse(404, {"profile": None})})
    assert fetch_wildcat_market_metadata(web3, MARKET, BORROWER, now_=NOW + datetime.timedelta(days=3), cache_path=tmp_path) == metadata

    # A changed response schema keeps the cached values, like a transport failure
    install_responses(monkeypatch, {"/market-summary/": FakeResponse(200, {"description": {"text": "x"}}), "/profiles/": FakeResponse(200, {"profile": {"name": 123}})})
    assert fetch_wildcat_market_metadata(web3, MARKET, BORROWER, now_=NOW + datetime.timedelta(days=5), cache_path=tmp_path) == metadata

    # A market description is stored while a partial profile keeps omitted fields
    install_responses(monkeypatch, {"/market-summary/": FakeResponse(200, {"description": "This market raises working capital."}), "/profiles/": FakeResponse(200, {"profile": {"name": "Wintermute Trading Ltd", "description": None}})})
    assert fetch_wildcat_market_metadata(web3, MARKET, BORROWER, now_=NOW + datetime.timedelta(days=6), cache_path=tmp_path) == metadata | {"market_description": "This market raises working capital."}
