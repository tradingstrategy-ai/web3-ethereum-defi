"""Synchronous execution of Ethereum-settled Lighter public-pool investments.

Pool operations are API-key signed and bypass the Ethereum guard. The caller
must run one submitting process per account/key, persist every prepared signed
operation before submission, and reconcile uncertain responses before proceeding.

The optional signer is the pinned official `Python SDK
<https://github.com/elliottech/lighter-python/tree/fd4ee2530f78940cbed3dd80131d0fa66f74ad2a>`_.
Integer ``share_amount`` is the protocol's raw share quantity: sizing uses current
pool equity divided by ``total_shares``, never an assumed token decimal scale or
a daily chart price. See the `public-pool rules
<https://docs.lighter.xyz/trading/public-pools>`_.
"""

import datetime
import enum
import json
import logging
import threading
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from decimal import ROUND_DOWN, Decimal
from typing import Any, Callable, Protocol

import requests

from eth_defi.compat import native_datetime_utc_now
from eth_defi.lighter.api import LIGHTER_COLLATERAL_TOLERANCE, LIGHTER_MIN_MAINNET_USDC
from eth_defi.lighter.constants import LIGHTER_API_URL, LIGHTER_CHAIN_ID, LIGHTER_ETHEREUM, LIGHTER_USDC_ETHEREUM
from eth_defi.lighter.pubkey import MAX_API_KEY_INDEX, MIN_API_KEY_INDEX
from eth_defi.lighter.session import LighterSession
from eth_defi.lighter.valuation import LighterEquity, _parse_decimal, fetch_lighter_account_by_index, parse_lighter_account_equity
from eth_defi.lighter.vault import fetch_system_config
from eth_defi.token import TokenDetails

logger = logging.getLogger(__name__)

#: Signing domain of the Ethereum-settled Lighter network, not EVM chain ID 1.
LIGHTER_SIGNING_CHAIN_ID = 304
#: The official signer identifies USDC as asset 3 and uses route 0 for collateral.
LIGHTER_USDC_ASSET_ID = 3
#: API success code, distinct from transaction execution status.
API_SUCCESS = 200
#: Raw pool-share limit enforced by the official signer.
MAX_POOL_SHARES = (1 << 60) - 1


@dataclass(slots=True, frozen=True)
class LighterAccountIdentity:
    """Account identity including separate custody and signing networks."""

    #: Safe-owned Lighter account.
    account_index: int
    #: User API-key slot.
    api_key_index: int = 4
    #: Ethereum custody chain.
    ethereum_chain_id: int = 1
    #: Lighter signer chain/domain.
    signing_chain_id: int = LIGHTER_SIGNING_CHAIN_ID
    #: Dataset identifier remains independent of both network identities.
    dataset_chain_id: int = 9998
    #: Execution records must name the API deployment.
    api_url: str = LIGHTER_API_URL

    def __post_init__(self) -> None:
        """Reject accidental use of the Robinhood or synthetic signing domain."""
        assert self.ethereum_chain_id == 1 and self.signing_chain_id == LIGHTER_SIGNING_CHAIN_ID
        assert self.api_url == LIGHTER_API_URL and self.dataset_chain_id == LIGHTER_CHAIN_ID
        assert self.account_index >= 0 and MIN_API_KEY_INDEX <= self.api_key_index <= MAX_API_KEY_INDEX


@dataclass(slots=True, frozen=True)
class LighterPoolState:
    """Current execution-oriented pool state, without historical chart prices."""

    #: Pool account identifier.
    pool_index: int
    #: Protocol status; zero permits new investment.
    status: int
    #: Current gross pool equity in USDC.
    equity: Decimal
    #: Raw protocol share supply.
    total_shares: int
    #: Operator-owned raw shares, used to enforce capacity.
    operator_shares: int
    #: Operator's minimum fraction of supply.
    min_operator_share_rate: Decimal
    #: Fraction of positive investor profit charged on redemption.
    operator_fee: Decimal
    #: Collateral immediately available to the pool.
    available_balance: Decimal
    #: LLP has additional staking and cooldown restrictions.
    is_llp: bool
    #: Current LLP cooldown read from systemConfig.
    cooldown: datetime.timedelta
    #: Naive UTC time of observation.
    observed_at: datetime.datetime

    @property
    def share_value(self) -> Decimal:
        """Return current USDC per raw share, with no assumed decimal scale."""
        assert self.total_shares > 0 and self.equity >= 0, "Pool cannot be priced"
        return self.equity / self.total_shares

    @property
    def identifier(self) -> str:
        """Return the existing dataset-compatible pool identifier."""
        return f"lighter-pool-{self.pool_index}"


