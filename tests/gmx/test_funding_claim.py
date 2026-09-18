"""Tests for GMX trader funding-fee claims."""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from web3 import Web3

from eth_defi.abi import get_abi_by_filename
from eth_defi.gmx.events import GMXEventData
from eth_defi.gmx.funding import (
    ClaimableFundingFee,
    build_claim_funding_fees_call,
    extract_claimed_funding_fees,
    fetch_claimable_funding_fees,
)
from eth_defi.provider.multi_provider import create_multi_provider_web3


MARKET = Web3.to_checksum_address("0x1000000000000000000000000000000000000001")
OTHER_MARKET = Web3.to_checksum_address("0x1000000000000000000000000000000000000002")
USDC = Web3.to_checksum_address("0x2000000000000000000000000000000000000001")
WETH = Web3.to_checksum_address("0x2000000000000000000000000000000000000002")
ACCOUNT = Web3.to_checksum_address("0x3000000000000000000000000000000000000001")
ROUTER = Web3.to_checksum_address("0x4000000000000000000000000000000000000001")
DATASTORE = Web3.to_checksum_address("0x5000000000000000000000000000000000000001")


def _make_exchange_router(web3: Web3):
    abi = get_abi_by_filename("gmx/ExchangeRouter.json")
    return web3.eth.contract(address=ROUTER, abi=abi)


def test_build_claim_funding_fees_call(monkeypatch):
    """The builder wraps parallel claim arrays in the guard-compatible multicall."""
    web3 = Web3()
    router = _make_exchange_router(web3)
    monkeypatch.setattr("eth_defi.gmx.funding.GMXConfig", lambda _web3: SimpleNamespace(chain="arbitrum"))
    monkeypatch.setattr("eth_defi.gmx.funding.get_exchange_router_contract", lambda _web3, _chain: router)

    bound_call = build_claim_funding_fees_call(
        web3,
        [
            ClaimableFundingFee(MARKET, USDC, 123),
            ClaimableFundingFee(OTHER_MARKET, USDC, 456),
        ],
        ACCOUNT,
    )

    assert bound_call.fn_name == "multicall"
    inner_calls = bound_call.args[0]
    assert len(inner_calls) == 1
    function, decoded = router.decode_function_input(inner_calls[0])
    assert function.fn_name == "claimFundingFees"
    assert decoded == {
        "markets": [MARKET, OTHER_MARKET],
        "tokens": [USDC, USDC],
        "receiver": ACCOUNT,
    }


def test_build_claim_funding_fees_call_rejects_empty_and_duplicates(monkeypatch):
    """Malformed claim batches are rejected before they can be signed."""
    web3 = Web3()
    router = _make_exchange_router(web3)
    monkeypatch.setattr("eth_defi.gmx.funding.GMXConfig", lambda _web3: SimpleNamespace(chain="arbitrum"))
    monkeypatch.setattr("eth_defi.gmx.funding.get_exchange_router_contract", lambda _web3, _chain: router)

    with pytest.raises(ValueError, match="empty"):
        build_claim_funding_fees_call(web3, [], ACCOUNT)

    claim = ClaimableFundingFee(MARKET, USDC, 123)
    with pytest.raises(ValueError, match="Duplicate"):
        build_claim_funding_fees_call(web3, [claim, claim], ACCOUNT)


def test_fetch_claimable_funding_fees_filters_and_deduplicates(monkeypatch):
    """Discovery returns non-zero reserve claims and does not repeat equal tokens."""
    web3 = MagicMock(spec=Web3)
    monkeypatch.setattr("eth_defi.gmx.funding.GMXConfig", lambda _web3: SimpleNamespace(chain="arbitrum"))
    monkeypatch.setattr(
        "eth_defi.gmx.funding.Markets",
        lambda _config: SimpleNamespace(
            get_available_markets=lambda: {
                MARKET: {
                    "long_token_address": USDC,
                    "short_token_address": USDC,
                },
                OTHER_MARKET: {
                    "long_token_address": WETH,
                    "short_token_address": USDC,
                },
            }
        ),
    )
    datastore = MagicMock()
    datastore.address = DATASTORE
    datastore.encode_abi.return_value = "0x1234"
    monkeypatch.setattr("eth_defi.gmx.funding.get_datastore_contract", lambda _web3, _chain: datastore)
    aggregate = MagicMock()
    aggregate.call.return_value = [(True, (123).to_bytes(32, "big")), (True, (0).to_bytes(32, "big"))]
    multicall = MagicMock()
    multicall.functions.aggregate3.return_value = aggregate
    monkeypatch.setattr("eth_defi.gmx.funding.get_multicall_contract", lambda _web3: multicall)

    claims = fetch_claimable_funding_fees(
        web3,
        ACCOUNT,
        token_addresses=[USDC],
        block_identifier=123456,
    )

    assert claims == [ClaimableFundingFee(MARKET, USDC, 123)]
    assert len(multicall.functions.aggregate3.call_args.args[0]) == 2
    aggregate.call.assert_called_once_with(block_identifier=123456)


def test_extract_claimed_funding_fees(monkeypatch):
    """Receipt parsing filters unrelated accounts and keeps exact raw amounts."""
    other_account = Web3.to_checksum_address("0x3000000000000000000000000000000000000002")
    events = [
        GMXEventData(
            event_name="FundingFeesClaimed",
            address_items={"market": MARKET, "token": USDC, "account": ACCOUNT, "receiver": ACCOUNT},
            uint_items={"amount": 123, "nextPoolValue": 456},
        ),
        GMXEventData(
            event_name="FundingFeesClaimed",
            address_items={"market": OTHER_MARKET, "token": USDC, "account": other_account, "receiver": ACCOUNT},
            uint_items={"amount": 999},
        ),
    ]
    monkeypatch.setattr("eth_defi.gmx.funding.find_events_by_name", lambda *_args: iter(events))

    claims = extract_claimed_funding_fees(Web3(), {"logs": []}, account=ACCOUNT, receiver=ACCOUNT)

    assert len(claims) == 1
    assert claims[0].amount == 123
    assert claims[0].next_pool_value == 456


@pytest.mark.skipif(
    not os.environ.get("JSON_RPC_ARBITRUM"),
    reason="requires JSON_RPC_ARBITRUM",
)
def test_fetch_claimable_funding_fees_arbitrum():
    """Read the gmx-ai Safe's historical USDC funding from the real DataStore."""
    web3 = create_multi_provider_web3(os.environ["JSON_RPC_ARBITRUM"])
    safe = Web3.to_checksum_address("0x7838A4E4ecD438c1BdD13b014675c7e877b8b490")
    usdc = Web3.to_checksum_address("0xaf88d065e77c8cC2239327C5EDb3A432268e5831")

    claims = fetch_claimable_funding_fees(
        web3,
        safe,
        token_addresses=[usdc],
        block_identifier=506_391_839,
    )

    assert len(claims) >= 40
    assert sum(claim.amount for claim in claims) == 33_523_756
