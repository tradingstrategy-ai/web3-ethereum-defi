"""Ethereum FCS classification, accounting and conditional exit coverage."""

# Fixed-state assertions and pytest parametrisation deliberately use literals.
# ruff: noqa: FBT001, PLC2701, PLR2004

import datetime
import os
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import HARDCODED_PROTOCOLS, _get_hardcoded_protocol_features, create_vault_instance, create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.erc_4626.vault import ERC4626HistoricalReader
from eth_defi.erc_4626.vault_protocol.frankencoin.constants import FRANKENCOIN_SHARES_ADDRESS, FRANKENCOIN_SHARES_DEPLOYMENT_BLOCK, FRANKENCOIN_SHARES_DEPLOYMENT_TIME, FRANKENCOIN_ZCHF_ADDRESS
from eth_defi.erc_4626.vault_protocol.frankencoin.shares import FrankencoinSharesVault
from eth_defi.event_reader.multicall_batcher import MultiprocessMulticallReader
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.token import TokenDiskCache
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.curator import get_curator_name, identify_curator, is_protocol_curator
from eth_defi.vault.fee import VaultFeeMode
from eth_defi.vault.strategy_tag import STRATEGY_TAG_METADATA, StrategyTag

#: FCS deployed after ETHEREUM_MIDNIGHT_BLOCK. Use the verified research
#: snapshot as a fixed exception, with its own shared fork and warm RPC cache.
FCS_FORK_BLOCK = 26_089_593

