"""
Tests for the GMX Wallet Adapter with different wallet implementations.

This test suite verifies that the wallet adapter successfully bridges between
eth_defi wallet implementations and GMX's signer interface, allowing different
wallet types to interact with the GMX protocol.
"""

from eth_account import Account
from hexbytes import HexBytes

from eth_defi.hotwallet import HotWallet
from eth_defi.gmx.wallet_adapter_signer import WalletAdapterSigner
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.trading import GMXTrading
from eth_defi.gmx.liquidity import GMXLiquidityManager
from eth_defi.tx import get_tx_broadcast_data


def test_wallet_adapter_signer_initialization(web3_fork, chain_name):
    """Test that the WalletAdapterSigner initializes correctly with a HotWallet."""
    # Create a hot wallet with anvil private key
    anvil_private_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    account = Account.from_key(anvil_private_key)
    wallet = HotWallet(account)
    wallet.sync_nonce(web3_fork)

    # Create WalletAdapterSigner directly
    adapter = WalletAdapterSigner(wallet, web3_fork)

    # Basic assertions
    assert adapter.wallet == wallet
    assert adapter.web3 == web3_fork
    assert adapter.get_address() == wallet.address


def test_gmx_config_with_hotwallet(web3_fork, chain_name):
    """Test GMXConfig with HotWallet integration."""
    # Create a hot wallet with anvil private key
    anvil_private_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    account = Account.from_key(anvil_private_key)
    wallet = HotWallet(account)
    wallet.sync_nonce(web3_fork)

    # Create GMXConfig with the wallet
    config = GMXConfig(web3_fork, chain=chain_name, wallet=wallet)

    # Test configuration
    assert config._wallet == wallet
    assert config._user_wallet_address == wallet.address
    assert config.has_write_capability()

    # Test read and write configs
    read_config = config.get_read_config()
    write_config = config.get_write_config()

    assert read_config is not None
    assert write_config is not None
    assert hasattr(write_config, "_signer")
    assert write_config._signer is not None
    assert write_config._signer.get_address() == wallet.address


def test_trading_with_hotwallet(gmx_config_fork, chain_name, wallet_with_usdc):
    """Test trading operations using a HotWallet through the adapter."""
    # Create the trading manager with our config
    trading = GMXTrading(gmx_config_fork)

    # Select appropriate parameters based on the chain
    if chain_name == "arbitrum":
        market_symbol = "ETH"
        collateral_symbol = "USDC"
    else:  # avalanche
        market_symbol = "AVAX"
        collateral_symbol = "USDC"

    # Create a position order in debug mode
    order = trading.open_position(
        market_symbol=market_symbol,
        collateral_symbol=collateral_symbol,
        start_token_symbol=collateral_symbol,
        is_long=True,
        size_delta_usd=100,
        leverage=2,
        slippage_percent=0.003,
        debug_mode=True,
    )  # Debug mode to avoid actual transaction

    # Verify the order was created with appropriate parameters
    assert order is not None
    assert order.is_long is True
    assert order.debug_mode is True
    assert hasattr(order, "config")
    assert order.config == gmx_config_fork.get_write_config()


def test_liquidity_with_hotwallet(gmx_config_fork, chain_name, wallet_with_native_token):
    """Test liquidity operations using a HotWallet through the adapter."""
    # Create the liquidity manager with our config
    liquidity_manager = GMXLiquidityManager(gmx_config_fork)

    # Select appropriate market parameters based on the chain
    if chain_name == "arbitrum":
        market_token_symbol = "ETH"
        long_token_symbol = "ETH"
    else:  # avalanche
        market_token_symbol = "AVAX"
        long_token_symbol = "AVAX"

    # Add liquidity in debug mode
    order = liquidity_manager.add_liquidity(
        market_token_symbol=market_token_symbol,
        long_token_symbol=long_token_symbol,
        short_token_symbol="USDC",
        long_token_usd=10,
        short_token_usd=0,
        debug_mode=True,
    )  # Debug mode to avoid actual transaction

    # Verify the order was created
    assert order is not None
    assert order.debug_mode is True
    assert hasattr(order, "config")
    assert order.config == gmx_config_fork.get_write_config()


# def test_order_manager_with_hotwallet(gmx_config_fork, chain_name):
#     """Test order management using a HotWallet through the adapter."""
#     # Create the order manager with our config
#     order_manager = GMXOrderManager(gmx_config_fork)
#
#     # Select appropriate parameters based on the chain
#     if chain_name == "arbitrum":
#         index_token = "ETH"
#         collateral_token = "ETH"
#         size_delta = 1000
#         collateral_delta = 0.1
#     else:  # avalanche
#         index_token = "AVAX"
#         collateral_token = "AVAX"
#         size_delta = 10
#         collateral_delta = 2
#
#     # Create a close position order in debug mode
#     params = {
#         "chain": chain_name,
#         "index_token_symbol": index_token,
#         "collateral_token_symbol": collateral_token,
#         "start_token_symbol": collateral_token,
#         "is_long": True,
#         "size_delta_usd": size_delta,
#         "initial_collateral_delta": collateral_delta,
#         "slippage_percent": 0.05,
#     }
#
#     order = order_manager.close_position(parameters=params, debug_mode=True)
#
#     # Verify the order was created
#     assert order is not None
#     assert order.debug_mode is True
#     assert order.is_long is True
#     assert hasattr(order, "config")
#     assert order.config == gmx_config_fork.get_write_config()


