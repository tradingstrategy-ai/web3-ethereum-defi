"""Test MONY FACT Diamond registration and unavailable public valuation."""

import datetime
import os
from decimal import Decimal
from pathlib import Path

import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import (
    ODA_FACT_JLTXX_ADDRESS,
    ODA_FACT_JLTXX_FIRST_SEEN_AT,
    ODA_FACT_JLTXX_FIRST_SEEN_AT_BLOCK,
    ODA_FACT_MONY_ADDRESS,
    ODA_FACT_MONY_FIRST_SEEN_AT,
    ODA_FACT_MONY_FIRST_SEEN_AT_BLOCK,
    _get_hardcoded_protocol_features,  # noqa: PLC2701 - validates chain-aware internal router.
    create_vault_instance_autodetect,
)
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.tokenised_fund.kinexys import backfill
from eth_defi.tokenised_fund.kinexys.vault import KINEXYS_WHITELISTED_FLOW_REASON, MONY_NAV_SOURCE, OdaFactVault
from eth_defi.tokenised_fund.vault import TokenisedFundDepositManager
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.vaultdb import VaultDatabase

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

#: Fixed historical Ethereum block for exact MONY ERC-20 supply assertions.
MONY_TEST_BLOCK = 25_550_000
MONY_EXPECTED_RAW_TOTAL_SUPPLY = 1_020_704_227_800
MONY_EXPECTED_DECIMALS = 4


@pytest.fixture(scope="module")
def anvil_ethereum_fork() -> AnvilLaunch:
    """Fork Ethereum at the fixed MONY test block.

    :return:
        Running local Anvil fork.
    """

    if JSON_RPC_ETHEREUM is None:
        pytest.skip("JSON_RPC_ETHEREUM needed to run MONY fork tests")
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=MONY_TEST_BLOCK)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork: AnvilLaunch) -> Web3:
    """Create Web3 connected to the fixed MONY Anvil fork.

    :param anvil_ethereum_fork:
        Running local Anvil fork.
    :return:
        Fork-connected Web3 instance.
    """

    return create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, retries=2)


def test_mony_is_chain_aware_hardcoded_fact_product() -> None:
    """Classify MONY only on its known Ethereum deployment.

    :return:
        None.
    """

    assert _get_hardcoded_protocol_features(ODA_FACT_MONY_ADDRESS, chain_id=1) == {ERC4626Feature.oda_fact_like}
    assert _get_hardcoded_protocol_features(ODA_FACT_MONY_ADDRESS, chain_id=8453) is None


def test_mony_does_not_invent_a_nav_or_public_flow() -> None:
    """Keep MONY supply-only until an authoritative NAV source is available.

    :return:
        None.
    """

    vault = OdaFactVault(web3=None, spec=VaultSpec(1, ODA_FACT_MONY_ADDRESS))

    assert vault.description == "My OnChain Net Yield Fund"
    assert vault.short_description == "U.S. Treasury and Treasury-backed repurchase-agreement money-market strategy"
    assert vault.fetch_share_price() is None
    assert vault.fetch_total_assets() is None
    assert vault.fetch_nav() is None
    assert vault.fetch_info()["nav_source"] == MONY_NAV_SOURCE
    assert vault.fetch_info()["nav_estimated"] is False
    assert vault.fetch_deposit_closed_reason() == KINEXYS_WHITELISTED_FLOW_REASON
    assert vault.fetch_redemption_closed_reason() == KINEXYS_WHITELISTED_FLOW_REASON
    assert vault.get_flags() == {VaultFlag.tokenised_fund}
    assert "no on-chain NAV" in vault.get_notes()
    assert vault.get_deposit_manager_capability().as_initial_public_schema() == {"can_deposit": False, "can_redeem": False}
    assert isinstance(vault.get_deposit_manager(), TokenisedFundDepositManager)
    with pytest.raises(NotImplementedError):
        vault.get_flow_manager()


def test_mony_live_erc20_supply_and_unavailable_nav(web3: Web3) -> None:
    """Read MONY supply from its FACT facet without treating it as valuation.

    :param web3:
        Fixed-block Ethereum fork connection.
    :return:
        None.
    """

    vault = create_vault_instance_autodetect(web3, ODA_FACT_MONY_ADDRESS)

    assert isinstance(vault, OdaFactVault)
    assert vault.share_token.name == "My OnChain Net Yield Fund"
    assert vault.share_token.symbol == "MONY"
    assert vault.share_token.decimals == MONY_EXPECTED_DECIMALS
    assert vault.share_token.contract.functions.totalSupply().call(block_identifier=MONY_TEST_BLOCK) == MONY_EXPECTED_RAW_TOTAL_SUPPLY
    assert vault.fetch_total_supply(MONY_TEST_BLOCK) == Decimal("102070422.78")
    assert vault.fetch_share_price(MONY_TEST_BLOCK) is None
    assert vault.fetch_total_assets(MONY_TEST_BLOCK) is None
    assert vault.fetch_nav(MONY_TEST_BLOCK) is None

    reader = vault.get_historical_reader(stateful=False)
    call_results = [call.call_as_result(web3, block_identifier=MONY_TEST_BLOCK, ignore_error=True) for call in reader.construct_multicalls()]
    timestamp = datetime.datetime.fromtimestamp(web3.eth.get_block(MONY_TEST_BLOCK)["timestamp"], tz=datetime.UTC).replace(tzinfo=None)
    read = reader.process_result(MONY_TEST_BLOCK, timestamp, call_results)

    assert read.share_price is None
    assert read.total_supply == Decimal("102070422.78")
    assert read.total_assets is None


