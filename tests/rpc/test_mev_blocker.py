"""Test MEV blocker provider switching."""
import pytest
from web3 import HTTPProvider, Web3

from eth_defi.provider.anvil import launch_anvil, AnvilLaunch
from eth_defi.provider.mev_blocker import MEVBlockerProvider

from eth_defi.hotwallet import HotWallet
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.uniswap_v2.utils import ZERO_ADDRESS


@pytest.fixture(scope="module")
def anvil() -> AnvilLaunch:
    """Launch Anvil for the test backend."""
    anvil = launch_anvil()
    try:
        yield anvil
    finally:
        anvil.close()


@pytest.fixture()
def mev_blocker_provider(anvil: AnvilLaunch) -> MEVBlockerProvider:
    provider = MEVBlockerProvider(
        call_provider=HTTPProvider(anvil.json_rpc_url),
        transact_provider=HTTPProvider(anvil.json_rpc_url),
    )
    return provider


def test_mev_blocker_call(mev_blocker_provider: MEVBlockerProvider):
    """Read only methods route through the call provider"""
    web3 = Web3(mev_blocker_provider)
    block_number = web3.eth.block_number
    assert block_number == 0
    assert mev_blocker_provider.provider_counter["call"] == 1
    assert mev_blocker_provider.provider_counter["transact"] == 0


def test_mev_blocker_send_transaction(mev_blocker_provider: MEVBlockerProvider):
    """eth_sendTransaction goes through the MEV blocker"""
    web3 = Web3(mev_blocker_provider)
    account = web3.eth.accounts[0]
    assert mev_blocker_provider.provider_counter["call"] == 1
    assert mev_blocker_provider.provider_counter["transact"] == 0
    tx_hash = web3.eth.send_transaction({"to": ZERO_ADDRESS, "from": account, "value": 1})
    assert_transaction_success_with_explanation(web3, tx_hash)
    assert mev_blocker_provider.provider_counter["call"] == 8  # Account for various gas cost methods
    assert mev_blocker_provider.provider_counter["transact"] == 1


def test_mev_blocker_send_transaction_raw(mev_blocker_provider: MEVBlockerProvider):
    """eth_sendTransactionRaw goes through the MEV blocker"""

    web3 = Web3(mev_blocker_provider)
    wallet = HotWallet.create_for_testing(web3)

    signed_tx = wallet.sign_transaction_with_new_nonce(
        {
            "from": wallet.address,
            "to": ZERO_ADDRESS,
            "value": 1,
            "gas": 100_000,
            "gasPrice": web3.eth.gas_price,
        }
    )

    # Account for setup API counts from create_for_testing()
    assert mev_blocker_provider.provider_counter["call"] == 10
    assert mev_blocker_provider.provider_counter["transact"] == 1
    tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)
    assert mev_blocker_provider.provider_counter["call"] == 11
    assert mev_blocker_provider.provider_counter["transact"] == 2
