"""CCTP V2 DepositForBurn event reading tests.

Tests the engine-agnostic :mod:`eth_defi.cctp.events` scanner:

- Pure decoding of a real production log into :class:`CCTPDepositForBurn`
  (no network needed).
- Live scans on Arbitrum around a known production burn, exercising both
  the HyperSync engine and the chunked ``eth_getLogs`` fallback.

Environment variables for the live tests:

- ``JSON_RPC_ARBITRUM``: Arbitrum RPC endpoint
- ``HYPERSYNC_API_KEY``: optional, exercises the HyperSync engine
"""

import os

import pytest
from web3 import Web3

from eth_defi.cctp.events import (
    DEPOSIT_FOR_BURN_EVENT_TOPIC0,
    CCTPDepositForBurn,
    _decode_deposit_for_burn,
    _fetch_events_get_logs,
    fetch_deposit_for_burn_events,
)
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")
HYPERSYNC_API_KEY = os.environ.get("HYPERSYNC_API_KEY")

#: A production multichain Lagoon vault Safe that performed CCTP burns on Arbitrum
PRODUCTION_SAFE = "0xF5313fa3cAC75D42FDbeD31e0F4263C5CA23DC1A"

#: A known production depositForBurn() transaction from PRODUCTION_SAFE
PRODUCTION_BURN_TX = "0x7a3c7ba8b770bb7db401e47cf916ceb7592a82335744ee0b4f6f838f7c1b2834"

#: Block of PRODUCTION_BURN_TX on Arbitrum
PRODUCTION_BURN_BLOCK = 472_123_720


def test_decode_deposit_for_burn():
    """Decode a real production DepositForBurn log into the slotted dataclass.

    1. Build a web3-style log dict using the raw topics/data captured from
       the production burn transaction on Arbitrum.
    2. Decode it and verify every field: token, depositor, amount, mint
       recipient, destination domain and finality threshold.
    """

    # 1. Raw log captured from PRODUCTION_BURN_TX
    log = {
        "topics": [
            DEPOSIT_FOR_BURN_EVENT_TOPIC0,
            # burnToken: Arbitrum native USDC
            "0x000000000000000000000000af88d065e77c8cc2239327c5edb3a432268e5831",
            # depositor: the Safe
            "0x000000000000000000000000f5313fa3cac75d42fdbed31e0f4263c5ca23dc1a",
            # minFinalityThreshold: 2000 (standard transfer)
            "0x00000000000000000000000000000000000000000000000000000000000007d0",
        ],
        "data": (
            "0x00000000000000000000000000000000000000000000000000000000004c4b40"
            "000000000000000000000000f5313fa3cac75d42fdbed31e0f4263c5ca23dc1a"
            "0000000000000000000000000000000000000000000000000000000000000006"
            "00000000000000000000000028b5a0e9c621a5badaa536219b3a228c8168cf5d"
            "0000000000000000000000000000000000000000000000000000000000000000"
            "0000000000000000000000000000000000000000000000000000000000000000"
            "00000000000000000000000000000000000000000000000000000000000000e0"
            "0000000000000000000000000000000000000000000000000000000000000000"
        ),
        "blockNumber": PRODUCTION_BURN_BLOCK,
        "transactionHash": PRODUCTION_BURN_TX,
        "logIndex": 5,
    }

    # 2. Decode and verify every field
    event = _decode_deposit_for_burn(42161, log)
    assert isinstance(event, CCTPDepositForBurn)
    assert event.chain_id == 42161
    assert event.block_number == PRODUCTION_BURN_BLOCK
    assert event.transaction_hash == PRODUCTION_BURN_TX
    assert event.log_index == 5
    assert event.burn_token == Web3.to_checksum_address("0xaf88d065e77c8cC2239327C5EDb3A432268e5831")
    assert event.depositor == Web3.to_checksum_address(PRODUCTION_SAFE)
    assert event.amount == 5_000_000
    assert event.mint_recipient == Web3.to_checksum_address(PRODUCTION_SAFE)
    assert event.destination_domain == 6  # Base
    assert event.max_fee == 0
    assert event.min_finality_threshold == 2000


@pytest.mark.skipif(not JSON_RPC_ARBITRUM, reason="JSON_RPC_ARBITRUM needed")
def test_fetch_deposit_for_burn_events_get_logs_fallback():
    """The eth_getLogs fallback engine finds a known production burn.

    1. Scan a narrow block window around the known production burn using
       the fallback engine directly.
    2. Verify the burn is found and decoded correctly.
    """
    web3 = create_multi_provider_web3(JSON_RPC_ARBITRUM)

    # 1. Narrow scan window around the known burn with the fallback engine
    from eth_defi.cctp.transfer import get_token_messenger_v2

    events = _fetch_events_get_logs(
        web3,
        42161,
        get_token_messenger_v2(web3).address,
        PRODUCTION_SAFE,
        PRODUCTION_BURN_BLOCK - 1_000,
        PRODUCTION_BURN_BLOCK + 1_000,
    )

    # 2. The production burn is found and decoded
    matches = [e for e in events if e.transaction_hash == PRODUCTION_BURN_TX]
    assert len(matches) == 1, f"Expected production burn in scan, got {events}"
    assert matches[0].amount == 5_000_000
    assert matches[0].destination_domain == 6


@pytest.mark.skipif(
    not (JSON_RPC_ARBITRUM and HYPERSYNC_API_KEY),
    reason="JSON_RPC_ARBITRUM and HYPERSYNC_API_KEY needed",
)
def test_fetch_deposit_for_burn_events_hypersync():
    """The HyperSync engine finds a known production burn over a wide range.

    1. Scan a wide block window (1M+ blocks, infeasible for plain
       eth_getLogs on capped providers) through the public entry point with
       a HyperSync API key, so the HyperSync engine is selected.
    2. Verify the burn is found and equals the fallback engine's decoding.
    """
    web3 = create_multi_provider_web3(JSON_RPC_ARBITRUM)

    # 1. Wide scan through the engine-selecting entry point
    events = fetch_deposit_for_burn_events(
        web3,
        depositor=PRODUCTION_SAFE,
        start_block=PRODUCTION_BURN_BLOCK - 1_000_000,
        end_block=PRODUCTION_BURN_BLOCK + 1_000,
        hypersync_api_key=HYPERSYNC_API_KEY,
    )

    # 2. The production burn is found with correct fields
    matches = [e for e in events if e.transaction_hash == PRODUCTION_BURN_TX]
    assert len(matches) == 1, f"Expected production burn in scan, got {len(events)} events"
    assert matches[0].amount == 5_000_000
    assert matches[0].destination_domain == 6
    assert matches[0].depositor == Web3.to_checksum_address(PRODUCTION_SAFE)
