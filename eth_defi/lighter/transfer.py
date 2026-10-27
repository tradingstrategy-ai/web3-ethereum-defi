"""Lighter transfers between accounts owned by the same L1 address.

A Safe-owned Lighter master account funds its own sub-accounts with an L2
``Transfer`` transaction signed by the source account's API key. Transfers
between accounts of the same owner need no L1 signature, which a Safe could
not produce. Transfers to another owner are out of scope.

The fee is charged on top of the transferred amount and the signed fee must
match the sequencer's value exactly, so it is always read from the
authenticated ``transferFeeInfo`` endpoint. Observed on Ethereum mainnet on
2026-10-26: the authenticated endpoint quoted 0 USDC for same-owner transfers
(master to sub-account and between sub-accounts) and 1 USDC to a different
owner, and Lighter charged nothing for the same-owner transfers. The public,
unauthenticated endpoint quotes 1 USDC regardless of the destination, so do
not use it for same-owner transfers. Lighter's minimum transfer is 1 USDC.

These helpers need the optional official Lighter SDK; see
:py:func:`eth_defi.lighter.sdk.import_lighter_sdk`.

Authoritative Lighter documentation:

- Transfers and minimums: https://apidocs.lighter.xyz/docs/deposits-transfers-and-withdrawals
- Transfer fee: https://docs.lighter.xyz/api/info/transferfeeinfo.md
- Transfer history: https://docs.lighter.xyz/api/transactions/transfer_history.md
"""

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from eth_defi.lighter.api import LIGHTER_STATE_POLL_SECONDS
from eth_defi.lighter.constants import LIGHTER_ROUTE_PERP
from eth_defi.lighter.sdk import LighterAuthTokenManager, import_lighter_sdk, is_lighter_tx_accepted

logger = logging.getLogger(__name__)

#: Native USDC precision on Lighter.
LIGHTER_USDC_DECIMALS = 6

#: Transfers require an exactly 32-byte memo; an empty memo is all zeroes.
LIGHTER_EMPTY_TRANSFER_MEMO = "0x" + "00" * 32


class LighterTransferError(RuntimeError):
    """Raised when a Lighter account transfer is rejected or ambiguous."""


@dataclass(slots=True, frozen=True)
class LighterTransferRecord:
    """One executed transfer read from Lighter transfer history."""

    #: L2 transaction hash returned when the transfer was submitted.
    tx_hash: str

    #: Account debited by the transfer.
    from_account_index: int

    #: Account credited by the transfer.
    to_account_index: int

    #: Amount credited to the destination, in USDC.
    amount: Decimal

    #: Fee charged to the source on top of the amount, in USDC.
    fee: Decimal

    #: Unix timestamp reported by Lighter.
    timestamp: int


def convert_usdc_to_lighter_raw(amount: Decimal) -> int:
    """Convert a USDC amount to Lighter's integer base units.

    Lighter USDC has six decimals. Unlike rounding, this refuses amounts that
    a signed transaction cannot represent exactly.

    :param amount:
        Non-negative USDC amount with at most six decimals.
    :return:
        Amount in raw units.
    :raises ValueError:
        If the amount is negative or has sub-unit precision.
    """
    if amount < 0:
        raise ValueError(f"Lighter USDC amount must not be negative: {amount}")
    raw = amount.scaleb(LIGHTER_USDC_DECIMALS)
    if raw != raw.to_integral_value():
        raise ValueError(f"Lighter USDC amount has more than {LIGHTER_USDC_DECIMALS} decimals: {amount}")
    return int(raw)


def get_sdk_float_amount(amount: Decimal) -> float:
    """Return a float that the SDK converts back to the exact raw amount.

    The official SDK takes transfer amounts as floats and computes
    ``int(amount * 1e6)``, which truncates values such as ``1.005`` to the
    wrong raw unit. This returns the closest float, nudged upwards when
    needed, for which that conversion is exact.

    :param amount:
        USDC amount with at most six decimals.
    :return:
        Float amount safe to pass to SDK transfer methods.
    """
    raw = convert_usdc_to_lighter_raw(amount)
    scale = 10**LIGHTER_USDC_DECIMALS
    value = float(amount)
    for _attempt in range(16):
        if int(value * scale) == raw:
            return value
        value = math.nextafter(value, math.inf)
    raise ValueError(f"Cannot represent {amount} USDC exactly for the Lighter SDK")


def create_lighter_auth_token_manager(client: Any, api_key_index: int) -> LighterAuthTokenManager:
    """Create an auth-token manager for authenticated SDK reads.

    The fee quote and transfer history are authenticated reads, so they are
    retried once with a fresh token if Lighter rejects an expired one.
    Transfer submission is not wrapped.

    See https://apidocs.lighter.xyz/docs/authentication

    :param client:
        ``lighter.SignerClient`` bound to the account being read.
    :param api_key_index:
        API-key slot that signs the auth token.
    :return:
        Token manager refreshing tokens before expiry.
    """

    def create_auth_token() -> tuple[str | None, object | None]:
        return client.create_auth_token_with_expiry(api_key_index=api_key_index)

    return LighterAuthTokenManager(token_factory=create_auth_token)


async def fetch_lighter_transfer_fee(
    client: Any,
    auth_manager: LighterAuthTokenManager,
    *,
    to_account_index: int,
) -> Decimal:
    """Read the USDC fee Lighter charges for one transfer.

    Uses the authenticated ``transferFeeInfo`` endpoint, which quotes 0 USDC
    between accounts of the same owner. See the module docstring.

    :param client:
        ``lighter.SignerClient`` bound to the source account.
    :param auth_manager:
        Auth-token manager for the source account.
    :param to_account_index:
        Destination account.
    :return:
        Fee in USDC, charged on top of the transferred amount.
    """
    lighter = import_lighter_sdk()
    info_api = lighter.InfoApi(client.api_client)
    info = await auth_manager.call(
        lambda auth: info_api.transfer_fee_info(
            authorization=auth,
            account_index=client.account_index,
            to_account_index=to_account_index,
        ),
        operation_name="transfer fee",
    )
    return Decimal(int(info.transfer_fee_usdc)).scaleb(-LIGHTER_USDC_DECIMALS)


