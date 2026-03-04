"""Mock oracle setup and price helpers for GMX fork testing."""

import json
import logging
from pathlib import Path

from eth_utils import to_checksum_address
from web3 import Web3

from eth_defi.chain import get_chain_name
from eth_defi.gmx.core import OraclePrices
from eth_defi.gmx.testing.constants import ARBITRUM_DEFAULTS, _resolve_contract_address, _resolve_token_address
from eth_defi.gmx.testing.fork_provider import deal_eth, detect_provider_type, set_code
from eth_defi.trace import assert_transaction_success_with_explanation

logger = logging.getLogger(__name__)

#: Path to the eth_defi package root (three levels up from this file)
_ETH_DEFI_ROOT = Path(__file__).resolve().parent.parent.parent


def fetch_on_chain_oracle_prices(web3: Web3) -> tuple[int, int]:
    """Fetch current oracle prices from GMX before replacing with mock.

    Queries the actual on-chain oracle to get prices that will pass
    GMX's validation, since GMX validates mock prices against actual
    chain state.

    :return:
        ``(eth_price_usd, usdc_price_usd)`` as integers in USD
    """
    try:
        chain = get_chain_name(web3.eth.chain_id).lower()
        oracle = OraclePrices(chain)
        prices = oracle.get_recent_prices()

        weth_address = _resolve_token_address(chain, "WETH", ARBITRUM_DEFAULTS["weth"])
        usdc_address = _resolve_token_address(chain, "USDC", ARBITRUM_DEFAULTS["usdc"])

        if weth_address in prices:
            weth_price_formatted = int(prices[weth_address]["maxPriceFull"])
            eth_price_usd = weth_price_formatted // (10**12)
        else:
            logger.warning("WETH price not found in oracle, using default 3000")
            eth_price_usd = 3000

        if usdc_address in prices:
            usdc_price_formatted = int(prices[usdc_address]["maxPriceFull"])
            usdc_price_usd = max(1, round(usdc_price_formatted / (10**24)))
        else:
            usdc_price_usd = 1

        logger.info("Fetched on-chain oracle prices: ETH=$%d, USDC=$%d", eth_price_usd, usdc_price_usd)
        return eth_price_usd, usdc_price_usd

    except Exception as e:
        logger.warning("Could not fetch on-chain prices: %s, using defaults", e)
        return 3000, 1


def get_mock_oracle_price(web3: Web3, token_symbol: str = "WETH") -> float:
    """Read the configured price from the mock oracle on the fork.

    Queries the MockOracleProvider contract directly to get the
    price that was set during fixture setup, avoiding any drift from
    the GMX API.

    Use this in tests to get the exact price the mock oracle will use,
    ensuring limit order trigger prices match the oracle's acceptable range.

    :param web3: Web3 instance connected to the fork
    :param token_symbol: Token symbol (``"WETH"`` or ``"USDC"``)
    :return: Price in USD as float
    """
    chain = get_chain_name(web3.eth.chain_id).lower()

    production_provider_address = _resolve_contract_address(
        chain,
        ("chainlinkdatastreamprovider", "gmoracleprovider"),
        ARBITRUM_DEFAULTS["chainlink_provider"],
    )

    contract_path = _ETH_DEFI_ROOT / "abi" / "gmx" / "MockOracleProvider.json"
    with open(contract_path) as f:
        contract_data = json.load(f)

    mock = web3.eth.contract(address=production_provider_address, abi=contract_data["abi"])

    if token_symbol == "WETH":
        token_address = _resolve_token_address(chain, "WETH", ARBITRUM_DEFAULTS["weth"])
        decimals_factor = 10**12
    else:
        token_address = _resolve_token_address(chain, "USDC", ARBITRUM_DEFAULTS["usdc"])
        decimals_factor = 10**24

    min_price, max_price = mock.functions.tokenPrices(token_address).call()
    price_usd = max_price / decimals_factor

    logger.debug("Mock oracle price for %s: $%.2f", token_symbol, price_usd)
    return price_usd