@dataclass(slots=True, frozen=True)
class LighterPoolHolding:
    """Investor shares, cost information and fee-aware estimated value."""

    #: Pool account.
    pool_index: int
    #: Raw shares held by the investor.
    shares: int
    #: Protocol entry value in USDC.
    entry_usdc: Decimal
    #: Protocol principal amount in USDC.
    principal: Decimal
    #: Last entry timestamp, naive UTC.
    entered_at: datetime.datetime
    #: Current gross value before operator fees.
    gross_value: Decimal
    #: Estimated operator fee on positive profits.
    estimated_fee: Decimal
    #: Time of the current pool observation.
    observed_at: datetime.datetime

    @property
    def estimated_proceeds(self) -> Decimal:
        """Return an estimate, not a signed minimum redemption guarantee."""
        return self.gross_value - self.estimated_fee


class LighterOperationStage(enum.StrEnum):
    """Persisted API transaction lifecycle, never inferred from acceptance alone."""

    #: Signed and saved, without a submission attempt.
    prepared = "prepared"
    #: Accepted by the API, awaiting execution confirmation.
    submitted = "submitted"
    #: Submission or execution outcome needs reconciliation.
    unknown = "unknown"
    #: Matching successful execution observed.
    confirmed = "confirmed"
    #: Explicit rejection observed.
    rejected = "rejected"


@dataclass(slots=True)
class LighterOperation:
    """Prepared signed transaction and subsequent execution observations."""

    #: Complete deployment and account identity.
    identity: LighterAccountIdentity
    #: ``mint_shares``, ``burn_shares`` or ``withdraw``.
    kind: str
    #: Pool account, absent for collateral withdrawal.
    pool_index: int | None
    #: Raw shares or raw USDC, according to kind.
    amount: int
    #: Explicit nonce assigned before signing.
    nonce: int
    #: Transaction type returned by the official signer.
    tx_type: int
    #: Exact signed payload; not the API private key.
    tx_info: str = field(repr=False)
    #: Deterministic hash returned before submission.
    tx_hash: str
    #: Current observation of the lifecycle.
    stage: LighterOperationStage = LighterOperationStage.prepared
    #: Venue receipt, when available.
    receipt: dict[str, Any] | None = field(default=None, repr=False)

    def as_dict(self) -> dict[str, Any]:
        """Return restart data for protected caller-owned persistence.

        :return: JSON-compatible operation including signed payload and nonce.
        """
        data = asdict(self)
        data["stage"] = self.stage.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LighterOperation":
        """Load an operation for reconciliation, without submitting it again.

        :param data: Saved ``as_dict()`` result.
        :return: Hydrated operation with its original transaction identity.
        """
        data = dict(data)
        data["identity"] = LighterAccountIdentity(**data["identity"])
        data["stage"] = LighterOperationStage(data["stage"])
        return cls(**data)


class LighterSyncSigner(Protocol):
    """Synchronous subset of the optional official SignerClient."""

    def sign_mint_shares(self, public_pool_index: int, share_amount: int, *, nonce: int, api_key_index: int) -> tuple[int, str, str, str | None]: ...
    def sign_burn_shares(self, public_pool_index: int, share_amount: int, *, nonce: int, api_key_index: int) -> tuple[int, str, str, str | None]: ...
    def sign_withdraw(self, asset_index: int, route_type: int, amount: int, *, nonce: int, api_key_index: int) -> tuple[int, str, str, str | None]: ...


def _assert_ethereum(session: LighterSession) -> None:
    """Require the Ethereum-settled execution deployment.

    Execution records and signing domains must agree with the selected session.

    :param session: Session whose deployment is checked.
    """
    assert session.api_url == LIGHTER_API_URL and session.deployment == LIGHTER_ETHEREUM, "Unsupported Lighter execution deployment"


def _required_decimal(data: dict[str, Any], key: str) -> Decimal:
    """Parse one required finite decimal instead of applying a zero fallback.

    Missing or non-finite monetary data makes execution valuation unavailable.

    :param data: Raw venue record.
    :param key: Required monetary field.
    :return: Finite monetary value.
    """
    return _parse_decimal(data.get(key), key)


def fetch_execution_account(session: LighterSession, account_index: int) -> tuple[dict[str, Any], LighterEquity]:
    """Fetch strict equity and investor holdings from the account endpoint.

    Validate required fields before using the older permissive equity parser.
    See https://apidocs.lighter.xyz/reference/account.

    :param session: Ethereum Lighter session.
    :param account_index: Investor account to read.
    :return: Raw account and equity, validated before the legacy parser runs.
    """
    _assert_ethereum(session)
    account = fetch_lighter_account_by_index(session, account_index)
    for key in ("collateral", "available_balance", "total_asset_value", "cross_initial_margin_requirement", "cross_maintenance_margin_requirement"):
        _required_decimal(account, key)
    assert isinstance(account.get("shares"), list), "Investor holdings are unavailable"
    assert isinstance(account.get("positions"), list), "Account positions are unavailable"
    for row in account["shares"]:
        assert type(row["shares_amount"]) is int and row["shares_amount"] >= 0, "Invalid raw share quantity"
        assert type(row["public_pool_index"]) is int, "Invalid public-pool identity"
        assert _required_decimal(row, "principal_amount") >= 0
        assert _required_decimal(row, "entry_usdc") >= 0
        assert type(row["entry_timestamp"]) is int and row["entry_timestamp"] >= 0
    for position in account["positions"]:
        _required_decimal(position, "unrealized_pnl")
    return account, parse_lighter_account_equity(account)


