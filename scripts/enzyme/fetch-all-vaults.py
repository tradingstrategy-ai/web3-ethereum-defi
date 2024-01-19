""""Fetch all Enzyme vaults, TVL, policies and such from on-chain data.

Example:

.. code-block:: shell

    export JSON_RPC_URL=...
    # Read blocks 25,000,000 - 26,000,000 around when Enzyme was deployment on Polygon
    python scripts/enzyme/fetch-all-vaults.py

"""
import csv
import logging
import os

import coloredlogs

from eth_defi.abi import get_deployed_contract
from eth_defi.enzyme.deployment import POLYGON_DEPLOYMENT, EnzymeDeployment
from eth_defi.enzyme.vault import Vault
from eth_defi.event_reader.conversion import convert_uint256_bytes_to_address, convert_uint256_string_to_address, decode_data
from eth_defi.event_reader.filter import Filter
from eth_defi.event_reader.multithread import MultithreadEventReader
from eth_defi.event_reader.progress_update import PrintProgressUpdate
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details

logger = logging.getLogger(__name__)


def setup_logging():
    level = os.environ.get("LOG_LEVEL", "info").upper()

    fmt = "%(asctime)s %(name)-44s %(message)s"
    date_fmt = "%H:%M:%S"
    coloredlogs.install(level=level, fmt=fmt, date_fmt=date_fmt)

    logging.basicConfig(level=level, handlers=[logging.StreamHandler()])

    # Mute noise
    logging.getLogger("web3.providers.HTTPProvider").setLevel(logging.WARNING)
    logging.getLogger("web3.RequestManager").setLevel(logging.WARNING)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)


def main():
    # Set up stdout logger
    setup_logging()

    # Set up Web3 connection
    json_rpc_url = os.environ.get("JSON_RPC_URL")
    assert json_rpc_url, f"You need to give JSON_RPC_URL environment variable pointing ot your full node"

    # Ankr max 1000 blocks once https://www.ankr.com/docs/rpc-service/service-plans/
    eth_getLogs_limit = os.environ.get("MAX_BLOCKS_ONCE", 1000)

    web3 = create_multi_provider_web3(json_rpc_url)

    rows_written = 0
    total_tvl = 0

    assert web3.eth.chain_id in (1, 137), "Only Ethereum mainnet and Polygon supported"
    end_block = web3.eth.block_number

    # Read Enzyme deployment from chain
    match web3.eth.chain_id:
        case 137:
            deployment = EnzymeDeployment.fetch_deployment(web3, POLYGON_DEPLOYMENT)
            start_block = POLYGON_DEPLOYMENT["deployed_at"]
        case 1:
            raise AssertionError(f"Chain {web3.eth.chain_id} not supported")
        case _:
            raise AssertionError(f"Chain {web3.eth.chain_id} not supported")

    print(f"Chain {web3.eth.chain_id}, fetched Enzyme deployment with ComptrollerLib as {deployment.contracts.comptroller_lib.address}")

    # Set up multithreaded Polygon event reader.
    # Print progress to the console how many blocks there are left to read.
    reader = MultithreadEventReader(
        json_rpc_url,
        max_threads=16,
        notify=PrintProgressUpdate(),
        max_blocks_once=eth_getLogs_limit
    )

    filter = Filter.create_filter(
        address=None,
        event_types=[deployment.contracts.fund_deployer.events.NewFundCreated],
    )

    with open(f"enzyme-vaults-chain-{web3.eth.chain_id}.csv", "wt") as f:
        csv_writer = csv.DictWriter(f, fieldnames=["vault", "name", "symbol", "block_created", "tx_hash", "tvl", "denomination_asset", "policies", "creator"])

        csv_writer.writeheader()

        for log in reader(
                web3,
                start_block,
                end_block,
                filter=filter,
        ):
            # event = log["event"]

            # event NewFundCreated(address indexed creator, address vaultProxy, address comptrollerProxy);
            # https://polygonscan.com/tx/0x08a4721b171233690251d95de91a688c7d2f18c2e82bedc0f86857b182e95a8c#eventlog
            creator = convert_uint256_string_to_address(log["topics"][1])
            args = decode_data(log["data"])
            vault_address = convert_uint256_bytes_to_address(args[0])
            vault = Vault.fetch(web3, vault_address)

            denomination_asset = vault.get_denomination_asset()

            # On Ethereum, Enzyme supports natice token which we need to handle specially
            denomination_token = fetch_erc20_details(web3, denomination_asset)

            try:
                tvl = vault.get_gross_asset_value()
                tvl = denomination_token.convert_to_decimals(tvl)
            except Exception as e:
                logger.warning(f"Could not read TVL for {vault_address}, tx {log['transactionHash']}", exc_info=e)
                tvl = 0

            policy_manager_address = vault.comptroller.functions.getPolicyManager().call()
            policy_manager = get_deployed_contract(web3, "enzyme/PolicyManager.json", policy_manager_address)
            policies = policy_manager.functions.getEnabledPoliciesForFund(vault.comptroller.address).call()

            name = vault.get_name()
            symbol = vault.get_symbol()

            csv_writer.writerow({
                "vault": vault.address,
                "name": name,
                "symbol": symbol,
                "block_created": log["blockNumber"],
                "tx_hash": log["transactionHash"],
                "tvl": tvl,
                "denomination_asset": denomination_token.symbol,
                "creator": creator,
                "policies": " ".join(policies),                
            })

            logger.info(f"Added {name} ({symbol}) at {log['blockNumber']:,}, TVL is {tvl:,} {denomination_token.symbol}")

            rows_written += 1
            # TODO: Do vaults mix stablecoins and native assets as TVL             
            total_tvl += tvl

            if rows_written % 10 == 0:
                logger.info("%d CSV rows written", rows_written)


    reader.close()
    logger.info(f"Scanned {rows_written} vaults, total TVL is {total_tvl}")


if __name__ == "__main__":
    main()
