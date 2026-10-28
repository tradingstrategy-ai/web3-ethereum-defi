"""Current CCTP route fees and Fast Transfer capacity.

Uses Circle's `fee <https://developers.circle.com/api-reference/cctp/all/get-burn-usdc-fees>`_
and `allowance <https://developers.circle.com/api-reference/cctp/all/get-fast-burn-usdc-allowance>`_
endpoints. Quotes are estimates; ``maxFee`` remains the transaction's ceiling.
"""

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal

import requests

from eth_defi.cctp.constants import CHAIN_ID_TO_CCTP_DOMAIN, FINALITY_THRESHOLD_FAST, FINALITY_THRESHOLD_STANDARD, IRIS_API_BASE_URL
from eth_defi.token import TokenDetails


@dataclass(slots=True, frozen=True)
class CCTPFeeQuote:
    """Validated quote for one gross burn amount."""

    #: Requested source finality.
    finality: int
    #: Gross amount in raw source token units.
    amount_raw: int
    #: Fee rounded upwards in raw source token units.
    fee_raw: int
    #: Maximum authorised fee in raw source token units.
    max_fee_raw: int


def fetch_cctp_fee_quote(
    usdc: TokenDetails,
    source_chain_id: int,
    destination_chain_id: int,
    net_amount: Decimal,
    max_fee: Decimal = Decimal("0.01"),
    *,
    session: requests.Session | None = None,
) -> CCTPFeeQuote:
    """Validate a gross burn that can deliver at least the requested net amount.

    HyperEVM uses its already-fast standard route. Ethereum, Arbitrum and Base
    use Fast Transfer; unsupported bootstrap source chains fail explicitly.
    Fast allowance is returned by Circle in whole USDC, not raw token units.

    :param usdc: Source native USDC metadata, used for integer conversion.
    :param source_chain_id: Source EVM chain ID.
    :param destination_chain_id: Destination EVM chain ID.
    :param net_amount: Minimum USDC to deliver after fees.
    :param max_fee: Fee ceiling included in, not added after, the gross burn.
    :param session: Optional reusable HTTP client.
    :return: Current validated fee and burn parameters.
    """
    assert net_amount.is_finite() and net_amount > 0, "Net amount must be positive"
    assert max_fee.is_finite() and max_fee >= 0, "Fee allowance must be non-negative"
    assert source_chain_id in {1, 999, 8453, 42161}, "Unsupported activation source chain"
    finality = FINALITY_THRESHOLD_STANDARD if source_chain_id == 999 else FINALITY_THRESHOLD_FAST  # noqa: PLR2004
    source = CHAIN_ID_TO_CCTP_DOMAIN[source_chain_id]
    destination = CHAIN_ID_TO_CCTP_DOMAIN[destination_chain_id]
    amount_raw = usdc.convert_to_raw(net_amount + max_fee)
    max_fee_raw = usdc.convert_to_raw(max_fee)
    assert usdc.convert_to_decimals(amount_raw) == net_amount + max_fee, "Amount exceeds USDC precision"
    assert usdc.convert_to_decimals(max_fee_raw) == max_fee, "Fee exceeds USDC precision"
    client = session if session is not None else requests
    response = client.get(f"{IRIS_API_BASE_URL}/v2/burn/USDC/fees/{source}/{destination}", timeout=30)
    response.raise_for_status()
    quotes = [q for q in response.json() if q["finalityThreshold"] == finality]
    assert len(quotes) == 1, "No unique Circle quote for requested finality"
    bps = Decimal(str(quotes[0]["minimumFee"]))
    assert bps.is_finite() and bps >= 0, "Invalid Circle fee quote"
    fee_raw = int((Decimal(amount_raw) * bps / Decimal(10_000)).to_integral_value(rounding=ROUND_CEILING))
    assert fee_raw <= max_fee_raw, "Circle fee exceeds the configured fee ceiling"
    if finality == FINALITY_THRESHOLD_FAST:
        response = client.get(f"{IRIS_API_BASE_URL}/v2/fastBurn/USDC/allowance", timeout=30)
        response.raise_for_status()
        allowance = Decimal(str(response.json()["allowance"]))
        assert allowance.is_finite() and allowance >= net_amount + max_fee, "Circle Fast Transfer allowance is insufficient"
    return CCTPFeeQuote(finality, amount_raw, fee_raw, max_fee_raw)