def fetch_pool_state(session: LighterSession, pool_index: int) -> LighterPoolState:
    """Fetch current pool equity, raw supply, capacity and LLP constraints.

    Operator fees and minimum operator share rates are converted from percentage
    points to fractions. Share quantities remain raw protocol integers.

    :param session: Ethereum Lighter API session.
    :param pool_index: Public pool account index.
    :return: Strict current observation; missing data raises rather than zero-fills.
    """
    _assert_ethereum(session)
    observed_at = native_datetime_utc_now()
    account = fetch_lighter_account_by_index(session, pool_index)
    info = account["pool_info"]
    config = fetch_system_config(session)
    assert isinstance(info, dict), "Account is not a public pool"
    assert all(type(info[key]) is int for key in ("status", "total_shares", "operator_shares")), "Pool share quantities and status must be integers"
    # The API returns both rates in percentage points (for example 30% fee and 5% operator share).
    state = LighterPoolState(
        pool_index=pool_index,
        status=info["status"],
        equity=_required_decimal(account, "total_asset_value"),
        total_shares=info["total_shares"],
        operator_shares=info["operator_shares"],
        min_operator_share_rate=_required_decimal(info, "min_operator_share_rate") / Decimal(100),
        operator_fee=_required_decimal(info, "operator_fee") / Decimal(100),
        available_balance=_required_decimal(account, "available_balance"),
        is_llp=pool_index == int(config["liquidity_pool_index"]),
        cooldown=datetime.timedelta(milliseconds=int(config["liquidity_pool_cooldown_period"])),
        observed_at=observed_at,
    )
    assert state.total_shares > 0 and 0 <= state.operator_shares <= state.total_shares
    assert 0 <= state.operator_fee <= 1 and 0 <= state.min_operator_share_rate <= 1
    return state


def fetch_pool_holdings(session: LighterSession, account_index: int) -> Iterator[LighterPoolHolding]:
    """Fetch each investor holding and its current gross and fee-adjusted values.

    Neither these values nor account equity are added together here: their
    inclusion relationship must be established by real mint/burn observations.

    :param session: Ethereum Lighter session.
    :param account_index: Investor account.
    :return: Iterator of current holdings; missing holdings data is an error.
    """
    account, _ = fetch_execution_account(session, account_index)
    for row in account["shares"]:
        state = fetch_pool_state(session, int(row["public_pool_index"]))
        yield _parse_pool_holding(row, state)


def _parse_pool_holding(row: dict[str, Any], pool: LighterPoolState) -> LighterPoolHolding:
    """Value one validated investor row using its own current pool observation.

    :param row: Raw investor share record, already validated with the account.
    :param pool: Matching current pool state.
    :return: Investor holding without reading unrelated pools.
    """
    shares = int(row["shares_amount"])
    principal = _required_decimal(row, "principal_amount")
    gross = shares * pool.share_value
    return LighterPoolHolding(pool.pool_index, shares, _required_decimal(row, "entry_usdc"), principal, datetime.datetime.fromtimestamp(int(row["entry_timestamp"]), datetime.UTC).replace(tzinfo=None), gross, max(gross - principal, Decimal(0)) * pool.operator_fee, pool.observed_at)


