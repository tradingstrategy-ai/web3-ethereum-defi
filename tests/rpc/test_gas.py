"""Gas helpers."""

import os
from unittest.mock import PropertyMock, patch

import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount

from eth_typing import HexAddress
from web3 import Web3, EthereumTesterProvider
from web3._utils.transactions import fill_nonce

from eth_defi.compat import check_if_retry_on_failure, install_retry_middleware_compat
from eth_defi.gas import BlockType, estimate_gas_fees, GasPriceMethod, apply_gas, GasPriceSuggestion, node_default_gas_price_strategy, GAS_PRICE_BUFFER_MULTIPLIER, MIN_PRIORITY_FEE_PER_CHAIN
from eth_defi.hotwallet import HotWallet
from eth_defi.hyperliquid.block import HYPEREVM_BIG_BLOCK_GAS_LIMIT, HYPEREVM_SMALL_BLOCK_LOOKBACK, fetch_small_block_base_fee
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import create_token
from eth_defi.tx import get_tx_broadcast_data


@pytest.fixture
def tester_provider():
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    return EthereumTesterProvider()


@pytest.fixture
def eth_tester(tester_provider):
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    return tester_provider.ethereum_tester


@pytest.fixture
def web3(tester_provider):
    """Set up a local unit testing blockchain."""
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    return Web3(tester_provider)


@pytest.fixture()
def deployer(web3) -> str:
    """Deploy account.

    Do some account allocation for tests.
    """
    return web3.eth.accounts[0]


@pytest.fixture()
def hot_wallet_account(web3) -> LocalAccount:
    """User account.

    Do some account allocation for tests.
    """
    return Account.create()


def test_gas_fees_london(web3: Web3, deployer: str):
    """Estimate gas fees on London hard-fork compatible blockchain.

    Note: We cannot test for non-London EVMs, as EthereumTester does not support them.

    https://github.com/ethereum/eth-tester/issues/233
    """
    fees = estimate_gas_fees(web3)
    assert fees.method == GasPriceMethod.london
    assert fees.base_fee == 1000000000
    # max_fee_per_gas = (priority_fee + 2 * base_fee) * buffer = (1G + 2G) * 1.12 = 3.36G
    assert fees.max_fee_per_gas == int(3000000000 * GAS_PRICE_BUFFER_MULTIPLIER)
    assert fees.max_priority_fee_per_gas == 1000000000


def test_gas_fees_london_no_buffer(web3: Web3, deployer: str):
    """Gas estimation with buffer disabled gives the raw value."""
    fees = estimate_gas_fees(web3, gas_price_buffer_multiplier=1.0)
    assert fees.method == GasPriceMethod.london
    assert fees.base_fee == 1000000000
    assert fees.max_fee_per_gas == 3000000000
    assert fees.max_priority_fee_per_gas == 1000000000


def test_gas_fees_min_priority_fee_explicit(web3: Web3):
    """An explicit minimum priority fee floors the RPC suggestion.

    EthereumTester suggests a 1 gwei priority fee, so a 5 gwei floor must
    override it, and ``maxFeePerGas`` must be derived from the floored value.
    """
    fees = estimate_gas_fees(web3, min_priority_fee=5_000_000_000)
    assert fees.method == GasPriceMethod.london
    assert fees.max_priority_fee_per_gas == 5_000_000_000
    # max_fee_per_gas = (floored priority fee + 2 * base fee) * buffer
    assert fees.max_fee_per_gas == int((5_000_000_000 + 2 * 1_000_000_000) * GAS_PRICE_BUFFER_MULTIPLIER)


def test_gas_fees_min_priority_fee_mainnet_default(web3: Web3):
    """The Ethereum mainnet per-chain priority fee floor is applied by default.

    A near-zero RPC priority fee suggestion (375,524 wei, as seen in the
    2026-06-10 Lagoon deployment incident) must be floored to the 1 gwei
    mainnet default so transactions do not linger unmined in the mempool.

    ``chain_id`` and ``max_priority_fee`` are mocked because EthereumTester
    cannot present itself as Ethereum mainnet nor suggest a near-zero tip.
    """
    with patch.object(type(web3.eth), "chain_id", new_callable=PropertyMock, return_value=1), patch.object(type(web3.eth), "max_priority_fee", new_callable=PropertyMock, return_value=375_524):
        fees = estimate_gas_fees(web3)
    assert fees.method == GasPriceMethod.london
    assert fees.max_priority_fee_per_gas == MIN_PRIORITY_FEE_PER_CHAIN[1] == 1_000_000_000
    assert fees.max_fee_per_gas == int((1_000_000_000 + 2 * 1_000_000_000) * GAS_PRICE_BUFFER_MULTIPLIER)