@pytest.fixture
def backfill_mony_module():
    """Return the Kinexys backfill module.

    :return:
        Loaded migration module.
    """

    return backfill


def test_mony_backfill_preserves_discovery_cursor_when_writing_metadata(tmp_path: Path, backfill_mony_module) -> None:
    """Keep unrelated discovery and price pipeline state untouched by MONY repair.

    :param tmp_path:
        Isolated metadata database output directory.
    :param backfill_mony_module:
        Loaded MONY migration module.
    :return:
        None.
    """

    database = VaultDatabase(last_scanned_block={1: 25_000_000, 42161: 400_000_000})
    output_path = tmp_path / "vault-metadata-db.pickle"
    row = {"Name": "My OnChain Net Yield Fund"}

    spec = backfill_mony_module.update_mony_metadata(database, row, dry_run=False, output_path=output_path)

    assert spec == VaultSpec(1, ODA_FACT_MONY_ADDRESS)
    assert database.last_scanned_block == {1: 25_000_000, 42161: 400_000_000}
    assert database.leads[spec].first_seen_at_block == ODA_FACT_MONY_FIRST_SEEN_AT_BLOCK
    assert database.leads[spec].first_seen_at == ODA_FACT_MONY_FIRST_SEEN_AT
    assert database.rows[spec] == row
    assert VaultDatabase.read(output_path).last_scanned_block == database.last_scanned_block


def test_mony_backfill_dry_run_does_not_write_metadata(tmp_path: Path, backfill_mony_module) -> None:
    """Make MONY dry-run validation free of pipeline state changes.

    :param tmp_path:
        Isolated metadata database output directory.
    :param backfill_mony_module:
        Loaded MONY migration module.
    :return:
        None.
    """

    output_path = tmp_path / "vault-metadata-db.pickle"
    database = VaultDatabase(last_scanned_block={1: 25_000_000})

    backfill_mony_module.update_mony_metadata(database, {"Name": "MONY"}, dry_run=True, output_path=output_path)

    assert not output_path.exists()
    assert database.rows == {}
    assert database.leads == {}


def test_kinexys_backfill_refreshes_jltxx_and_mony_without_discovery_cursor_change(tmp_path: Path, backfill_mony_module) -> None:
    """Refresh both persisted FACT rows while retaining shared scan state.

    :param tmp_path:
        Isolated metadata database output directory.
    :param backfill_mony_module:
        Loaded Kinexys migration module.
    :return:
        None.
    """

    database = VaultDatabase(last_scanned_block={1: 25_500_000, 42161: 400_000_000})
    output_path = tmp_path / "vault-metadata-db.pickle"
    jltxx_spec = VaultSpec(1, ODA_FACT_JLTXX_ADDRESS)
    mony_spec = VaultSpec(1, ODA_FACT_MONY_ADDRESS)
    rows = {
        jltxx_spec: {"Name": "JPMorgan OnChain Liquidity-Token Money Market Fund", "Denomination": "USD"},
        mony_spec: {"Name": "My OnChain Net Yield Fund", "Denomination": "USD"},
    }

    specs = backfill_mony_module.update_kinexys_metadata(database, rows, dry_run=False, output_path=output_path)

    assert set(specs) == {jltxx_spec, mony_spec}
    assert database.last_scanned_block == {1: 25_500_000, 42161: 400_000_000}
    assert database.leads[jltxx_spec].first_seen_at_block == ODA_FACT_JLTXX_FIRST_SEEN_AT_BLOCK
    assert database.leads[jltxx_spec].first_seen_at == ODA_FACT_JLTXX_FIRST_SEEN_AT
    assert database.leads[mony_spec].first_seen_at_block == ODA_FACT_MONY_FIRST_SEEN_AT_BLOCK
    assert database.leads[mony_spec].first_seen_at == ODA_FACT_MONY_FIRST_SEEN_AT
    assert database.rows == rows
    assert VaultDatabase.read(output_path).last_scanned_block == database.last_scanned_block
