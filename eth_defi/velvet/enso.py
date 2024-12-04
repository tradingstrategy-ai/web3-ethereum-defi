"""Perform Enso intent-based swap on Velvet Capital vault.

- See https://www.enso.finance/
"""
import logging
from pprint import pformat

import requests
from eth_typing import HexAddress
from requests import HTTPError

from eth_defi.velvet.config import VELVET_DEFAULT_API_URL

logger = logging.getLogger(__name__)


class VelvetSwapError(Exception):
    """Error reply from velvet txn API"""


def swap_with_velvet_and_enso(
    chain_id: int,
    rebalance_address: HexAddress,
    owner_address: HexAddress,
    token_in: HexAddress,
    token_out: HexAddress,
    swap_amount: int,
    slippage: float,
    remaining_tokens: set[HexAddress],
    api_url: str = VELVET_DEFAULT_API_URL,
) -> dict:
    """Set up a Enzo + Velvet swap tx.

    :param rebalance_address:
        Vault's rebalancer address

    :param slippage:
        Max slippage expressed as 0...1 where 1 = 100%

    :return:
        Constructor transsaction payload.
    """

    assert type(slippage) == float, f"Got {type(slippage)} instead of float: {slippage}"
    assert 0 <= slippage <= 1
    assert token_in.startswith("0x"), f"Got {token_in} instead of hex string"
    assert token_out.startswith("0x"), f"Got {token_out} instead of hex string"
    assert rebalance_address.startswith('0x'), f"Got {rebalance_address} instead of hex string"
    assert owner_address.startswith('0x'), f"Got {owner_address} instead of hex string"
    assert len(remaining_tokens) >= 1, f"At least the vault reserve currency must be always left"
    assert type(swap_amount) == int, f"Got {type(swap_amount)} instead of int, swap amount must be the raw number of tokens"

    payload = {
        "rebalanceAddress": rebalance_address,
        "sellToken": token_in,
        "buyToken": token_out,
        "sellAmount": str(swap_amount),
        "slippage": str(int(slippage * 10_000)),  # 100 = 1%
        "remainingTokens": list(remaining_tokens),
        "owner": owner_address
    }

    # Log out everything, so we can post the data for others to debug
    logger.info("Velvet + Enso swap, slippage is %f:\n%s", slippage, pformat(payload))

    url = f"{api_url}/rebalance/txn"
    resp = requests.post(url, json=payload)

    try:
        resp.raise_for_status()
    except HTTPError as e:
        raise VelvetSwapError(f"Velvet API error on {api_url}, code {resp.status_code}: {resp.text}") from e

    data = resp.json()

    if "error" in data:
        raise VelvetSwapError(str(data))

    tx = {
        "to": data["to"],
        "data": data["data"],
        "gas": int(data["gasLimit"]),
        "gasPrice": int(data["gasPrice"]),
        "chainId": chain_id,
    }

    logger.info("Tx data is:\n%s", pformat(tx))
    return tx

