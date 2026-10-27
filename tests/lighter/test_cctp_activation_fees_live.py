"""Read real Circle route quotes without sending any transactions."""

from decimal import Decimal
from unittest.mock import Mock

import pytest

from eth_defi.cctp.constants import FINALITY_THRESHOLD_FAST, FINALITY_THRESHOLD_STANDARD
from eth_defi.cctp.fees import fetch_cctp_fee_quote
from eth_defi.token import TokenDetails


@pytest.mark.parametrize(("source", "finality"), [(999, FINALITY_THRESHOLD_STANDARD), (8453, FINALITY_THRESHOLD_FAST)])
def test_circle_activation_fee_quote(source: int, finality: int) -> None:
    """Parse live HyperEVM standard and Base Fast Transfer quotes and allowance.

    Only token metadata is local; both Circle HTTP endpoints are real. This
    checks decoding and availability, not bridge execution or final fees.

    :param source: Mainnet source chain ID.
    :param finality: Expected source-specific finality policy.
    """
    usdc = TokenDetails(contract=Mock(), decimals=6)
    quote = fetch_cctp_fee_quote(usdc, source, 1, Decimal(1))
    assert quote.finality == finality
    assert quote.amount_raw == usdc.convert_to_raw(Decimal("1.01"))
    assert 0 <= quote.fee_raw <= quote.max_fee_raw
