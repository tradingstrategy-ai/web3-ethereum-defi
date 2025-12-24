"""Perform Enso intent-based swap on Velvet Capital vault.

- See https://www.enso.finance/
"""

import logging
from pprint import pformat

import requests
from eth_typing import HexAddress
from requests import HTTPError
from requests.exceptions import RetryError
from requests.sessions import HTTPAdapter

from eth_defi.velvet.config import VELVET_DEFAULT_API_URL, VELVET_GAS_EXTRA_SAFETY_MARGIN
from eth_defi.velvet.logging_retry import LoggingRetry

logger = logging.getLogger(__name__)


class VelvetSwapError(Exception):
    """Error reply from velvet txn API"""


# swap_with_velvet_and_enso
def swap_with_velvet_intent(
    chain_id: int,
    portfolio_address: HexAddress,
    owner_address: HexAddress,
    token_in: HexAddress,
    token_out: HexAddress,
    swap_amount: int,
    slippage: float,
    remaining_tokens: set[HexAddress],
    api_url: str = VELVET_DEFAULT_API_URL,
    gas_safety_margin: int = VELVET_GAS_EXTRA_SAFETY_MARGIN,
    retries=5,
) -> dict:
    """Set up a Enzo + Velvet swap tx.

    :param portfolio_address:
        Vault's rebalancer address

    :param slippage:
        Max slippage expressed as 0...1 where 1 = 100%

    :param gas_safety_margin:
        Gas estimation fails

    :return:
        Constructor transsaction payload.
    """

    assert type(slippage) == float, f"Got {type(slippage)} instead of float: {slippage}"
    assert 0 <= slippage <= 1
    assert token_in.startswith("0x"), f"Got {token_in} instead of hex string"
    assert token_out.startswith("0x"), f"Got {token_out} instead of hex string"
    assert portfolio_address.startswith("0x"), f"Got {portfolio_address} instead of hex string"
    assert owner_address.startswith("0x"), f"Got {owner_address} instead of hex string"
    assert len(remaining_tokens) >= 1, f"At least the vault reserve currency must be always left"
    assert type(swap_amount) == int, f"Got {type(swap_amount)} instead of int, swap amount must be the raw number of tokens"

    session = requests.Session()

    if retries > 0:
        retry_policy = LoggingRetry(
            total=retries,
            backoff_factor=0.1,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["POST"],  # Need to whitelist POST
        )
        session.mount("https://", HTTPAdapter(max_retries=retry_policy))

    payload = {
        "portfolio": portfolio_address,
        "sellToken": token_in,
        "buyToken": token_out,
        "sellAmount": str(swap_amount),
        "slippage": str(int(slippage * 10_000)),  # 100 = 1%
        "remainingTokens": list(remaining_tokens),
        "owner": owner_address,
        "chainID": chain_id,
    }

    # Log out everything, so we can post the data for others to debug
    logger.info("Velvet + Enso swap, slippage is %f:\n%s", slippage, pformat(payload))

    url = f"{api_url}/portfolio/trade"

    try:
        try:
            resp = session.post(url, json=payload)
            resp.raise_for_status()
        except RetryError as e:
            # Run out of retries
            # Don't let RetryError mask the real err0r, send one more time to get good exception
            logger.warning("Run out of retries")
            resp = requests.post(url, json=payload)
            resp.raise_for_status()
    except HTTPError as e:
        raise VelvetSwapError(f"Velvet API error on {api_url}, code {resp.status_code}: {resp.text}\nParameters were:\n{pformat(payload)}") from e

    data = resp.json()

    if "error" in data:
        raise VelvetSwapError(str(data))

    tx = {
        "to": data["to"],
        "data": data["data"],
        "gas": int(data["gasLimit"]) + gas_safety_margin,
        "gasPrice": int(data["gasPrice"]),
        "chainId": chain_id,
    }

    logger.info("Tx data is:\n%s", pformat(tx))
    return tx


swap_with_velvet_and_enso = swap_with_velvet_intent