def prepare_pool_amount(
    pool: LighterPoolState,
    amount_usdc: Decimal,
    *,
    available_collateral: Decimal,
    holding: LighterPoolHolding | None = None,
    full_exit: bool = False,
    llp_deposit_limit: Decimal | None = None,
    max_age: datetime.timedelta = datetime.timedelta(seconds=30),
) -> int:
    """Size an integer mint or burn against current state and known restrictions.

    A holding selects redemption, even if the pool is closed to new deposits.
    For LLP entry, callers must obtain a current staking-derived deposit limit;
    an absent limit is unavailable, never unlimited. No automatic staking occurs.
    Monetary values are estimates: mint/burn signatures carry only share amounts.

    :param pool: Current pool observation.
    :param amount_usdc: Target monetary size, rounded down to raw shares.
    :param available_collateral: Investor available balance, for minting.
    :param holding: Existing holding for exits.
    :param full_exit: Burn the entire raw holding, avoiding rounding dust.
    :param llp_deposit_limit: Verified remaining LLP staking capacity, if entering LLP.
    :param max_age: Maximum observation age before sizing must be refreshed.
    :return: Positive raw protocol share amount.
    """
    now = native_datetime_utc_now()
    assert datetime.timedelta(0) <= now - pool.observed_at <= max_age, "Stale or future pool observation"
    assert amount_usdc.is_finite() and (amount_usdc > 0 or (full_exit and amount_usdc == 0))
    if full_exit:
        assert holding is not None
        shares = holding.shares
    else:
        assert pool.share_value > 0, "Pool has no positive share value for monetary sizing"
        shares = int((amount_usdc / pool.share_value).to_integral_value(rounding=ROUND_DOWN))
    if holding is not None:
        assert holding.pool_index == pool.pool_index and holding.shares > 0
        assert datetime.timedelta(0) <= now - holding.observed_at <= max_age, "Stale holding"
        assert shares <= holding.shares, "Insufficient investor shares"
        assert shares * pool.share_value <= pool.available_balance, "Pool has insufficient redeemable collateral"
        if pool.is_llp:
            assert now >= holding.entered_at + pool.cooldown, "LLP withdrawal cooldown has not elapsed"
    else:
        assert not full_exit and pool.status == 0, "Pool is closed to deposits"
        assert available_collateral.is_finite() and amount_usdc <= available_collateral, "Insufficient available collateral"
        if pool.min_operator_share_rate > 0:
            assert pool.operator_shares >= (pool.total_shares + shares) * pool.min_operator_share_rate, "Pool is full"
        if pool.is_llp:
            assert llp_deposit_limit is not None and llp_deposit_limit.is_finite(), "LLP staking capacity is unavailable"
            assert amount_usdc <= llp_deposit_limit, "LLP staking limit exceeded"
    assert 0 < shares <= MAX_POOL_SHARES, "Share quantity is zero or out of signer range"
    return shares


def fetch_next_nonce(session: LighterSession, identity: LighterAccountIdentity) -> int:
    """Fetch the next nonce for a single API-key pair, for initialisation only.

    This replay-safe read does not authorise repeating an uncertain operation.

    :param session: Ethereum API session.
    :param identity: Account and key slot.
    :return: Next explicit nonce. Never refresh it to retry uncertain submissions.
    """
    _assert_ethereum(session)
    response = session.get(f"{session.api_url}/api/v1/nextNonce", params={"account_index": identity.account_index, "api_key_index": identity.api_key_index}, timeout=30)
    response.raise_for_status()
    data = response.json()
    assert data["code"] == API_SUCCESS
    nonce = int(data["nonce"])
    assert nonce >= 0
    return nonce


def fetch_operation_status(session: LighterSession, operation: LighterOperation) -> dict[str, Any] | None:
    """Fetch a matching transaction receipt without resubmitting the operation.

    See https://apidocs.lighter.xyz/reference/tx. A not-yet-indexed hash remains
    uncertain; account and nonce mismatches are hard errors.

    :param session: Original Ethereum deployment session.
    :param operation: Saved signed transaction.
    :return: Verified transaction dict, or ``None`` while it is not indexed.
    """
    _assert_ethereum(session)
    assert operation.identity.api_url == session.api_url
    response = session.get(f"{session.api_url}/api/v1/tx", params={"by": "hash", "value": operation.tx_hash}, timeout=30)
    if response.status_code == 400 and response.json().get("code") == 21500:  # noqa: PLR2004
        return None
    response.raise_for_status()
    data = response.json()
    assert data["code"] == API_SUCCESS
    tx = data
    assert tx["hash"].removeprefix("0x").lower() == operation.tx_hash.removeprefix("0x").lower()
    assert int(tx["account_index"]) == operation.identity.account_index and int(tx["api_key_index"]) == operation.identity.api_key_index
    assert int(tx["nonce"]) == operation.nonce and int(tx["type"]) == operation.tx_type
    return tx


