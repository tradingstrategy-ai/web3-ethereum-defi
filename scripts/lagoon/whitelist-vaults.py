"""Build a Gnosis Safe transaction for a guard smart contract to whitelist multiple vaults.

TODO: Unfinished code

- Creates multiple Safe transactions that co-signers need to sign and execute in the Safe UI

Example usage:

.. code-block:: shell

    export JSON_RPC_URL=$JSON_RPC_ARBITRUM
    export SAFE_ADDRESS=0x62e6a0111f6DaeDf94d24197C32e469EA8eF1A8E
    export TRADING_STRATEGY_MODULE_ADDRESS=0xF137881aa61580E057648526e58DE60489CA5f85
    export VAULT_ADDRESSES="0x959f3807f0aa7921e18c78b00b2819ba91e52fef, 0xe5a4f22fcb8893ba0831babf9a15558b5e83446f, 0x75288264fdfea8ce68e6d852696ab1ce2f3e5004, 0x58bfc95a864e18e8f3041d2fcd3418f48393fe6a, 0x2d5fde3d24ed3e7c548a59039eee5af8200f9291, 0xb739ae19620f7ecb4fb84727f205453aa5bc1ad2, 0x9fa306b1f4a6a83fec98d8ebbabedff78c407f6b, 0xd15a07a4150b0c057912fe883f7ad22b97161591, 0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9, 0x5e777587d6f9261a85d7f062790d4cee71081ba1, 0x0b2b2b2076d95dda7817e785989fe353fe955ef9, 0x6ca200319a0d4127a7a473d6891b86f34e312f42, 0x4f63cfea7458221cb3a0eee2f31f7424ad34bb58, 0x8a1ef3066553275829d1c0f64ee8d5871d5ce9d3, 0x407d3d942d0911a2fea7e22417f81e27c02d6c6f, 0xacb7432a4bb15402ce2afe0a7c9d5b738604f6f9, 0x64ca76e2525fc6ab2179300c15e343d73e42f958, 0x0df2e3a0b5997adc69f8768e495fd98a4d00f134, 0xa7781f1d982eb9000bc1733e29ff5ba2824cdbe5, 0xa53cf822fe93002aeae16d395cd823ece161a6ac, 0xa60643c90a542a95026c0f1dbdb0615ff42019cf, 0x4a8e91248e5602d0d34a5e86a9f1b60e8f2dc721, 0x4b6f1c9e5d470b97181786b26da0d0945a7cf027, 0xd3443ee1e91af28e5fb858fbd0d72a63ba8046e0, 0x36b69949d60d06eccc14de0ae63f4e00cc2cd8b9, 0xdc1ab820c92735e7a5e48f10fa3d8424ec47a93e, 0x444868b6e8079ac2c55eea115250f92c2b2c4d14, 0xbc404429558292ee2d769e57d57d6e74bbd2792d, 0x7e97fa6893871a2751b5fe961978dccb2c201e65, 0x7788a3538c5fc7f9c7c8a74eac4c898fc8d87d92, 0x5f851f67d24419982ecd7b7765defd64fbb50a97, 0x250cf7c82bac7cb6cf899b6052979d4b5ba1f9ca, 0x79f76e343807ea194789d114e61be6676e6bbeda, 0x5c0c306aaa9f877de636f4d5822ca9f2e81563ba, 0xe4783824593a50bfe9dc873204cec171ebc62de0, 0x87deae530841a9671326c9d5b9f91bdb11f3162c, 0x940098b108fb7d0a7e374f6eded7760787464609, 0xd089b4cb88dacf4e27be869a00e9f7e2e3c18193, 0xe07f1151887b8fdc6800f737252f6b91b46b5865, 0x037dff1c12805707d7c29f163e0f09fc9102657a, 0x7f6501d3b98ee91f9b9535e4b0ac710fb0f9e0bc, 0x7cfadfd5645b50be87d546f42699d863648251ad, 0x7c574174da4b2be3f705c6244b4bfa0815a8b3ed, 0x5579e27129110bbc9c0ec1388acbf7ad04771b76, 0x1c107c4233ab3056254e717c7a67f9917079b615, 0x1a996cb54bb95462040408c06122d45d6cdb6096, 0x97901cf9f064c40f538c5f7b53420a02cb68c644, 0xaa38b9475d7a9ea7a2a2bada7e41d56c5db132b8, 0x004626a008b1acdc4c74ab51644093b155e59a23, 0xd85e038593d7a098614721eae955ec2022b9b91b"
    # Use the vault deployer private key which should be one of the multisig co-signers
    export PRIVATE_KEY="..."

"""

import logging
import os

from tabulate import tabulate
from tqdm_loggable.auto import tqdm

from safe_eth.safe import Safe
from safe_eth.safe.multi_send import MultiSend, MultiSendTx, MultiSendOperation, get_multi_send_contract
from safe_eth.eth import EthereumClient

from eth_defi.abi import get_deployed_contract, encode_function_call
from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.gains.vault import GainsVault
from eth_defi.lagoon.vault import LagoonVault
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.safe.deployment import fetch_safe_deployment
from eth_defi.safe.tx import propose_safe_transaction
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging, chunked


logger = logging.getLogger(__name__)


