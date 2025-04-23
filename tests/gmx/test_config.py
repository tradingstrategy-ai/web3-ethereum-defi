"""
Tests for GMXConfig with parametrized chain testing.

This test suite verifies the configuration functionality across different chains
and with various wallet implementations.
"""
import pytest
from web3 import Web3
from eth_account import Account
from unittest.mock import MagicMock

from eth_defi.gmx.config import GMXConfig
from eth_defi.hotwallet import HotWallet
from eth_defi.basewallet import BaseWallet
from tests.gmx.conftest import CHAIN_CONFIG


def test_init_basic(chain_name, web3_mainnet):
    """Test basic initialization without wallet or private key."""
    config = GMXConfig(web3_mainnet, chain=chain_name)

    # Check attributes
    assert config.chain == chain_name
    assert config._wallet is None
    assert config._user_wallet_address is None
    assert config._rpc_url is not None

    # Check configs
    assert config._read_config is not None
    assert config._write_config is None
    assert not config.has_write_capability()


def test_init_with_wallet_address(chain_name, web3_mainnet):
    """Test initialization with wallet address only."""
    wallet_address = "0x1234567890123456789012345678901234567890"
    config = GMXConfig(web3_mainnet, chain=chain_name, user_wallet_address=wallet_address)

    assert config._user_wallet_address == wallet_address
    assert config._base_config_dict["user_wallet_address"] == wallet_address
    assert config._wallet is None
    assert not config.has_write_capability()


def test_init_with_hot_wallet(chain_name, web3_mainnet):
    """Test initialization with HotWallet."""
    # Create a hot wallet
    private_key = "0x1234567890123456789012345678901234567890123456789012345678901234"
    account = Account.from_key(private_key)
    wallet = HotWallet(account)
    wallet.sync_nonce(web3_mainnet)

    config = GMXConfig(web3_mainnet, chain=chain_name, wallet=wallet)

    assert config._wallet is wallet
    assert config._user_wallet_address == wallet.address
    assert config._write_config is not None
    assert config.has_write_capability()

    # Ensure the user wallet address is properly passed to the ConfigManager
    assert config._base_config_dict["user_wallet_address"] == wallet.address


def test_init_with_mock_provider_wallet(chain_name, web3_mainnet):
    """Test initialization with a mock wallet that acts like Web3ProviderWallet."""
    # Create a mock wallet that mimics Web3ProviderWallet
    mock_wallet = MagicMock(spec=BaseWallet)
    # Set up the main_address property to return a fixed address
    mock_wallet.get_main_address.return_value = "0x1234567890123456789012345678901234567890"
    mock_wallet.current_nonce = 0

    config = GMXConfig(web3_mainnet, chain=chain_name, wallet=mock_wallet)

    assert config._wallet is mock_wallet
    assert config._user_wallet_address == "0x1234567890123456789012345678901234567890"
    assert config._write_config is not None
    assert config.has_write_capability()


def test_init_with_wallet_and_address(chain_name, web3_mainnet):
    """Test initialization with both wallet and explicit address."""
    # Create a hot wallet
    private_key = "0x1234567890123456789012345678901234567890123456789012345678901234"
    account = Account.from_key(private_key)
    wallet = HotWallet(account)
    wallet.sync_nonce(web3_mainnet)

    # Use a different address than the wallet's address
    other_address = "0x9876543210987654321098765432109876543210"

    config = GMXConfig(web3_mainnet, chain=chain_name, wallet=wallet, user_wallet_address=other_address)

    # The explicitly provided address should be respected, but wallet should still be used for signing
    assert config._wallet is wallet
    assert config._user_wallet_address == other_address  # User-provided address is respected
    assert config._write_config is not None
    assert config.has_write_capability()

    # Check the ConfigManager has the user-provided address
    assert config._base_config_dict["user_wallet_address"] == other_address


def test_from_private_key_legacy_support(chain_name, web3_mainnet):
    """Test the from_private_key class method for backward compatibility."""
    private_key = "0x1234567890123456789012345678901234567890123456789012345678901234"

    config = GMXConfig.from_private_key(web3_mainnet, private_key, chain=chain_name)

    assert config._wallet is not None
    assert isinstance(config._wallet, HotWallet)
    assert config._user_wallet_address == config._wallet.address
    assert config.has_write_capability()

    # Check that write config was properly created
    write_config = config.get_write_config()
    assert write_config.chain == chain_name


