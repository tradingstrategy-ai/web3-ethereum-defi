"""Lagoon Base mainnet fork swap test.

- View Safe here https://app.safe.global/home?safe=base:0x20415f3Ec0FEA974548184bdD6e67575D128953F
"""
import pytest
from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3

from eth_defi.abi import get_abi_by_filename, encode_function_call
from eth_defi.lagoon.vault import LagoonVault
from eth_defi.token import TokenDetails
from eth_defi.uniswap_v2.constants import UNISWAP_V2_DEPLOYMENTS
from eth_defi.uniswap_v2.deployment import fetch_deployment
from eth_defi.uniswap_v2.swap import swap_with_slippage_protection


@pytest.fixture()
def uniswap_v2(web3):
    return fetch_deployment(
        web3,
        factory_address=UNISWAP_V2_DEPLOYMENTS["base"]["factory"],
        router_address=UNISWAP_V2_DEPLOYMENTS["base"]["router"],
        init_code_hash=UNISWAP_V2_DEPLOYMENTS["base"]["init_code_hash"],
    )


def test_lagoon_swap(
    web3: Web3,
    uniswap_v2,
    lagoon_vault: LagoonVault,
    base_weth: TokenDetails,
    base_usdc: TokenDetails,
    asset_manager: HexAddress,
):
    """Perform a basic swap.

    - The vault has whitelisted Uniswap router

    - Perform a swap USDC -> WETH

    - For starting balances see test_lagoon_fetch_portfolio
    """
    vault = lagoon_vault

    amount = int(0.5 * 10**16)  # Half a dollar

    # Creat a bound contract function instance
    # that presents Uniswap swap from the vault
    swap_call = swap_with_slippage_protection(
        uniswap_v2,
        recipient_address=lagoon_vault.safe_address,
        base_token=base_weth.contract,
        quote_token=base_usdc.contract,
        amount_in=amount,
    )

    contract_address = swap_call.address
    data_payload = encode_function_call(swap_call, swap_call.arguments)

    # TODO: Zero padding in the address?
    data = HexBytes(contract_address) + data_payload

    roled_tx = vault.transact_through_module(data)








