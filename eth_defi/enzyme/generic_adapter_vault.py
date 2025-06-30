"""Safe deployment of Enzyme vaults with generic adapter.

To patch the guard deployment in console:

.. code-block:: python

    import json
    from eth_defi.abi import get_deployed_contract

    deploy_data = json.load(open("deploy/STOCH-RSI-vault-info.json", "rt))
    guard_address = deploy_data["guard"]

    guard = get_deployed_contract(web3, "guard/GuardV0", guard_address)

"""

import logging
import os
import time
from pathlib import Path
from typing import Collection

from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.aave_v3.constants import AAVE_V3_DEPLOYMENTS, AAVE_V3_NETWORKS
from eth_defi.aave_v3.deployment import fetch_deployment as fetch_aave_deployment
from eth_defi.enzyme.deployment import EnzymeDeployment
from eth_defi.enzyme.policy import (
    create_safe_default_policy_configuration_for_generic_adapter,
)
from eth_defi.enzyme.vault import Vault
from eth_defi.foundry.forge import deploy_contract_with_forge
from eth_defi.hotwallet import HotWallet
from eth_defi.one_delta.constants import ONE_DELTA_DEPLOYMENTS
from eth_defi.one_delta.deployment import fetch_deployment as fetch_1delta_deployment
from eth_defi.provider.anvil import is_anvil
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.uniswap_v2.constants import QUICKSWAP_DEPLOYMENTS, UNISWAP_V2_DEPLOYMENTS
from eth_defi.abi import ZERO_ADDRESS
from eth_defi.uniswap_v3.constants import UNISWAP_V3_DEPLOYMENTS

logger = logging.getLogger(__name__)


CONTRACTS_ROOT = Path(os.path.dirname(__file__)) / ".." / ".." / "contracts"


def _get_chain_slug(web3: Web3) -> str:
    return {
        31337: "anvil",  # only for testing
        1: "ethereum",
        137: "polygon",
        42161: "arbitrum",
    }[web3.eth.chain_id]


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
    one_delta=False,
    aave=False,
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
    web3 = deployment.web3
    deployed_at_block = web3.eth.block_number
    chain_slug = _get_chain_slug(web3)
    logger.info(
        "Deploying Enzyme vault. Enzyme fund deployer: %s, Terms of service: %s, USDC: %s, Etherscan API key: %s, block %d",
        deployment.contracts.fund_deployer.address,
        terms_of_service.address if terms_of_service is not None else "-",
        denomination_asset.address,
        etherscan_api_key,
        deployed_at_block,
    )

    guard = deploy_guard(
        web3,
        deployer=deployer,
        asset_manager=asset_manager,
        owner=owner,
        denomination_asset=denomination_asset,
        whitelisted_assets=whitelisted_assets,
        mock_guard=mock_guard,
        etherscan_api_key=etherscan_api_key,
        uniswap_v2=uniswap_v2,
        uniswap_v3=uniswap_v3,
        aave=aave,
        one_delta=one_delta,
    )

    generic_adapter = deploy_generic_adapter_with_guard(
        deployment,
        deployer,
        guard=guard,
        etherscan_api_key=etherscan_api_key,
    )
    logger.info("GuardedGenericAdapter is deployed at %s", generic_adapter.address)

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

    deployer.sync_nonce(web3)

    # Some issue with Polygon deployment,
    # bind_vault() fails in the estimate gas
    if not is_anvil(web3):
        logger.info("Making sure all contract deployment txs propagade")
        time.sleep(30)

    bind_vault(
        generic_adapter,
        vault,
        production,
        meta,
        deployer,
    )

    # asset manager role is the trade executor
    if asset_manager != owner:
        tx_hash = vault.functions.addAssetManagers([asset_manager]).transact({"from": deployer.address})
        assert_transaction_success_with_explanation(web3, tx_hash)

    # Need to resync the nonce, because it was used outside HotWallet
    deployer.sync_nonce(web3)

    if terms_of_service is not None:
        assert denomination_asset.address
        assert comptroller.address
        assert terms_of_service.address
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

    # Give generic adapter back reference to the vault
    assert vault.functions.getCreator().call() != ZERO_ADDRESS, f"Bad vault creator {vault.functions.getCreator().call()}"

    whitelist_sender_receiver(
        guard,
        deployer,
        allow_sender=vault.address,
        allow_receiver=generic_adapter.address,
    )

    assert generic_adapter.functions.getIntegrationManager().call() == deployment.contracts.integration_manager.address
    assert comptroller.functions.getDenominationAsset().call() == denomination_asset.address
    assert vault.functions.getTrackedAssets().call() == [denomination_asset.address]
    if asset_manager != deployer.address:
        assert vault.functions.canManageAssets(asset_manager).call()

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

    logger.info(
        "Deployed. Vault is %s, initial owner is %s, asset manager is %s",
        vault.vault.address,
        vault.get_owner(),
        asset_manager,
    )

    return vault


