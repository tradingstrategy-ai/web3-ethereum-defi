"""Test read-only Theo thBILL iToken support."""

import datetime
import os
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import flaky
import pytest

from eth_defi.erc_4626.classification import create_vault_instance, create_vault_instance_autodetect, identify_vault_features
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.tokenised_fund.theo import backfill
from eth_defi.tokenised_fund.theo.constants import ETHEREUM_CHAIN_ID, THBILL_ETHEREUM
from eth_defi.tokenised_fund.theo.historical import TheoITokenHistoricalReader
from eth_defi.tokenised_fund.theo.vault import THEO_ITOKEN_RESTRICTED_FLOW_REASON, TheoITokenVault
from eth_defi.vault.curator import identify_curator

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
THBILL_TEST_BLOCK = 25_553_175
THBILL_EXPECTED_TOTAL_SUPPLY = Decimal("97323026.986138")
THBILL_DECIMALS = 6


def test_thbill_hardcoded_classification_is_chain_aware() -> None:
    """Route thBILL only on its reviewed canonical Ethereum deployment."""

    broken_probe = SimpleNamespace(success=True, result=b"")
    ethereum_features = identify_vault_features(THBILL_ETHEREUM.token, calls={"EVM IS BROKEN SHIT": broken_probe}, debug_text="thBILL Ethereum", chain_id=ETHEREUM_CHAIN_ID)
    other_chain_features = identify_vault_features(THBILL_ETHEREUM.token, calls={"EVM IS BROKEN SHIT": broken_probe}, debug_text="thBILL wrong chain", chain_id=31_337)
    assert ethereum_features == {ERC4626Feature.theo_itoken_like}
    assert ERC4626Feature.theo_itoken_like not in other_chain_features


def test_thbill_adapter_blocks_unreviewed_public_flows() -> None:
    """Keep the multi-asset, KYC-gated product outside public dealing APIs."""

    vault = create_vault_instance(SimpleNamespace(eth=SimpleNamespace(chain_id=ETHEREUM_CHAIN_ID)), THBILL_ETHEREUM.token, features={ERC4626Feature.theo_itoken_like})
    assert isinstance(vault, TheoITokenVault)
    assert vault.get_protocol_name() == "Theo"
    assert get_vault_protocol_name({ERC4626Feature.theo_itoken_like}) == "Theo"
    assert vault.fetch_deposit_closed_reason() == THEO_ITOKEN_RESTRICTED_FLOW_REASON
    assert vault.fetch_redemption_closed_reason() == THEO_ITOKEN_RESTRICTED_FLOW_REASON
    assert vault.get_deposit_manager_capability() is None
    with pytest.raises(NotImplementedError, match="KYC approval"):
        vault.get_deposit_manager()


def test_thbill_history_never_invents_price_or_tvl() -> None:
    """Export verified supply only when basket pricing is not configured."""

    reader = TheoITokenHistoricalReader.__new__(TheoITokenHistoricalReader)
    reader.vault = SimpleNamespace(address=THBILL_ETHEREUM.token, share_token=SimpleNamespace(convert_to_decimals=lambda raw_amount: Decimal(raw_amount) / Decimal(10**6)))
    call = EncodedCall(func_name="totalSupply", address=THBILL_ETHEREUM.token, data=b"", extra_data={"function": "totalSupply", "vault": THBILL_ETHEREUM.token})
    read = reader.process_result(123, datetime.datetime(2026, 7, 17, tzinfo=datetime.UTC).replace(tzinfo=None), [EncodedCallResult(call=call, success=True, result=(123_456_789).to_bytes(32, "big"), block_identifier=123)])
    assert read.total_supply == Decimal("123.456789")
    assert read.share_price is None
    assert read.total_assets is None
    assert read.deposits_open is False
    assert read.redemption_open is False
    assert read.errors == ["Theo thBILL iToken has no reviewed scalar NAV/share source; basket valuation is not configured"]


def test_thbill_uses_verified_curator_address_mapping() -> None:
    """Attribute the supported token to Theo's protocol-managed curator."""

    assert identify_curator(chain_id=ETHEREUM_CHAIN_ID, vault_token_symbol="thBILL", vault_name="unrelated display name", vault_address=THBILL_ETHEREUM.token, protocol_slug="theo") == "theo-curator"


@flaky.flaky
@pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run this test")
def test_thbill_live_token_surface() -> None:
    """Read the canonical iToken's fixed-block ERC-20 surface."""

    web3 = create_multi_provider_web3(JSON_RPC_ETHEREUM)
    vault = create_vault_instance_autodetect(web3, THBILL_ETHEREUM.token)
    assert isinstance(vault, TheoITokenVault)
    assert vault.name == THBILL_ETHEREUM.product_name
    assert vault.symbol == "thBILL"
    assert vault.share_token.decimals == THBILL_DECIMALS
    assert vault.fetch_total_supply(THBILL_TEST_BLOCK) == THBILL_EXPECTED_TOTAL_SUPPLY
    with pytest.raises(NotImplementedError, match="no reviewed scalar NAV/share source"):
        vault.fetch_share_price(THBILL_TEST_BLOCK)
    assert vault.get_deposit_manager_capability() is None


@flaky.flaky
@pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run this test")
def test_thbill_scan_record_is_unpriced_not_broken() -> None:
    """Export thBILL supply without manufacturing NAV or a public lifecycle."""

    web3 = create_multi_provider_web3(JSON_RPC_ETHEREUM)
    detection = ERC4262VaultDetection(
        chain=ETHEREUM_CHAIN_ID,
        address=THBILL_ETHEREUM.token,
        first_seen_at_block=THBILL_ETHEREUM.first_seen_at_block,
        first_seen_at=THBILL_ETHEREUM.first_seen_at,
        features={ERC4626Feature.theo_itoken_like},
        updated_at=THBILL_ETHEREUM.first_seen_at,
        deposit_count=0,
        redeem_count=0,
    )
    record = create_vault_scan_record(web3, detection=detection, block_identifier=THBILL_TEST_BLOCK, token_cache=TokenDiskCache())
    assert record["Name"] == THBILL_ETHEREUM.product_name
    assert record["Protocol"] == "Theo"
    assert record["NAV"] is None
    assert record["Shares"] == THBILL_EXPECTED_TOTAL_SUPPLY
    assert record["_deposit_manager"] is None
    assert record["_deposit_closed_reason"] == THEO_ITOKEN_RESTRICTED_FLOW_REASON


def test_thbill_migration_preserves_unrelated_scanner_data() -> None:
    """Require the targeted migration to retain global scanner state."""

    source = Path(backfill.__file__).read_text(encoding="utf-8")
    assert "prior_watermark" in source
    assert "last_scanned_block.pop" in source
    assert "reader state and price Parquet files were not changed" in source
