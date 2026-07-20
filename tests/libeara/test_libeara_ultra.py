"""Test Libeara ULTRA classification and supply-only behaviour."""

import datetime
import os
from decimal import Decimal
from types import SimpleNamespace

import pytest

from eth_defi.erc_4626.classification import create_vault_instance, identify_vault_features
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.tokenised_fund.libeara import backfill_ultra
from eth_defi.tokenised_fund.libeara.constants import ARBITRUM_CHAIN_ID, LIBEARA_HARDCODED_LEADS, LIBEARA_ULTRA_ARBITRUM
from eth_defi.tokenised_fund.libeara.historical import LibearaVaultHistoricalReader
from eth_defi.tokenised_fund.libeara.vault import LIBEARA_NAV_UNAVAILABLE_ERROR_PREFIX, LIBEARA_RESTRICTED_FLOW_REASON, LibearaVault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase


class DummyToken:
    """Convert six-decimal ULTRA supply observations."""

    @staticmethod
    def convert_to_decimals(raw_amount: int) -> Decimal:
        """Convert raw token units.

        :param raw_amount: Six-decimal raw amount.
        :return: Human-readable supply.
        """

        return Decimal(raw_amount) / Decimal(10**6)


class DummyUltraVault:
    """Provide the minimum ULTRA adapter surface for reader tests."""

    address = "0x0000000000000000000000000000000000000001"
    share_token = DummyToken()
    is_ultra = True


@pytest.fixture
def backfill_ultra_module():
    """Return the Libeara ULTRA backfill module.

    :return: Imported migration module.
    """

    return backfill_ultra


def test_libeara_ultra_hardcoded_classification_is_chain_aware() -> None:
    """Classify ULTRA only at its reviewed Arbitrum address."""

    broken_probe = SimpleNamespace(success=True, result=b"")
    expected = {ERC4626Feature.libeara_like}
    assert (ARBITRUM_CHAIN_ID, LIBEARA_ULTRA_ARBITRUM.token, LIBEARA_ULTRA_ARBITRUM.first_seen_at_block, LIBEARA_ULTRA_ARBITRUM.first_seen_at) in LIBEARA_HARDCODED_LEADS
    assert identify_vault_features(LIBEARA_ULTRA_ARBITRUM.token, {"EVM IS BROKEN SHIT": broken_probe}, "libeara", chain_id=ARBITRUM_CHAIN_ID) == expected
    assert identify_vault_features(LIBEARA_ULTRA_ARBITRUM.token, {"EVM IS BROKEN SHIT": broken_probe}, "wrong chain", chain_id=1) != expected


def test_libeara_ultra_adapter_blocks_public_flows_and_unknown_nav() -> None:
    """Expose no generic dealing manager or unverified ULTRA valuation."""

    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=ARBITRUM_CHAIN_ID))
    vault = create_vault_instance(web3, LIBEARA_ULTRA_ARBITRUM.token, features={ERC4626Feature.libeara_like})
    assert isinstance(vault, LibearaVault)
    assert vault.manager_name == "Wellington Management"
    assert vault.curator_slug == "wellington-management"
    assert vault.get_deposit_manager_capability() is None
    assert vault.fetch_deposit_closed_reason() == LIBEARA_RESTRICTED_FLOW_REASON
    assert vault.fetch_redemption_closed_reason() == LIBEARA_RESTRICTED_FLOW_REASON
    with pytest.raises(NotImplementedError, match="No verified"):
        vault.fetch_share_price()
    with pytest.raises(NotImplementedError, match="subscriptions"):
        vault.get_deposit_manager()


def test_libeara_ultra_historical_reader_keeps_supply_without_nav() -> None:
    """Retain supply while explicitly marking ULTRA NAV unavailable."""

    reader = LibearaVaultHistoricalReader.__new__(LibearaVaultHistoricalReader)
    reader.vault = DummyUltraVault()
    reader.reader_state = None
    call = EncodedCall(func_name="totalSupply", address=DummyUltraVault.address, data=b"", extra_data={"function": "totalSupply"})
    result = EncodedCallResult(call=call, success=True, result=(36_192_127_917_021).to_bytes(32, "big"), block_identifier=123)
    timestamp = datetime.datetime(2025, 7, 18, 7, 9, 32, tzinfo=datetime.UTC).replace(tzinfo=None)
    read = reader.process_result(123, timestamp, [result])
    assert read.total_supply == Decimal("36192127.917021")
    assert read.share_price is None
    assert read.total_assets is None
    assert read.errors is not None
    assert LIBEARA_NAV_UNAVAILABLE_ERROR_PREFIX in read.errors[0]


@pytest.mark.parametrize("existing_cursor", [None, 358_900_000])
def test_libeara_ultra_metadata_upsert_preserves_unrelated_state(backfill_ultra_module, existing_cursor: int | None) -> None:
    """Keep discovery watermarks and unrelated rows during ULTRA registration.

    :param backfill_ultra_module: Loaded metadata-only migration.
    :param existing_cursor: Existing Arbitrum cursor, if present.
    """

    vault_db = VaultDatabase()
    if existing_cursor is not None:
        vault_db.last_scanned_block[ARBITRUM_CHAIN_ID] = existing_cursor
    unrelated = VaultSpec(ARBITRUM_CHAIN_ID, "0x0000000000000000000000000000000000000002")
    vault_db.rows[unrelated] = {"Name": "Unrelated vault"}

    backfill_ultra_module.upsert_ultra_metadata(vault_db, {"Name": "ULTRA"})

    if existing_cursor is None:
        assert ARBITRUM_CHAIN_ID not in vault_db.last_scanned_block
    else:
        assert vault_db.last_scanned_block[ARBITRUM_CHAIN_ID] == existing_cursor
    assert vault_db.rows[unrelated]["Name"] == "Unrelated vault"
    ultra_spec = VaultSpec(ARBITRUM_CHAIN_ID, LIBEARA_ULTRA_ARBITRUM.token)
    assert vault_db.rows[ultra_spec]["Name"] == "ULTRA"
    assert ultra_spec in vault_db.leads


@pytest.mark.skipif(os.environ.get("JSON_RPC_ARBITRUM") is None, reason="JSON_RPC_ARBITRUM needed to run this test")
def test_libeara_ultra_supply_at_fixed_block() -> None:
    """Read the reviewed ULTRA supply at a fixed Arbitrum archive block."""

    web3 = create_multi_provider_web3(os.environ["JSON_RPC_ARBITRUM"])
    vault = create_vault_instance(web3, LIBEARA_ULTRA_ARBITRUM.token, features={ERC4626Feature.libeara_like})
    assert isinstance(vault, LibearaVault)
    assert vault.fetch_total_supply(390_000_000) == Decimal("36192127.917021")
    assert [call.extra_data["function"] for call in vault.get_historical_reader(stateful=False).construct_multicalls()] == ["totalSupply"]
