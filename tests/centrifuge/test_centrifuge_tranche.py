"""Test direct Centrifuge Tranche token support for JTRSY."""

# ruff: noqa: PLR6301

import datetime
import os
from decimal import Decimal
from types import SimpleNamespace

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance, create_vault_instance_autodetect, identify_vault_features
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.erc_4626.vault import VaultReaderState
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.tokenised_fund.centrifuge.constants import ETHEREUM_CHAIN_ID, JTRSY_ETHEREUM
from eth_defi.tokenised_fund.centrifuge.historical import CentrifugeTrancheHistoricalReader
from eth_defi.tokenised_fund.centrifuge.vault import CENTRIFUGE_TRANCHE_BLOCKED_FLOW_REASON, CentrifugeTrancheVault
from eth_defi.tokenised_fund.vault import TokenisedFundDepositManager
from eth_defi.vault.curator import identify_curator

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

JTRSY_TEST_BLOCK = 25_553_175
JTRSY_EXPECTED_TOTAL_SUPPLY = Decimal("783691015.920462")
JTRSY_EXPECTED_HOOK = "0x21Cdcc686fECd9Fb0d3ee300E555C06497B55EcC"
JTRSY_EXPECTED_USDC_VAULT = "0xFE6920eB6C421f1179cA8c8d4170530CDBdfd77A"
USDC_ETHEREUM = "0xA0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def web3() -> Web3:
    """Create the Ethereum archive-node connection.

    :return:
        Multi-provider Web3 instance.
    """

    return create_multi_provider_web3(JSON_RPC_ETHEREUM)


class DummyTrancheToken:
    """Convert synthetic raw JTRSY amounts to six-decimal shares."""

    def convert_to_decimals(self, raw_amount: int) -> Decimal:
        """Convert raw token amounts.

        :param raw_amount:
            Six-decimal Tranche amount.
        :return:
            Human-readable share amount.
        """

        return Decimal(raw_amount) / Decimal(10**6)


class DummyTrancheVault:
    """Minimum vault surface for historical-reader unit coverage."""

    address = JTRSY_ETHEREUM.token
    share_token = DummyTrancheToken()


def test_jtrsy_hardcoded_classification_is_chain_aware() -> None:
    """Route JTRSY only on its reviewed Ethereum deployment."""

    broken_probe = SimpleNamespace(success=True, result=b"")
    ethereum_features = identify_vault_features(
        JTRSY_ETHEREUM.token,
        calls={"EVM IS BROKEN SHIT": broken_probe},
        debug_text="JTRSY Ethereum",
        chain_id=ETHEREUM_CHAIN_ID,
    )
    assert ethereum_features == {ERC4626Feature.centrifuge_tranche_like}

    unsupported_chain_features = identify_vault_features(
        JTRSY_ETHEREUM.token,
        calls={"EVM IS BROKEN SHIT": broken_probe},
        debug_text="JTRSY wrong chain",
        chain_id=31_337,
    )
    assert ERC4626Feature.centrifuge_tranche_like not in unsupported_chain_features


def test_jtrsy_adapter_blocks_public_flows() -> None:
    """Keep the direct Tranche token outside public dealing APIs."""

    vault = create_vault_instance(
        SimpleNamespace(eth=SimpleNamespace(chain_id=ETHEREUM_CHAIN_ID)),
        JTRSY_ETHEREUM.token,
        features={ERC4626Feature.centrifuge_tranche_like},
    )
    assert isinstance(vault, CentrifugeTrancheVault)
    assert vault.get_protocol_name() == "Centrifuge"
    assert get_vault_protocol_name({ERC4626Feature.centrifuge_tranche_like}) == "Centrifuge"
    assert vault.fetch_deposit_closed_reason() == CENTRIFUGE_TRANCHE_BLOCKED_FLOW_REASON
    assert vault.fetch_redemption_closed_reason() == CENTRIFUGE_TRANCHE_BLOCKED_FLOW_REASON
    assert vault.get_deposit_manager_capability().as_initial_public_schema() == {"can_deposit": False, "can_redeem": False}
    assert isinstance(vault.get_deposit_manager(), TokenisedFundDepositManager)
    reader = vault.get_historical_reader(stateful=True)
    assert isinstance(reader.reader_state, VaultReaderState)


