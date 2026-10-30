# ruff: noqa: PLR2004 - Absolute fixed-block characterisation values.
"""Characterise native strUSD using the shared Ethereum fork and real RPC."""

import datetime
import os
from decimal import Decimal

import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.erc_4626.vault_protocol.tori.constants import TORI_ASSET_ADDRESS, TORI_VAULT_ADDRESS
from eth_defi.erc_4626.vault_protocol.tori.vault import ToriVault
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import ETHEREUM_MIDNIGHT_BLOCK
from eth_defi.vault.base import WithdrawalDelayType, WithdrawalPeriod

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM required"),
    pytest.mark.xdist_group("fork:ethereum:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Reuse the fixed-block Ethereum fork for read-only assertions.

    The shared pool and RPC cache follow eth_defi.testing.anvil_fork_pool;
    these tests do not mutate chain state or require snapshot isolation.

    :param anvil_fork_pool: Session-owned shared fork pool.
    :return: Web3 at the canonical Ethereum midnight block.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_ETHEREUM, ETHEREUM_MIDNIGHT_BLOCK)


def test_tori_staking_vault(web3: Web3) -> None:
    """Read the real deployment at a reproducible block.

    Absolute values were read from Ethereum at block 25,598,869. This also
    exercises the inherited ERC-4626 accounting against the actual provider.

    :param web3: Shared Ethereum fork connection.
    :return: ``None`` after checking identity, valuation and withdrawal timing.
    """
    vault = create_vault_instance_autodetect(web3, TORI_VAULT_ADDRESS)
    assert isinstance(vault, ToriVault)
    assert vault.features == {ERC4626Feature.tori_like}
    assert vault.get_protocol_name() == "Tori Finance"
    assert vault.name == "Staked trUSD"
    assert vault.symbol == "strUSD"
    assert vault.share_token.decimals == vault.denomination_token.decimals == 18
    assert vault.denomination_token.address.lower() == TORI_ASSET_ADDRESS
    assert vault.fetch_total_assets(ETHEREUM_MIDNIGHT_BLOCK) == Decimal("25191902.324647899163613529")
    assert vault.vault_contract.functions.totalSupply().call(block_identifier=ETHEREUM_MIDNIGHT_BLOCK) == 25014022102434023833995912
    assert vault.fetch_cooldown_duration(ETHEREUM_MIDNIGHT_BLOCK) == datetime.timedelta(days=7)
    assert vault.get_withdrawal_period() == WithdrawalPeriod(datetime.timedelta(days=7), datetime.timedelta(days=7), WithdrawalDelayType.delay)
    assert vault.get_deposit_manager_capability() is None
    assert vault.get_fee_data().performance is None

    timestamp = datetime.datetime.fromtimestamp(web3.eth.get_block(ETHEREUM_MIDNIGHT_BLOCK)["timestamp"], datetime.UTC).replace(tzinfo=None)
    detection = ERC4262VaultDetection(1, TORI_VAULT_ADDRESS, ETHEREUM_MIDNIGHT_BLOCK, timestamp, vault.features, timestamp, 1, 0)
    row = create_vault_scan_record(web3, detection, ETHEREUM_MIDNIGHT_BLOCK, token_cache={})
    assert row["Protocol"] == "Tori Finance"
    assert row["Name"] == "Staked trUSD"
    assert row["NAV"] == Decimal("25191902.324647899163613529")
    assert row["_description"] == vault.description
    assert row["_strategy_tags"] == vault.get_strategy_tags()
    assert row["_withdrawal_period"] == vault.get_withdrawal_period()
    assert row["_deposit_manager"] is None
    assert row["Perf fee"] is None


def test_tori_live_provider_read() -> None:
    """Verify a successful current-state read through the supplied real RPC.

    This minimal external integration check uses the configured provider,
    including authenticated URLs, without printing credentials or transacting.

    :return: ``None`` after checking the canonical asset and cooldown response.
    """
    web3 = create_multi_provider_web3(JSON_RPC_ETHEREUM)
    vault = create_vault_instance_autodetect(web3, TORI_VAULT_ADDRESS)
    block = web3.eth.block_number
    assert vault.vault_contract.functions.asset().call(block_identifier=block).lower() == TORI_ASSET_ADDRESS
    assert datetime.timedelta(0) <= vault.fetch_cooldown_duration(block) <= datetime.timedelta(days=90)
