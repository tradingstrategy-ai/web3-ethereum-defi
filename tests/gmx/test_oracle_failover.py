"""Failover behaviour of the GMX oracle price query.

``/signed_prices/latest`` is served by four GMX API tiers: primary, backup,
fallback and fallback-2. ``gmxapi.ai`` is not one of them, because it answers
404 on the price endpoints.

Only the network boundary (``requests.get``) and the backoff sleeps are
replaced. The real :py:class:`~eth_defi.gmx.core.oracle.OraclePrices` logic runs
against real ``requests.Response`` objects, so these tests run offline and need
no RPC credentials (unlike ``test_gmx_oracle.py``, whose ``chain_name``
parametrisation skips the whole module without ``JSON_RPC_ARBITRUM``).
"""

import json
import logging
import time
from collections.abc import Iterator
from types import SimpleNamespace

import pytest
import requests

from eth_defi.gmx.constants import (
    GMX_API_URLS,
    GMX_API_URLS_BACKUP,
    GMX_API_URLS_FALLBACK,
    GMX_API_URLS_FALLBACK_2,
)
from eth_defi.gmx.core import oracle as oracle_module
from eth_defi.gmx.core.oracle import OraclePrices, clear_oracle_prices_cache
from eth_defi.gmx.tier_health import clear_tier_health

ORACLE_PATH = "/signed_prices/latest"

PRIMARY = GMX_API_URLS["arbitrum"] + ORACLE_PATH
BACKUP = GMX_API_URLS_BACKUP["arbitrum"] + ORACLE_PATH
FALLBACK = GMX_API_URLS_FALLBACK["arbitrum"] + ORACLE_PATH
FALLBACK_2 = GMX_API_URLS_FALLBACK_2["arbitrum"] + ORACLE_PATH

#: One entry of a real ``/signed_prices/latest`` response, all 28 fields as served
#: (the hex ``blob`` is shortened).
SIGNED_PRICE_ENTRY = {
    "id": "12028346949",
    "minBlockNumber": None,
    "minBlockHash": None,
    "oracleDecimals": None,
    "tokenSymbol": "USDG",
    "tokenAddress": "0x004B506865409877C9fA29bfb1ebA929984B9bbC",
    "minPrice": None,
    "maxPrice": None,
    "signer": None,
    "signature": None,
    "signatureWithoutBlockHash": None,
    "createdAt": "2026-09-21T06:38:59.258Z",
    "minBlockTimestamp": 1789972738,
    "oracleKeeperKey": "realtimeFeed",
    "maxBlockTimestamp": 1789972738,
    "maxBlockNumber": None,
    "maxBlockHash": None,
    "maxPriceFull": "1000044566535766700000000",
    "minPriceFull": "999927886969548800000000",
    "oracleKeeperRecordId": None,
    "oracleKeeperFetchType": "ws",
    "oracleType": "realtimeFeed2",
    "blob": "0x00094baebfda9b87680d8e59aa20a3e565126640ee7caeab3cd965e5568b17ee",
    "isValid": True,
    "invalidReason": None,
    "marketStatus": 2,
    "feedVersion": "v3",
    "stalenessSeconds": None,
}

PAYLOAD = {"signedPrices": [SIGNED_PRICE_ENTRY]}


