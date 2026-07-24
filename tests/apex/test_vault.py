"""ApeX public envelope parsing and pagination tests."""

# ruff: noqa: ARG002, PLR2004

import copy
import json
from pathlib import Path
from typing import Callable

import pytest

from eth_defi.apex.session import ApexAPIError
from eth_defi.apex.vault import (
    ApexRankingPage,
    fetch_stabilised_vaults,
    parse_history,
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