def deploy_guard(
    web3: Web3,
    deployer: HotWallet,
    asset_manager: HexAddress | str,
    owner: HexAddress | str,
    denomination_asset: Contract,
    whitelisted_assets: Collection[TokenDetails] | None = None,
    etherscan_api_key: str | None = None,
    uniswap_v2=True,
    uniswap_v3=True,
    one_delta=False,
    aave=False,
    mock_guard=False,
) -> Contract:
    """Deploy a new GuardV0 smart contract.

    - To be associated with Enzyme vault or SimpleVault

    - Can be deployment standalone and the vault upgraded to use a newer version of the guard

    See :py:func:`deploy_vault_with_generic_adapter` for more details.

    :param mock_guard:
        Set to true to disable actual deployment.

        Used in legacy unit test setup.
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
    deployed_at_block = web3.eth.block_number
    chain_slug = _get_chain_slug(web3)
    logger.info(
        "Deploying Guard. USDC: %s, Etherscan API key: %s, block %d",
        denomination_asset.address,
        etherscan_api_key,
        deployed_at_block,
    )

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

    # Need to resync the nonce, because it was used outside HotWallet
    deployer.sync_nonce(web3)

    if not mock_guard:
        usdc_token = fetch_erc20_details(web3, denomination_asset.address)
        all_assets = [usdc_token] + whitelisted_assets
        for asset in all_assets:
            logger.info("Whitelisting %s", asset)

            # Check token address is valie
            token = fetch_erc20_details(web3, asset.address)
            logger.info("Decimals of %s is %s", token.symbol, token.decimals)
            assert token.decimals > 0

            tx_hash = guard.functions.whitelistToken(asset.address, f"Whitelisting {asset.symbol}").transact({"from": deployer.address})
            assert_transaction_success_with_explanation(web3, tx_hash)

    if not mock_guard:
        match web3.eth.chain_id:
            case 137:
                uniswap_v3_router = UNISWAP_V3_DEPLOYMENTS["polygon"]["router"]
                uniswap_v2_router = QUICKSWAP_DEPLOYMENTS["polygon"]["router"]
            case 1:
                uniswap_v2_router = UNISWAP_V2_DEPLOYMENTS["ethereum"]["router"]
                uniswap_v3_router = UNISWAP_V3_DEPLOYMENTS["ethereum"]["router"]
            case 42161:
                if uniswap_v2:
                    raise NotImplementedError(f"Uniswap v2 not configured for Arbitrum yet")
                uniswap_v2_router = None
                uniswap_v3_router = UNISWAP_V3_DEPLOYMENTS["arbitrum"]["router"]
            case _:
                logger.error("Uniswap not supported for chain %d", web3.eth.chain_id)
                uniswap_v2_router = None
                uniswap_v3_router = None

        if uniswap_v2 and uniswap_v2_router:
            logger.info("Whitelisting Uniswap/Quickswap V2 router %s", uniswap_v2_router)
            tx_hash = guard.functions.whitelistUniswapV2Router(uniswap_v2_router, "").transact({"from": deployer.address})
            assert_transaction_success_with_explanation(web3, tx_hash)

        if uniswap_v3 and uniswap_v3_router:
            logger.info("Whitelisting Uniswap V3 router %s", uniswap_v3_router)
            tx_hash = guard.functions.whitelistUniswapV3Router(uniswap_v3_router, "").transact({"from": deployer.address})
            assert_transaction_success_with_explanation(web3, tx_hash)

        if one_delta or aave:
            assert chain_slug in AAVE_V3_DEPLOYMENTS, f"Chain {chain_slug} not supported for Aave v3"

            aave_v3_deployment = fetch_aave_deployment(
                web3,
                pool_address=AAVE_V3_DEPLOYMENTS[chain_slug]["pool"],
                data_provider_address=AAVE_V3_DEPLOYMENTS[chain_slug]["data_provider"],
                oracle_address=AAVE_V3_DEPLOYMENTS[chain_slug]["oracle"],
            )
            aave_pool_address = aave_v3_deployment.pool.address
        else:
            aave_pool_address = None

        if one_delta:
            assert chain_slug in ONE_DELTA_DEPLOYMENTS, f"Chain {chain_slug} not supported for 1delta"

            one_delta_deployment = fetch_1delta_deployment(
                web3,
                flash_aggregator_address=ONE_DELTA_DEPLOYMENTS[chain_slug]["broker_proxy"],
                broker_proxy_address=ONE_DELTA_DEPLOYMENTS[chain_slug]["broker_proxy"],
                quoter_address=ONE_DELTA_DEPLOYMENTS[chain_slug]["quoter"],
            )

            broker_proxy_address = one_delta_deployment.broker_proxy.address

            logger.info("Whitelisting 1delta: %s and Aave: %s", broker_proxy_address, aave_pool_address)

            note = "Allow 1delta"
            tx_hash = guard.functions.whitelistOnedelta(broker_proxy_address, aave_pool_address, note).transact({"from": deployer.address})
            assert_transaction_success_with_explanation(web3, tx_hash)

        if aave:
            note = f"Allow Aave v3 pool"
            tx_hash = guard.functions.whitelistAaveV3(aave_pool_address, note).transact({"from": deployer.address})
            assert_transaction_success_with_explanation(web3, tx_hash)

            match web3.eth.chain_id:
                case 1:
                    assert web3.eth.chain_id == 1, "TODO: Add support for non-mainnet chains"
                    ausdc_address = "0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c"
                    logger.info("Aave whitelisting for pool %s, aUSDC %s", aave_pool_address, ausdc_address)

                    note = f"Aave v3 pool whitelisting for USDC"
                    tx_hash = guard.functions.whitelistToken(ausdc_address, note).transact({"from": deployer.address})

                case 42161:
                    # Arbitrum
                    aave_tokens = AAVE_V3_NETWORKS["arbitrum"].token_contracts

                    # TODO: We automatically list all main a tokens as allowed assets
                    # we should limit here only to what the strategy needs,
                    # as these tokens may have their liquidity to dry up in the future
                    for symbol, token in aave_tokens.items():
                        logger.info(
                            "Aave whitelisting for pool %s, atoken:%s address: %s",
                            symbol,
                            aave_pool_address,
                            token.token_address,
                        )
                        note = f"Whitelisting Aave {symbol}"
                        tx_hash = guard.functions.whitelistToken(token.token_address, note).transact({"from": deployer.address})
                        assert_transaction_success_with_explanation(web3, tx_hash)
                case _:
                    raise NotImplementedError(f"TODO: Add support for non-mainnet chains, got {web3.eth.chain_id}")

            assert_transaction_success_with_explanation(web3, tx_hash)

    deployer.sync_nonce(web3)

    return guard


def deploy_generic_adapter_with_guard(
    deployment: EnzymeDeployment,
    deployer: HotWallet,
    guard: Contract,
    etherscan_api_key: str | None = None,
) -> Contract:
    """Deploy a new generic adapter for a vault.

    TODO: If the vault has existing generic adapter, we do not currently revoke the old adapter.
    """

    assert isinstance(deployment, EnzymeDeployment), f"Got {deployment}"
    assert isinstance(guard, Contract), f"Got {guard}"

    web3 = deployment.web3

    assert CONTRACTS_ROOT.exists(), f"Cannot find contracts folder {CONTRACTS_ROOT.resolve()} - are you running from git checkout?"

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

    deployer.sync_nonce(web3)

    return generic_adapter


def whitelist_sender_receiver(
    guard: Contract,
    deployer: HotWallet,
    allow_sender: str | None = None,
    allow_receiver: str | None = None,
):
    """Configure guard to allow vault to trade with tokens.

    - Configure where incoming/outgoing tokens
    """

    web3 = guard.w3

    # When swap is performed, the tokens will land on the integration contract
    # and this contract must be listed as the receiver.
    # Enzyme will then internally move tokens to its vault from here.
    if allow_receiver:
        tx_hash = guard.functions.allowReceiver(allow_receiver, "").transact({"from": deployer.address})
        assert_transaction_success_with_explanation(web3, tx_hash)

    # Because Enzyme does not pass the asset manager address to through integration manager,
    # we set the vault address itself as asset manager for the guard
    if allow_sender:
        tx_hash = guard.functions.allowSender(allow_sender, "").transact({"from": deployer.address})
        assert_transaction_success_with_explanation(web3, tx_hash)

    logger.info("GenericAdapter %s whitelisted as receiver, %s as sender", allow_receiver, allow_sender)

    if allow_sender:
        assert guard.functions.isAllowedSender(allow_sender).call()  # vault = asset manager for the guard

    if not (allow_receiver or allow_sender):
        # Production deployment foobar - add this warning message for now until figuring
        # out why allowReceiver() failed
        logger.warning("No receiver whitelisted")


def bind_vault(
    generic_adapter: Contract,
    vault: Contract,
    production: bool,
    meta: str,
    deployer: HotWallet,
    gas: int = 500_000,
):
    """Make GenericAdapter to work with a single vault only.

    :param gas:
        estimateGas will crash when calling bindVault() because the tx to deploy the contract
        has not hit all RPCs yet.
    """
    assert isinstance(vault, Contract), f"Got {vault}"

    assert generic_adapter.functions.vault().call() == ZERO_ADDRESS, "vault() accessor tells vault already bound"
    assert generic_adapter.functions.guard().call() != ZERO_ADDRESS, "Does not look like GuardedGenericAdapter: guard() accessor missing"

    web3 = vault.w3
    tx_hash = generic_adapter.functions.bindVault(
        vault.address,
        production,
        meta,
    ).transact(
        {
            "from": deployer.address,
            "gas": gas,
        }
    )
    assert_transaction_success_with_explanation(web3, tx_hash)