async def request_lighter_transfer(
    client: Any,
    *,
    to_account_index: int,
    amount: Decimal,
    fee: Decimal,
    api_key_index: int,
) -> str:
    """Submit one same-owner USDC transfer from the client's account.

    Signs an L2 ``Transfer`` between perpetuals balances with the SDK's
    ``transfer_same_master_account``. See
    https://apidocs.lighter.xyz/docs/deposits-transfers-and-withdrawals

    The submission is not idempotent: do not retry it blindly. Persist the
    returned hash first, then confirm it with :py:func:`wait_for_lighter_transfer`.

    :param client:
        ``lighter.SignerClient`` bound to the source account.
    :param to_account_index:
        Destination account owned by the same L1 address.
    :param amount:
        USDC amount credited to the destination. Lighter's minimum is 1 USDC.
    :param fee:
        Exact fee from :py:func:`fetch_lighter_transfer_fee`. A negative fee
        is rejected.
    :param api_key_index:
        API-key slot that signs the transfer.
    :return:
        L2 transaction hash.
    """
    if amount <= 0:
        raise ValueError(f"Lighter transfer amount must be positive: {amount}")
    fee_raw = convert_usdc_to_lighter_raw(fee)
    _tx_info, response, error = await client.transfer_same_master_account(
        to_account_index=to_account_index,
        asset_id=client.ASSET_ID_USDC,
        route_from=LIGHTER_ROUTE_PERP,
        route_to=LIGHTER_ROUTE_PERP,
        amount=get_sdk_float_amount(amount),
        fee=fee_raw,
        memo=LIGHTER_EMPTY_TRANSFER_MEMO,
        api_key_index=api_key_index,
    )
    if not is_lighter_tx_accepted(response, error):
        raise LighterTransferError("Lighter transfer was rejected")
    tx_hash = str(response.tx_hash)
    logger.info(
        "Lighter transfer accepted: %s USDC from account %d to %d, fee %s, tx %s",
        amount,
        client.account_index,
        to_account_index,
        fee,
        tx_hash,
    )
    return tx_hash


async def fetch_lighter_transfer_record(
    client: Any,
    auth_manager: LighterAuthTokenManager,
    *,
    account_index: int,
    tx_hash: str,
) -> LighterTransferRecord | None:
    """Find one transfer in an account's transfer history.

    Transfer history is the authoritative record of an executed transfer,
    including the fee actually charged. Balance changes are not used because
    other activity on the account can mask them.

    See https://docs.lighter.xyz/api/transactions/transfer_history.md

    :param client:
        ``lighter.SignerClient`` bound to ``account_index``.
    :param auth_manager:
        Auth-token manager for ``account_index``.
    :param account_index:
        Account whose history is read, normally the source.
    :param tx_hash:
        Transfer hash returned by :py:func:`request_lighter_transfer`.
    :return:
        The executed transfer, or ``None`` while it is not in history.
    """
    lighter = import_lighter_sdk()
    transaction_api = lighter.TransactionApi(client.api_client)
    history = await auth_manager.call(
        lambda auth: transaction_api.transfer_history(
            account_index=account_index,
            authorization=auth,
        ),
        operation_name="transfer history",
    )
    matches = [row for row in history.transfers if str(row.tx_hash).lower() == tx_hash.lower()]
    if len(matches) > 1:
        raise LighterTransferError(f"Lighter transfer history has {len(matches)} rows for {tx_hash}")
    if not matches:
        return None
    row = matches[0]
    return LighterTransferRecord(
        tx_hash=str(row.tx_hash),
        from_account_index=int(row.from_account_index),
        to_account_index=int(row.to_account_index),
        amount=Decimal(str(row.amount)),
        fee=Decimal(str(row.fee)),
        timestamp=int(row.timestamp),
    )


async def wait_for_lighter_transfer(
    client: Any,
    auth_manager: LighterAuthTokenManager,
    *,
    account_index: int,
    tx_hash: str,
    timeout: float,
) -> LighterTransferRecord:
    """Wait until a submitted transfer appears in transfer history.

    Polls :py:func:`fetch_lighter_transfer_record` every
    :py:data:`~eth_defi.lighter.api.LIGHTER_STATE_POLL_SECONDS` until the
    sequencer has executed the transfer.

    :param client:
        ``lighter.SignerClient`` bound to ``account_index``.
    :param auth_manager:
        Auth-token manager for ``account_index``.
    :param account_index:
        Account whose history is read, normally the source.
    :param tx_hash:
        Transfer hash returned by :py:func:`request_lighter_transfer`.
    :param timeout:
        Maximum seconds to wait.
    :return:
        The executed transfer.
    :raises TimeoutError:
        If the transfer is not visible in time. A timeout does not prove that
        no funds moved; reconcile from transfer history before retrying.
    """
    deadline = time.monotonic() + timeout
    while True:
        record = await fetch_lighter_transfer_record(client, auth_manager, account_index=account_index, tx_hash=tx_hash)
        if record is not None:
            return record
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"Lighter transfer {tx_hash} was not visible in transfer history within {timeout} seconds")
        logger.info("Waiting for Lighter transfer %s, %.0f seconds remaining", tx_hash, remaining)
        await asyncio.sleep(min(LIGHTER_STATE_POLL_SECONDS, remaining))
