# remember to run `pip install python-dotenv`

import os
from dotenv import load_dotenv
from web3 import Web3

from eth_account import Account

from eth_defi.abi import get_deployed_contract, get_abi_by_filename, get_contract
from eth_defi.uniswap_v3.deployment import UniswapV3Deployment
from eth_defi.uniswap_v3.swap import swap_with_slippage_protection
from eth_defi.gas import estimate_gas_fees, apply_gas
from eth_defi.hotwallet import HotWallet


# load environment variables from .env file
load_dotenv()

private_key = os.getenv("PRIVATE_KEY")
hot_wallet_address = os.getenv("ADDRESS")
api_key = os.getenv("API_KEY")

# web3 instance
web3 = Web3(Web3.HTTPProvider(api_key))


# create hot wallet
account = Account.from_key(private_key)
hot_wallet = HotWallet(account)
hot_wallet.sync_nonce(web3)


# deployment addresses
# see https://docs.uniswap.org/contracts/v3/reference/deployments
factory_address = "0x1F98431c8aD98523631AE4a59f267346ea31F984"
weth_address = "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619"
swap_router_address = "0xE592427A0AEce92De3Edee1F18E0157C05861564"
position_manager_address = "0xC36442b4a4522E871399CD717aBDD847Ab11FE88"
quoter_address = "0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6"
# pool_contract is generic and does not need a deployment address


# deployment address contracts
factory = get_deployed_contract(web3, "uniswap_v3/IUniswapV3Factory.json", factory_address)
swap_router = get_deployed_contract(web3, "uniswap_v3/ISwapRouter.json", swap_router_address)
position_manager = get_deployed_contract(web3, "uniswap_v3/INonfungiblePositionManager.json", position_manager_address)
quoter = get_deployed_contract(web3, "uniswap_v3/IQuoter.json", quoter_address)
weth = get_deployed_contract(web3, "uniswap_v3/IWETH9.json", weth_address)
pool_contract = get_contract(web3, "uniswap_v3/UniswapV3Pool.json")


# token contracts
# weth is defined above
erc20_abi = get_abi_by_filename("ERC20MockDecimals.json")
usdc = get_deployed_contract(web3, "ERC20MockDecimals.json", "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")


# create uniswap v3 deployment

deployment = UniswapV3Deployment(
    web3=web3,
    factory=factory,
    weth=weth,
    swap_router=swap_router,
    position_manager=position_manager,
    quoter=quoter,
    PoolContract=pool_contract,
)


# perform swap for 1 USDC

swap_func = swap_with_slippage_protection(
    uniswap_v3_deployment=deployment,
    recipient_address=hot_wallet_address,
    base_token=weth,
    quote_token=usdc,
    pool_fees=[5],
    amount_in=1,
)
tx = swap_func.build_transaction(
    {
        "from": hot_wallet_address,
        "chainId": web3.eth.chain_id,
        "gas": 350_000,  # estimate max 350k gas per swap
    }
)
# tx = fill_nonce(web3, tx)
gas_fees = estimate_gas_fees(web3)

apply_gas(tx, gas_fees)

signed_tx = hot_wallet.sign_transaction_with_new_nonce(tx)
tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
tx_receipt = web3.eth.get_transaction_receipt(tx_hash)
assert tx_receipt.status == 1
