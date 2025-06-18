"""Enzyme vault policies.

To make your Enzyme vaults safe against rug pulls at least the following policies should be enabled
- Cumulative slippage tolerance (can bleed only 10% a week)
- Vault adapter policy (prevent asset manager to call an arbitrary smart contract with vault assets)

By default, Enzyme vault does not have any adapters set when you create vaults programmatically.
Enzyme frontend has some vault policies by default, but Enzyme frontend is not open source.

"""

import enum
from typing import Iterable
import logging

from eth_abi import encode
from eth_typing import HexAddress
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.enzyme.deployment import EnzymeDeployment, VaultPolicyConfiguration
from eth_defi.enzyme.vault import Vault
from eth_defi.hotwallet import HotWallet
from eth_defi.trace import assert_transaction_success_with_explanation


logger = logging.getLogger(__name__)


class AddressListUpdateType(enum.Enum):
    """What kind of delta operation we do on an address.

    Taken from Enzyme's JS core.
    """

    None_ = 0
    AddOnly = 1
    RemoveOnly = 2
    AddAndRemove = 3


def get_vault_policies(vault: Vault) -> Iterable[Contract]:
    """Get policy contracts enabled on the vault.

    :param vault:
        Enzyme vault

    :return:
        Iterable of enabled policy smart contracts
    """

    web3 = vault.web3
    policy_manager_address = vault.comptroller.functions.getPolicyManager().call()
    policy_manager = get_deployed_contract(web3, "enzyme/PolicyManager.json", policy_manager_address)
    policies = policy_manager.functions.getEnabledPoliciesForFund(vault.comptroller.address).call()
    for policy_address in policies:
        policy = get_deployed_contract(web3, "enzyme/IPolicy.json", policy_address)
        yield policy