def test_jtrsy_history_never_invents_price_or_tvl() -> None:
    """Export supply only when the direct token lacks a NAV source."""

    reader = CentrifugeTrancheHistoricalReader.__new__(CentrifugeTrancheHistoricalReader)
    reader.vault = DummyTrancheVault()
    reader.reader_state = None
    call = EncodedCall(
        func_name="totalSupply",
        address=JTRSY_ETHEREUM.token,
        data=b"",
        extra_data={"function": "totalSupply", "vault": JTRSY_ETHEREUM.token},
    )
    read = reader.process_result(
        block_number=123,
        timestamp=datetime.datetime(2026, 7, 17, tzinfo=datetime.UTC).replace(tzinfo=None),
        call_results=[EncodedCallResult(call=call, success=True, result=(123_456_789).to_bytes(32, "big"), block_identifier=123)],
    )

    assert read.total_supply == Decimal("123.456789")
    assert read.share_price is None
    assert read.total_assets is None
    assert read.deposits_open is False
    assert read.redemption_open is False
    assert read.errors == ["Centrifuge Tranche token has no on-chain NAV/share source; linked vault valuation is not configured"]


def test_jtrsy_uses_verified_curator_address_mapping() -> None:
    """Attribute the reviewed fund to its existing Janus Henderson curator."""

    curator = identify_curator(
        chain_id=ETHEREUM_CHAIN_ID,
        vault_token_symbol="JTRSY",
        vault_name="unrelated display name",
        vault_address=JTRSY_ETHEREUM.token,
        protocol_slug="centrifuge",
    )
    assert curator == "janus-henderson-anemoy"


@flaky.flaky
def test_jtrsy_live_token_surface(web3: Web3) -> None:
    """Read the reviewed direct token and linked USDC vault at a fixed block."""

    vault = create_vault_instance_autodetect(web3, JTRSY_ETHEREUM.token)
    assert isinstance(vault, CentrifugeTrancheVault)
    assert vault.symbol == "JTRSY"
    assert vault.fetch_total_supply(JTRSY_TEST_BLOCK) == JTRSY_EXPECTED_TOTAL_SUPPLY
    assert vault.fetch_compliance_hook(JTRSY_TEST_BLOCK) == JTRSY_EXPECTED_HOOK
    assert vault.fetch_linked_vault(USDC_ETHEREUM, JTRSY_TEST_BLOCK) == JTRSY_EXPECTED_USDC_VAULT
    assert vault.get_deposit_manager_capability().as_initial_public_schema() == {"can_deposit": False, "can_redeem": False}

    with pytest.raises(NotImplementedError, match="does not expose NAV/share"):
        vault.fetch_share_price(JTRSY_TEST_BLOCK)


@flaky.flaky
def test_jtrsy_scan_record_is_unpriced_not_broken(web3: Web3) -> None:
    """Keep the direct token's scan record useful without fabricating TVL."""

    detection = ERC4262VaultDetection(
        chain=ETHEREUM_CHAIN_ID,
        address=JTRSY_ETHEREUM.token,
        first_seen_at_block=JTRSY_ETHEREUM.first_seen_at_block,
        first_seen_at=JTRSY_ETHEREUM.first_seen_at,
        features={ERC4626Feature.centrifuge_tranche_like},
        updated_at=JTRSY_ETHEREUM.first_seen_at,
        deposit_count=0,
        redeem_count=0,
    )
    record = create_vault_scan_record(
        web3,
        detection=detection,
        block_identifier=JTRSY_TEST_BLOCK,
        token_cache=TokenDiskCache(),
    )

    assert record["Name"] == JTRSY_ETHEREUM.product_name
    assert record["Protocol"] == "Centrifuge"
    assert record["NAV"] is None
    assert record["Shares"] == JTRSY_EXPECTED_TOTAL_SUPPLY
    assert record["_deposit_manager"] is None
    assert record["_deposit_closed_reason"] == CENTRIFUGE_TRANCHE_BLOCKED_FLOW_REASON
