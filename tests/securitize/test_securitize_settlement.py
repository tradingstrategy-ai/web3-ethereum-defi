"""Test ARKVX NAV reconstruction from Securitize subscription settlements."""

import datetime
import logging
import os
from decimal import Decimal
from types import SimpleNamespace

import pytest
from eth_abi import encode
from web3 import Web3

from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.tokenised_fund.securitize import settlement as settlement_module
from eth_defi.tokenised_fund.securitize.backfill import has_historical_price
from eth_defi.tokenised_fund.securitize.description import ARKVX_ETHEREUM, ARKVX_PROSPECTUS_URL, ARKVX_REPURCHASE_OFFER_URL, ARKVX_SEC_ORDER_URL
from eth_defi.tokenised_fund.securitize.settlement import (
    DEPOSIT_GENERATION_FULFILLED_TOPIC,
    REDEMPTION_GENERATION_FULFILLED_TOPIC,
    SECURITIZE_SETTLEMENT_FEEDS,
    SecuritizeSettlementFeed,
    SecuritizeSettlementPrice,
    calculate_settlement_share_price,
    decode_settlement_prices,
    fetch_settlement_prices,
    find_settlement_price_at,
)
from eth_defi.tokenised_fund.securitize.vault import SecuritizeVault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import VaultFeeMode
from eth_defi.vault.flow_events import IndexedVaultFlowLog
from eth_defi.vault.price_source import PriceSource
from eth_defi.vault.risk import BROKEN_VAULT_CONTRACTS

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
HYPERSYNC_API_KEY = os.environ.get("HYPERSYNC_API_KEY")

ARKVX_FEED = SECURITIZE_SETTLEMENT_FEEDS[ARKVX_ETHEREUM.chain_id, ARKVX_ETHEREUM.token]

#: Fixed Ethereum block after the generation 4 settlement on 2026-09-24.
ARKVX_TEST_BLOCK = 26_050_000

#: ``(generation id, fulfilment block, raw navPrice, ARK published NAV of the prior business day)``.
ARKVX_SETTLEMENTS = (
    (1, 26_005_046, 61_743_053_906_400_000_000, Decimal("60.51")),
    (2, 26_005_139, 61_746_617_466_100_000_000, Decimal("60.51")),
    (3, 26_033_419, 62_144_886_363_600_000_000, Decimal("60.90")),
    (4, 26_047_916, 61_722_430_245_100_000_000, Decimal("60.49")),
)

#: Minimal read surface of the verified ``AsyncFundVault`` implementation.
ASYNC_FUND_VAULT_ABI = [
    {
        "name": "getDepositGeneration",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "generationId", "type": "uint256"}],
        "outputs": [
            {
                "name": "data",
                "type": "tuple",
                "components": [
                    {"name": "status", "type": "uint8"},
                    {"name": "navPrice", "type": "uint256"},
                    {"name": "totalPendingDeposits", "type": "uint256"},
                ],
            }
        ],
    },
    {
        "name": "navProvider",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "address"}],
    },
]

#: ``GenerationStatus.Fulfilled`` in ``AsyncFundVault``.
GENERATION_FULFILLED = 4


def create_settlement_log(topic: str, generation_id: int, block_number: int, data: bytes) -> IndexedVaultFlowLog:
    """Create a Hypersync-shaped settlement log.

    :param topic:
        Event topic0.
    :param generation_id:
        Indexed generation id.
    :param block_number:
        Emitting block.
    :param data:
        ABI-encoded non-indexed event data.
    :return:
        Indexed log as returned by the Hypersync helper.
    """

    return IndexedVaultFlowLog(
        address=Web3.to_checksum_address(ARKVX_FEED.vault),
        topics=[topic, f"0x{generation_id:064x}", None, None],
        data=f"0x{data.hex()}",
        block_number=block_number,
        block_timestamp=None,
        transaction_hash="0x00",
        log_index=0,
    )


def create_arkvx_timeline() -> list[SecuritizeSettlementPrice]:
    """Create the known ARKVX settlement timeline from fixed onchain values.

    :return:
        Settlement prices for generations 1-4.
    """

    return [SecuritizeSettlementPrice.from_wad(block_number, generation_id, nav_price_wad, ARKVX_FEED) for generation_id, block_number, nav_price_wad, _published_nav in ARKVX_SETTLEMENTS]


def test_settlement_share_price_matches_published_nav() -> None:
    """Every ARKVX settlement reproduces ARK's published NAV once the 2% fee is removed."""

    for _generation_id, _block_number, nav_price_wad, published_nav in ARKVX_SETTLEMENTS:
        assert calculate_settlement_share_price(nav_price_wad, ARKVX_FEED) == published_nav