class LighterPoolExecutor:
    """Serialise one account/key's prepare, submit and reconciliation lifecycle.

    Use one instance and one submitting process per account/API-key pair.
    An uncertain operation blocks new preparations until explicitly reconciled.
    Persistence is injected so downstream callers can use their existing state.
    """

    def __init__(self, session: LighterSession, identity: LighterAccountIdentity, signer: LighterSyncSigner, persist: Callable[[LighterOperation], None]) -> None:
        """Bind a signer and mandatory durable recorder without submitting.

        :param session: Original Ethereum Lighter session.
        :param identity: Deployment and key identity.
        :param signer: Official signer's synchronous methods or a compatible adapter.
        :param persist: Must durably record the operation before returning.
        """
        _assert_ethereum(session)
        self.session, self.identity, self.signer, self.persist = session, identity, signer, persist
        self._lock = threading.RLock()
        self._pending: LighterOperation | None = None
        self._next_nonce: int | None = None

    def prepare(self, kind: str, amount: int, pool_index: int | None = None) -> LighterOperation:
        """Sign and durably record one operation before any submission.

        Mint/burn quantities must first pass ``prepare_pool_amount()``. Withdrawal
        quantities are raw USDC from ``TokenDetails.convert_to_raw()``.

        :param kind: ``mint_shares``, ``burn_shares`` or ``withdraw``.
        :param amount: Positive raw shares or raw USDC.
        :param pool_index: Required for share operations only.
        :return: Saved operation. A pending predecessor prevents preparation.
        """
        with self._lock:
            assert self._pending is None, "Reconcile the previous operation before signing another"
            assert type(amount) is int and 0 < amount < 2**63
            nonce = fetch_next_nonce(self.session, self.identity) if self._next_nonce is None else self._next_nonce
            kwargs = {"nonce": nonce, "api_key_index": self.identity.api_key_index}
            if kind == "mint_shares":
                assert pool_index is not None and amount <= MAX_POOL_SHARES
                result = self.signer.sign_mint_shares(pool_index, amount, **kwargs)
            elif kind == "burn_shares":
                assert pool_index is not None and amount <= MAX_POOL_SHARES
                result = self.signer.sign_burn_shares(pool_index, amount, **kwargs)
            elif kind == "withdraw":
                assert pool_index is None
                result = self.signer.sign_withdraw(LIGHTER_USDC_ASSET_ID, 0, amount, **kwargs)
            else:
                raise ValueError(f"Unsupported Lighter operation: {kind}")
            tx_type, tx_info, tx_hash, error = result
            if error is not None:
                message = "Official Lighter signer rejected the operation"
                raise ValueError(message)
            assert tx_info and tx_hash
            payload = json.loads(tx_info)
            assert int(payload["FromAccountIndex" if kind == "withdraw" else "AccountIndex"]) == self.identity.account_index
            assert int(payload["ApiKeyIndex"]) == self.identity.api_key_index and int(payload["Nonce"]) == nonce
            if pool_index is not None:
                assert int(payload["PublicPoolIndex"]) == pool_index and int(payload["ShareAmount"]) == amount
            else:
                assert int(payload["AssetIndex"]) == LIGHTER_USDC_ASSET_ID and int(payload["RouteType"]) == 0 and int(payload["Amount"]) == amount
            operation = LighterOperation(self.identity, kind, pool_index, amount, nonce, int(tx_type), tx_info, tx_hash)
            self._pending = operation
            self.persist(operation)
            return operation

    def submit(self, operation: LighterOperation) -> None:
        """Submit once; acceptance never marks execution as confirmed.

        Any uncertain transport outcome remains blocked for reconciliation.
        A fresh HTTP session with no retry adapter prevents automatic POST replay.

        :param operation: This executor's prepared and persisted transaction.
        """
        with self._lock:
            assert operation is self._pending and operation.stage == LighterOperationStage.prepared
            operation.stage = LighterOperationStage.unknown
            self.persist(operation)
            # A plain requests session prevents custom read retry adapters from replaying mutations.
            with requests.Session() as submission:
                response = submission.post(f"{self.session.api_url}/api/v1/sendTx", data={"tx_type": operation.tx_type, "tx_info": operation.tx_info}, timeout=30, allow_redirects=False)
            # The API uses HTTP 400 for explicit transaction rejections.
            # Other HTTP failures leave the outcome uncertain.
            if response.status_code != 400:  # noqa: PLR2004
                response.raise_for_status()
            assert response.status_code in {200, 400}, "Unexpected submission response; reconcile before retrying"
            data = response.json()
            assert isinstance(data, dict) and type(data.get("code")) is int, "Malformed submission response; outcome remains uncertain"
            if response.status_code == 400 and data["code"] == API_SUCCESS:  # noqa: PLR2004
                response.raise_for_status()
            if data.get("code") != API_SUCCESS:
                operation.stage = LighterOperationStage.rejected
                operation.receipt = data
                self.persist(operation)
                self._pending = None
                self._next_nonce = None
                message = "Lighter rejected the signed transaction; inspect the saved operation"
                raise ValueError(message)
            assert str(data["tx_hash"]).removeprefix("0x").lower() == operation.tx_hash.removeprefix("0x").lower(), "Accepted hash differs from signed hash"
            operation.stage = LighterOperationStage.submitted
            self.persist(operation)

    def reconcile(self, operation: LighterOperation, timeout: float = 300, poll_interval: float = 5) -> LighterOperation:
        """Reconcile an original hash after submission or process restart.

        Pending/unknown outcomes keep the account blocked. Status 2 with an
        execution timestamp is the supported success observation. Status 0 is
        rejection. Other codes never become success by default; unfamiliar
        statuses remain pending for manual investigation rather than replay.

        :param operation: Original saved operation, never an automatically re-signed replacement.
        :param timeout: Maximum time to await execution.
        :param poll_interval: Logged read-only polling interval.
        :return: Confirmed operation; rejection raises and timeout leaves it pending.
        """
        with self._lock:
            assert operation.identity == self.identity
            assert self._pending is None or self._pending.tx_hash == operation.tx_hash
            assert operation.stage not in {LighterOperationStage.rejected, LighterOperationStage.confirmed}, "Operation already terminal"
            self._pending = operation
            deadline = time.monotonic() + timeout
            while True:
                tx = fetch_operation_status(self.session, operation)
                if tx is not None:
                    operation.receipt = tx
                    status = int(tx["status"])
                    if status == 0:
                        operation.stage = LighterOperationStage.rejected
                        self.persist(operation)
                        self._pending, self._next_nonce = None, None
                        message = "Lighter transaction execution failed"
                        raise ValueError(message)
                    if status == 2 and int(tx["executed_at"]) > 0:  # noqa: PLR2004
                        operation.stage = LighterOperationStage.confirmed
                        self.persist(operation)
                        self._pending, self._next_nonce = None, operation.nonce + 1
                        return operation
                    if status not in {1, 2, 3}:
                        logger.warning("Unrecognised Lighter transaction status %s for %s; retaining pending operation", status, operation.tx_hash)
                if time.monotonic() >= deadline:
                    operation.stage = LighterOperationStage.unknown
                    self.persist(operation)
                    raise TimeoutError(f"Lighter transaction {operation.tx_hash} remains unconfirmed; reconcile before resubmission")
                logger.info("Waiting for Lighter %s transaction %s, nonce %d", operation.kind, operation.tx_hash, operation.nonce)
                time.sleep(poll_interval)


