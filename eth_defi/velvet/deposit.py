"""Velvet deposit handling.

- Need to call proprietary centralised API to make a deposit
"""

from pprint import pformat
import logging

import requests
from eth_typing import HexAddress
from requests import HTTPError
from web3 import Web3

from eth_defi.velvet.config import VELVET_DEFAULT_API_URL, VELVET_GAS_EXTRA_SAFETY_MARGIN

logger = logging.getLogger(__name__)


class VelvetDepositError(Exception):
    """Error reply from velvet txn API"""


def deposit_to_velvet(
    portfolio: HexAddress | str,
    from_address: HexAddress | str,
    deposit_token_address: HexAddress | str,
    amount: int,
    chain_id: int,
    slippage: float,
    api_url=VELVET_DEFAULT_API_URL,
    gas_safety_margin: int = VELVET_GAS_EXTRA_SAFETY_MARGIN,
) -> dict:
    """Construct Velvet deposit payload.

    -

    - See https://github.com/Velvet-Capital/3rd-party-integration/issues/2#issuecomment-2490845963 for details
    """
    assert portfolio.startswith("0x")
    assert from_address.startswith("0x")
    assert deposit_token_address.startswith("0x")
    assert type(amount) == int
    # payload = {
    #     "portfolio": "0x444ef5b66f3dc7f3d36fe607f84fcb2f3a666902",
    #     "depositAmount": 1,
    #     "depositToken": "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb",
    #     "user": "0x3C96e2Fc58332746fbBAB5eC44f01572F99033ed",
    #     "depositType": "batch",
    #     "tokenType": "erc20"
    # }

    payload = {
        "portfolio": portfolio,
        "depositAmount": str(amount),
        "depositToken": deposit_token_address,
        "user": from_address,
        "depositType": "batch",
        "tokenType": "erc20",
        "slippage": str(int(slippage * 10_000)),  # 100 = 1%
    }

    url = f"{api_url}/portfolio/deposit"

    logger.info("Velvet deposit to %s with params:\n%s", url, pformat(payload))

    resp = requests.post(url, json=payload)

    try:
        resp.raise_for_status()
    except HTTPError as e:
        raise VelvetDepositError(f"Velvet API error on {api_url}, code {resp.status_code}: {resp.text}") from e

    tx_data = resp.json()

    if "error" in tx_data:
        raise VelvetDepositError(str(tx_data))

    tx_data["from"] = Web3.to_checksum_address(from_address)
    tx_data["chainId"] = chain_id
    tx_data["gas"] = int(tx_data["gasLimit"]) + gas_safety_margin
    return tx_data