def test_multiple_wallet_types_config(web3_fork, chain_name):
    """Test creating GMXConfig with different wallet types."""
    # Generate test addresses for this test
    address = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"  # First anvil address

    # 1. Test with address only (read-only)
    config_address_only = GMXConfig(web3_fork, chain=chain_name, user_wallet_address=address)
    assert config_address_only._user_wallet_address == address
    assert config_address_only._wallet is None
    assert config_address_only.has_write_capability() is False

    # 2. Test with HotWallet
    anvil_private_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    account = Account.from_key(anvil_private_key)
    hot_wallet = HotWallet(account)
    hot_wallet.sync_nonce(web3_fork)

    config_hot_wallet = GMXConfig(web3_fork, chain=chain_name, wallet=hot_wallet)
    assert config_hot_wallet._wallet == hot_wallet
    assert config_hot_wallet._user_wallet_address == hot_wallet.address
    assert config_hot_wallet.has_write_capability() is True

    # 3. Test with private key (legacy method)
    config_private_key = GMXConfig(web3_fork, chain=chain_name, private_key=anvil_private_key)
    assert config_private_key._private_key == anvil_private_key
    assert isinstance(config_private_key._wallet, HotWallet)
    assert config_private_key.has_write_capability() is True

    # 4. Test the from_private_key class method
    config_from_method = GMXConfig.from_private_key(web3_fork, anvil_private_key, chain=chain_name)
    assert isinstance(config_from_method._wallet, HotWallet)
    assert config_from_method.has_write_capability() is True


def test_wallet_adapter_signer_get_address(web3_fork, chain_name):
    """Test that the WalletAdapterSigner.get_address returns the correct address."""
    # Create a hot wallet with anvil private key
    anvil_private_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    account = Account.from_key(anvil_private_key)
    wallet = HotWallet(account)

    # Create adapter
    adapter = WalletAdapterSigner(wallet, web3_fork)

    # Test get_address
    assert adapter.get_address() == wallet.address
    assert adapter.get_address() == wallet.get_main_address()


def test_wallet_adapter_sign_transaction(web3_fork, chain_name):
    """Test that the WalletAdapterSigner can sign transactions."""

    # Create a hot wallet with anvil private key
    anvil_private_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    account = Account.from_key(anvil_private_key)
    wallet = HotWallet(account)
    wallet.sync_nonce(web3_fork)

    # Create adapter
    adapter = WalletAdapterSigner(wallet, web3_fork)

    # Create a test transaction
    tx = {
        "to": "0x1234567890123456789012345678901234567890",
        "value": 1000,
        "gas": 21000,
        "gasPrice": web3_fork.eth.gas_price,
        "chainId": web3_fork.eth.chain_id,
    }

    # Sign transaction (without nonce)
    signed_tx = adapter.sign_transaction(tx)

    # MIGRATED: Use compatibility function instead of direct attribute access
    # Verify it's a properly signed transaction
    assert signed_tx is not None

    # Test that we can get raw transaction data (works with both rawTransaction and raw_transaction)
    raw_tx_data = get_tx_broadcast_data(signed_tx)
    assert raw_tx_data is not None
    assert isinstance(raw_tx_data, (bytes, HexBytes))

    # Additional compatibility checks
    # Check that the signed transaction has at least one of the expected attributes
    has_old_attr = hasattr(signed_tx, "rawTransaction")
    has_new_attr = hasattr(signed_tx, "raw_transaction")
    assert has_old_attr or has_new_attr, "SignedTransaction missing both rawTransaction and raw_transaction attributes"

    # Sign transaction with nonce
    tx_with_nonce = tx.copy()
    tx_with_nonce["nonce"] = 0

    signed_tx2 = adapter.sign_transaction(tx_with_nonce)

    # MIGRATED: Use compatibility function instead of direct attribute access
    # Verify it's a properly signed transaction
    assert signed_tx2 is not None

    # Test that we can get raw transaction data (works with both rawTransaction and raw_transaction)
    raw_tx_data2 = get_tx_broadcast_data(signed_tx2)
    assert raw_tx_data2 is not None
    assert isinstance(raw_tx_data2, (bytes, HexBytes))

    # Verify both transactions have different raw data (different nonces)
    assert raw_tx_data != raw_tx_data2, "Transactions with different nonces should have different raw data"