#: Base fee of small block 50464971, the last one before the 2026-11-05 incident.
SMALL_BLOCK_BASE_FEE = 2_202_900_000

#: What Goldsky's ``eth_gasPrice`` gives at a big block: its 0.1 gwei base fee plus a tip.
GOLDSKY_GAS_PRICE = 300_000_000


def _fetch_hyperevm_block(block_identifier: str | int) -> dict:
    """Mock block reads with a big block as ``latest`` and a small block before it."""
    if block_identifier == "latest":
        return {"number": 50464972, "baseFeePerGas": 100_000_000, "gasLimit": 30_000_000}
    assert block_identifier == 50464971
    return {"number": 50464971, "baseFeePerGas": SMALL_BLOCK_BASE_FEE, "gasLimit": 3_000_000}


@pytest.mark.parametrize("chain_id", [998, 999])
def test_gas_fees_hyperevm_big_block_as_latest(web3: Web3, chain_id: int) -> None:
    """HyperEVM fees follow the latest small block when ``latest`` is a big block.

    Uses the block values of the 2026-11-05 Hyper-AI crash. A settlement signed
    while big block 50464972 was ``latest`` got a 0.336 gwei ``maxFeePerGas``
    against a small-block base fee of about 2.2 gwei. It stayed unmined for
    96 minutes, well past the ten minute confirmation timeout.

    ``eth_gasPrice`` must not be used, as on Goldsky it also reflects the big block.

    The RPC values are mocked because EthereumTester cannot produce the two
    HyperEVM block types.
    """
    priority_fee = 100_000_000

    with (
        patch.object(type(web3.eth), "chain_id", new_callable=PropertyMock, return_value=chain_id),
        patch.object(type(web3.eth), "max_priority_fee", new_callable=PropertyMock, return_value=priority_fee),
        patch.object(type(web3.eth), "gas_price", new_callable=PropertyMock, return_value=GOLDSKY_GAS_PRICE) as gas_price,
        patch.object(web3.eth, "get_block", side_effect=_fetch_hyperevm_block),
    ):
        fees = estimate_gas_fees(web3)

    assert fees.method == GasPriceMethod.london
    assert fees.base_fee == SMALL_BLOCK_BASE_FEE
    assert fees.max_priority_fee_per_gas == priority_fee
    assert fees.max_fee_per_gas == int((priority_fee + 2 * SMALL_BLOCK_BASE_FEE) * GAS_PRICE_BUFFER_MULTIPLIER)
    gas_price.assert_not_called()


def test_gas_fees_hyperevm_small_block_as_latest(web3: Web3) -> None:
    """A small block as ``latest`` is used directly, without another block read."""
    small_block = {"number": 50464971, "baseFeePerGas": SMALL_BLOCK_BASE_FEE, "gasLimit": 3_000_000}

    with patch.object(web3.eth, "get_block", return_value=small_block) as get_block:
        fees = estimate_gas_fees(web3, chain_id=999)

    assert fees.base_fee == SMALL_BLOCK_BASE_FEE
    get_block.assert_called_once_with("latest")


def test_gas_fees_hyperevm_large_block_type(web3: Web3) -> None:
    """A large block sender is priced from ``eth_bigBlockGasPrice``, not the small blocks."""
    with (
        patch.object(type(web3.eth), "chain_id", new_callable=PropertyMock, return_value=999),
        patch.object(type(web3.eth), "max_priority_fee", new_callable=PropertyMock, return_value=0),
        patch.object(web3.eth, "get_block", return_value={"baseFeePerGas": 2_000_000_000}),
        patch("eth_defi.gas.is_anvil", return_value=False),
        patch.object(web3.manager, "request_blocking", return_value="0x5f5e100") as request_blocking,
    ):
        fees = estimate_gas_fees(web3, block_type=BlockType.large)

    assert fees.base_fee == 100_000_000
    assert fees.max_fee_per_gas == int(2 * 100_000_000 * GAS_PRICE_BUFFER_MULTIPLIER)
    request_blocking.assert_called_once_with("eth_bigBlockGasPrice", [])


def test_big_block_gas_price_is_retried() -> None:
    """``eth_bigBlockGasPrice`` retries like the standard ``eth_gasPrice`` it stands in for.

    The retry middleware and the HTTP provider each keep their own allowlist.
    """
    web3 = Web3(Web3.HTTPProvider("http://localhost:1"))
    install_retry_middleware_compat(web3)

    assert check_if_retry_on_failure("eth_bigBlockGasPrice")
    assert "eth_bigBlockGasPrice" in web3.provider.exception_retry_configuration.method_allowlist
    assert "eth_gasPrice" in web3.provider.exception_retry_configuration.method_allowlist


