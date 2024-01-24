"""Enzyme vault policies.

To make your Enzyme vaults safe against rug pulls at least the following policies should be enabled
- Cumulative slippage tolerance (can bleed only 10% a week)
- Vault adapter policy (prevent asset manager to call an arbitrary smart contract with vault assets)

By default, Enzyme vault does not have any adapters set.
"""
from typing import Iterable

from eth_abi import encode
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.enzyme.deployment import EnzymeDeployment, VaultPolicyConfiguration
from eth_defi.enzyme.vault import Vault


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
    generic_adapter: Contract,
    cumulative_slippage_tolerance=10,
) -> VaultPolicyConfiguration:
    """.asdf

    An example vault deployment tx by the Enzyme UI:

    - https://polygonscan.com/tx/0xb26ca057152000b4154852ca8823e2b9c86546e770561a9af2924d0fadcb3b1c
    """

    # Sanity check

    contracts = deployment.contracts

    assert contracts.cumulative_slippage_tolerance_policy is not None
    assert contracts.allowed_adapters_policy is not None
    assert contracts.only_remove_dust_external_position_policy is not None
    assert contracts.only_untrack_dust_or_priceless_assets_policy is not None
    assert contracts.allowed_external_position_types is not None

    assert contracts.cumulative_slippage_tolerance_policy.functions.identifier().call() == "CUMULATIVE_SLIPPAGE_TOLERANCE"
    assert contracts.allowed_adapters_policy.functions.identifier().call() == "ALLOWED_ADAPTERS_POLICY"
    assert contracts.only_remove_dust_external_position_policy.functions.identifier().call() == "ONLY_REMOVE_DUST_EXTERNAL_POSITION_POLICY"
    assert contracts.only_untrack_dust_or_priceless_assets_policy.functions.identifier().call() == "ONLY_UNTRACK_DUST_OR_PRICELESS_ASSETS_POLICY"
    assert contracts.allowed_external_position_types.functions.identifier().call() == "ALLOWED_EXTERNAL_POLICY_TYPES"

    # Construct vault deployment payload
    ONE_HUNDRED_PERCENT = 10**18  # See CumulativeSlippageTolerancePolicy

    policies = {
        # See CumulativeSlippageTolerancePolicy.addFundSettings
        contracts.cumulative_slippage_tolerance_policy.address: encode(["uint64"], [int(cumulative_slippage_tolerance * ONE_HUNDRED_PERCENT)]),
        # See AddressListRegistryPerUserPolicyBase.addFundSettings
        contracts.allowed_adapters_policy.address: encode(["address", "bytes"] , [generic_adapter.address, b""]),
        # See AddressListRegistryPerUserPolicyBase.addFundSettings
        contracts.only_remove_dust_external_position_policy.address: b"",
        contracts.only_untrack_dust_or_priceless_assets_policy.address: b"",
        contracts.allowed_external_position_types.address: b"",
    }

    return VaultPolicyConfiguration(policies)
