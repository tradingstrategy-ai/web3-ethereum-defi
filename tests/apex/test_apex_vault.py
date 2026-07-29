"""ApeX public envelope parsing and pagination tests."""

# ruff: noqa: ARG002, PLR2004

import copy
import json
from pathlib import Path
from typing import Callable

import pytest

from eth_defi.apex.session import ApexAPIError, create_apex_session_pool
from eth_defi.apex.vault import (
    ApexRankingPage,
    fetch_official_vault_histories,
    fetch_official_vaults,
    fetch_stabilised_vaults,
    parse_history,
    parse_official_vault_histories,
    parse_official_vaults,
    parse_ranking_page,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> dict:
    with (FIXTURES / name).open() as inp:
        return json.load(inp)


def test_parse_ranking_page_retains_all_vaults() -> None:
    """Retain terminal and non-terminal vaults with shared addresses."""
    page = parse_ranking_page(_fixture("ranking-page.json"))
    assert page.total_size == 2
    assert len(page.vaults) == 2
    first, second = page.vaults
    assert first.vault_id == "1001"
    assert first.synthetic_address == "apex-vault-1001"
    assert first.share_price == pytest.approx(1.1234567890123457)
    assert first.created_at is not None
    assert first.created_at.tzinfo is None
    assert first.purchase_fee_rate_raw == "0"
    assert first.reported_ethereum_address == second.reported_ethereum_address
    assert second.status == "VAULT_FINISHED"


@pytest.mark.parametrize(
    "payload",
    (
        {"code": 2, "msg": "page error"},
        {},
        {"data": None},
        {"data": {"totalSize": 1, "vaultList": None}},
        {"data": {"totalSize": "1", "vaultList": []}},
    ),
)
def test_parse_ranking_page_rejects_bad_envelope(payload: dict) -> None:
    """Reject application errors and malformed ranking envelopes."""
    with pytest.raises(ApexAPIError):
        parse_ranking_page(payload)


@pytest.mark.parametrize("vault_id", ({}, [], True, 1.5, None))
def test_parse_ranking_page_rejects_compound_vault_ids(vault_id: object) -> None:
    """Reject non-string and non-integer values as persistent vault identities."""
    payload = _fixture("ranking-page.json")
    payload["data"]["vaultList"][0]["vaultId"] = vault_id
    with pytest.raises(ApexAPIError, match="vaultId must be"):
        parse_ranking_page(payload)


def test_parse_ranking_page_accepts_integer_vault_id() -> None:
    """Normalise a scalar integer vault identity to its string key."""
    payload = _fixture("ranking-page.json")
    payload["data"]["vaultList"][0]["vaultId"] = 1001
    assert parse_ranking_page(payload).vaults[0].vault_id == "1001"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("vaultNetValue", True, "not numeric"),
        ("createdTime", True, "integer millisecond"),
        ("createdTime", 1753228800000.5, "integer millisecond"),
    ),
)
def test_parse_ranking_page_rejects_malformed_scalar_values(field: str, value: object, message: str) -> None:
    """Reject booleans and fractional timestamps instead of coercing them."""
    payload = _fixture("ranking-page.json")
    payload["data"]["vaultList"][0][field] = value
    with pytest.raises(ApexAPIError, match=message):
        parse_ranking_page(payload)


def test_parse_history_orders_and_derives_supply() -> None:
    """Order exact source timestamps and derive valid share supply."""
    points = parse_history(_fixture("history.json"))
    assert len(points) == 3
    assert points == tuple(sorted(points, key=lambda point: point.timestamp))
    assert points[0].total_supply == pytest.approx(100)
    assert points[1].net_value == 0
    assert points[1].total_supply is None


def test_parse_history_duplicate_equivalence_and_conflict() -> None:
    """Collapse equivalent duplicates and reject conflicting timestamps."""
    payload = _fixture("history.json")
    duplicate = copy.deepcopy(payload["data"]["timeValue"][0])
    duplicate["netValue"] = "1.25"
    payload["data"]["timeValue"].append(duplicate)
    assert len(parse_history(payload)) == 3
    payload["data"]["timeValue"][-1]["totalValue"] = "126"
    with pytest.raises(ApexAPIError, match="conflicting"):
        parse_history(payload)


def test_parse_official_vaults_and_batch_histories() -> None:
    """Parse official type fields and preserve their independent histories."""
    vaults = parse_official_vaults(_fixture("official-vaults.json"))
    histories = parse_official_vault_histories(_fixture("official-history.json"), ("10000", "10001"))
    assert [vault.vault_id for vault in vaults] == ["10000", "10001"]
    assert vaults[0].vault_type == "NORMAL_OFFICIAL_VAULT_TYPE"
    assert histories["10000"][0].timestamp < histories["10000"][1].timestamp
    assert histories["10001"][0].total_value == pytest.approx(20020)


