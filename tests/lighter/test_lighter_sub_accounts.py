"""Lighter multi-account discovery, sub-account creation and transfer helpers.

The mocked tests replace only Lighter's HTTP responses and the optional SDK
signer, because sub-account creation and transfers are L2 transactions that
cannot be exercised without a funded Lighter account. The live test reads a
real owner's account tree from the public API without credentials.
"""

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from pytest_mock import MockerFixture

import eth_defi.lighter.sub_account as sub_account_module
import eth_defi.lighter.transfer as transfer_module
from eth_defi.lighter.api import LIGHTER_SUB_ACCOUNT_INDEX_START, LighterAccounts, fetch_lighter_accounts
from eth_defi.lighter.session import create_lighter_session
from eth_defi.lighter.sub_account import LighterSubAccountError, create_lighter_sub_accounts
from eth_defi.lighter.transfer import (
    LIGHTER_EMPTY_TRANSFER_MEMO,
    LighterTransferError,
    create_lighter_auth_token_manager,
    fetch_lighter_transfer_fee,
    request_lighter_transfer,
    wait_for_lighter_transfer,
)
from eth_defi.lighter.valuation import fetch_lighter_account_by_index, fetch_lighter_accounts_equity

#: Safe owning the mocked account tree.
OWNER = "0x0000000000000000000000000000000000000001"
#: Master account in the mocked responses.
MASTER_INDEX = 700
#: First and second sub-accounts, created in descending index order.
SUB_1 = LIGHTER_SUB_ACCOUNT_INDEX_START + 9
SUB_2 = LIGHTER_SUB_ACCOUNT_INDEX_START + 8
#: Public pool operated by the owner, which is not a trading sub-account.
POOL_INDEX = LIGHTER_SUB_ACCOUNT_INDEX_START + 5
#: Live sub-account of a third-party owner observed on 2026-10-26.
LIVE_SUB_ACCOUNT_INDEX = 281474976710639


class FakeResponse:
    """Minimal successful requests response."""

    def __init__(self, data: dict) -> None:
        self.data = data
        self.status_code = 200

    def raise_for_status(self) -> None:
        """Simulate a successful response."""

    def json(self) -> dict:
        """Return response JSON."""
        return self.data


class FakeSession:
    """Lighter session returning queued payloads."""

    api_url = "https://lighter.example"

    def __init__(self, responses: list[dict]) -> None:
        self.responses = responses

    def get(self, url: str, params: dict, timeout: float) -> FakeResponse:
        """Return the next payload."""
        del url, params, timeout
        return FakeResponse(self.responses.pop(0))


def _accounts_payload(*sub_indices: int) -> dict:
    """Build an ``accountsByL1Address`` payload with a master, sub-accounts and a pool."""
    entries = [{"index": MASTER_INDEX, "account_type": 0}]
    entries += [{"index": index, "account_type": 1} for index in sub_indices]
    entries.append({"index": POOL_INDEX, "account_type": 2})
    return {"sub_accounts": entries}


def _account(index: int, total: str) -> dict:
    """Build one ``account`` endpoint record."""
    return {"account_index": index, "collateral": total, "total_asset_value": total, "available_balance": total}


def test_fetch_lighter_accounts_and_equity_classify_owned_accounts() -> None:
    """Discover a master account, its sub-accounts and their equity, ignoring pools.

    The executor needs every trading account of the Safe and must value them
    from one response; a pool the owner operates is not a trading account.

    1. Parse an account tree with a master, two sub-accounts and a pool.
    2. Parse the multi-account equity snapshot keyed by account index.
    3. Reject a response with two master accounts.
    """
    # 1. Parse an account tree with a master, two sub-accounts and a pool.
    accounts = fetch_lighter_accounts(FakeSession([_accounts_payload(SUB_2, SUB_1)]), OWNER)  # type: ignore[arg-type]
    assert accounts == LighterAccounts(master_index=MASTER_INDEX, sub_account_indices=(SUB_1, SUB_2))
    assert accounts.get_all_indices() == (MASTER_INDEX, SUB_1, SUB_2)

    # 2. Parse the multi-account equity snapshot keyed by account index.
    equity = fetch_lighter_accounts_equity(
        FakeSession([{"accounts": [_account(MASTER_INDEX, "10.5"), _account(SUB_1, "20"), _account(SUB_2, "0")]}]),  # type: ignore[arg-type]
        OWNER,
    )
    assert {index: value.get_total() for index, value in equity.items()} == {MASTER_INDEX: Decimal("10.5"), SUB_1: Decimal("20"), SUB_2: Decimal("0")}

    # 3. Reject a response with two master accounts.
    with pytest.raises(ValueError, match="2 master accounts"):
        fetch_lighter_accounts(FakeSession([{"sub_accounts": [{"index": 1, "account_type": 0}, {"index": 2, "account_type": 0}]}]), OWNER)  # type: ignore[arg-type]


