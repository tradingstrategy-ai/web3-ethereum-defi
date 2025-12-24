"""CoW swap order status monitoring."""

import datetime
import time
from dataclasses import dataclass
import logging
from typing import TypedDict, Literal

import requests
from hexbytes import HexBytes

from eth_defi.compat import native_datetime_utc_now
from eth_defi.cow.order import GPv2OrderData
from eth_defi.cow.api import CowAPIError, get_cowswap_api


logger = logging.getLogger(__name__)


#: What status words a CowSwap order have?
#:
#: See CompletionOrderStatus in https://docs.cow.fi/cow-protocol/reference/apis/orderbook
#:
#: [ open, scheduled, active, solved, executing, traded, cancelled ]
CowSwapOrderStatus = Literal["scheduled", "open", "active", "solved", "executing", "traded", "cancelled"]


@dataclass(slots=True, frozen=True)
class CowSwapResult:
    """A full result of a CowSwap order posting and status."""

    #: Our order UID.
    order_uid: HexBytes

    #: Order data we submitted for the swap.
    order: GPv2OrderData

    #: The final JSON data result of the status endpoint after we switched away from open status.
    #:
    #: See https://docs.cow.fi/cow-protocol/reference/apis/orderbook
    final_status_reply: dict

    def get_status(self) -> CowSwapOrderStatus:
        """Get final order status."""
        return self.final_status_reply["type"]


class CowSwapStatusReply(TypedDict):
    type: CowSwapOrderStatus


def wait_order_complete(
    chain_id: int,
    uid: str,
    trade_timeout: datetime.timedelta = datetime.timedelta(minutes=10),
    api_timeout: datetime.timedelta = datetime.timedelta(seconds=60),
    poll_sleep: float = 10.0,
) -> dict:
    """Wait for CowSwap order to complete by polling status endpoint."""

    assert type(uid) == str, f"Expected str uid, got {type(uid)}"
    base_url = get_cowswap_api(chain_id)
    final_url = f"{base_url}/api/v1/orders/{uid}/status"

    #
    # {
    #   "type": "open",
    #   "value": [
    #     {
    #       "solver": "string",
    #       "executedAmounts": {
    #         "sell": "1234567890",
    #         "buy": "1234567890"
    #       }
    #     }
    #   ]
    # }

    started = native_datetime_utc_now()

    deadline = native_datetime_utc_now() + trade_timeout
    logger.info("Fetching order data %s, timeout is %s", final_url, trade_timeout)
    cycle = 0
    while native_datetime_utc_now() < deadline:
        response = requests.get(
            final_url,
            timeout=api_timeout.total_seconds(),
        )

        try:
            response.raise_for_status()
        except requests.HTTPError as e:
            error_message = response.text
            logger.error(f"Error fetching CowSwap order status: {error_message}")
            raise CowAPIError(f"Error fetching CowSwap order status: {response.status_code} {error_message}") from e

        duration = native_datetime_utc_now() - started

        data = response.json()

        type_ = data["type"]
        if type_ in ("cancelled", "traded"):
            logger.info(f"CowSwap order {uid} completed with status {type_} in {duration}")
            return data

        cycle += 1
        logger.info(
            "Waiting for CowSwap to complete cycle %d, order %s is %s,, passed UID is %s, sleeping %s...",
            cycle,
            type_,
            duration,
            uid,
            poll_sleep,
        )
        time.sleep(poll_sleep)

    raise CowAPIError(f"Timeout waiting for CowSwap order {uid} to complete after {trade_timeout}, final status was {type_}")
