"""Enzyme generic adapter helpers.

- GenericAdapter is a vault adapter that allow the vault to perform any transaction

- GenericAdapter is not yet part of standard Enzyme distribution (not audited)

- Transactions are send in ABI encoded bundles

- Bundle can contain one or more smart contract calls like `approve()` and then `swapExactTokensForTokens()`

See :py:func:`eth_defi.enzyme.uniswap_v2.prepare_swap` how to use the generic adapter to make trades.

- Tokens are hold in `Vault` smart contract by default. They are transferred to GenericAdapter with
  `postActionIncomingAssetsTransferHandler` and `postActionSpendAssetsTransferHandler` function
  modifiers on `executeCalls()`

- Any `allowance()` is set on GenericAdapter address

.. warning::

    GenericAdapter is unaudited and dangerous. Do not use in production yet.

    For example, anyone can steal approve()'ed tokens in the example implementation if not handled correctly.

`See the GenericAdapter source code on Github <https://github.com/tradingstrategy-ai/ethdubai-2023-hackathon/blob/master/forge/src/SushiAdapter.sol>`__.

"""

import logging
from typing import TypeAlias, Collection, Tuple, Final

from eth_abi import encode
from eth_abi.exceptions import EncodingError
from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.contract.contract import Contract, ContractFunction


from eth_defi.enzyme.integration_manager import IntegrationManagerActionId

ExternalCall: TypeAlias = Tuple[Contract, bytes]

Asset: TypeAlias = Contract | HexAddress

Signer: TypeAlias = HexAddress

#: See IntegrationManager.sol
EXECUTE_CALLS_SELECTOR: Final[str] = Web3.keccak(b"executeCalls(address,bytes,bytes)")[0:4]


logger = logging.getLogger(__name__)


def _addressify(asset: Contract | HexAddress | str):
    assert isinstance(asset, Contract) or type(asset) == HexAddress or type(asset) == str, f"Got bad asset: {asset}"
    if isinstance(asset, Contract):
        return asset.address
    return asset


def _addressify_collection(assets: Collection[Contract | HexAddress]):
    return [_addressify(a) for a in assets]


def encode_generic_adapter_execute_calls_args(incoming_assets: Collection[Asset], min_incoming_asset_amounts: Collection[int], spend_assets: Collection[Asset], spend_asset_amounts: Collection[int], external_calls: Collection[ExternalCall]):
    """Encode arguments for a generic adapter call."""

    #   const encodedExternalCallsData = encodeArgs(
    #     ['address[]', 'bytes[]'],
    #     [externalCallsData.contracts, externalCallsData.callsData],
    #   );

    addresses = [_addressify(t[0]) for t in external_calls]
    datas = [t[1] for t in external_calls]

    try:
        encoded_external_calls_data = encode(["address[]", "bytes[]"], [addresses, datas])
    except EncodingError as e:
        raise EncodingError(f"Could not encode: {addresses} {datas}") from e

    #   return encodeArgs(
    #     ['address[]', 'uint256[]', 'address[]', 'uint256[]', 'bytes'],
    #     [incomingAssets, minIncomingAssetAmounts, spendAssets, spendAssetAmounts, encodedExternalCallsData],
    #   );

    all_args_encoded = encode(
        ["address[]", "uint256[]", "address[]", "uint256[]", "bytes"],
        [_addressify_collection(incoming_assets), min_incoming_asset_amounts, _addressify_collection(spend_assets), spend_asset_amounts, encoded_external_calls_data],
    )

    return all_args_encoded


# export function callOnIntegrationArgs({
#   adapter,
#   selector,
#   encodedCallArgs,
# }: {
#   adapter: AddressLike;
#   selector: BytesLike;
#   encodedCallArgs: BytesLike;
# }) {
#   return encodeArgs(['address', 'bytes4', 'bytes'], [adapter, selector, encodedCallArgs]);
# }


def encode_call_on_integration_args(
    adapter: Contract,
    selector: bytes,
    encoded_call_args: bytes | HexBytes,
):
    """No idea yet."""

    assert type(selector) in (bytes, HexBytes)
    assert type(encoded_call_args) in (bytes, HexBytes), f"encoded_call_args is {encoded_call_args} {type(encoded_call_args)}"
    assert len(selector) == 4, f"Selector is {selector} {type(selector)}"
    assert len(encoded_call_args) > 0

    return encode(["address", "bytes4", "bytes"], [_addressify(adapter), selector, encoded_call_args])


def execute_calls_for_generic_adapter(
    comptroller: Contract,
    external_calls: Collection[ExternalCall],
    generic_adapter: Contract,
    integration_manager: Contract,
    incoming_assets: Collection[Asset],
    min_incoming_asset_amounts: Collection[int],
    spend_assets: Collection[Asset],
    spend_asset_amounts: Collection[int],
) -> ContractFunction:
    """Create a vault buy/sell transaction using a generic adapter.

    :return:
        A contract function object with bound arguments
    """

    logger.info("execute_calls_for_generic_adapter(): %s %s %s %s %s %s %s %s", comptroller, external_calls, generic_adapter, integration_manager, incoming_assets, min_incoming_asset_amounts, spend_assets, spend_asset_amounts)

    # Sanity checks
    assert isinstance(comptroller, Contract)
    assert len(external_calls) > 0
    assert isinstance(generic_adapter, Contract)
    # assert len(incoming_assets) > 0
    assert isinstance(integration_manager, Contract)
    # assert len(min_incoming_asset_amounts) > 0
    # assert len(spend_asset_amounts) > 0
    # assert len(spend_assets) > 0

    execute_call_args = encode_generic_adapter_execute_calls_args(
        incoming_assets=incoming_assets,
        min_incoming_asset_amounts=min_incoming_asset_amounts,
        spend_assets=spend_assets,
        spend_asset_amounts=spend_asset_amounts,
        external_calls=external_calls,
    )

    call_args = encode_call_on_integration_args(
        generic_adapter,
        EXECUTE_CALLS_SELECTOR,
        execute_call_args,
    )

    # See ComptrollerLib.sol
    call = comptroller.functions.callOnExtension(integration_manager.address, IntegrationManagerActionId.CallOnIntegration.value, call_args)

    return call