def test_parse_official_vault_history_rejects_partial_batch() -> None:
    """Do not accept a partial official history batch as an empty response."""
    payload = _fixture("official-history.json")
    payload["data"]["timeValueBatch"].pop()
    with pytest.raises(ApexAPIError, match="omitted"):
        parse_official_vault_histories(payload, ("10000", "10001"))


class _QueuedPool:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = payloads
        self.deadlines: list[float] = []

    def fetch_json(
        self,
        path: str,
        *,
        params: dict[str, str | int],
        operation_deadline: float,
        validator: Callable[[object], ApexRankingPage],
    ) -> ApexRankingPage:
        assert path == "vault/ranking"
        self.deadlines.append(operation_deadline)
        return validator(self.payloads.pop(0))


class _OfficialQueuedPool:
    """Small public-endpoint double recording paths and parameters."""

    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = payloads
        self.calls: list[tuple[str, dict[str, str | int]]] = []

    def fetch_json(
        self,
        path: str,
        *,
        params: dict[str, str | int],
        operation_deadline: float,
        validator: Callable[[object], object],
    ) -> object:
        del operation_deadline
        self.calls.append((path, params))
        return validator(self.payloads.pop(0))


def _ranking_payload(total: int, rows: list[dict]) -> dict:
    return {"data": {"totalSize": total, "vaultList": rows}}


def test_fetch_stabilised_vaults_uses_second_pass_and_shared_deadline() -> None:
    """Use second-pass metrics under one shared ranking deadline."""
    rows = _fixture("ranking-page.json")["data"]["vaultList"]
    second_pass_rows = copy.deepcopy(rows)
    second_pass_rows[0]["vaultNetValue"] = "1.5"
    pool = _QueuedPool(
        [
            _ranking_payload(2, rows),
            _ranking_payload(2, second_pass_rows),
        ]
    )
    vaults = fetch_stabilised_vaults(pool, limit=100, operation_timeout=10, attempts=1)
    assert len(vaults) == 2
    assert vaults[0].share_price == 1.5
    assert len(set(pool.deadlines)) == 1


def test_fetch_stabilised_vaults_rejects_membership_churn() -> None:
    """Reject a same-sized membership change between ranking passes."""
    rows = _fixture("ranking-page.json")["data"]["vaultList"]
    changed = copy.deepcopy(rows)
    changed[1]["vaultId"] = "9999"
    pool = _QueuedPool([_ranking_payload(2, rows), _ranking_payload(2, changed)])
    with pytest.raises(ApexAPIError, match="Could not stabilise"):
        fetch_stabilised_vaults(pool, operation_timeout=10, attempts=1)


def test_fetch_stabilised_vaults_rejects_duplicate_ids() -> None:
    """Reject duplicate logical IDs within a ranking pass."""
    rows = copy.deepcopy(_fixture("ranking-page.json")["data"]["vaultList"])
    rows[1]["vaultId"] = rows[0]["vaultId"]
    pool = _QueuedPool([_ranking_payload(2, rows)])
    with pytest.raises(ApexAPIError, match="duplicate vault IDs"):
        fetch_stabilised_vaults(pool, operation_timeout=10, attempts=1)


def test_fetch_stabilised_vaults_rejects_row_count_mismatch() -> None:
    """Reject a complete pass whose rows do not match its reported total."""
    rows = _fixture("ranking-page.json")["data"]["vaultList"]
    pool = _QueuedPool([_ranking_payload(3, rows), _ranking_payload(3, [])])
    with pytest.raises(ApexAPIError, match="reported 3"):
        fetch_stabilised_vaults(pool, operation_timeout=10, attempts=1)


def test_fetch_official_vault_endpoints_use_distinct_listing_and_batch_paths() -> None:
    """Use the API paths absent from the user-vault ranking reader."""
    pool = _OfficialQueuedPool([_fixture("official-vaults.json"), _fixture("official-history.json")])
    vaults = fetch_official_vaults(pool, operation_timeout=10)
    histories = fetch_official_vault_histories(pool, ("10000", "10001"), operation_timeout=10)
    assert len(vaults) == 2
    assert set(histories) == {"10000", "10001"}
    assert pool.calls == [
        ("vault/official-vaults", {}),
        ("vault/fund-net-value-batch", {"vaultIds": "10000,10001"}),
    ]


@pytest.mark.live
@pytest.mark.timeout(30)
def test_live_official_vault_listing_accepts_non_paginated_total_size() -> None:
    """Accept the live official listing despite its non-paginated ``totalSize`` value.

    ApeX currently reports ``totalSize=0`` while returning its official vaults
    in ``vaultList``. This focused integration check exercises the real HTTP
    response and parser without writing scanner state or reading history.
    """
    with create_apex_session_pool(pool_maxsize=1, retries=0) as session_pool:
        vaults = fetch_official_vaults(session_pool, operation_timeout=30)

    assert {vault.vault_id for vault in vaults} >= {"10000", "10001"}
