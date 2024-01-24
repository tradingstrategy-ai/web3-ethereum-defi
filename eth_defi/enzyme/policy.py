"""Enzyme vault policies.

To make your Enzyme vaults safe against rug pulls at least the following policies should be enabled
- Cumulative slippage tolerance (can bleed only 10% a week)
- Vault adapter policy (prevent asset manager to call an arbitrary smart contract with vault assets)

By default, Enzyme vault does not have any adapters set.
"""
from typing import Iterable

from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
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


def configure_default_safe_policies(vault: Vault):
    pass