@pytest.fixture(autouse=True)
def _isolated_oracle_module(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Skip real backoff sleeps in the oracle module and isolate its module-level price cache and host health."""
    clear_oracle_prices_cache()
    clear_tier_health()
    monkeypatch.setattr(oracle_module, "time", SimpleNamespace(sleep=lambda _seconds: None, time=time.time))
    yield
    clear_oracle_prices_cache()
    clear_tier_health()


def _response(status_code: int, payload: dict) -> requests.Response:
    """Build a real ``requests.Response`` carrying a JSON body."""
    response = requests.Response()
    response.status_code = status_code
    response._content = json.dumps(payload).encode()
    return response


def _install_fake_get(monkeypatch: pytest.MonkeyPatch, answers: dict[str, requests.Response]) -> list[str]:
    """Route ``requests.get`` by URL. A URL without an answer behaves as an unreachable host.

    :param answers:
        Response served per exact URL.
    :return:
        List every requested URL is appended to, in call order.
    """
    calls: list[str] = []

    def fake_get(url: str, *_args, **_kwargs) -> requests.Response:
        calls.append(url)
        if url not in answers:
            raise requests.exceptions.ConnectionError(f"unreachable: {url}")
        return answers[url]

    monkeypatch.setattr(oracle_module.requests, "get", fake_get)
    return calls


def test_get_recent_prices_served_by_fallback_2_when_three_tiers_are_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prices still load when only the last tier is up.

    Catches: the query giving up after the backup tier and raising even though
    fallback-2 is healthy (a dead ``gmxinfra.io`` domain plus a dead backup).
    """
    _install_fake_get(monkeypatch, {FALLBACK_2: _response(200, PAYLOAD)})

    prices = OraclePrices("arbitrum").get_recent_prices(use_cache=False)

    assert prices == {"0x004B506865409877C9fA29bfb1ebA929984B9bbC": SIGNED_PRICE_ENTRY}


def test_make_query_tries_all_four_tiers_in_order_before_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every tier is asked, in failover order, and ``gmxapi.ai`` is never asked.

    Catches: a tier missing from or misordered in the chain, and the ``gmxapi.ai``
    tier (404 on this endpoint) being added by mistake.
    """
    calls = _install_fake_get(monkeypatch, {})
    oracle = OraclePrices("arbitrum")

    with pytest.raises(requests.exceptions.RequestException):
        oracle._make_query(max_retries=1)

    assert calls == [PRIMARY, BACKUP, FALLBACK, FALLBACK_2]


def test_make_query_stops_at_the_first_tier_that_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    """A healthy backup ends the walk, so later tiers see no traffic.

    Catches: continuing down the chain after a successful answer.
    """
    calls = _install_fake_get(monkeypatch, {BACKUP: _response(200, PAYLOAD)})
    oracle = OraclePrices("arbitrum")

    response = oracle._make_query(max_retries=1)

    assert response.json() == PAYLOAD
    assert calls == [PRIMARY, BACKUP]


def test_make_query_without_backup_url_never_reaches_fallback_tiers(monkeypatch: pytest.MonkeyPatch) -> None:
    """``backup_oracle_url = None`` keeps disabling failover altogether.

    ``test_gmx_oracle.py::test_make_query_invalid_url`` relies on this to get an exception.
    Catches: fallback tiers walked unconditionally. They are healthy here, so a
    wrongly walked fallback would answer and the expected exception would not occur.
    """
    calls = _install_fake_get(monkeypatch, {FALLBACK: _response(200, PAYLOAD), FALLBACK_2: _response(200, PAYLOAD)})
    oracle = OraclePrices("arbitrum")
    oracle.backup_oracle_url = None

    with pytest.raises(requests.exceptions.RequestException):
        oracle._make_query(max_retries=1)

    assert calls == [PRIMARY]


def test_testnet_chain_uses_mainnet_fallback_tiers(monkeypatch: pytest.MonkeyPatch) -> None:
    """A testnet oracle walks the same mainnet tiers as the mainnet one.

    Catches: fallback URLs looked up by the requested chain instead of the mapped
    oracle chain. ``arbitrum_sepolia`` has its own DigitalOcean entry in the fallback tables.
    """
    calls = _install_fake_get(monkeypatch, {})
    oracle = OraclePrices("arbitrum_sepolia")

    with pytest.raises(requests.exceptions.RequestException):
        oracle._make_query(max_retries=1)

    assert calls == [PRIMARY, BACKUP, FALLBACK, FALLBACK_2]


def test_make_query_logs_which_tier_served_a_failover(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """A switch to a non-primary tier is visible in the logs.

    Catches: a silent failover, where operators cannot tell that the primary is
    down and which endpoint is actually feeding prices.
    """
    _install_fake_get(monkeypatch, {FALLBACK_2: _response(200, PAYLOAD)})
    oracle = OraclePrices("arbitrum")

    with caplog.at_level(logging.INFO):
        oracle._make_query(max_retries=1)

    info_messages = [record.getMessage() for record in caplog.records if record.levelno == logging.INFO]
    assert any(GMX_API_URLS_FALLBACK_2["arbitrum"] in message for message in info_messages)


def test_make_query_is_silent_when_primary_answers(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """A healthy primary logs nothing at INFO or above.

    Catches: logging every successful query. The oracle is polled continuously,
    so that noise would drown out the real failover messages.
    """
    _install_fake_get(monkeypatch, {PRIMARY: _response(200, PAYLOAD)})
    oracle = OraclePrices("arbitrum")

    with caplog.at_level(logging.INFO):
        oracle._make_query(max_retries=1)

    assert [record for record in caplog.records if record.levelno >= logging.INFO] == []
