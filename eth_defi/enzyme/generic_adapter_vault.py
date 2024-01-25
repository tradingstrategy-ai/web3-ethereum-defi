"""Safe deployment of Enzyme vaults with generic adapter. """
import logging

from eth_typing import HexAddress
from web3.contract import Contract

from eth_defi.deploy import deploy_contract
from eth_defi.enzyme.deployment import EnzymeDeployment
from eth_defi.enzyme.policy import create_safe_default_policy_configuration_for_generic_adapter
from eth_defi.enzyme.vault import Vault
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.uniswap_v2.utils import ZERO_ADDRESS

logger = logging.getLogger(__name__)


def deploy_generic_adapter_vault(
    deployment: EnzymeDeployment,
    deployer: HexAddress,
    asset_manager: HexAddress,
    owner: HexAddress,
    usdc: Contract,
    terms_of_service: Contract,
) -> Vault:
    """Deploy an Enzyme vault and make it secure.

    Deploys an Enzyme vault in a specific way we want to have it deployed.

    - Set up default policies

    - Assign a generic adapter

    - Assign a USDC payment forwarder with terms of service sign up

    - Assign asset manager role and transfer ownership

    :param deployment:
        Enzyme deployment we use.

    :param deployer:
        Web3.py deployer account we use.

    :param asset_manager:
        Give trading access to this hot wallet address.

    :param terms_of_service:
        Terms of service contract we use.

    :param owner:
        Nominated new owner.

        Immediately transfer vault ownership from a deployer to a multisig owner.
        Multisig needs to confirm this by calling `claimOwnership`.

    :return:
        Freshly deployed vault
    """

    logger.info(
        "Deploying Enzyme vault. Enzyme fund deployer is %s, Terms of service is %s, USDC is %s",
        deployment.contracts.fund_deployer.address,
        terms_of_service.address,
        usdc.address,
    )

    web3 = deployment.web3

    guard = deploy_contract(
        web3,
        f"guard/GuardV0.json",
        deployer,
    )
    assert guard.functions.getInternalVersion().call() == 1

    generic_adapter = deploy_contract(
        web3,
        f"GuardedGenericAdapter.json",
        deployer,
        deployment.contracts.integration_manager.address,
        guard.address,
    )

    policy_configuration = create_safe_default_policy_configuration_for_generic_adapter(
        deployment,
        generic_adapter,
    )

    comptroller, vault = deployment.create_new_vault(
        deployer,
        usdc,
        policy_configuration=policy_configuration,
    )

    assert comptroller.functions.getDenominationAsset().call() == usdc.address
    assert vault.functions.getTrackedAssets().call() == [usdc.address]

    # asset manager role is the trade executor
    vault.functions.addAssetManagers([asset_manager]).transact({"from": deployer})

    payment_forwarder = deploy_contract(
        web3,
        "TermedVaultUSDCPaymentForwarder.json",
        deployer,
        usdc.address,
        comptroller.address,
        terms_of_service.address,
    )

    # When swap is performed, the tokens will land on the integration contract
    # and this contract must be listed as the receiver.
    # Enzyme will then internally move tokens to its vault from here.
    guard.functions.allowReceiver(generic_adapter.address, "").transact({"from": deployer})

    # Because Enzyme does not pass the asset manager address to through integration manager,
    # we set the vault address itself as asset manager for the guard
    tx_hash = guard.functions.allowSender(vault.address, "").transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Give generic adapter back reference to the vault
    assert vault.functions.getCreator().call() != ZERO_ADDRESS, f"Bad vault creator {vault.functions.getCreator().call()}"
    tx_hash = generic_adapter.functions.bindVault(vault.address).transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, tx_hash)

    receipt = web3.eth.get_transaction_receipt(tx_hash)
    deployed_at_block = receipt["blockNumber"]

    assert generic_adapter.functions.getIntegrationManager().call() == deployment.contracts.integration_manager.address
    assert comptroller.functions.getDenominationAsset().call() == usdc.address
    assert vault.functions.getTrackedAssets().call() == [usdc.address]
    assert vault.functions.canManageAssets(asset_manager).call()
    assert guard.functions.isAllowedSender(vault.address).call()  # vault = asset manager for the guard

    # We cannot directly transfer the ownership to a multisig,
    # but we can set nominated ownership pending
    if owner != deployer:
        vault.functions.setNominatedOwner(owner).transact({"from": deployer})

    vault = Vault.fetch(
        web3,
        vault_address=vault.address,
        payment_forwarder=payment_forwarder.address,
        generic_adapter_address=generic_adapter.address,
        deployed_at_block=deployed_at_block,
    )
    assert vault.guard_contract.address == guard.address

    logger.info(
        "Deployed. Vault is %s, owner is %s, asset manager is %s",
        vault.vault.address,
        vault.get_owner(),
        asset_manager,
    )

    return vault
