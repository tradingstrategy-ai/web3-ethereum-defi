""""Fetch all Enzyme vaults, TVL, policies and such from on-chain data.

Example how to run:

.. code-block:: shell

    export JSON_RPC_URL=...
    python scripts/enzyme/fetch-all-vaults.py

- This script does not find some old Enzyme vaults (which are migrated?),
  because `NewFundCreated` event seems to be a recent addon

"""
import csv
from functools import lru_cache
import logging
import os

import coloredlogs

from eth_defi.abi import get_deployed_contract
from eth_defi.enzyme.deployment import POLYGON_DEPLOYMENT, ETHEREUM_DEPLOYMENT, EnzymeDeployment
from eth_defi.enzyme.price_feed import UnsupportedBaseAsset
from eth_defi.enzyme.vault import Vault
from eth_defi.event_reader.conversion import convert_uint256_bytes_to_address, convert_uint256_string_to_address, decode_data
from eth_defi.event_reader.filter import Filter
from eth_defi.event_reader.multithread import MultithreadEventReader
from eth_defi.event_reader.progress_update import PrintProgressUpdate
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.chainlink.token_price import get_native_token_price_with_chainlink, get_token_price_with_chainlink
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)


def get_exchange_rate(vault: Vault, denomination_token: TokenDetails) -> float | None:
    """Get USD exchange rate of Enzyme vault denomination asset.

    Handle all special cases.

    See Chainlink feeds: https://docs.chain.link/data-feeds/price-feeds/addresses

    :param vault:
        Enzyme vault

    :param denomination_token:
        Prefetched vault denomination token details

    :return:
        Quote token/USD rate.

        None if we cannot handle this token.

    :raise NotImplementedError:
        If we have encountered an issue we have not seen before
    """

    web3 = vault.web3

    # Because of dead Chainlink feeds, we need a lot of special conditions to convert TVL to USD
    if denomination_token.address.lower() == "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2".lower():
        # Enzyme treats wrapped Ethereum specially on mainnet
        _, round_data = get_native_token_price_with_chainlink(web3)
        exchange_rate = round_data.price
    elif denomination_token.address.lower() == "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619".lower():
        # Wrapped ETH on Polygon
        # Use Polygon ETH/USD feed
        _, _, round_data = get_token_price_with_chainlink(web3, "0xF9680D99D6C9589e2a93a78A04A279e509205945")
        exchange_rate = round_data.price
    elif denomination_token.address.lower() == "0x82a6c4AF830caa6c97bb504425f6A66165C2c26e".lower():
        # BNB on Polygon
        _, _, round_data = get_token_price_with_chainlink(web3, "0xF9680D99D6C9589e2a93a78A04A279e509205945")
        exchange_rate = round_data.price
    elif denomination_token.address.lower() == "0x3BA4c387f786bFEE076A58914F5Bd38d668B42c3".lower():
        # BNB (Pos) on Polygon
        _, _, round_data = get_token_price_with_chainlink(web3, "0xF9680D99D6C9589e2a93a78A04A279e509205945")
        exchange_rate = round_data.price
    elif denomination_token.address.lower() == "0x831753DD7087CaC61aB5644b308642cc1c33Dc13".lower():
        # Quickswap Polygon
        # Uninteresting, ignore
        return 0
    elif denomination_token.address.lower() == "0xD85d1e945766Fea5Eda9103F918Bd915FbCa63E6".lower():
        # Celsius on Polygon
        # Alex Mashinsky deserves zero
        return 0
    elif denomination_token.address.lower() == "0x03ab458634910AaD20eF5f1C8ee96F1D6ac54919".lower():
        # RAI
        # Just skip it
        return None
    elif denomination_token.address.lower() == "0x056Fd409E1d7A124BD7017459dFEa2F387b6d5Cd".lower():
        # Gemini dollar GUSD, assume 1:1 par
        exchange_rate = 1.0
    elif denomination_token.address.lower() == "0xB8c77482e45F1F44dE1745F52C74426C631bDD52".lower():
        # BNB on Ethereum
        _, _, round_data = get_token_price_with_chainlink(web3, "0x14e613AC84a31f709eadbdF89C6CC390fDc9540A")
        exchange_rate = round_data.price
    else:
        try:
            exchange_rate = vault.fetch_denomination_token_usd_exchange_rate()
        except UnsupportedBaseAsset as e:
            # If we did not handle special assets above, then bork out here
            # with a helpful message
            raise NotImplementedError(f"Cannot get conversion rate for {denomination_token}") from e

    return exchange_rate