def fetch_withdrawal_history(session: LighterSession, account_index: int, auth_token: str, cursor: str | None = None) -> dict[str, Any]:
    """Fetch an authenticated page of secure-withdrawal history.

    Authentication refresh may wrap this replay-safe read. Match a history ID
    from the confirmed transaction event; amount/time matching is insufficient.
    See https://apidocs.lighter.xyz/reference/withdraw_history.

    :param session: Ethereum API session.
    :param account_index: Safe-owned account.
    :param auth_token: Short-lived official signer auth token, never logged.
    :param cursor: Next-page cursor from an earlier response.
    :return: Raw page with ``withdraws`` and pagination fields.
    """
    _assert_ethereum(session)
    response = session.get(f"{session.api_url}/api/v1/withdraw/history", params={"account_index": account_index, **({"cursor": cursor} if cursor else {})}, headers={"authorization": auth_token}, timeout=30)
    response.raise_for_status()
    data = response.json()
    assert data["code"] == API_SUCCESS and isinstance(data["withdraws"], list)
    return data


def fetch_withdrawals(session: LighterSession, account_index: int, auth_token: str) -> Iterator[dict[str, Any]]:
    """Iterate the complete withdrawal history, including older pending requests.

    Following every cursor prevents first-page snapshots from mistaking an old
    withdrawal for a newly submitted one. Repeated cursors fail explicitly.

    :param session: Ethereum session.
    :param account_index: Safe-owned account.
    :param auth_token: Short-lived token for replay-safe history reads.
    :return: Withdrawal rows in the provider's page order.
    """
    cursor = None
    seen = set()
    while True:
        data = fetch_withdrawal_history(session, account_index, auth_token, cursor)
        yield from data["withdraws"]
        cursor = data.get("cursor")
        if not cursor:
            return
        assert cursor not in seen, "Withdrawal history pagination loop"
        seen.add(cursor)


def fetch_matching_withdrawal(session: LighterSession, account_index: int, withdrawal_id: str, auth_token: str) -> dict[str, Any] | None:
    """Find one secure USDC withdrawal by exact history ID across pages.

    The caller identifies the ID from execution evidence; this function never
    substitutes a row with a similar amount or timestamp.

    :param session: Ethereum session.
    :param account_index: Safe-owned account.
    :param withdrawal_id: Identifier obtained from execution evidence.
    :param auth_token: Short-lived token for replay-safe history reads.
    :return: Exact secure USDC row, or ``None`` until visible.
    """
    row = next((row for row in fetch_withdrawals(session, account_index, auth_token) if str(row["id"]) == withdrawal_id), None)
    if row is not None:
        assert row["type"] == "secure" and int(row["asset_id"]) == LIGHTER_USDC_ASSET_ID
        _required_decimal(row, "amount")
    return row


