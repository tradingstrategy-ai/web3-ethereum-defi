"""Regression tests for Accountable historical redemption settlement markers."""

import datetime
import os

import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.accountable.settlement import (
    ACCOUNTABLE_PROTOCOL_NAME,
    fetch_accountable_settlements,
    get_accountable_settlement_events_by_topic,
)
from eth_defi.erc_4626.vault_protocol.accountable.vault import AccountableVault
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_MONAD = os.environ.get("JSON_RPC_MONAD")
SUSN_VAULT = "0x58ba69b289De313E66A13B7D1F822Fc98b970554"
BATCHED_SETTLEMENT_BLOCK = 85_323_091
BATCHED_SETTLEMENT_TX = "0x1df1ce4350e22db66f59424327c1078759f5b62915bb71bba85a78db33bc40ae"

pytestmark = pytest.mark.skipif(JSON_RPC_MONAD is None, reason="JSON_RPC_MONAD needed to run these tests")


@pytest.fixture(scope="module")
def web3() -> Web3:
    """Connect directly to Monad because historical Anvil forks are unavailable.

    :return:
        Configured Monad JSON-RPC client.
    """
    return create_multi_provider_web3(JSON_RPC_MONAD)


@pytest.fixture(scope="module")
def vault(web3: Web3) -> AccountableVault:
    """Open the known sUSN Accountable vault.

    :param web3:
        Monad JSON-RPC client.
    :return:
        Accountable sUSN vault adapter.
    """
    vault = create_vault_instance_autodetect(web3, SUSN_VAULT)
    assert isinstance(vault, AccountableVault)
    return vault


@pytest.mark.timeout(180)
def test_accountable_batched_claimability_is_one_settlement_marker(vault: AccountableVault) -> None:
    """Collapse four historical claimability logs into one generic transaction marker.

    :param vault:
        Known Accountable sUSN vault.
    """
    event_by_topic = get_accountable_settlement_events_by_topic(vault)
    assert set(event_by_topic) == {"0x4dd5187225a2ae5f5ea35ca7b1732180f848cc4b6f7dce34b4c5e9f384d77dec"}

    settlements = fetch_accountable_settlements(
        vault,
        BATCHED_SETTLEMENT_BLOCK,
        BATCHED_SETTLEMENT_BLOCK,
        use_hypersync=False,
        chunk_size=1,
    )

    assert len(settlements) == 1
    settlement = settlements[0]
    assert settlement.chain_id == 143
    assert settlement.address.lower() == SUSN_VAULT.lower()
    assert settlement.block_number == BATCHED_SETTLEMENT_BLOCK
    assert settlement.tx_hash == BATCHED_SETTLEMENT_TX
    assert settlement.protocol == ACCOUNTABLE_PROTOCOL_NAME
    assert settlement.event_name == "RedeemClaimable"
    assert settlement.timestamp == datetime.datetime(2026, 7, 3, 11, 54, 16)
