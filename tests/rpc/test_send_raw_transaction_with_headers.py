import pytest


@pytest.fixture
def hot_wallet(
    web3,
    usdc,
    usdc_supply_amount,
    faucet,
) -> HotWallet:
    """Hotwallet account."""
    hw = HotWallet(Account.create())
    hw.sync_nonce(web3)

    # give hot wallet some native token
    web3.eth.send_transaction(
        {
            "from": web3.eth.accounts[9],
            "to": hw.address,
            "value": 1 * 10**18,
        }
    )

    # and USDC
    tx_hash = faucet.functions.mint(usdc.address, hw.address, usdc_supply_amount).transact()
    assert_transaction_success_with_explanation(web3, tx_hash)

    return hw


def test_send_raw_transaction_with_headers(web3: Web3, large_busd_holder: HexAddress, user_1):
    """Test custom send_raw_transaction_with_response"""

    user = Account.create()
    hot_wallet = HotWallet(user)
    hot_wallet.sync_nonce(web3)

    busd_details = fetch_erc20_details(web3, "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56")
    busd = busd_details.contract

    # Transfer 1 BUSD to the user 1
    tx_payload = busd.functions.transfer(user_1.address, 1 * 10**18).build_transaction({"from": large_busd_holder})
    signed = hot_wallet.sign_transaction_with_new_nonce(tx_payload)
    tx_hash, response = web3.eth.send_raw_transaction(signed.rawTransaction)
