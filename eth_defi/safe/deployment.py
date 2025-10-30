"""Deploy Safe multisig wallets.

- Helpers for deploying Safe, managing owners and modifying the deployment

Safe source code:

- https://github.com/safe-global/safe-smart-account/blob/main/contracts/Safe.sol
"""

import logging
import time

from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress
from safe_eth.safe import Safe
from safe_eth.safe.safe import SafeV141
from web3 import Web3
from web3.contract.contract import ContractFunction

from eth_defi.abi import ONE_ADDRESS_STR
from eth_defi.gas import estimate_gas_price, apply_gas
from eth_defi.provider.anvil import is_anvil
from eth_defi.safe.execute import execute_safe_tx
from eth_defi.safe.safe_compat import create_safe_ethereum_client
from eth_defi.trace import assert_transaction_success_with_explanation


logger = logging.getLogger(__name__)


def deploy_safe(
    web3: Web3,
    deployer: LocalAccount,
    owners: list[HexAddress | str],
    threshold: int,
    master_copy_address="0x29fcB43b46531BcA003ddC8FCB67FFE91900C762",
) -> Safe:
    """Deploy a new Safe wallet.

    - Use version Safe v 1.4.1

    :param deployer:
        Must be LocalAccount due to Safe library limitations.

    :param master_copy_address:

        Default Safe 1.4.1 layer two master copy address.

        See ``MASTER_COPIES``

        - Use +L2 version tag for Layer two chains
        - https://github.com/safe-global/safe-eth-py/blob/76489e9641f4f1f4ea4dbbcee2dc2ce42e84d24f/safe_eth/safe/addresses.py#L16

        Discussion

        - https://ethereum.stackexchange.com/questions/167329/programmatically-creating-safe-ui-compatible-multisig-wallets
    """

    assert len(owners) >= 1, "Safe must have at least one owner"

    assert isinstance(deployer, LocalAccount), f"Safe can be only deployed using LocalAccount"
    for a in owners:
        assert type(a) == str and a.startswith("0x"), f"owners must be hex addresses, got {type(a)}"

    logger.info("Deploying safe.\nInitial cosigner list: %s\nInitial threshold: %s", owners, threshold)
    ethereum_client = create_safe_ethereum_client(web3)

    owners = [Web3.to_checksum_address(a) for a in owners]
    master_copy_address = Web3.to_checksum_address(master_copy_address)

    safe_tx_stuff = SafeV141.create(
        ethereum_client,
        deployer,
        master_copy_address,
        owners,
        threshold,
    )

    tx_hash = safe_tx_stuff.tx_hash
    assert_transaction_success_with_explanation(web3, tx_hash)

    contract_address = safe_tx_stuff.contract_address
    safe = SafeV141(contract_address, ethereum_client)

    # Check that we can read back Safe data.
    # If this call fails make sure you do not have bad fork block number set
    # for your Anvil.
    retrieved_owners = safe.retrieve_owners()
    assert retrieved_owners == owners

    logger.info("Safe deployed at %s", safe.address)
    return safe


def add_new_safe_owners(
    web3: Web3,
    safe: Safe,
    deployer: LocalAccount,
    owners: list[HexAddress | str],
    threshold: int,
    gas_per_tx=500_000,
    gnosis_safe_state_safety_sleep=20,
):
    """Update Safe owners and threshold list.

    - Safe cannot replace the existing owner list
    - Designed to create the owner list after a deployment.
    - The multisig must be in 1-of-1 deployer state

    .. note ::

        We cannot remove deployer account from the list, but it must be done by the new owners

    :param gas_per_tx:
        Gas limit for a single transaction.

    :param between_calls_sleep:
        Deployer hack


    More info:

    - https://github.com/safe-global/safe-smart-account/blob/main/contracts/base/OwnerManager.sol#L56C14-L56C35
    """

    assert isinstance(safe, Safe), f"Not safe: {safe}"
    assert isinstance(deployer, LocalAccount), f"Safe can be only updated using deployer LocalAccount"

    logger.info(
        "Updating Safe owner list: %s with threshold %d",
        owners,
        threshold,
    )

    gas_estimate = estimate_gas_price(web3)

    # Add all owners
    for owner in owners:
        assert isinstance(owner, str), f"Owner must be hex addresses, got {type(owner)}"
        assert owner.startswith("0x"), f"Owner must be hex addresses, got {type(owner)}"

        if owner == deployer.address:
            logger.info("Deployer: already exist on Safe cosigner")
            continue

        logger.info("Adding owner %s", owner)
        tx = safe.contract.functions.addOwnerWithThreshold(owner, 1).build_transaction(
            {"from": deployer.address, "gas": gas_per_tx, "gasPrice": 0},
        )

        safe_tx = safe.build_multisig_tx(safe.address, 0, tx["data"])
        safe_tx.sign(deployer._private_key.hex())
        tx_hash, tx = execute_safe_tx(
            safe_tx,
            tx_sender_private_key=deployer._private_key.hex(),
            tx_gas=1_000_000,
            gas_fee=gas_estimate,
        )
        assert_transaction_success_with_explanation(web3, tx_hash)

        if not is_anvil(web3):
            # Don't do transactions two fast or we might get
            # require(currentOwner > lastOwner && owners[currentOwner] != address(0) && currentOwner != SENTINEL_OWNERS, "GS026");
            logger.info("Sleeping for %d seconds to avoid Safe state safety issues", gnosis_safe_state_safety_sleep)
            time.sleep(gnosis_safe_state_safety_sleep)

    # Change the threshold
    logger.info("Changing signing threshold to: %d", threshold)
    tx = safe.contract.functions.changeThreshold(threshold).build_transaction(
        {"from": deployer.address, "gas": gas_per_tx, "gasPrice": 0},
    )
    safe_tx = safe.build_multisig_tx(safe.address, 0, tx["data"])
    safe_tx.sign(deployer._private_key.hex())
    tx_hash, tx = execute_safe_tx(
        safe_tx,
        tx_sender_private_key=deployer._private_key.hex(),
        tx_gas=1_000_000,
        gas_fee=gas_estimate,
    )
    assert_transaction_success_with_explanation(web3, tx_hash)
    logger.info("Owners updated")


def fetch_safe_deployment(
    web3: Web3,
    address: HexAddress | str,
) -> SafeV141:
    """Wrap Safe contract as Safe Python proxy object"""
    ethereum_client = create_safe_ethereum_client(web3)
    safe = SafeV141(address, ethereum_client)
    return safe


def disable_safe_module(
    web3: Web3,
    safe_address: str,
    module_address: HexAddress | str,
) -> ContractFunction:
    """Spoof Safe.disableModule() call on a forked mainnet.

    - Safe makes disable module transaction unnecessary complicated,
      because the internal linked list is exposed

    :raise ValueError:
        Module is not enabled.

    :return:
        Bound ContractFunction to call on the Safe contract.
    """
    safe = fetch_safe_deployment(web3, safe_address)
    modules = safe.retrieve_modules()

    try:
        idx = modules.index(module_address)
    except ValueError as e:
        raise ValueError(f"Module {module_address} not found in Safe {safe_address} modules: {modules}") from e

    if idx == 0:
        # See https://github.com/safe-global/safe-smart-account/pull/993
        previous_module = ONE_ADDRESS_STR
    else:
        previous_module = modules[idx - 1]

    return safe.contract.functions.disableModule(previous_module, module_address)
