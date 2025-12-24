"""Enzyme vault ERC-20 helpers."""

from eth_typing import HexAddress
from web3.contract import Contract
from web3.contract.contract import ContractFunction

from eth_defi.abi import encode_function_call
from eth_defi.enzyme.deployment import EnzymeDeployment
from eth_defi.enzyme.generic_adapter import execute_calls_for_generic_adapter
from eth_defi.enzyme.vault import Vault


# fmt: off
def prepare_transfer(
    enzyme: EnzymeDeployment,
    vault: Vault,
    generic_adapter: Contract,
    token: Contract,
    receiver: HexAddress | str,
    amount: int
) -> ContractFunction:
    """Prepare an ERC-20 transfer out from the Enzyme vault.

    - Tells the Enzyme vault to move away some tokes

    - Should be blocked by GuardV0, only useable by governance

    :param enzyme:
        Enzyme deploymeent

    :param vault:
        Vault that needs to perform the swap

    :param generic_adapter:
        GenericAdapter contract we use for swaps

    :param token:
        ERC-20 token we send

    :param receiver:
        The receiver of tokens

    :param amount:
        Token amount, raw

    :return:
        Transaction object that can be signed and executed
    """

    # Prepare the swap parameters
    spend_asset_amounts = [amount]
    spend_assets = [token.address]
    incoming_assets = []
    min_incoming_assets_amounts = []

    # The vault performs a swap on Uniswap v2
    encoded_transfer = encode_function_call(token.functions.transfer, [receiver, amount])

    bound_call = execute_calls_for_generic_adapter(
        comptroller=vault.comptroller,
        external_calls=((token, encoded_transfer),),
        generic_adapter=generic_adapter,
        incoming_assets=incoming_assets,
        integration_manager=enzyme.contracts.integration_manager,
        min_incoming_asset_amounts=min_incoming_assets_amounts,
        spend_asset_amounts=spend_asset_amounts,
        spend_assets=spend_assets,
    )

    return bound_call


def prepare_approve(
    enzyme: EnzymeDeployment,
    vault: Vault,
    generic_adapter: Contract,
    token: Contract,
    receiver: HexAddress | str,
    amount: int,
) -> ContractFunction:
    """Prepare an ERC-20 approve() out from the Enzyme vault.

    - Tells the Enzyme vault to move away some tokes

    - Should be blocked by GuardV0, only useable by governance

    :param enzyme:
        Enzyme deploymeent

    :param vault:
        Vault that needs to perform the swap

    :param generic_adapter:
        GenericAdapter contract we use for swaps

    :param token:
        ERC-20 token we send

    :param receiver:
        The receiver of tokens

    :param amount:
        Token amount, raw

    :return:
        Transaction object that can be signed and executed
    """

    # Prepare the swap parameters
    spend_asset_amounts = [amount]
    spend_assets = [token.address]
    incoming_assets = []
    min_incoming_assets_amounts = []

    # The vault performs a swap on Uniswap v2
    encoded_transfer = encode_function_call(token.functions.approve, [receiver, amount])

    bound_call = execute_calls_for_generic_adapter(
        comptroller=vault.comptroller,
        external_calls=((token, encoded_transfer),),
        generic_adapter=generic_adapter,
        incoming_assets=incoming_assets,
        integration_manager=enzyme.contracts.integration_manager,
        min_incoming_asset_amounts=min_incoming_assets_amounts,
        spend_asset_amounts=spend_asset_amounts,
        spend_assets=spend_assets,
    )

    return bound_call


def prepare_transfer_sneaky(
    enzyme: EnzymeDeployment,
    vault: Vault,
    generic_adapter: Contract,
    token: Contract,
    receiver: HexAddress | str,
    amount: int
) -> ContractFunction:
    """Prepare an ERC-20 transfer out from the Enzyme vault.

    - Tells the Enzyme vault to move away some tokes

    - Should be blocked by GuardV0, only useable by governance

    :param enzyme:
        Enzyme deploymeent

    :param vault:
        Vault that needs to perform the swap

    :param generic_adapter:
        GenericAdapter contract we use for swaps

    :param token:
        ERC-20 token we send

    :param receiver:
        The receiver of tokens

    :param amount:
        Token amount, raw

    :return:
        Transaction object that can be signed and executed
    """

    spent = int(amount * 0.05)

    # Prepare the swap parameters
    spend_asset_amounts = [amount]
    spend_assets = [token.address]
    incoming_assets = [token.address]
    min_incoming_assets_amounts = [amount - spent - 10_000]

    # The vault performs a swap on Uniswap v2
    encoded_transfer = encode_function_call(token.functions.transfer, [receiver, spent])

    bound_call = execute_calls_for_generic_adapter(
        comptroller=vault.comptroller,
        external_calls=((token, encoded_transfer),),
        generic_adapter=generic_adapter,
        incoming_assets=incoming_assets,
        integration_manager=enzyme.contracts.integration_manager,
        min_incoming_asset_amounts=min_incoming_assets_amounts,
        spend_asset_amounts=spend_asset_amounts,
        spend_assets=spend_assets,
    )

    return bound_call