def test_gas_fees_hyperevm_small_block_type(web3: Web3) -> None:
    """An explicit small block type prices the same as the HyperEVM default."""
    with patch.object(web3.eth, "get_block", side_effect=_fetch_hyperevm_block):
        fees = estimate_gas_fees(web3, chain_id=999, block_type=BlockType.small)

    assert fees.base_fee == SMALL_BLOCK_BASE_FEE


def test_gas_fees_chain_id_argument(web3: Web3) -> None:
    """A given chain id selects the chain's pricing without reading it from the RPC."""
    with (
        patch.object(type(web3.eth), "chain_id", new_callable=PropertyMock, return_value=1) as rpc_chain_id,
        patch.object(web3.eth, "get_block", side_effect=_fetch_hyperevm_block),
    ):
        fees = estimate_gas_fees(web3, chain_id=999)

    assert fees.base_fee == SMALL_BLOCK_BASE_FEE
    rpc_chain_id.assert_not_called()


@pytest.mark.skipif(not os.environ.get("JSON_RPC_HYPERLIQUID"), reason="JSON_RPC_HYPERLIQUID environment variable required")
def test_gas_fees_hyperevm_real_rpc() -> None:
    """Both HyperEVM fee markets can be read from a real provider.

    Current fees move with every block, so only check they are usable, then
    replay the incident block, whose fees are fixed.
    """
    web3 = create_multi_provider_web3(os.environ["JSON_RPC_HYPERLIQUID"])

    for block_type in BlockType:
        fees = estimate_gas_fees(web3, block_type=block_type)
        assert fees.base_fee > 0
        assert fees.max_fee_per_gas > fees.base_fee

    # The big block of the 2026-11-05 incident must resolve to the small block before it
    big_block = web3.eth.get_block(50464972)
    assert big_block["gasLimit"] == HYPEREVM_BIG_BLOCK_GAS_LIMIT
    assert fetch_small_block_base_fee(web3, big_block) > 2_000_000_000


@pytest.mark.parametrize("block_type", list(BlockType))
def test_gas_fees_anvil_fork_of_hyperevm(web3: Web3, block_type: BlockType) -> None:
    """An Anvil fork prices from ``latest`` despite its chain id and 30M gas limit.

    A fork has one fee market. Treating locally mined blocks as large blocks
    would walk back to an upstream fee, and Anvil lacks ``eth_bigBlockGasPrice``.
    """
    latest = {"number": 100, "baseFeePerGas": 1_000_000_000, "gasLimit": 30_000_000}

    with (
        patch("eth_defi.gas.is_anvil", return_value=True),
        patch.object(web3.eth, "get_block", return_value=latest) as get_block,
    ):
        fees = estimate_gas_fees(web3, chain_id=999, block_type=block_type)

    assert fees.base_fee == 1_000_000_000
    get_block.assert_called_once_with("latest")


def test_small_block_base_fee_consecutive_big_blocks(web3: Web3) -> None:
    """The walk continues past more than one big block."""
    big_block = {"number": 102, "baseFeePerGas": 100_000_000, "gasLimit": HYPEREVM_BIG_BLOCK_GAS_LIMIT}
    blocks = {
        101: {"number": 101, "baseFeePerGas": 100_000_000, "gasLimit": HYPEREVM_BIG_BLOCK_GAS_LIMIT},
        100: {"number": 100, "baseFeePerGas": SMALL_BLOCK_BASE_FEE, "gasLimit": 3_000_000},
    }

    with patch.object(web3.eth, "get_block", side_effect=blocks.get):
        assert fetch_small_block_base_fee(web3, big_block) == SMALL_BLOCK_BASE_FEE


def test_small_block_base_fee_lookback_exhausted(web3: Web3) -> None:
    """A provider that only returns big blocks raises, rather than walking forever.

    Falling back to a big block's base fee would repeat the 2026-11-05 incident.
    """
    big_block = {"number": 100, "baseFeePerGas": 100_000_000, "gasLimit": HYPEREVM_BIG_BLOCK_GAS_LIMIT}

    with patch.object(web3.eth, "get_block", return_value=big_block) as get_block:
        with pytest.raises(RuntimeError, match="No small block"):
            fetch_small_block_base_fee(web3, big_block)

    # The first block is supplied by the caller, so it needs no read
    assert get_block.call_count == HYPEREVM_SMALL_BLOCK_LOOKBACK - 1