def fetch_llp_deposit_limit(session: LighterSession, account_index: int, auth_token: str, current_llp_value: Decimal) -> Decimal:
    """Fetch the staking-derived remaining LLP allocation allowance.

    This currently interprets ``max_llp_amount`` as total capacity and subtracts
    the current gross holding. Validate that interpretation on the funded
    account before production use; no automatic staking is performed.
    See https://apidocs.lighter.xyz/reference/accountlimits.

    :param session: Ethereum session.
    :param account_index: Investor account.
    :param auth_token: Official signer auth token.
    :param current_llp_value: Current gross LLP holding already consuming capacity.
    :return: Remaining monetary capacity; no staking transaction is performed.
    """
    _assert_ethereum(session)
    response = session.get(f"{session.api_url}/api/v1/accountLimits", params={"account_index": account_index}, headers={"authorization": auth_token}, timeout=30)
    response.raise_for_status()
    data = response.json()
    assert data["code"] == API_SUCCESS
    limit = _required_decimal(data, "max_llp_amount")
    assert current_llp_value.is_finite() and current_llp_value >= 0 and limit >= 0
    return max(limit - current_llp_value, Decimal(0))


def wait_for_claimable_withdrawal(
    session: LighterSession,
    account_index: int,
    withdrawal_id: str,
    fetch_auth_token: Callable[[], str],
    *,
    timeout: float = 86400,
    poll_interval: float = 30,
) -> dict[str, Any]:
    """Wait for the exact secure-withdrawal row to become claimable on Ethereum.

    Use ``fetch_lighter_withdrawal_delay()`` for the current operator estimate.
    This bounded wait uses actual status and returns the actual claimable amount.

    :param session: Ethereum API session.
    :param account_index: Safe-owned account.
    :param withdrawal_id: Exact execution-derived withdrawal-history ID.
    :param fetch_auth_token: Refreshable read-only token provider.
    :param timeout: Overall bound, including protocol delay.
    :param poll_interval: Logged polling interval.
    :return: Matching claimable row; caller claims its ``amount`` through the guard.
    """
    deadline = time.monotonic() + timeout
    while True:
        row = fetch_matching_withdrawal(session, account_index, withdrawal_id, fetch_auth_token())
        if row is not None:
            if row["status"] == "claimable":
                assert _required_decimal(row, "amount") > 0
                return row
            assert row["status"] not in {"failed", "refunded", "completed"}, "Secure withdrawal failed, was refunded or already completed; reconcile the original request and L1 claim"
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Secure withdrawal {withdrawal_id} is not yet claimable; reconcile the existing request")
        logger.info("Waiting for secure withdrawal %s; status %s", withdrawal_id, row["status"] if row else "not indexed")
        time.sleep(poll_interval)


def reconcile_bootstrap(subscription: Decimal, safe_balances: tuple[Decimal, ...], lighter_collateral: Decimal, bridge_receivables: Decimal, actual_fees: Decimal, *, tolerance: Decimal = LIGHTER_COLLATERAL_TOLERANCE) -> None:
    """Check bootstrap conservation without enabling unverified aggregate pool NAV.

    Values must refer to the same observation boundary. A bridge receivable is
    removed when its destination receipt is counted. A missing value is an error.

    :param subscription: Accounted initial subscription in USDC.
    :param safe_balances: All participating Safes' settled native USDC.
    :param lighter_collateral: Confirmed account collateral, with no pool exposure.
    :param bridge_receivables: Net unsettled incoming USDC, counted only once.
    :param actual_fees: Incurred bridge fees.
    :param tolerance: Existing API decimal-rounding tolerance.
    """
    values = (*safe_balances, lighter_collateral, bridge_receivables, actual_fees, subscription)
    assert tolerance.is_finite() and tolerance >= 0
    assert all(isinstance(v, Decimal) and v.is_finite() and v >= 0 for v in values), "Bootstrap valuation is unavailable"
    observed = sum(safe_balances, Decimal(0)) + lighter_collateral + bridge_receivables + actual_fees
    assert abs(observed - subscription) <= tolerance, f"Bootstrap does not reconcile: subscription {subscription}, assets plus fees {observed}"


def prepare_pool_deposit(executor: LighterPoolExecutor, pool_index: int, amount_usdc: Decimal, auth_token: str | None = None) -> LighterOperation:
    """Read current constraints and prepare a dynamic public-pool investment.

    Fetch and validate collateral, capacity and any LLP allowance before signing.
    The signed share quantity cannot enforce a minimum or maximum USDC price.

    :param executor: Single account/key writer and durable operation recorder.
    :param pool_index: Dynamically selected Ethereum public pool.
    :param amount_usdc: Estimated USDC to allocate, rounded down in shares.
    :param auth_token: Required for authenticated LLP staking-capacity reads only.
    :return: Signed, durably saved mint operation, not yet submitted.
    """
    with executor._lock:
        account, equity = fetch_execution_account(executor.session, executor.identity.account_index)
        pool = fetch_pool_state(executor.session, pool_index)
        limit = None
        if pool.is_llp:
            assert auth_token, "LLP entry requires an authenticated capacity read"
            held = sum((int(row["shares_amount"]) * pool.share_value for row in account["shares"] if int(row["public_pool_index"]) == pool_index), Decimal(0))
            limit = fetch_llp_deposit_limit(executor.session, executor.identity.account_index, auth_token, held)
        shares = prepare_pool_amount(pool, amount_usdc, available_collateral=equity.available_balance, llp_deposit_limit=limit)
        return executor.prepare("mint_shares", shares, pool_index)


