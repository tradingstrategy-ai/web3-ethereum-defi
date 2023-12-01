from eth_defi.trace import assert_transaction_success_with_explanation


def _execute_tx(web3, hot_wallet, fn, gas=350_000):
    tx = fn.build_transaction({"from": hot_wallet.address, "gas": gas})
    signed = hot_wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)


def _print_current_balances(logger, address, usdc, weth, ausdc, vweth, wmatic=None, vwmatic=None):
    output = f"""
    ------------------------
    Current balance:
        USDC: {usdc.contract.functions.balanceOf(address).call() / 1e6}
        aUSDC: {ausdc.contract.functions.balanceOf(address).call() / 1e6}
        WETH: {weth.contract.functions.balanceOf(address).call() / 1e18}
        vWETH: {vweth.contract.functions.balanceOf(address).call() / 1e18}
    """

    if wmatic and vwmatic:
        output += f"""    WMATIC: {wmatic.contract.functions.balanceOf(address).call() / 1e18}
        vWMATIC: {vwmatic.contract.functions.balanceOf(address).call() / 1e18}
    """

    output += "------------------------\n\n"

    logger.info(output)