def _require_event_loop(client: "FakeSignerClient") -> "FakeSignerClient":
    """Return the fake client, failing like ``aiohttp`` outside a running event loop."""
    asyncio.get_running_loop()
    return client


class FakeSignerClient:
    """Stand-in for ``lighter.SignerClient`` recording signed L2 transactions."""

    ASSET_ID_USDC = 3

    def __init__(self, account_index: int, reject: bool = False) -> None:
        self.account_index = account_index
        self.api_client = object()
        self.reject = reject
        self.created = 0
        self.transfers: list[dict[str, Any]] = []
        self.closed = False

    async def create_sub_account(self, api_key_index: int = 255) -> tuple[str, Any, str | None]:
        """Accept or reject one sub-account creation signed through the nonce manager."""
        assert api_key_index == 255
        if self.reject:
            return None, None, "rejected"
        self.created += 1
        return "{}", SimpleNamespace(code=200, tx_hash=f"0xcreate{self.created}"), None

    async def transfer_same_master_account(self, **kwargs: Any) -> tuple[str, Any, None]:
        """Record one same-owner transfer."""
        self.transfers.append(kwargs)
        return "{}", SimpleNamespace(code=200, tx_hash="0xtransfer"), None

    def create_auth_token_with_expiry(self, api_key_index: int) -> tuple[str, None]:
        """Return a deterministic auth token."""
        return f"token-{api_key_index}", None

    async def close(self) -> None:
        """Record client shutdown."""
        self.closed = True


def test_create_lighter_sub_accounts_signs_with_master_key(mocker: MockerFixture) -> None:
    """Create sub-accounts with the master API key and return only the new indices.

    Sub-account creation is an L2 transaction signed by the master key, and an
    earlier sub-account must not be mistaken for a new one. The real SDK
    client opens an ``aiohttp`` connector, which fails outside a running event
    loop, so the fake factory requires one too.

    1. Create two sub-accounts for a master that already owns one.
    2. Assert the new indices in creation order and that the signer was closed.
    3. Raise a public error when Lighter rejects a creation.
    """
    # 1. Create two sub-accounts for a master that already owns one.
    existing = LIGHTER_SUB_ACCOUNT_INDEX_START + 10
    client = FakeSignerClient(MASTER_INDEX)
    mocker.patch.object(sub_account_module, "create_lighter_signer_client", side_effect=lambda *_args, **_kwargs: _require_event_loop(client))
    session = FakeSession([_accounts_payload(existing), _accounts_payload(existing, SUB_1, SUB_2)])
    indices = create_lighter_sub_accounts(session=session, l1_address=OWNER, master_account_index=MASTER_INDEX, api_key_index=4, api_private_key="0x" + "22" * 40, count=2, timeout=1)  # type: ignore[arg-type]

    # 2. Assert the new indices in creation order and that the signer was closed.
    assert indices == [SUB_1, SUB_2]
    assert client.created == 2
    assert client.closed

    # 3. Raise a public error when Lighter rejects a creation.
    rejecting_client = FakeSignerClient(MASTER_INDEX, reject=True)
    mocker.patch.object(sub_account_module, "create_lighter_signer_client", side_effect=lambda *_args, **_kwargs: _require_event_loop(rejecting_client))
    with pytest.raises(LighterSubAccountError, match="rejected"):
        create_lighter_sub_accounts(session=FakeSession([_accounts_payload()]), l1_address=OWNER, master_account_index=MASTER_INDEX, api_key_index=4, api_private_key="0x" + "22" * 40, count=1, timeout=1)  # type: ignore[arg-type]