def create_safe_default_policy_configuration_for_generic_adapter(
    deployment: EnzymeDeployment,
    generic_adapter: Contract | None = None,
    cumulative_slippage_tolerance: int = 10,
) -> VaultPolicyConfiguration:
    """Create safe policies for a vault.

    - Asset manager cannot steal assets

    An example vault deployment tx by the Enzyme UI:

    - https://polygonscan.com/tx/0xb26ca057152000b4154852ca8823e2b9c86546e770561a9af2924d0fadcb3b1c

    :param deployment:
        Our Enzyme

    :param generic_adapter:
        Lock trading to use only this adapter if given.

    :param cumulative_slippage_tolerance:
        How many percent we can have total slippage per week.
    """

    # Sanity check

    contracts = deployment.contracts

    assert contracts.cumulative_slippage_tolerance_policy is not None
    assert contracts.allowed_adapters_policy is not None
    assert contracts.only_remove_dust_external_position_policy is not None
    assert contracts.only_untrack_dust_or_priceless_assets_policy is not None
    assert contracts.allowed_external_position_types_policy is not None

    assert contracts.cumulative_slippage_tolerance_policy.functions.identifier().call() == "CUMULATIVE_SLIPPAGE_TOLERANCE"
    assert contracts.allowed_adapters_policy.functions.identifier().call() == "ALLOWED_ADAPTERS", f"Got {contracts.allowed_adapters_policy.functions.identifier().call()}"
    assert contracts.only_remove_dust_external_position_policy.functions.identifier().call() == "ONLY_REMOVE_DUST_EXTERNAL_POSITION"
    assert contracts.only_untrack_dust_or_priceless_assets_policy.functions.identifier().call() == "ONLY_UNTRACK_DUST_OR_PRICELESS_ASSETS"
    assert contracts.allowed_external_position_types_policy.functions.identifier().call() == "ALLOWED_EXTERNAL_POSITION_TYPES", f"Got {contracts.allowed_external_position_types_policy.functions.identifier().call()}"

    # Construct vault deployment payload
    ONE_HUNDRED_PERCENT = 10**18  # See CumulativeSlippageTolerancePolicy

    # From AllowedSharesTransferRecipientsPolicy.test.ts
    #
    # addressListRegistryPolicyArgs({
    #     newListsArgs: [
    #       {
    #         initialItems: [],
    #         updateType: AddressListUpdateType.None,
    #       },
    #     ],
    #   }),

    policies = {
        # See CumulativeSlippageTolerancePolicy.addFundSettings
        contracts.cumulative_slippage_tolerance_policy.address: encode(["uint64"], [cumulative_slippage_tolerance * ONE_HUNDRED_PERCENT // 100]),
        # See AddressListRegistryPerUserPolicyBase.addFundSettings
        contracts.only_remove_dust_external_position_policy.address: b"",
        contracts.only_untrack_dust_or_priceless_assets_policy.address: b"",
        contracts.allowed_external_position_types_policy.address: b"",
    }

    # Lock adapter policy to use only our adapter
    if generic_adapter is not None:
        policies.update(
            {
                contracts.allowed_adapters_policy.address: encode_single_address_list_policy_args(generic_adapter.address),
            }
        )

    return VaultPolicyConfiguration(policies)


def encode_single_address_list_policy_args(
    address: HexAddress,
    update_type=AddressListUpdateType.None_,
) -> bytes:
    """How to pass an address list to a fund deployer.

    Needed for AllowedAdaptersPolicy and.

    .. note ::

        Half-baked implementation just to get the deployment going
    """

    # export function addressListRegistryPolicyArgs({
    #   existingListIds = [],
    #   newListsArgs = [],
    # }: {
    #   existingListIds?: BigNumberish[];
    #   newListsArgs?: {
    #     updateType: AddressListUpdateType;
    #     initialItems: AddressLike[];
    #   }[];
    # }) {
    #   return encodeArgs(
    #     ['uint256[]', 'bytes[]'],
    #     [
    #       existingListIds,
    #       newListsArgs.map(({ updateType, initialItems }) =>
    #         encodeArgs(['uint256', 'address[]'], [updateType, initialItems]),
    #       ),
    #     ],
    #   );
    # }

    existing_list_ids = []
    initial_items = [address]
    new_list_args = [encode(["uint256", "address[]"], [update_type.value, initial_items])]
    return encode(["uint256[]", "bytes[]"], [existing_list_ids, new_list_args])


def update_adapter_policy(
    vault: Vault,
    generic_adapter: Contract,
    deployer: HotWallet,
):
    """Set vault to use a new generic adapter.

    - Overwrite the existing AllowedAdapterPolicy configuration

    .. warning::

        Existing adapter policy cannot be changed. Only new set.

    """
    assert isinstance(generic_adapter, Contract)

    web3 = vault.web3
    policy_manager_address = vault.comptroller.functions.getPolicyManager().call()
    contracts = vault.deployment.contracts
    policy_manager = get_deployed_contract(web3, "enzyme/PolicyManager.json", policy_manager_address)

    logger.info(
        "update_adapter_policy(), fund owner is %s, deployer is %s",
        vault.get_owner(),
        deployer.address,
    )

    assert deployer is not None
    assert vault.comptroller
    assert generic_adapter
    assert contracts.allowed_adapters_policy, "AllowedAdaptersPolicy contract address missing in Enzyme configuration"
    assert contracts.allowed_adapters_policy.functions.identifier().call() == "ALLOWED_ADAPTERS", f"Got {contracts.allowed_adapters_policy.functions.identifier().call()}"

    assert vault.get_owner() == deployer.address, "update_adapter_policy(): You can perform this transaction only as a vault owner"

    # tx_hash = policy_manager.functions.disablePolicyForFund(
    #     vault.comptroller.address,
    #     contracts.allowed_adapters_policy.address,
    # ).transact({"from": deployer.address})
    # assert_transaction_success_with_explanation(web3, tx_hash)

    tx_hash = policy_manager.functions.enablePolicyForFund(
        vault.comptroller.address,
        contracts.allowed_adapters_policy.address,
        encode_single_address_list_policy_args(generic_adapter.address),
    ).transact({"from": deployer.address})
    assert_transaction_success_with_explanation(web3, tx_hash)
