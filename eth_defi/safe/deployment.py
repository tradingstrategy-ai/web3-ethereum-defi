"""Deploy Safe multisig wallets."""
import logging

from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress
from safe_eth.safe import Safe
from safe_eth.safe.safe import SafeV141
from web3 import Web3

from eth_defi.safe.safe_compat import create_safe_ethereum_client

logger = logging.getLogger(__name__)


def deploy_safe(
    web3: Web3,
    deployer: LocalAccount,
    owners: list[HexAddress | str],
    threshold: int,
    master_copy_address = "0x41675C099F32341bf84BFc5382aF534df5C7461a",
) -> Safe:
    """Deploy a new Safe wallet.

    - Use version Safe v 1.4.1

    :param deployer:
        Must be LocalAccount due to Safe library limitations.

    :param master_copy_address:
        See Safe info.

        - https://help.safe.global/en/articles/40834-verify-safe-creation
        - https://basescan.org/address/0x41675C099F32341bf84BFc5382aF534df5C7461a
    """

    assert isinstance(deployer, LocalAccount), f"Safe can be only deployed using LocalAccount"
    for a in owners:
        assert type(a) == str and a.startswith("0x"), f"owners must be hex addresses, got {type(a)}"

    logger.info("Deploying safe.\nOwners: %s\nThreshold: %s", owners, threshold)
    ethereum_client = create_safe_ethereum_client(web3)

    owners = [Web3.to_checksum_address(a) for a in owners]
    master_copy_address = Web3.to_checksum_address(master_copy_address)

    safe_tx = SafeV141.create(
        ethereum_client,
        deployer,
        master_copy_address,
        owners,
        threshold,
    )
    contract_address = safe_tx.contract_address
    safe = SafeV141(contract_address, ethereum_client)

    # Check that we can read back Safe data
    retrieved_owners = safe.retrieve_owners()
    assert retrieved_owners == owners
    return safe