def setup_mock_oracle(
    web3: Web3,
    eth_price_usd: int | None = None,
    usdc_price_usd: int | None = None,
):
    """Setup mock oracle by replacing bytecode at production address.

    Follows GMX's pattern from forked-env-example:

    1. Fetch current on-chain prices (if not provided)
    2. Deploy MockOracleProvider and get its bytecode
    3. Replace production oracle bytecode using ``anvil_setCode``
    4. Configure prices on the mock at production address

    This ensures mock prices match on-chain state for GMX validation.

    :param web3: Web3 instance
    :param eth_price_usd: ETH price in USD (if None, fetches from chain)
    :param usdc_price_usd: USDC price in USD (if None, fetches from chain)
    """
    provider_type = detect_provider_type(web3)
    chain = get_chain_name(web3.eth.chain_id).lower()
    logger.info("Setting up mock oracle (provider: %s)", provider_type)

    if eth_price_usd is None or usdc_price_usd is None:
        fetched_eth, fetched_usdc = fetch_on_chain_oracle_prices(web3)
        eth_price_usd = eth_price_usd or fetched_eth
        usdc_price_usd = usdc_price_usd or fetched_usdc

    logger.info("Using prices: ETH=$%d, USDC=$%d", eth_price_usd, usdc_price_usd)

    production_provider_address = _resolve_contract_address(
        chain,
        ("chainlinkdatastreamprovider", "gmoracleprovider"),
        ARBITRUM_DEFAULTS["chainlink_provider"],
    )

    contract_path = _ETH_DEFI_ROOT / "abi" / "gmx" / "MockOracleProvider.json"
    with open(contract_path) as f:
        contract_data = json.load(f)

    abi = contract_data["abi"]

    if "deployedBytecode" in contract_data:
        bytecode = contract_data["deployedBytecode"]
        if isinstance(bytecode, dict) and "object" in bytecode:
            bytecode = bytecode["object"]
    else:
        raise Exception("Could not find bytecode in MockOracleProvider.json")

    if not bytecode.startswith("0x"):
        bytecode = "0x" + bytecode

    logger.info("Loaded MockOracleProvider deployedBytecode: %d chars", len(bytecode))

    original_bytecode_before = web3.eth.get_code(production_provider_address)
    logger.info(
        "Original bytecode at %s: %d bytes",
        production_provider_address,
        len(original_bytecode_before),
    )

    logger.info("Replacing bytecode at production address %s...", production_provider_address)
    set_code(web3, production_provider_address, bytecode)

    new_bytecode = web3.eth.get_code(production_provider_address)
    expected_bytecode_bytes = bytes.fromhex(bytecode[2:]) if bytecode.startswith("0x") else bytes.fromhex(bytecode)

    logger.info("Verification after replacement:")
    logger.info("  Original (Chainlink) bytecode: %d bytes", len(original_bytecode_before))
    logger.info("  New (Mock) bytecode: %d bytes", len(new_bytecode))
    logger.info("  Expected (from JSON) bytecode: %d bytes", len(expected_bytecode_bytes))

    if original_bytecode_before == new_bytecode:
        if new_bytecode == expected_bytecode_bytes:
            logger.info("NOTE: Bytecode at address was already MockOracleProvider (from previous run)")
            logger.info("  Anvil persists state between runs. This is OK - mock is already installed.")
        else:
            logger.error("FAILED: Bytecode was NOT changed! Still has original Chainlink bytecode")
            logger.error("The anvil_setCode/tenderly_setCode call did not work")
            raise Exception("Bytecode replacement failed - code is still the original")

    if new_bytecode != expected_bytecode_bytes:
        logger.error("FAILED: New bytecode does NOT match expected mock bytecode")
        logger.error("Expected: %s...", expected_bytecode_bytes.hex()[:100])
        logger.error("Got: %s...", new_bytecode.hex()[:100])
        raise Exception("Bytecode replacement failed - code does not match expected mock")

    logger.info("Bytecode replacement verified:")
    logger.info("  Bytecode changed from original Chainlink provider")
    logger.info("  New bytecode matches MockOracleProvider.json")

    mock = web3.eth.contract(address=production_provider_address, abi=abi)
    logger.info("Mock oracle loaded at production address: %s", mock.address)

    logger.info("Testing mock oracle functions...")
    try:
        is_chainlink = mock.functions.isChainlinkOnChainProvider().call()
        logger.info("  isChainlinkOnChainProvider() = %s", is_chainlink)

        should_adjust = mock.functions.shouldAdjustTimestamp().call()
        logger.info("  shouldAdjustTimestamp() = %s", should_adjust)

        assert not is_chainlink, f"Expected isChainlinkOnChainProvider() to return False, got {is_chainlink}"
        assert not should_adjust, f"Expected shouldAdjustTimestamp() to return False, got {should_adjust}"

        logger.info("Mock functions working correctly - bytecode replacement successful!")
    except AssertionError:
        raise
    except Exception as e:
        logger.error("Failed to call mock functions: %s", e)
        logger.error("This suggests the bytecode replacement didn't work properly.")
        raise

    account = web3.eth.accounts[0]
    deal_eth(web3, account, 100_000_000 * 10**18)

    # WETH: 18 decimals -> price * 10^12
    weth_address = _resolve_token_address(chain, "WETH", ARBITRUM_DEFAULTS["weth"])
    weth_price = int(eth_price_usd * (10**12))
    logger.info("Setting WETH price to %d...", weth_price)

    weth_tx = mock.functions.setPrice(weth_address, weth_price, weth_price).build_transaction(
        {
            "from": account,
            "gas": 500_000,
            "gasPrice": web3.eth.gas_price,
        }
    )
    weth_tx_hash = web3.eth.send_transaction(weth_tx)
    assert_transaction_success_with_explanation(web3, weth_tx_hash, "Set WETH price on mock oracle")

    # USDC: 6 decimals -> price * 10^24
    usdc_address = _resolve_token_address(chain, "USDC", ARBITRUM_DEFAULTS["usdc"])
    usdc_price = int(usdc_price_usd * (10**24))
    logger.info("Setting USDC price to %d...", usdc_price)

    usdc_tx = mock.functions.setPrice(usdc_address, usdc_price, usdc_price).build_transaction({"from": account, "gas": 500_000, "gasPrice": web3.eth.gas_price})
    usdc_tx_hash = web3.eth.send_transaction(usdc_tx)
    assert_transaction_success_with_explanation(web3, usdc_tx_hash, "Set USDC price on mock oracle")

    logger.info("Mock oracle configured: ETH=$%d, USDC=$%d", eth_price_usd, usdc_price_usd)
    return production_provider_address
