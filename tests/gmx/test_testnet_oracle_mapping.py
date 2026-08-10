"""Testnet tokens must resolve to a mainnet oracle address.

Testnets have no oracle of their own, so
:class:`~eth_defi.gmx.core.oracle.OraclePrices` returns prices keyed by *mainnet*
token addresses even on a testnet chain. A testnet token therefore has to be
translated before the price lookup, or it simply is not in the dict.

``OrderArgumentParser`` used to keep a private copy of that translation table,
which had drifted from the canonical one: it omitted Arbitrum Sepolia's regular
USDC — so pricing collateral in it raised ``KeyError`` — and it mapped Sepolia CRV
to SOL's mainnet feed, which does not fail at all, it just prices the collateral
off the wrong asset.
"""

from __future__ import annotations

import pytest

from eth_defi.gmx.contracts import NETWORK_TOKENS, TESTNET_TO_MAINNET_ORACLE_TOKENS
from eth_defi.gmx.order.order_argument_parser import OrderArgumentParser

#: Arbitrum Sepolia GMX test tokens.
_SEPOLIA = NETWORK_TOKENS["arbitrum_sepolia"]

#: Mainnet feeds the testnet tokens must resolve onto.
_MAINNET_USDC = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
_MAINNET_WETH = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
_MAINNET_CRV = "0xe5f01aeAcc8288E9838A60016AB00d7b6675900b"

#: SOL on mainnet — what Sepolia CRV was previously, wrongly, mapped to.
_MAINNET_SOL = "0x2bcC6D6CdBbDC0a4071e48bb3B969b06B3330c07"


def _resolve(token_address: str) -> str:
    """Call the mapping helper without constructing a full parser."""
    return OrderArgumentParser._get_oracle_address_for_token(
        None,  # `self` is unused by this method
        token_address,
        "arbitrum_sepolia",
    )


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("USDC", _MAINNET_USDC),
        ("USDC.SG", _MAINNET_USDC),
        ("WETH", _MAINNET_WETH),
        ("ETH", _MAINNET_WETH),
        ("CRV", _MAINNET_CRV),
    ],
)
def test_sepolia_tokens_resolve_to_mainnet_feeds(symbol, expected):
    assert _resolve(_SEPOLIA[symbol]) == expected


def test_regular_usdc_is_mapped():
    """The gap that broke every order collateralised in regular USDC."""
    assert _SEPOLIA["USDC"] in TESTNET_TO_MAINNET_ORACLE_TOKENS
    assert _resolve(_SEPOLIA["USDC"]) != _SEPOLIA["USDC"], "unmapped — would KeyError on price lookup"


def test_crv_is_not_priced_as_sol():
    """The silent variant: a wrong mapping prices collateral off another asset."""
    assert _resolve(_SEPOLIA["CRV"]) == _MAINNET_CRV
    assert _resolve(_SEPOLIA["CRV"]) != _MAINNET_SOL


def test_every_sepolia_token_is_covered():
    """No GMX Sepolia token may be left without a feed."""
    unmapped = [symbol for symbol, address in _SEPOLIA.items() if address not in TESTNET_TO_MAINNET_ORACLE_TOKENS]
    assert unmapped == []


def test_mainnet_addresses_pass_through_untouched():
    """Only testnet chains translate; mainnet addresses are already oracle keys."""
    resolved = OrderArgumentParser._get_oracle_address_for_token(None, _MAINNET_USDC, "arbitrum")
    assert resolved == _MAINNET_USDC
