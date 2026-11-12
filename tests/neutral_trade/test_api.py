"""Focused contract, exclusion, bounded-pagination and real API checks."""

import os

# ruff: noqa: PLR2004
import pytest
import requests

from eth_defi.neutral_trade.api import WSOL_MINT, NeutralTradeAPIError, NeutralTradeClient, parse_vault

ADDRESS = "J7qhMAKnB6G5dvoAN9281ufabajKbyGQxxd2bq6R7fPJ"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


@pytest.fixture
def vault_record() -> dict:
    """Supply a synthetic public directory record.

    The identity is a known API account; values are synthetic, not live evidence.

    :return: Minimal directory record for contract tests.
    """
    return {"bundleKey": ADDRESS, "chain": "solana", "name": "Fixture", "visible": True, "private": False, "asset": {"mint": USDC, "symbol": "USDC", "decimals": 6}}


def test_wsol_exclusion_precedes_reads(vault_record: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exclude WSOL even when its symbol is misleading.

    Both detail and history must reject the vault without calling transport.

    :param vault_record: Synthetic directory record.
    :param monkeypatch: Test-local transport replacement.
    """
    vault_record["asset"]["mint"] = WSOL_MINT
    vault = parse_vault(vault_record)
    assert vault.skip_reason == "unsupported_denomination"
    with NeutralTradeClient("fixture-secret") as client:

        def unexpected(*_: object, **__: object) -> dict:
            pytest.fail("Excluded vault caused a network read")

        monkeypatch.setattr(client, "_fetch", unexpected)
        with pytest.raises(NeutralTradeAPIError, match="excluded"):
            client.fetch_vault(vault)
        with pytest.raises(NeutralTradeAPIError, match="excluded"):
            list(client.fetch_history_pages(vault, "snapshots"))


def test_history_keeps_exact_values_and_stops(vault_record: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """Traverse two pages while preserving lossless source amount strings.

    A null cursor ends inspection; the original address retains its case.

    :param vault_record: Synthetic directory record.
    :param monkeypatch: Test-local transport replacement.
    """
    calls = []

    def fetch(_path: str, params: dict) -> dict:
        calls.append(dict(params))
        return {"data": {"vaultId": ADDRESS, "series": [{"pps": "9007199254740993"}], "nextCursor": "page-two" if len(calls) == 1 else None}}

    with NeutralTradeClient("fixture-secret") as client:
        monkeypatch.setattr(client, "_fetch", fetch)
        pages = list(client.fetch_history_pages(parse_vault(vault_record), "share-price", max_pages=10))
    assert len(pages) == 2
    assert pages[0]["data"]["series"][0]["pps"] == "9007199254740993"
    assert calls[1]["cursor"] == "page-two"
    assert parse_vault(vault_record).address == ADDRESS


def test_repeating_cursor_fails(vault_record: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject a cursor loop instead of claiming complete history.

    The first page can be inspected, but the repeated continuation fails.

    :param vault_record: Synthetic directory record.
    :param monkeypatch: Test-local transport replacement.
    """
    with NeutralTradeClient("fixture-secret") as client:
        monkeypatch.setattr(client, "_fetch", lambda *_args: {"data": {"vaultId": ADDRESS, "snapshots": [], "nextCursor": "loop"}})
        with pytest.raises(NeutralTradeAPIError, match="repeating"):
            list(client.fetch_history_pages(parse_vault(vault_record), "snapshots"))


def test_partner_reads_require_key() -> None:
    """Fail absent credentials before transport.

    Missing authentication must not be confused with an empty directory.
    """
    with NeutralTradeClient() as client:
        with pytest.raises(NeutralTradeAPIError, match="API_KEY"):
            list(client.fetch_vaults())


@pytest.mark.live
def test_live_public_yields() -> None:
    """Verify a successful real public provider response.

    Validate the catalogue shape without fixing live counts or balances.
    """
    with NeutralTradeClient() as client:
        result = client.fetch_public_yields()
    assert isinstance(result["data"]["vaults"], list)
    assert result["data"]["count"] == len(result["data"]["vaults"])


@pytest.mark.live
def test_live_partner_contract() -> None:
    """Verify real authenticated directory and historical reads when configured.

    A required validation run fails rather than skipping missing credentials.
    Hidden/private and WSOL records are not inspected or printed.
    """
    key = os.environ.get("NEUTRAL_TRADE_API_KEY")
    if not key:
        if os.environ.get("REQUIRE_NEUTRAL_TRADE_LIVE_TESTS", "false").lower() == "true":
            pytest.fail("NEUTRAL_TRADE_API_KEY is required for this validation run")
        pytest.skip("Partner credential not supplied")
    with NeutralTradeClient(key) as client:
        client.fetch_status()
        vault = next((v for v in client.fetch_vaults() if v.public and not v.skip_reason), None)
        assert vault is not None, "No eligible vault; authenticated verification incomplete"
        client.fetch_vault(vault)
        client.fetch_metrics(vault)
        for endpoint in ("share-price", "snapshots"):
            pages = list(client.fetch_history_pages(vault, endpoint, page_size=1, max_pages=2))
            field = "series" if endpoint == "share-price" else "snapshots"
            assert pages[0]["data"][field], "No historical data; verification incomplete"
            if pages[0]["data"]["nextCursor"] is not None:
                assert len(pages) == 2


@pytest.mark.parametrize("status", [301, 401, 403])
def test_http_errors_do_not_leak_credentials(status: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse redirects and auth failures without including private diagnostics.

    A server may reflect the supplied key in its body; do not surface it.

    :param status: HTTP failure or redirect to simulate.
    :param monkeypatch: Test-local request replacement.
    """
    response = requests.Response()
    response.status_code = status
    response._content = b"fixture-secret"
    response._content_consumed = True
    with NeutralTradeClient("fixture-secret") as client:

        def fetch(_url: str, **kwargs: object) -> requests.Response:
            assert kwargs["allow_redirects"] is False
            return response

        monkeypatch.setattr(client._session, "get", fetch)
        with pytest.raises(NeutralTradeAPIError) as error:
            client.fetch_status()
    assert "fixture-secret" not in str(error.value)
    assert str(status) in str(error.value)


def test_retry_honours_quota_without_sleeping(monkeypatch: pytest.MonkeyPatch) -> None:
    """Respect Retry-After within the operation budget.

    Simulated time avoids slowing focused tests and verifies a real retry.

    :param monkeypatch: Test-local request and clock replacements.
    """
    clock = [0.0]
    calls = []

    def sleep(seconds: float) -> None:
        clock[0] += seconds

    def fetch(_url: str, **_kwargs: object) -> requests.Response:
        calls.append(1)
        response = requests.Response()
        response.status_code = 429 if len(calls) == 1 else 200
        response.headers["Retry-After"] = "7"
        response._content = b'{"data": {}}'
        response._content_consumed = True
        return response

    monkeypatch.setattr("eth_defi.neutral_trade.api.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("eth_defi.neutral_trade.api.time.sleep", sleep)
    with NeutralTradeClient("fixture-secret") as client:
        monkeypatch.setattr(client._session, "get", fetch)
        assert client.fetch_status() == {"data": {}}
    assert clock[0] == 7.0
    assert len(calls) == 2


@pytest.mark.parametrize("content", [b'{"data": []}', b"invalid fixture-secret", b"x" * (4 * 1024 * 1024 + 1)])
def test_invalid_or_oversized_response(content: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail closed on malformed or oversized payloads without leaking the body.

    Response validation must not turn a failed provider into an empty catalogue.

    :param content: Invalid response bytes.
    :param monkeypatch: Test-local transport replacement.
    """
    response = requests.Response()
    response.status_code = 200
    response._content = content
    response._content_consumed = True
    with NeutralTradeClient("fixture-secret") as client:
        monkeypatch.setattr(client._session, "get", lambda *_args, **_kwargs: response)
        with pytest.raises(NeutralTradeAPIError) as error:
            client.fetch_status()
    assert "fixture-secret" not in str(error.value)


def test_metrics_and_hidden_vault_guard(vault_record: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """Read metrics for an eligible vault and block private records.

    Metrics must have the same pre-read admission checks as historical data.

    :param vault_record: Synthetic directory entry.
    :param monkeypatch: Test-local transport replacement.
    """
    with NeutralTradeClient("fixture-secret") as client:
        calls = []

        def fetch(path: str) -> dict:
            calls.append(path)
            return {"data": {"rateUnit": "decimal"}}

        monkeypatch.setattr(client, "_fetch", fetch)
        assert client.fetch_metrics(parse_vault(vault_record))["data"]["rateUnit"] == "decimal"
        vault_record["private"] = True
        with pytest.raises(NeutralTradeAPIError, match="excluded"):
            client.fetch_metrics(parse_vault(vault_record))
    assert calls == [f"/v2/vault/{ADDRESS}/metrics"]


def test_partial_directory_rejects_bad_record(vault_record: dict, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Retain healthy leads when another record is malformed.

    Only rejected counts are logged, never unknown/private identifiers.

    :param vault_record: Synthetic directory entry.
    :param monkeypatch: Test-local transport replacement.
    :param caplog: Captured diagnostics.
    """
    with NeutralTradeClient("fixture-secret") as client:
        monkeypatch.setattr(client, "_fetch", lambda *_args: {"data": {"vaults": [vault_record, {"private-id": "do-not-print"}], "unavailableVaults": []}})
        assert len(list(client.fetch_vaults())) == 1
    assert "rejected: 1" in caplog.text
    assert "do-not-print" not in caplog.text


def test_evm_history_accepts_address_case(vault_record: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """Match EVM keys case-insensitively while retaining Solana's exact case.

    Checksummed directory records may pair with uniform-case history IDs.

    :param vault_record: Synthetic directory entry.
    :param monkeypatch: Test-local transport replacement.
    """
    vault_record.update(bundleKey="0x" + "aB" * 20, chain="evm:4663")
    vault_record["asset"]["mint"] = "0x" + "12" * 20
    with NeutralTradeClient("fixture-secret") as client:
        monkeypatch.setattr(client, "_fetch", lambda *_args: {"data": {"vaultId": vault_record["bundleKey"].lower(), "series": [], "nextCursor": None}})
        assert len(list(client.fetch_history_pages(parse_vault(vault_record), "share-price"))) == 1


@pytest.mark.parametrize("field", ["bundleKey", "mint"])
def test_directory_requires_complete_solana_accounts(vault_record: dict, field: str) -> None:
    """Reject base58-looking directory identities with the wrong decoded width.

    The API's broad text pattern does not establish an account; invalid keys
    must be rejected before source persistence, metadata conversion or history.

    :param vault_record: Synthetic otherwise valid public directory record.
    :param field: Vault or denomination identity to corrupt.
    :return: ``None`` after rejecting the invalid native directory record.
    """
    if field == "bundleKey":
        vault_record[field] = "z" * 44
    else:
        vault_record["asset"][field] = "z" * 44
    with pytest.raises(NeutralTradeAPIError, match="32-byte Solana"):
        parse_vault(vault_record)