def test_get_read_config(chain_name, web3_mainnet):
    """Test get_read_config method returns a ConfigManager."""
    config = GMXConfig(web3_mainnet, chain=chain_name)
    read_config = config.get_read_config()

    assert read_config.chain == chain_name
    assert read_config.private_key is None


def test_get_write_config_with_wallet(chain_name, web3_mainnet):
    """Test get_write_config method with wallet."""
    # Create a hot wallet
    private_key = "0x1234567890123456789012345678901234567890123456789012345678901234"
    account = Account.from_key(private_key)
    wallet = HotWallet(account)
    wallet.sync_nonce(web3_mainnet)

    config = GMXConfig(web3_mainnet, chain=chain_name, wallet=wallet)

    write_config = config.get_write_config()
    assert write_config.chain == chain_name

    # The wallet-adapter signer should be set on the ConfigManager
    assert write_config._signer is not None


def test_get_write_config_without_wallet(chain_name, web3_mainnet):
    """Test get_write_config method without wallet should raise ValueError."""
    config = GMXConfig(web3_mainnet, chain=chain_name)

    with pytest.raises(ValueError) as excinfo:
        config.get_write_config()

    assert "No wallet provided" in str(excinfo.value)


def test_has_write_capability(chain_name, web3_mainnet):
    """Test has_write_capability method."""
    # Without wallet
    config = GMXConfig(web3_mainnet, chain=chain_name)
    assert not config.has_write_capability()

    # With wallet
    private_key = "0x1234567890123456789012345678901234567890123456789012345678901234"
    account = Account.from_key(private_key)
    wallet = HotWallet(account)
    wallet.sync_nonce(web3_mainnet)

    config_with_wallet = GMXConfig(web3_mainnet, chain=chain_name, wallet=wallet)
    assert config_with_wallet.has_write_capability()


def test_get_chain(chain_name, web3_mainnet):
    """Test get_chain method."""
    config = GMXConfig(web3_mainnet, chain=chain_name)
    assert config.get_chain() == chain_name


def test_get_wallet_address(chain_name, web3_mainnet):
    """Test get_wallet_address method."""
    # Without wallet address
    config = GMXConfig(web3_mainnet, chain=chain_name)
    assert config.get_wallet_address() is None

    # With explicit wallet address
    wallet_address = "0x1234567890123456789012345678901234567890"
    config_with_address = GMXConfig(web3_mainnet, chain=chain_name, user_wallet_address=wallet_address)
    assert config_with_address.get_wallet_address() == wallet_address

    # With wallet
    private_key = "0x1234567890123456789012345678901234567890123456789012345678901234"
    account = Account.from_key(private_key)
    wallet = HotWallet(account)
    wallet.sync_nonce(web3_mainnet)

    config_with_wallet = GMXConfig(web3_mainnet, chain=chain_name, wallet=wallet)
    assert config_with_wallet.get_wallet_address() == wallet.address


def test_get_network_info(chain_name, web3_mainnet):
    """Test get_network_info method."""
    config = GMXConfig(web3_mainnet, chain=chain_name)

    network_info = config.get_network_info()
    assert network_info["chain"] == chain_name
    assert isinstance(network_info["rpc_url"], str)
    assert "chain_id" in network_info

    assert network_info["chain_id"] == CHAIN_CONFIG[chain_name]["chain_id"]


def test_wallet_adapter_signer(chain_name, web3_mainnet):
    """Test the WalletAdapterSigner works correctly."""
    # Create a hot wallet with a known private key
    private_key = "0x1234567890123456789012345678901234567890123456789012345678901234"
    account = Account.from_key(private_key)
    wallet = HotWallet(account)
    wallet.sync_nonce(web3_mainnet)

    # Create config with the wallet
    config = GMXConfig(web3_mainnet, chain=chain_name, wallet=wallet)

    # Get the write config with the adapter signer
    write_config = config.get_write_config()

    # Check that the adapter signer has the correct wallet address
    assert write_config._signer.get_address() == wallet.address