def test_lighter_transfer_helpers_use_exact_amounts(mocker: MockerFixture) -> None:
    """Quote, submit and confirm a master-to-sub-account USDC transfer.

    The SDK takes float amounts, so the signed raw amount and fee must be
    exact, and confirmation must come from transfer history, not balances.

    1. Quote the transfer fee and submit a transfer with an amount that a naive float conversion truncates.
    2. Confirm the transfer from transfer history.
    3. Reject an ambiguous history with two rows for one hash.
    """
    history_rows = [SimpleNamespace(tx_hash="0xtransfer", from_account_index=MASTER_INDEX, to_account_index=SUB_1, amount="1.005", fee="0", timestamp=1)]

    class FakeInfoApi:
        def __init__(self, api_client: object) -> None:
            del api_client

        async def transfer_fee_info(self, authorization: str, account_index: int, to_account_index: int) -> Any:
            assert authorization == "token-4"
            assert (account_index, to_account_index) == (MASTER_INDEX, SUB_1)
            return SimpleNamespace(transfer_fee_usdc=0)

    class FakeTransactionApi:
        def __init__(self, api_client: object) -> None:
            del api_client

        async def transfer_history(self, account_index: int, authorization: str) -> Any:
            assert account_index == MASTER_INDEX
            return SimpleNamespace(transfers=history_rows)

    mocker.patch.object(transfer_module, "import_lighter_sdk", return_value=SimpleNamespace(InfoApi=FakeInfoApi, TransactionApi=FakeTransactionApi))
    client = FakeSignerClient(MASTER_INDEX)
    auth = create_lighter_auth_token_manager(client, api_key_index=4)

    # 1. Quote the transfer fee and submit a transfer with an amount that a naive float conversion truncates.
    fee = asyncio.run(fetch_lighter_transfer_fee(client, auth, to_account_index=SUB_1))
    tx_hash = asyncio.run(request_lighter_transfer(client, to_account_index=SUB_1, amount=Decimal("1.005"), fee=fee, api_key_index=4))
    sent = client.transfers[0]
    assert fee == Decimal(0)
    assert tx_hash == "0xtransfer"
    assert int(sent["amount"] * 10**6) == 1_005_000
    assert (sent["fee"], sent["memo"], sent["to_account_index"], sent["api_key_index"]) == (0, LIGHTER_EMPTY_TRANSFER_MEMO, SUB_1, 4)

    # 2. Confirm the transfer from transfer history.
    record = asyncio.run(wait_for_lighter_transfer(client, auth, account_index=MASTER_INDEX, tx_hash=tx_hash, timeout=1))
    assert (record.from_account_index, record.to_account_index, record.amount) == (MASTER_INDEX, SUB_1, Decimal("1.005"))

    # 3. Reject an ambiguous history with two rows for one hash.
    history_rows.append(history_rows[0])
    with pytest.raises(LighterTransferError, match="2 rows"):
        asyncio.run(wait_for_lighter_transfer(client, auth, account_index=MASTER_INDEX, tx_hash=tx_hash, timeout=1))


@pytest.mark.timeout(60)
def test_lighter_multi_account_reads_live() -> None:
    """Read a real owner's master, sub-accounts and equity from the public API.

    The by-address endpoints are new integrations, so they are checked against
    the real Lighter API without credentials.

    1. Resolve the L1 owner of a known live sub-account.
    2. Discover the owner's account tree.
    3. Value every owned account from one by-address equity snapshot.
    """
    session = create_lighter_session()
    try:
        # 1. Resolve the L1 owner of a known live sub-account.
        owner = fetch_lighter_account_by_index(session, LIVE_SUB_ACCOUNT_INDEX)["l1_address"]

        # 2. Discover the owner's account tree.
        accounts = fetch_lighter_accounts(session, owner)
        assert accounts is not None
        assert LIVE_SUB_ACCOUNT_INDEX in accounts.sub_account_indices
        assert accounts.master_index < LIGHTER_SUB_ACCOUNT_INDEX_START

        # 3. Value every owned account from one by-address equity snapshot.
        equity = fetch_lighter_accounts_equity(session, owner)
        assert set(accounts.get_all_indices()) <= set(equity)
        assert all(value.get_total() >= 0 for value in equity.values())
    finally:
        session.close()