def whitelist_vaults(
    safe: Safe,
    trading_strategy_module,
    vaults: list[ERC4626Vault],
    private_key: str,
):
    """Whitelist a chunk of vaults on the TradingStrategyModule via Safe multisend transaction.

    - Creates a Safe transaction that proposes the whitelisting of the new vaults
    - Propose the transaction by using one of the private keys of the Safe
    - The transaction is send to Gnosis Safe UI transaction service for users to sign
    """

    target = trading_strategy_module.address

    multi_send_txs = []
    for vault in vaults:
        notes = f"Whitelisting vault {vault.name}"

        # In theory we could have different functions for vault whitelists,
        # but for now we just assume all use the same function
        match vault:
            case LagoonVault():  # ERC-7540
                whitelist_call = trading_strategy_module.functions.whitelistERC4626(vault.address, notes)
            case GainsVault():
                whitelist_call = trading_strategy_module.functions.whitelistERC4626(vault.address, notes)
            case ERC4626Vault():
                whitelist_call = trading_strategy_module.functions.whitelistERC4626(vault.address, notes)
            case _:
                raise RuntimeError(f"Unsupported vault type {type(vault)} at address {vault.address}")

        logger.info("Whitelisting vault %s at guard %s", vault.address, target)

        # Check our individual whitelist calsl work, because Gnosis Safe MultiSendCallOnly contract
        # has the worsst developer experience and you cannot get any kind of useful diagnostics information for
        # failed transactions with it.
        # If the transaction does not revert, we should get a gas estimation for it.
        tx = {
            "from": safe.address,
        }
        estimated_gas = whitelist_call.estimate_gas(tx)
        logger.info(f"Estimated gas for whitelisting vault {vault.address}: {estimated_gas:,}")

        single_call_data = encode_function_call(whitelist_call)
        multi_send_txs.append(
            MultiSendTx(
                operation=MultiSendOperation.CALL,
                to=target,
                value=0,
                data=single_call_data,
            )
        )

    ethereum_client: EthereumClient = safe.ethereum_client

    multi_send = MultiSend(ethereum_client)  # uses known multisend addresses by default
    # Build the inner-multi-send byte blob (this is the bytes parameter expected by multiSend)
    inner_bytes = multi_send.build_tx_data(multi_send_txs)  # raw packed transactions

    multi_send_contract = multi_send.get_contract()
    multi_send_calldata = encode_function_call(
        multi_send_contract.functions.multiSend,
        [inner_bytes],
    )

    safe_tx = propose_safe_transaction(
        safe=safe,
        address=multi_send.get_contract().address,
        private_key=private_key,
        data=multi_send_calldata,
    )
    print(f"Safe transaction proposed, safeTxHash: {safe_tx.safe_tx_hash.hex()}")


def main():
    setup_console_logging()

    json_rpc_url = os.environ["JSON_RPC_URL"]
    safe_address = os.environ["SAFE_ADDRESS"]
    trading_strategy_module_address = os.environ["TRADING_STRATEGY_MODULE_ADDRESS"]
    vault_address_debug_limit = int(os.environ.get("VAULT_ADDRESS_DEBUG_LIMIT", 999))  # Allow faster manual testing
    vault_addresses = [a.strip() for a in os.environ["VAULT_ADDRESSES"].split(",")]
    private_key = os.environ["PRIVATE_KEY"]

    vault_addresses = vault_addresses[:vault_address_debug_limit]

    # How many vaults we can cram into a single Safe multicall transaction
    chunk_size = 30

    web3 = create_multi_provider_web3(json_rpc_url)

    chain_name = get_chain_name(web3.eth.chain_id)
    print(f"Connected to {chain_name}, last block is {web3.eth.block_number:,}")

    safe = fetch_safe_deployment(
        web3,
        safe_address,
    )
    print(f"Connected to Safe at address {safe_address}, owners: {safe.retrieve_owners()}, threshold: {safe.retrieve_threshold()}")

    trading_strategy_module = get_deployed_contract(
        web3,
        "safe-integration/TradingStrategyModuleV0.json",
        trading_strategy_module_address,
    )

    # On consequtive runs, speed up the operations by caching ERC-20 token metadata
    token_cache = TokenDiskCache()

    trading_strategy_module_version = trading_strategy_module.functions.getTradingStrategyModuleVersion().call()

    print(f"Whitelisting vaults on TradingStrategyModule at address {trading_strategy_module_address} version {trading_strategy_module_version} for Safe {safe_address}")

    # Prepare vault instances to whitelist, and check
    # we can raad onchain data of them and there are not broken vaults/addresses
    vaults = []
    data = []
    broken_vaults = []
    for addr in tqdm(vault_addresses, desc="Processing vaults"):
        vault = create_vault_instance_autodetect(
            web3,
            addr,
            token_cache=token_cache,
        )
        protocol_name = get_vault_protocol_name(vault.features)
        vaults.append(vault)
        data.append(
            {
                "Name": vault.name,
                "Address": addr,
                "Denomination": vault.denomination_token.symbol,
                "Protocol": protocol_name,
                "Features": ", ".join(f.value for f in vault.features),
            }
        )

        if ERC4626Feature.broken in vault.features:
            broken_vaults.append(vault)

    # Display what we are about to whitelist
    table_fmt = tabulate(
        data,
        headers="keys",
        tablefmt="fancy_grid",
    )
    print("The following vaults will be whitelisted:")
    print(table_fmt)

    for vault in broken_vaults:
        print(f"Vault {vault.address} ({vault.name}) is broken, refuses to whitelist")

    assert not broken_vaults, "Refuses to whitelist broken vaults: check data"

    for idx, chunk in enumerate(chunked(vaults, chunk_size)):
        print(f"Whitelisting chunk {idx + 1} / {(len(vaults) - 1) // chunk_size + 1}, size {len(chunk)}")
        whitelist_vaults(
            safe,
            trading_strategy_module,
            chunk,
            private_key=private_key,
        )

    print("All done.")


if __name__ == "__main__":
    main()