def test_settlement_share_price_rounds_half_up() -> None:
    """Round an exact half cent up, as published fund NAVs do, not to even."""

    feed = SecuritizeSettlementFeed(chain_id=1, token=ARKVX_FEED.token, vault=ARKVX_FEED.vault, first_block=1, subscription_fee=0.0)

    assert calculate_settlement_share_price(60_485_000_000_000_000_000, feed) == Decimal("60.49")


def test_decode_settlement_prices_warns_about_redemptions_and_jumps(caplog: pytest.LogCaptureFixture) -> None:
    """Price deposits only, and flag redemption settlements and suspicious NAV jumps."""

    logs = [
        create_settlement_log(DEPOSIT_GENERATION_FULFILLED_TOPIC, 1, 100, encode(["uint256", "uint256"], [61_743_053_906_400_000_000, 520_000_000])),
        create_settlement_log(REDEMPTION_GENERATION_FULFILLED_TOPIC, 1, 150, encode(["uint256", "uint256", "uint256"], [61_000_000_000_000_000_000, 10**18, 1_000_000])),
        create_settlement_log(DEPOSIT_GENERATION_FULFILLED_TOPIC, 2, 200, encode(["uint256", "uint256"], [80_000_000_000_000_000_000, 500_000_000])),
    ]

    with caplog.at_level(logging.WARNING):
        prices = decode_settlement_prices(logs, ARKVX_FEED)

    assert [(price.block_number, price.generation_id, price.share_price) for price in prices] == [(100, 1, Decimal("60.51")), (200, 2, Decimal("78.40"))]
    assert prices[0].settlement_price == Decimal("61.7430539064")
    assert "fulfilled redemption generation 1 at block 150" in caplog.text
    assert "moved from 60.51 to 78.40 at deposit generation 2, block 200" in caplog.text


def test_find_settlement_price_at() -> None:
    """Use the latest settlement at or before the sampled block."""

    timeline = create_arkvx_timeline()

    assert find_settlement_price_at(timeline, 26_005_045) is None
    assert find_settlement_price_at(timeline, 26_005_046).generation_id == 1
    assert find_settlement_price_at(timeline, 26_033_418).generation_id == ARKVX_SETTLEMENTS[1][0]
    assert find_settlement_price_at(timeline, ARKVX_TEST_BLOCK).share_price == Decimal("60.49")


def test_arkvx_historical_reader_uses_settlement_prices() -> None:
    """Price supply with the settlement in force, and flag rows before the first settlement."""

    vault = SecuritizeVault(Web3(), VaultSpec(chain_id=ARKVX_ETHEREUM.chain_id, vault_address=ARKVX_ETHEREUM.token))
    vault.settlement_prices = create_arkvx_timeline()
    vault.share_token = SimpleNamespace(convert_to_decimals=lambda raw: Decimal(raw) / Decimal(10**6))
    reader = vault.get_historical_reader(stateful=False)
    supply_result = SimpleNamespace(call=SimpleNamespace(extra_data={"function": "totalSupply"}), success=True, result=(33_279_000).to_bytes(32, "big"))
    timestamp = datetime.datetime(2026, 9, 24, 22, 0, tzinfo=datetime.UTC).replace(tzinfo=None)

    read = reader.process_result(ARKVX_TEST_BLOCK, timestamp, [supply_result])
    assert read.share_price == Decimal("60.49")
    assert read.total_supply == Decimal("33.279")
    assert read.total_assets == Decimal("2013.04671")
    assert read.management_fee == pytest.approx(0.0275)
    assert read.performance_fee == 0.0
    assert read.errors is None

    early_read = reader.process_result(26_000_000, timestamp, [supply_result])
    assert early_read.share_price is None
    assert early_read.total_assets is None
    assert early_read.errors == [f"Securitize DSToken {vault.address} has no fulfilled deposit generation at or before block 26000000"]


def test_arkvx_product_metadata() -> None:
    """Expose ARKVX fees, lock-up, price source, notes and exclusions."""

    vault = SecuritizeVault(Web3(), VaultSpec(chain_id=ARKVX_ETHEREUM.chain_id, vault_address=ARKVX_ETHEREUM.token))
    fee_data = vault.get_fee_data()

    assert has_historical_price(ARKVX_ETHEREUM)
    assert vault.get_share_price_source() == PriceSource.smart_contract_event
    assert fee_data.fee_mode == VaultFeeMode.internalised_skimming
    assert (fee_data.management, fee_data.performance, fee_data.deposit, fee_data.withdraw) == (0.0275, 0.0, 0.02, 0.0)
    assert vault.get_estimated_lock_up() is None
    assert all(url in vault.get_notes() for url in (ARKVX_PROSPECTUS_URL, ARKVX_SEC_ORDER_URL, ARKVX_REPURCHASE_OFFER_URL))
    assert ARKVX_FEED.vault in BROKEN_VAULT_CONTRACTS


