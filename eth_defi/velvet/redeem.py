"""Velvet deposit handling.

- Need to call proprietary centralised API to make a deposit
"""

from pprint import pformat
import logging

import requests
from eth_typing import HexAddress
from requests import HTTPError
from web3 import Web3

from eth_defi.velvet.config import VELVET_DEFAULT_API_URL


logger = logging.getLogger(__name__)


class VelvetRedemptionError(Exception):
    """Error reply from velvet txn API"""


def redeem_from_velvet_velvet(
    portfolio: HexAddress | str,
    from_address: HexAddress | str,
    withdraw_token_address: HexAddress | str,
    amount: int,
    chain_id: int,
    slippage: float,
    api_url=VELVET_DEFAULT_API_URL,
) -> dict:
    """Construct Velvet redemption payload.

    - See https://github.com/Velvet-Capital/3rd-party-integration/issues/2#issuecomment-2497119390
    """
    assert from_address.startswith("0x")
    assert portfolio.startswith("0x")
    assert withdraw_token_address.startswith("0x")
    assert type(amount) == int

    payload = {
        "withdrawAmount": str(amount),
        "withdrawToken": withdraw_token_address,
        "user": from_address,
        "withdrawType": "batch",
        "tokenType": "erc20",
        "portfolio": portfolio,
        "slippage": str(int(slippage * 10_000)),  # 100 = 1%
    }

    url = f"{api_url}/portfolio/withdraw"

    logger.info("Velvet withdraw to %s with params:\n%s", url, pformat(payload))

    resp = requests.post(url, json=payload)

    try:
        resp.raise_for_status()
    except HTTPError as e:
        raise VelvetRedemptionError(f"Velvet API error on {api_url}, code {resp.status_code}: {resp.text}. Params: {pformat(payload)}") from e

    tx_data = resp.json()

    if "error" in tx_data:
        raise VelvetRedemptionError(str(tx_data))

    tx_data["from"] = Web3.to_checksum_address(from_address)
    tx_data["chainId"] = chain_id
    return tx_data
