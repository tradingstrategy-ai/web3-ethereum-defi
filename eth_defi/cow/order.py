"""Order data structures for CoW Swap.

How the order UID is computed:

.. code-block:: solidity

    function packOrderUidParams(
        bytes memory orderUid,
        bytes32 orderDigest,
        address owner,
        uint32 validTo
    ) internal pure {
        require(orderUid.length == UID_LENGTH, "GPv2: uid buffer overflow");

        // NOTE: Write the order UID to the allocated memory buffer. The order
        // parameters are written to memory in **reverse order** as memory
        // operations write 32-bytes at a time and we want to use a packed
        // encoding. This means, for example, that after writing the value of
        // `owner` to bytes `20:52`, writing the `orderDigest` to bytes `0:32`
        // will **overwrite** bytes `20:32`. This is desirable as addresses are
        // only 20 bytes and `20:32` should be `0`s:
        //
        //        |           1111111111222222222233333333334444444444555555
        //   byte | 01234567890123456789012345678901234567890123456789012345
        // -------+---------------------------------------------------------
        //  field | [.........orderDigest..........][......owner.......][vT]
        // -------+---------------------------------------------------------
        // mstore |                         [000000000000000000000000000.vT]
        //        |                     [00000000000.......owner.......]
        //        | [.........orderDigest..........]
        //
        // Additionally, since Solidity `bytes memory` are length prefixed,
        // 32 needs to be added to all the offsets.
        //
        // solhint-disable-next-line no-inline-assembly
        assembly {
            mstore(add(orderUid, 56), validTo)
            mstore(add(orderUid, 52), owner)
            mstore(add(orderUid, 32), orderDigest)
        }
    }
"""

import datetime
import logging
from dataclasses import dataclass
from enum import IntEnum
from pprint import pformat
from typing import TypedDict

import requests

from eth_defi.cow.api import get_cowswap_api, CowAPIError
from eth_defi.cow.constants import CHAIN_TO_EXPLORER

logger = logging.getLogger(__name__)


class GPv2OrderData(TypedDict):
    """See GPv2Order.Data struct in CowSwap contracts.

    Automatically decoded by Web3.py ABI machinery.
    """

    sell_token: str
    buy_token: str
    receiver: str
    sell_amount: int
    buy_amount: int
    valid_to: int
    app_data: bytes
    fee_amount: int
    kind: bytes
    partially_fillable: bool
    sell_token_balance: bytes
    buy_token_balance: bytes

    #: Presigned order trnasaction hash
    tx_hash: str | None

    #: Order UID (hash)
    uid: str | None

    chain_id: int | None


class SigningScheme(IntEnum):
    # The EIP-712 typed data signing scheme. This is the preferred scheme as it
    # provides more infomation to wallets performing the signature on the data
    # being signed.
    #
    # https://github.com/ethereum/EIPs/blob/master/EIPS/eip-712.md#definition-of-domainseparator
    EIP712 = 0b00
    # Message signed using eth_sign RPC call.
    ETHSIGN = 0b01
    # Smart contract signatures as defined in EIP-1271.
    EIP1271 = 0b10
    # Pre-signed order.
    PRESIGN = 0b11


@dataclass(slots=True, frozen=True)
class PostOrderResponse:
    """Reply for opening an order at CowSwap API"""

    #: What CowSwap backend thinks should be the order UID
    order_uid: str

    #: Order data we posted to CowsSwap API
    order_data: GPv2OrderData

    def get_order_uid(self) -> str:
        """Get the order UID from the response data"""
        return self.order_data["uid"]

    def get_explorer_link(self) -> str:
        """Get CowSwap explorer link for the order."""
        base_url = CHAIN_TO_EXPLORER.get(self).order_data["chain_id"]
        return f"{base_url}/orders/{self.order_uid}"


def post_order(
    chain_id: int,
    order: GPv2OrderData,
    api_timeout: datetime.timedelta = datetime.timedelta(minutes=10),
) -> PostOrderResponse:
    """Decode CowSwap order from event log and post to CowSwap API

    - See OrderCreation structure at https://docs.cow.fi/cow-protocol/reference/apis/orderbook
    - You can debug orders in `CowSwap explorer <https://explorer.cow.fi/>`__ -
      remember to choose the correct chain

    Example error:

    .. code-block:: none

        eth_defi.cow.api.CowAPIError: Error posting CowSwap order: 404 {"errorType":"NoLiquidity","description":"no route found"}

    :raises CowAPIError:
        In the case API gives non-200 response.

    """

    base_url = get_cowswap_api(chain_id)
    final_url = f"{base_url}/api/v1/orders"

    # Javascript cannot handle certain types, so
    # we transform them for CoW REST API.
    crap_json = order.copy()
    crap_json["buyAmount"] = str(crap_json["buyAmount"])
    crap_json["sellAmount"] = str(crap_json["sellAmount"])
    crap_json["feeAmount"] = str(crap_json["feeAmount"])
    # https://docs.cow.fi/cow-protocol/reference/core/signing-schemes#presign
    # https://github.com/cowdao-grants/cow-py/blob/fd055fd647f56cf92ad0917c08b108a41d2a7e6c/cowdao_cowpy/cow/swap.py#L140
    crap_json["signature"] = "0x"
    crap_json["signingScheme"] = SigningScheme.PRESIGN.name.lower()

    # TODO: appData not supported

    logger.info(f"Posting CowSwap order to {final_url}: %s", pformat(crap_json))

    response = requests.post(
        final_url,
        json=crap_json,
        timeout=api_timeout.total_seconds(),
    )

    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        error_message = response.text
        logger.error(f"Error posting CowSwap order: {error_message}")
        raise CowAPIError(f"Error posting CowSwap order: {response.status_code} {error_message}\nData was:{pformat(crap_json)}\nEndpoint: {final_url}") from e

    posted_order_uid = response.json()

    logger.info("Received posted order UID from Cow backend: %s", posted_order_uid)

    if order.get("uid") is not None:
        # Cow Swap backend and SwapCowSwap compute the signed order UID differently.
        # Cow Swap will never see the onchain presigned order and the trade cannot ever complete.
        assert str(posted_order_uid) == str(order["uid"]), f"Posted order UID {posted_order_uid} does not match local order UID {order['uid']} for data:\n{pformat(order)}"

    return PostOrderResponse(
        order_uid=posted_order_uid,
        order_data=order,
    )
