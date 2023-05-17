"""USDC deployment management.

Manage USDC and other Center tokens

- Unit test deployment

- Live deployment on-chains

"""
from eth_defi.deploy import deploy_contract, ContractDeploymentFailed
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_typing import ChecksumAddress
from web3 import Web3

from eth_defi.trace import assert_transaction_success_with_explanation


def deploy_fiat_token(
    web3: Web3,
    deployer: ChecksumAddress,
    mint_amount=1_000_000,
    contract="centre/FiatTokenV2_1.json",
    token_name="USD Coin",
    token_symbol="USDC",
    token_currency="USD",
    decimals=6,
) -> TokenDetails:
    """Deploy USDC fiat token to be used in testing.

    :param mint_amount:
        Number of tokens to mint.

    """
    try:
        token_contract = deploy_contract(web3, contract, deployer)
    except ContractDeploymentFailed as e:
        assert_transaction_success_with_explanation(web3, e.tx_hash)

    # v1 init
    tx_hash = token_contract.functions.initialize(token_name, token_symbol, token_currency, decimals, deployer, deployer, deployer, deployer).transact(
        {
            "from": deployer,
        }
    )
    assert_transaction_success_with_explanation(web3, tx_hash)

    tx_hash = token_contract.functions.initializeV2(
        token_name,
    ).transact(
        {
            "from": deployer,
        }
    )
    assert_transaction_success_with_explanation(web3, tx_hash)

    tx_hash = token_contract.functions.initializeV2_1(
        deployer,
    ).transact(
        {
            "from": deployer,
        }
    )
    assert_transaction_success_with_explanation(web3, tx_hash)

    tx_hash = token_contract.functions.configureMinter(
        deployer,
        mint_amount * 10**decimals,
    ).transact(
        {
            "from": deployer,
        }
    )
    assert_transaction_success_with_explanation(web3, tx_hash)

    tx_hash = token_contract.functions.mint(
        deployer,
        mint_amount * 10**decimals,
    ).transact(
        {
            "from": deployer,
        }
    )
    assert_transaction_success_with_explanation(web3, tx_hash)

    token = fetch_erc20_details(web3, token_contract.address, contract_name=contract)

    return token
