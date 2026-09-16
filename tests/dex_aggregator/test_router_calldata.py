"""Tests for DEX aggregator router identification and calldata decoding."""

from eth_defi.dex_aggregator.router_calldata import decode_router_call, identify_call_path
from eth_defi.testing.dex_aggregator_samples import ROUTER_CALLDATA_SAMPLES


def test_decode_router_calls():
    """Decode the user's order from real aggregator router calldata.

    Tests that OKX, 1inch v5 and the unnamed ``Aggregator`` router calldata
    captured from Base transactions yield the user's minimum output and
    order tokens, which the propAMM execution-quality analysis needs.

    1. Decode each sample's calldata against its router
    2. Check aggregator slug, function name, tokens, input amount and slippage bound
    3. Check the same order is recovered through a call path wrapped in an ERC-4337 entry point
    """
    # 1. Decode each sample's calldata against its router
    for slug, sample in ROUTER_CALLDATA_SAMPLES.items():
        order = decode_router_call(sample["router"], sample["calldata"], frame_depth=0)
        assert order is not None, slug

        # 2. Check aggregator slug, function name, tokens, input amount and slippage bound
        assert order.aggregator == slug
        assert order.function == sample["function"]
        assert order.src_token == sample["src_token"]
        assert order.dst_token == sample["dst_token"]
        assert order.amount_in == sample["amount_in"]
        assert order.min_amount_out == sample["min_amount_out"]
        assert order.exact_output is False

    # 3. Check the same order is recovered through a call path wrapped in an ERC-4337 entry point
    sample = ROUTER_CALLDATA_SAMPLES["okx"]
    frames = [
        ("0x5ff137d4b0fdcd49dca30c7cf57e578a026d2789", "0x1fad948c", None),
        ("0x000100abaad02f1cfc8bbe32bd5a564817339e72", "0x34fcd5be", None),
        (sample["router"], sample["calldata"], None),
        ("0x55555522005BcAE1c2424D474BfD5ed477749E3e", "0x3ae8b298", None),
    ]
    ident = identify_call_path(frames, tx_from="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", tx_to=frames[0][0])
    assert ident.aggregator == "okx"
    assert ident.wallet_kind == "erc4337"
    assert ident.labels == ["erc4337-entrypoint-v0.6", "coinbase-smart-wallet", "okx"]
    assert ident.order is not None
    assert ident.order.min_amount_out == sample["min_amount_out"]
    assert ident.order.frame_depth == 2


def test_unknown_router_and_bot_path():
    """Unknown routers decode to nothing and bot paths are labelled as bots.

    1. Decode calldata against an address that is not a known router
    2. Identify a path through a known bot contract and an EIP-7702 self call
    """
    # 1. Decode calldata against an address that is not a known router
    assert decode_router_call("0x0000000000000000000000000000000000000001", "0x12345678" + "00" * 64) is None

    # 2. Identify a path through a known bot contract and an EIP-7702 self call
    bot = "0xaf3cefe9fbfb4962ef010d4b880a31297ec8260a"
    ident = identify_call_path([(bot, "0xb8b66f5b"), ("0xbbbbbbbbbb9cc5e90e3b3af64bdaf62c37eeffcb", "0xe0232b42")], tx_from=bot, tx_to=bot)
    assert ident.order is None
    assert ident.aggregator == "bot"
    assert ident.wallet_kind == "eip7702-self"
    assert ident.frontend == "bot-af3c"
    assert ident.labels == ["bot-af3c", "flashloan-helper"]


def test_truncated_router_calldata_does_not_raise():
    """A truncated trace for a known router selector is skipped, not a crash.

    Some RPC providers cap ``debug_traceTransaction`` response size for large
    calldata: the 4-byte selector on a frame still matches a known router, but
    the remaining bytes are short for that function's ABI shape. web3.py then
    raises ``eth_abi.exceptions.InsufficientDataBytes`` rather than the
    ``ValueError`` decode_router_call originally only caught -- this crashed
    a live 12-hour scan (2026-09-16) via an uncaught joblib worker exception.

    1. Decode a real OKX selector with calldata truncated to a few bytes
    2. Check it returns None instead of raising
    """
    sample = ROUTER_CALLDATA_SAMPLES["okx"]

    # 1. Decode a real OKX selector with calldata truncated to a few bytes
    truncated = sample["calldata"][:30]

    # 2. Check it returns None instead of raising
    assert decode_router_call(sample["router"], truncated) is None