@pytest.mark.parametrize("block_type", list(BlockType))
def test_gas_fees_block_type_single_fee_market(web3: Web3, block_type: BlockType) -> None:
    """A block type makes no difference on a chain with one fee market."""
    fees = estimate_gas_fees(web3, chain_id=8453, block_type=block_type)
    assert fees == estimate_gas_fees(web3, chain_id=8453)


def test_gas_fees_other_chains_use_latest_block(web3: Web3) -> None:
    """Chains with a single fee market keep pricing from the ``latest`` block.

    A block at the large block gas limit means nothing on these chains, so no
    earlier block is read.
    """
    latest = {"number": 100, "baseFeePerGas": 1_000_000_000, "gasLimit": 30_000_000}

    with (
        patch.object(type(web3.eth), "chain_id", new_callable=PropertyMock, return_value=8453),
        patch.object(web3.eth, "get_block", return_value=latest) as get_block,
    ):
        fees = estimate_gas_fees(web3)

    assert fees.base_fee == 1_000_000_000
    get_block.assert_called_once_with("latest")


def test_gas_fees_min_priority_fee_disabled(web3: Web3):
    """Passing ``min_priority_fee=0`` disables the per-chain floor.

    With the floor disabled, the raw near-zero RPC suggestion is used as-is
    even on Ethereum mainnet. Mocks as in the mainnet default test above.
    """
    with patch.object(type(web3.eth), "chain_id", new_callable=PropertyMock, return_value=1), patch.object(type(web3.eth), "max_priority_fee", new_callable=PropertyMock, return_value=375_524):
        fees = estimate_gas_fees(web3, min_priority_fee=0)
    assert fees.max_priority_fee_per_gas == 375_524


def test_raw_transaction_with_gas(web3: Web3, eth_tester, deployer: HexAddress, hot_wallet_account: LocalAccount):
    """Create a raw transaction with gas information."""

    gas_fees = estimate_gas_fees(web3)

    """Transfer tokens between users."""
    token = create_token(web3, deployer, "Cow token", "COWS", 100_000 * 10**18)

    # Drop some ETH and token to the hot wallet
    web3.eth.send_transaction({"from": deployer, "to": hot_wallet_account.address, "value": 1 * 10**18})
    token.functions.transfer(hot_wallet_account.address, 50_000 * 10**18).transact({"from": deployer})

    # Create a raw transaction
    # Move 10 tokens from deployer to user1
    # https://web3py.readthedocs.io/en/stable/contracts.html?highlight=buildTransaction#web3.contract.ContractFunction.build_transaction
    tx = token.functions.transfer(hot_wallet_account.address, 10 * 10**18).build_transaction(
        {
            "from": hot_wallet_account.address,
            "chainId": web3.eth.chain_id,
            "gas": 150_000,  # 150k gas should be more than enough for ERC20.transfer()
        }
    )

    tx = fill_nonce(web3, tx)
    apply_gas(tx, gas_fees)

    signed = hot_wallet_account.sign_transaction(tx)
    raw_bytes = get_tx_broadcast_data(signed)
    tx_hash = web3.eth.send_raw_transaction(raw_bytes)
    receipt = web3.eth.get_transaction_receipt(tx_hash)
    assert receipt.status == 1  # 1=success and mined


def test_build_transaction_legacy(web3: Web3, deployer: str, hot_wallet_account):
    """We can apply gas fees to the transactions signed with HotWallet."""

    # Unless we override the default gas price strategy,
    # web3.py is going to default to Ethereum mainnet London style transactions
    # that do not understand about "gasPrice" parameter
    web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)

    # 99 GWei
    gas_fees = GasPriceSuggestion(method=GasPriceMethod.legacy, legacy_gas_price=99 * 10**9)

    token = create_token(web3, deployer, "Cow token", "COWS", 100_000 * 10**18)

    # Drop some ETH and token to the hot wallet
    web3.eth.send_transaction({"from": deployer, "to": hot_wallet_account.address, "value": 1 * 10**18})

    # Build a transaction using gas hints and signed locally
    # before broadcasting
    hot_wallet = HotWallet(hot_wallet_account)
    hot_wallet.sync_nonce(web3)

    tx = token.functions.approve(deployer, 100).build_transaction({"from": hot_wallet.address})

    assert "gas" in tx
    apply_gas(tx, gas_fees)

    signed_tx = hot_wallet.sign_transaction_with_new_nonce(tx)
    signed_bytes = get_tx_broadcast_data(signed_tx)
    assert len(signed_bytes) > 0

    tx_hash = web3.eth.send_raw_transaction(signed_bytes)
    receipt = web3.eth.get_transaction_receipt(tx_hash)
    assert receipt.status == 1