pytestmark = pytest.mark.xdist_group("fork:ethereum:frankencoin-shares")


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Read FCS at a fixed post-deployment shared fork.

    The canonical Ethereum midnight predates FCS. Read-only tests share this
    exceptional block; no per-test mutations or private Anvil launches occur.

    :param anvil_fork_pool: Session-owned fork pool.
    :return: Ethereum fork with reviewed FCS state.
    """
    rpc_url = os.environ.get("JSON_RPC_ETHEREUM")
    if rpc_url is None:
        pytest.skip("JSON_RPC_ETHEREUM needed")
    return anvil_fork_pool.get_web3(rpc_url, FCS_FORK_BLOCK)


def test_fcs_hardcoded_classification_and_curator() -> None:
    """Route only the reviewed Ethereum deployment to the FCS adapter.

    Check address normalisation, chain scope and the shared protocol curator.

    :return: ``None`` after checking factory and label resolution.
    """
    features = {ERC4626Feature.frankencoin_fcs_like}
    assert HARDCODED_PROTOCOLS[FRANKENCOIN_SHARES_ADDRESS] == features
    assert _get_hardcoded_protocol_features(Web3.to_checksum_address(FRANKENCOIN_SHARES_ADDRESS), chain_id=1) == features
    assert _get_hardcoded_protocol_features(FRANKENCOIN_SHARES_ADDRESS, chain_id=8453) is None
    assert _get_hardcoded_protocol_features(FRANKENCOIN_SHARES_ADDRESS) is None
    connection = Web3()
    connection.eth._chain_id = lambda: 1
    vault = create_vault_instance(connection, FRANKENCOIN_SHARES_ADDRESS, features=features)
    assert isinstance(vault, FrankencoinSharesVault)
    assert vault.get_protocol_name() == "Frankencoin"
    curator = identify_curator(chain_id=1, vault_token_symbol="FCS", vault_name=vault.name, vault_address=FRANKENCOIN_SHARES_ADDRESS, protocol_slug="frankencoin", manager_name=vault.manager_name)
    assert curator == "frankencoin"
    assert is_protocol_curator(curator)
    assert get_curator_name(curator) == "Frankencoin"


def test_fcs_strategy_tags_and_fees() -> None:
    """Maintain address-scoped strategy tags and equity-specific fees.

    Mutating a returned tag set must not alter maintained classifications;
    unreviewed deployments retain missing-information semantics.

    :return: ``None`` after checking tags, fees and tracking capability.
    """
    vault = FrankencoinSharesVault(Web3(), VaultSpec(1, FRANKENCOIN_SHARES_ADDRESS), features={ERC4626Feature.frankencoin_fcs_like})
    tags = vault.get_strategy_tags()
    assert tags == {StrategyTag.protocol_equity, StrategyTag.lending, StrategyTag.rwa, StrategyTag.rwa_lending}
    tags.clear()
    assert vault.get_strategy_tags() == {StrategyTag.protocol_equity, StrategyTag.lending, StrategyTag.rwa, StrategyTag.rwa_lending}
    assert STRATEGY_TAG_METADATA[StrategyTag.protocol_equity]["label"] == "Protocol equity"
    assert vault.get_fee_data().deposit == 0.003
    assert vault.get_fee_data().withdraw == 0.003
    assert vault.get_fee_data().management == 0
    assert vault.get_fee_data().performance == 0
    assert vault.get_fee_mode() == VaultFeeMode.externalised
    assert vault.has_custom_fees()
    assert vault.get_risk() is None
    assert vault.get_estimated_lock_up() is None
    assert vault.get_withdrawal_period() is None
    assert vault.get_deposit_manager_capability() is None
    other_chain = FrankencoinSharesVault(Web3(), VaultSpec(8453, FRANKENCOIN_SHARES_ADDRESS))
    assert other_chain.get_strategy_tags() is None
    other_vault = FrankencoinSharesVault(Web3(), VaultSpec(1, "0x0000000000000000000000000000000000000001"))
    assert other_vault.get_strategy_tags() is None


@pytest.mark.parametrize("binding,eligible,enabled", [(False, False, False), (True, False, False), (True, True, True)])
def test_fcs_redemption_gates(binding: bool, eligible: bool, enabled: bool) -> None:
    """Explain both voting and underlying holding-period exit gates.

    Evaluate each restriction independently at the adapter's fixed block.

    :param binding: Whether the wrapper has sufficient underlying FPS votes.
    :param eligible: Whether the wrapper meets FPS holding-period requirements.
    :param enabled: Expected availability of direct ZCHF redemption.
    :return: ``None`` after checking the closure reason.
    """
    vault = FrankencoinSharesVault(Web3(), VaultSpec(1, FRANKENCOIN_SHARES_ADDRESS), features={ERC4626Feature.frankencoin_fcs_like}, default_block_identifier=FCS_FORK_BLOCK)
    shares = Mock()
    shares.functions.isBinding.return_value.call.return_value = binding
    equity = Mock()
    equity.functions.canRedeem.return_value.call.return_value = eligible
    vault.__dict__["vault_contract"] = shares
    vault.__dict__["equity_contract"] = equity
    reason = vault.fetch_redemption_closed_reason()
    assert (reason is None) is enabled
    if not binding:
        assert "voting power" in reason
    elif not eligible:
        assert "holding-period" in reason
    else:
        assert reason is None


def test_fcs_accounting_on_fork(web3: Web3) -> None:
    """Read real FCS backing and reference valuation at a fixed fork block.

    Exercise direct views and the historical multicall reader, including a
    non-zero redemption preview while actual exits are disabled. Mechanics:
    https://docs.frankencoin.com/pool-shares/fcs.

    :param web3: Shared Ethereum fork at the reviewed post-deployment block.
    :return: ``None`` after checking exact accounting and eligibility values.
    """
    vault = create_vault_instance_autodetect(web3, FRANKENCOIN_SHARES_ADDRESS)
    assert isinstance(vault, FrankencoinSharesVault)
    assert vault.denomination_token.address.lower() == FRANKENCOIN_ZCHF_ADDRESS
    assert vault.share_token.symbol == "FCS"
    assert vault.share_token.decimals == 18
    assert vault.fetch_total_assets(FCS_FORK_BLOCK) == Decimal("1383499.536765172204801324")
    assert vault.fetch_total_supply(FCS_FORK_BLOCK) == Decimal("3267.850967014514304151")
    assert vault.fetch_share_price(FCS_FORK_BLOCK) == Decimal("1270.100335722281423071")
    assert type(vault.get_historical_reader(stateful=False)) is ERC4626HistoricalReader
    assert "voting power" in vault.fetch_redemption_closed_reason()
    one_share = vault.share_token.convert_to_raw(Decimal(1))
    proceeds = vault.vault_contract.functions.previewRedeem(one_share).call(block_identifier=FCS_FORK_BLOCK)
    assert vault.denomination_token.convert_to_decimals(proceeds) == Decimal("1265.36759393144058231")
    pool = Web3.to_checksum_address("0xcD795ae77A7318A396D6645bAf0562d8a0312323")
    assert vault.vault_contract.functions.balanceOf(pool).call(block_identifier=FCS_FORK_BLOCK) == 233691360295905320551
    assert vault.vault_contract.functions.maxRedeem(pool).call(block_identifier=FCS_FORK_BLOCK) == 0
    # Exercise the actual batch reader as well as direct accounting views.
    timestamp = datetime.datetime(2026, 9, 30, 10, 7, 35)  # noqa: DTZ001 - fixed naive UTC snapshot.
    reader = vault.get_historical_reader(stateful=False)
    calls = list(reader.construct_multicalls())
    results = list(MultiprocessMulticallReader(web3).process_calls(FCS_FORK_BLOCK, calls, require_multicall_result=True, timestamp=timestamp))
    observation = reader.process_result(FCS_FORK_BLOCK, timestamp, results)
    assert observation.errors is None
    assert observation.share_price == Decimal("1270.100335722281423071")
    assert observation.total_assets == Decimal("1383499.536765172204801324")
    assert observation.total_supply == Decimal("3267.850967014514304151")


def test_fcs_scan_record_on_fork(web3: Web3, tmp_path: Path) -> None:
    """Materialise scanner metadata from the real FCS deployment.

    Verify labels, classification and unavailable redemption without inheriting
    savings-product accounting or transaction capability.

    :param web3: Shared Ethereum fork at the reviewed post-deployment block.
    :param tmp_path: Isolated ERC-20 metadata cache directory.
    :return: ``None`` after checking the rebuilt scan row.
    """
    detection = ERC4262VaultDetection(chain=1, address=FRANKENCOIN_SHARES_ADDRESS, first_seen_at_block=FRANKENCOIN_SHARES_DEPLOYMENT_BLOCK, first_seen_at=FRANKENCOIN_SHARES_DEPLOYMENT_TIME, features={ERC4626Feature.frankencoin_fcs_like}, updated_at=FRANKENCOIN_SHARES_DEPLOYMENT_TIME, deposit_count=0, redeem_count=0)
    cache = TokenDiskCache(tmp_path / "tokens.sqlite")
    try:
        row = create_vault_scan_record(web3, detection, FCS_FORK_BLOCK, cache)
    finally:
        cache.close()
    assert row["Name"] == "Frankencoin Shares"
    assert row["Protocol"] == "Frankencoin"
    assert row["_manager_name"] == "Frankencoin"
    assert row["_strategy_tags"] == {StrategyTag.protocol_equity, StrategyTag.lending, StrategyTag.rwa, StrategyTag.rwa_lending}
    assert row["NAV"] == Decimal("1383499.536765172204801324")
    assert row["_deposit_manager"] is None
    assert row["_lockup"] is None
    assert "voting power" in row["_redemption_closed_reason"]
    assert row["_share_price_type"] == "bonding_curve_reference"
