"""Safe deployment of Enzyme vaults with generic adapter. """
import logging
import os
from pathlib import Path
from typing import Collection

from eth_typing import HexAddress
from web3.contract import Contract

from eth_defi.enzyme.deployment import EnzymeDeployment
from eth_defi.enzyme.policy import create_safe_default_policy_configuration_for_generic_adapter
from eth_defi.enzyme.vault import Vault
from eth_defi.foundry.forge import deploy_contract_with_forge
from eth_defi.hotwallet import HotWallet
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.uniswap_v2.constants import UNISWAP_V2_DEPLOYMENTS
from eth_defi.uniswap_v2.utils import ZERO_ADDRESS
from eth_defi.uniswap_v3.constants import UNISWAP_V3_DEPLOYMENTS

logger = logging.getLogger(__name__)


CONTRACTS_ROOT = Path(os.path.dirname(__file__)) / ".." / ".." / "contracts"


def deploy_vault_with_generic_adapter(
    deployment: EnzymeDeployment,
    deployer: HotWallet,
    asset_manager: HexAddress | str,
    owner: HexAddress | str,
    denomination_asset: Contract,
    terms_of_service: Contract | None,
    fund_name="Example Fund",
    fund_symbol="EXAMPLE",
    whitelisted_assets: Collection[TokenDetails] | None = None,
    etherscan_api_key: str | None = None,
    production=False,
    meta: str = "",
    uniswap_v2=True,
    uniswap_v3=True,
    mock_guard=False,
) -> Vault:
    """Deploy an Enzyme vault and make it secure.

    Deploys an Enzyme vault in a specific way we want to have it deployed.

    - Because we want multiple deployed smart contracts to be verified on Etherscan,
      this deployed uses a Forge-based toolchain and thus the script
      can be only run from the git checkout where submodules are included.

    - Set up default policies

    - Assign a generic adapter

    - Assign a USDC payment forwarder with terms of service sign up

    - Assign asset manager role and transfer ownership

    - Whitelist USDC and the other given assets

    - Whitelist Uniswap v2 and v3 spot routers

    .. note ::

        The GuardV0 ownership is **not** transferred to the owner at the end of the deployment.
        You need to do it manually after configuring the guard.

    :param deployment:
        Enzyme deployment we use.

    :param deployer:
        Web3.py deployer account we use.

    :param asset_manager:
        Give trading access to this hot wallet address.

        Set to the deployer address to ignore.

    :param terms_of_service:
        Terms of service contract we use.

    :param owner:
        Nominated new owner.

        Immediately transfer vault ownership from a deployer to a multisig owner.
        Multisig needs to confirm this by calling `claimOwnership`.

        Set to the deployer address to ignore.

    :param whitelisted_assets:
        Whitelist these assets on Uniswap v2 and v3 spot market.

        USDC is always whitelisted.

    :param denomination_asset:
        USDC token used as the vault denomination currency.

    :param etherscan_api_key:
        Needed to verify deployed contracts.

    :param production:
        Production flag set on `GuardedGenericAdapterDeployed` event.

    :param meta:
        Metadata for `GuardedGenericAdapterDeployed` event.

    :param uniswap_v2:
        Whiteliste Uniswap v2 trading

    :param uniswap_v3:
        Whiteliste Uniswap v3 trading

    :param mock_guard:
        Deploy unit test mock of the guard

    :return:
        Freshly deployed vault
    """

    assert isinstance(deployer, HotWallet), f"Got {type(deployer)}"
    assert asset_manager.startswith("0x")
    assert owner.startswith("0x")

    assert CONTRACTS_ROOT.exists(), f"Cannot find contracts folder {CONTRACTS_ROOT.resolve()} - are you runnign from git checkout?"

    whitelisted_assets = whitelisted_assets or []
    for asset in whitelisted_assets:
        assert isinstance(asset, TokenDetails)

    # Log EtherScan API key
    # Nothing bad can be done with this key, but good diagnostics is more important
    logger.info(
        "Deploying Enzyme vault. Enzyme fund deployer: %s, Terms of service: %s, USDC: %s, Etherscan API key: %s",
        deployment.contracts.fund_deployer.address,
        terms_of_service.address if terms_of_service is not None else "-",
        denomination_asset.address,
        etherscan_api_key,
    )

    web3 = deployment.web3

    if not mock_guard:
        guard, tx_hash = deploy_contract_with_forge(
            web3,
            CONTRACTS_ROOT / "guard",
            "GuardV0.sol",
            f"GuardV0",
            deployer,
            etherscan_api_key=etherscan_api_key,
        )
        logger.info("GuardV0 is %s deployed at %s", guard.address, tx_hash.hex())
        assert guard.functions.getInternalVersion().call() == 1
    else:
        # Unit testing path
        guard, tx_hash = deploy_contract_with_forge(
            web3,
            CONTRACTS_ROOT / "guard",
            "MockGuard.sol",
            f"MockGuard",
            deployer,
            etherscan_api_key=etherscan_api_key,
        )
        logger.info("MockGuard is %s deployed at %s", guard.address, tx_hash.hex())

    # generic_adapter = deploy_contract(
    #     web3,
    #     f"GuardedGenericAdapter.json",
    #     deployer,
    #     deployment.contracts.integration_manager.address,
    #     guard.address,
    # )

    generic_adapter, tx_hash = deploy_contract_with_forge(
        web3,
        CONTRACTS_ROOT / "in-house",
        "GuardedGenericAdapter.sol",
        "GuardedGenericAdapter",
        deployer,
        [deployment.contracts.integration_manager.address, guard.address],
        etherscan_api_key=etherscan_api_key,
    )
    logger.info("GuardedGenericAdapter is %s deployed at %s", generic_adapter.address, tx_hash.hex())

    if deployment.contracts.cumulative_slippage_tolerance_policy is not None:
        policy_configuration = create_safe_default_policy_configuration_for_generic_adapter(
            deployment,
            generic_adapter,
        )
    else:
        # Legacy + unit test
        policy_configuration = None

    comptroller, vault = deployment.create_new_vault(
        deployer.address,
        denomination_asset,
        policy_configuration=policy_configuration,
        fund_name=fund_name,
        fund_symbol=fund_symbol,
    )

    assert comptroller.functions.getDenominationAsset().call() == denomination_asset.address
    assert vault.functions.getTrackedAssets().call() == [denomination_asset.address]

    # asset manager role is the trade executor
    if asset_manager != deployer.address:
        tx_hash = vault.functions.addAssetManagers([asset_manager]).transact({"from": deployer.address})
        assert_transaction_success_with_explanation(web3, tx_hash)

    # Need to resync the nonce, because it was used outside HotWallet
    deployer.sync_nonce(web3)

    if terms_of_service is not None:
        payment_forwarder, tx_hash = deploy_contract_with_forge(
            web3,
            CONTRACTS_ROOT / "in-house",
            "TermedVaultUSDCPaymentForwarder.sol",
            "TermedVaultUSDCPaymentForwarder",
            deployer,
            [denomination_asset.address, comptroller.address, terms_of_service.address],
            etherscan_api_key=etherscan_api_key,
        )
        logger.info("TermedVaultUSDCPaymentForwarder is %s deployed at %s", payment_forwarder.address, tx_hash.hex())
    else:
        # Legacy + unit test path
        payment_forwarder, tx_hash = deploy_contract_with_forge(
            web3,
            CONTRACTS_ROOT / "in-house",
            "VaultUSDCPaymentForwarder.sol",
            "VaultUSDCPaymentForwarder",
            deployer,
            [denomination_asset.address, comptroller.address],
            etherscan_api_key=etherscan_api_key,
        )
        logger.info("VaultUSDCPaymentForwarder is %s deployed at %s", payment_forwarder.address, tx_hash.hex())

    if not mock_guard:
        # When swap is performed, the tokens will land on the integration contract
        # and this contract must be listed as the receiver.
        # Enzyme will then internally move tokens to its vault from here.
        guard.functions.allowReceiver(generic_adapter.address, "").transact({"from": deployer.address})

        # Because Enzyme does not pass the asset manager address to through integration manager,
        # we set the vault address itself as asset manager for the guard
        tx_hash = guard.functions.allowSender(vault.address, "").transact({"from": deployer.address})
        assert_transaction_success_with_explanation(web3, tx_hash)

    # Give generic adapter back reference to the vault
    assert vault.functions.getCreator().call() != ZERO_ADDRESS, f"Bad vault creator {vault.functions.getCreator().call()}"
    tx_hash = generic_adapter.functions.bindVault(
        vault.address,
        production,
        meta,
    ).transact({"from": deployer.address})
    assert_transaction_success_with_explanation(web3, tx_hash)

    receipt = web3.eth.get_transaction_receipt(tx_hash)
    deployed_at_block = receipt["blockNumber"]

    assert generic_adapter.functions.getIntegrationManager().call() == deployment.contracts.integration_manager.address
    assert comptroller.functions.getDenominationAsset().call() == denomination_asset.address
    assert vault.functions.getTrackedAssets().call() == [denomination_asset.address]
    if asset_manager != deployer.address:
        assert vault.functions.canManageAssets(asset_manager).call()

    if not mock_guard:
        assert guard.functions.isAllowedSender(vault.address).call()  # vault = asset manager for the guard

        usdc_token = fetch_erc20_details(web3, denomination_asset.address)
        all_assets = [usdc_token] + whitelisted_assets
        for asset in all_assets:
            logger.info("Whitelisting %s", asset)
            tx_hash = guard.functions.whitelistToken(asset.address, f"Whitelisting {asset.symbol}").transact({"from": deployer.address})
            assert_transaction_success_with_explanation(web3, tx_hash)

    # We cannot directly transfer the ownership to a multisig,
    # but we can set nominated ownership pending
    if owner != deployer.address:
        tx_hash = vault.functions.setNominatedOwner(owner).transact({"from": deployer.address})
        assert_transaction_success_with_explanation(web3, tx_hash)
        logger.info("New vault owner nominated to be %s", owner)

    vault = Vault.fetch(
        web3,
        vault_address=vault.address,
        payment_forwarder=payment_forwarder.address,
        generic_adapter_address=generic_adapter.address,
        deployed_at_block=deployed_at_block,
        asset_manager=asset_manager,
    )
    vault.deployer_hot_wallet = deployer

    assert vault.guard_contract.address == guard.address

    if not mock_guard:
        match web3.eth.chain_id:
            case 137:
                uniswap_v3_router = UNISWAP_V3_DEPLOYMENTS["polygon"]["router"]
                uniswap_v2_router = None
            case 1:
                uniswap_v2_router = UNISWAP_V2_DEPLOYMENTS["ethereum"]["router"]
                uniswap_v3_router = UNISWAP_V3_DEPLOYMENTS["ethereum"]["router"]
            case _:
                logger.info("Uniswap not supported for chain %d", web3.eth.chain_id)
                uniswap_v2_router = None
                uniswap_v3_router = None

        if uniswap_v2 and uniswap_v2_router:
            logger.info("Whitelisting Uniswap V2 router %s", uniswap_v2_router)
            tx_hash = vault.guard_contract.functions.whitelistUniswapV2Router(uniswap_v2_router, "").transact({"from": deployer.address})
            assert_transaction_success_with_explanation(web3, tx_hash)

        if uniswap_v3 and uniswap_v3_router:
            logger.info("Whitelisting Uniswap V3 router %s", uniswap_v3_router)
            tx_hash = vault.guard_contract.functions.whitelistUniswapV3Router(uniswap_v3_router, "").transact({"from": deployer.address})
            assert_transaction_success_with_explanation(web3, tx_hash)

    logger.info(
        "Deployed. Vault is %s, initial owner is %s, asset manager is %s",
        vault.vault.address,
        vault.get_owner(),
        asset_manager,
    )

    return vault