def prepare_pool_redemption(executor: LighterPoolExecutor, pool_index: int, amount_usdc: Decimal | None = None) -> LighterOperation:
    """Prepare a partial or complete exit, including from unselected/closed pools.

    Read only the held target pool so an unavailable unrelated pool cannot block
    this exit. Redemption is separate from a withdrawal to Ethereum.

    :param executor: Serial account/key executor.
    :param pool_index: Held pool to exit, independent of strategy selection.
    :param amount_usdc: Estimated partial redemption; ``None`` burns all shares.
    :return: Signed, saved burn operation with integer shares.
    """
    with executor._lock:
        account, _ = fetch_execution_account(executor.session, executor.identity.account_index)
        matches = [row for row in account["shares"] if row["public_pool_index"] == pool_index]
        assert len(matches) == 1, "No unique holding for the requested pool"
        pool = fetch_pool_state(executor.session, pool_index)
        holding = _parse_pool_holding(matches[0], pool)
        shares = prepare_pool_amount(pool, holding.gross_value if amount_usdc is None else amount_usdc, available_collateral=Decimal(0), holding=holding, full_exit=amount_usdc is None)
        return executor.prepare("burn_shares", shares, pool_index)


def wait_for_available_collateral(session: LighterSession, account_index: int, minimum: Decimal, timeout: float = 300) -> Decimal:
    """Wait for post-burn collateral to become actually available before withdrawal.

    Poll current account state with bounded, logged waits rather than assuming
    the estimated burn proceeds are immediately spendable.

    :param session: Ethereum API session.
    :param account_index: Investor account.
    :param minimum: Required available amount, allowing a caller's explicit fee estimate.
    :param timeout: Overall bound in seconds.
    :return: Actual available collateral in USDC.
    """
    deadline = time.monotonic() + timeout
    while True:
        _, equity = fetch_execution_account(session, account_index)
        if equity.available_balance >= minimum:
            return equity.available_balance
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Available collateral is {equity.available_balance}; need {minimum}")
        logger.info("Waiting for redeemed collateral on account %d: %s, need %s", account_index, equity.available_balance, minimum)
        time.sleep(5)


def prepare_secure_withdrawal(executor: LighterPoolExecutor, usdc: "TokenDetails", amount: Decimal) -> LighterOperation:
    """Sign a secure withdrawal of available collateral to the account's L1 owner.

    This prepares route-zero USDC withdrawal through the official signer. It
    does not claim funds or use the EOA-only fast-withdrawal route.

    :param executor: Single account/key writer.
    :param usdc: Canonical Ethereum USDC metadata.
    :param amount: Monetary amount, distinct from the eventual claimable amount.
    :return: Saved API withdrawal. Submission and L1 claim are separate operations.
    """
    with executor._lock:
        assert usdc.address.lower() == LIGHTER_USDC_ETHEREUM.lower()
        assert amount.is_finite() and amount >= LIGHTER_MIN_MAINNET_USDC
        _, equity = fetch_execution_account(executor.session, executor.identity.account_index)
        assert equity.available_balance >= amount, "Insufficient collateral for secure withdrawal"
        raw = usdc.convert_to_raw(amount)
        assert usdc.convert_to_decimals(raw) == amount, "Withdrawal exceeds USDC precision"
        return executor.prepare("withdraw", raw)


def wait_for_pool_shares(session: LighterSession, account_index: int, pool_index: int, expected_shares: int, timeout: float = 300) -> int:
    """Confirm the actual holdings change after a mint or burn execution.

    A submitted transaction alone is insufficient evidence of the expected
    balance change. Missing holdings data raises instead of implying zero.

    :param session: Ethereum session.
    :param account_index: Investor account.
    :param pool_index: Executed pool.
    :param expected_shares: Exact expected raw share balance, including full-exit zero.
    :param timeout: Overall polling bound in seconds.
    :return: Confirmed raw investor share quantity.
    """
    deadline = time.monotonic() + timeout
    while True:
        account, _ = fetch_execution_account(session, account_index)
        rows = [row for row in account["shares"] if int(row["public_pool_index"]) == pool_index]
        assert len(rows) <= 1, "Duplicate pool holding"
        observed = int(rows[0]["shares_amount"]) if rows else 0
        if observed == expected_shares:
            return observed
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Pool {pool_index} holdings are {observed}, expected {expected_shares}; reconcile the executed transaction")
        logger.info("Waiting for pool %d shares on account %d: %d, expected %d", pool_index, account_index, observed, expected_shares)
        time.sleep(5)