def test_arkvx_historical_reader_aborts_when_timeline_unavailable() -> None:
    """Abort instead of writing unpriced rows when the settlement timeline cannot be read."""

    vault = SecuritizeVault(Web3(), VaultSpec(chain_id=ARKVX_ETHEREUM.chain_id, vault_address=ARKVX_ETHEREUM.token))

    def fail_settlement_lookup(block_number: int) -> None:
        raise RuntimeError(f"Settlement timeline unavailable at block {block_number}")

    vault.fetch_settlement_price_at = fail_settlement_lookup
    reader = vault.get_historical_reader(stateful=False)

    with pytest.raises(RuntimeError, match="timeline unavailable"):
        reader.process_result(ARKVX_TEST_BLOCK, datetime.datetime(2026, 9, 24, 22, 0, tzinfo=datetime.UTC).replace(tzinfo=None), [])


def test_empty_settlement_timeline_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Treat an empty Hypersync result after the known first settlement as an incomplete index."""

    monkeypatch.setattr(settlement_module, "fetch_vault_flow_logs_hypersync", lambda **_kwargs: [])

    with pytest.raises(RuntimeError, match="no deposit settlements"):
        fetch_settlement_prices(None, ARKVX_FEED, ARKVX_TEST_BLOCK)
    assert fetch_settlement_prices(None, ARKVX_FEED, ARKVX_FEED.first_block - 1) == []


@pytest.fixture(scope="module")
def web3() -> Web3:
    """Connect to an Ethereum archive node for fixed-block reads.

    These are read-only archive calls at :py:data:`ARKVX_TEST_BLOCK`, which is
    later than the shared Ethereum midnight fork block, so no Anvil fork is
    launched.
    """

    if JSON_RPC_ETHEREUM is None:
        pytest.skip("JSON_RPC_ETHEREUM needed to run ARKVX archive tests")
    return create_multi_provider_web3(JSON_RPC_ETHEREUM, retries=2)


def test_arkvx_settlement_state_at_fixed_block(web3: Web3) -> None:
    """Check the generation prices and NAV the timeline relies on against archive state."""

    subscription_vault = web3.eth.contract(Web3.to_checksum_address(ARKVX_FEED.vault), abi=ASYNC_FUND_VAULT_ABI)

    assert subscription_vault.functions.navProvider().call(block_identifier=ARKVX_TEST_BLOCK) == "0x0000000000000000000000000000000000000000"
    for generation_id, _block_number, nav_price_wad, _published_nav in ARKVX_SETTLEMENTS:
        status, nav_price, _pending = subscription_vault.functions.getDepositGeneration(generation_id).call(block_identifier=ARKVX_TEST_BLOCK)
        assert (status, nav_price) == (GENERATION_FULFILLED, nav_price_wad)


def test_arkvx_live_and_historical_paths_at_fixed_block(web3: Web3) -> None:
    """Read ARKVX supply and NAV through the live and historical adapter paths."""

    vault = SecuritizeVault(web3, VaultSpec(chain_id=ARKVX_ETHEREUM.chain_id, vault_address=ARKVX_ETHEREUM.token))
    vault.first_seen_at_block = 25_984_222
    vault.settlement_prices = create_arkvx_timeline()

    assert vault.fetch_share_price(ARKVX_TEST_BLOCK) == Decimal("60.49")
    assert vault.fetch_total_supply(ARKVX_TEST_BLOCK) == Decimal("33.279")

    reader = vault.get_historical_reader(stateful=False)
    call_results = [call.call_as_result(web3, block_identifier=ARKVX_TEST_BLOCK, ignore_error=True) for call in reader.construct_multicalls()]
    timestamp = datetime.datetime.fromtimestamp(web3.eth.get_block(ARKVX_TEST_BLOCK)["timestamp"], tz=datetime.UTC).replace(tzinfo=None)
    read = reader.process_result(ARKVX_TEST_BLOCK, timestamp, call_results)

    assert read.share_price == Decimal("60.49")
    assert read.total_supply == Decimal("33.279")
    assert read.total_assets == Decimal("2013.04671")
    assert read.errors is None


def test_arkvx_settlement_timeline_hypersync(web3: Web3) -> None:
    """Fetch the ARKVX settlement timeline from Hypersync.

    This is the real integration test for the settlement NAV source.
    """

    if HYPERSYNC_API_KEY is None:
        pytest.skip("HYPERSYNC_API_KEY needed to run the ARKVX Hypersync test")

    hypersync_client = configure_hypersync_from_env(web3).hypersync_client
    prices = fetch_settlement_prices(hypersync_client, ARKVX_FEED, ARKVX_TEST_BLOCK)

    assert prices == create_arkvx_timeline()