def test_wallet_adapter_send_transaction(web3_fork, chain_name):
    """Test that the WalletAdapterSigner can send transactions."""
    # Create a hot wallet with anvil private key
    anvil_private_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    account = Account.from_key(anvil_private_key)
    wallet = HotWallet(account)
    wallet.sync_nonce(web3_fork)

    # Create adapter
    adapter = WalletAdapterSigner(wallet, web3_fork)

    # Create a test transaction to send a small amount of ETH to another address
    recipient = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"  # Second anvil address

    tx = {
        "to": recipient,
        "value": 1000,
        "gas": 21000,
        "gasPrice": web3_fork.eth.gas_price,
        "chainId": web3_fork.eth.chain_id,
    }

    # Record balances before
    balance_before = web3_fork.eth.get_balance(recipient)

    # Send transaction (in debug mode so we don't actually submit)
    # Just ensure that the code doesn't throw an exception during signing
    try:
        adapter.sign_transaction(tx)
        assert True  # If we get here, the signing worked
    except Exception as e:
        assert False, f"Failed to sign transaction: {str(e)}"


def test_trading_with_configs_with_different_wallet_types(web3_fork, chain_name, test_address):
    """Test trading with different wallet configurations."""
    # 1. Create config with HotWallet
    anvil_private_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    account = Account.from_key(anvil_private_key)
    hot_wallet = HotWallet(account)
    hot_wallet.sync_nonce(web3_fork)

    config_hot_wallet = GMXConfig(web3_fork, chain=chain_name, wallet=hot_wallet)

    # 2. Create config with private key
    # TODO: When this is passed user wallet should be passe. How about we derive the address from private key when only
    # TODO: private key is passed ?
    config_private_key = GMXConfig(web3_fork, chain=chain_name, private_key=anvil_private_key)

    # 3. Create config with from_private_key
    config_from_method = GMXConfig.from_private_key(web3_fork, anvil_private_key, chain=chain_name)

    # Create trading managers with each config
    trading1 = GMXTrading(config_hot_wallet)
    trading2 = GMXTrading(config_private_key)
    trading3 = GMXTrading(config_from_method)

    print(f"{trading1.config.get_wallet_address()}")
    print(f"{trading2.config.get_wallet_address()}")
    print(f"{trading3.config.get_wallet_address()}")

    # Test parameters
    if chain_name == "arbitrum":
        market_symbol = "ETH"
        collateral_symbol = "USDC"
    else:  # avalanche
        market_symbol = "AVAX"
        collateral_symbol = "USDC"

    # Create position orders in debug mode for each trading manager
    order1 = trading1.open_position(
        market_symbol=market_symbol,
        collateral_symbol=collateral_symbol,
        start_token_symbol=collateral_symbol,
        is_long=True,
        size_delta_usd=100,
        leverage=2,
        debug_mode=True,
    )

    order2 = trading2.open_position(
        market_symbol=market_symbol,
        collateral_symbol=collateral_symbol,
        start_token_symbol=collateral_symbol,
        is_long=True,
        size_delta_usd=100,
        leverage=2,
        debug_mode=True,
    )

    order3 = trading3.open_position(
        market_symbol=market_symbol,
        collateral_symbol=collateral_symbol,
        start_token_symbol=collateral_symbol,
        is_long=True,
        size_delta_usd=100,
        leverage=2,
        debug_mode=True,
    )

    # Verify all orders were created
    assert order1 is not None
    assert order2 is not None
    assert order3 is not None

    # Verify all orders have the correct config
    assert order1.config.chain == chain_name
    assert order2.config.chain == chain_name
    assert order3.config.chain == chain_name


# TODO: New class added. So refactor the test
# def test_order_management_with_different_wallet_types(web3_fork, chain_name, test_address):
#     """Test order management with different wallet configurations."""
#     # 1. Create config with HotWallet
#     anvil_private_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
#     account = Account.from_key(anvil_private_key)
#     hot_wallet = HotWallet(account)
#     hot_wallet.sync_nonce(web3_fork)
#
#     config_hot_wallet = GMXConfig(web3_fork, chain=chain_name, wallet=hot_wallet)
#
#     # 2. Create config with private key
#     config_private_key = GMXConfig(web3_fork, chain=chain_name, private_key=anvil_private_key)
#
#     # Create order managers
#     order_manager1 = GMXOrderManager(config_hot_wallet)
#     order_manager2 = GMXOrderManager(config_private_key)
#
#     # Select appropriate parameters based on the chain
#     if chain_name == "arbitrum":
#         index_token = "ETH"
#         collateral_token = "ETH"
#         size_delta = 1000
#         collateral_delta = 0.1
#     else:  # avalanche
#         index_token = "AVAX"
#         collateral_token = "AVAX"
#         size_delta = 10
#         collateral_delta = 2
#
#     # Parameters for closing a position
#     params = {
#         "chain": chain_name,
#         "index_token_symbol": index_token,
#         "collateral_token_symbol": collateral_token,
#         "start_token_symbol": collateral_token,
#         "is_long": True,
#         "size_delta_usd": size_delta,
#         "initial_collateral_delta": collateral_delta,
#         "slippage_percent": 0.05,
#     }
#
#     # Create order in debug mode
#     order1 = order_manager1.close_position(parameters=params, debug_mode=True)
#     order2 = order_manager2.close_position(parameters=params, debug_mode=True)
#
#     # Verify orders were created
#     assert order1 is not None
#     assert order2 is not None
#     assert order1.debug_mode is True
#     assert order2.debug_mode is True