def main():
    # Set up stdout logger
    setup_console_logging()

    # Set up Web3 connection
    json_rpc_url = os.environ.get("JSON_RPC_URL")
    assert json_rpc_url, f"You need to give JSON_RPC_URL environment variable pointing ot your full node"

    # Ankr max 1000 blocks once https://www.ankr.com/docs/rpc-service/service-plans/
    eth_getLogs_limit = os.environ.get("MAX_BLOCKS_ONCE", 2500)

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
            deployment = EnzymeDeployment.fetch_deployment(web3, ETHEREUM_DEPLOYMENT)
            start_block = ETHEREUM_DEPLOYMENT["deployed_at"]
        case _:
            raise AssertionError(f"Chain {web3.eth.chain_id} not supported")

    logger.info(f"Chain {web3.eth.chain_id}, fetched Enzyme deployment with ComptrollerLib as {deployment.contracts.comptroller_lib.address}")

    # Set up multithreaded Polygon event reader.
    # Print progress to the console how many blocks there are left to read.
    reader = MultithreadEventReader(json_rpc_url, max_threads=8, notify=PrintProgressUpdate(), max_blocks_once=eth_getLogs_limit)

    filter = Filter.create_filter(
        address=None,
        event_types=[deployment.contracts.fund_deployer.events.NewFundCreated],
    )

    # Avoid extra RPC calls by caching policy identifiers
    @lru_cache(maxsize=100)
    def get_policy_name(policy_address):
        try:
            policy = get_deployed_contract(web3, "enzyme/IPolicy.json", policy_address)
        except Exception as e:
            raise RuntimeError(f"Cannot get identifier for policy {policy_address}") from e
        return policy.functions.identifier().call()

    fname = f"enzyme-vaults-chain-{web3.eth.chain_id}.csv"
    logger.info(f"Writing {fname}")

    with open(fname, "wt") as f:
        csv_writer = csv.DictWriter(f, fieldnames=["vault", "name", "symbol", "block_created", "tx_hash", "tvl", "denomination_asset", "policies", "creator"])

        csv_writer.writeheader()

        for log in reader(
            web3,
            start_block,
            end_block,
            filter=filter,
        ):
            # event NewFundCreated(address indexed creator, address vaultProxy, address comptrollerProxy);
            # https://polygonscan.com/tx/0x08a4721b171233690251d95de91a688c7d2f18c2e82bedc0f86857b182e95a8c#eventlog

            # Old style NewFundCreated event https://etherscan.io/tx/0x4a11fc3ed672b5d759a8ef8c89e05dccacfb3a8ef344756a1851b6dfa34a2148#eventlog
            # that is not detected

            creator = convert_uint256_string_to_address(log["topics"][1])
            args = decode_data(log["data"])
            vault_address = convert_uint256_bytes_to_address(args[0])
            vault = Vault.fetch(web3, vault_address)

            denomination_asset = vault.get_denomination_asset()
            denomination_token = fetch_erc20_details(web3, denomination_asset)

            exchange_rate = get_exchange_rate(vault, denomination_token)
            if exchange_rate is None:
                # Unsupported denomination token
                continue

            try:
                # Calculate TVL in USD
                tvl = vault.get_gross_asset_value()
                tvl = denomination_token.convert_to_decimals(tvl) * exchange_rate
            except Exception as e:
                logger.warning(f"Could not read TVL for {vault_address}, tx {log['transactionHash']}", exc_info=e)
                tvl = 0

            policy_manager_address = vault.comptroller.functions.getPolicyManager().call()
            policy_manager = get_deployed_contract(web3, "enzyme/PolicyManager.json", policy_manager_address)
            policies = policy_manager.functions.getEnabledPoliciesForFund(vault.comptroller.address).call()

            policy_names = [get_policy_name(p) for p in policies]

            name = vault.get_name()
            symbol = vault.get_symbol()

            csv_writer.writerow(
                {
                    "vault": vault.address,
                    "name": name,
                    "symbol": symbol,
                    "block_created": log["blockNumber"],
                    "tx_hash": log["transactionHash"],
                    "tvl": tvl,
                    "denomination_asset": denomination_token.symbol,
                    "creator": creator,
                    "policies": " ".join(policy_names),
                }
            )

            logger.info(f"Added {name} ({symbol}) at {log['blockNumber']:,}, TVL is {tvl:,} USD in {denomination_token.symbol}")

            rows_written += 1
            # TODO: Do vaults mix stablecoins and native assets as TVL
            total_tvl += tvl

            if rows_written % 10 == 0:
                logger.info("%d CSV rows written", rows_written)

    reader.close()
    logger.info(f"Scanned {rows_written} vaults, total TVL is {total_tvl:,}")


if __name__ == "__main__":
    main()
