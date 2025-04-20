"""
Tests for GMXConfig with parametrized chain testing.

This test suite verifies the configuration functionality across different chains.
"""
import pytest
from eth_defi.gmx.config import GMXConfig
from tests.gmx.conftest import CHAIN_CONFIG


def test_init_basic(chain_name, web3_mainnet):
    """Test basic initialization without private key."""
    config = GMXConfig(web3_mainnet, chain=chain_name)

    # Check attributes
    assert config.chain == chain_name
    assert config._private_key is None
    assert config._user_wallet_address is None
    assert config._rpc_url is not None

    # Check configs
    assert config._read_config is not None
    assert config._write_config is None
    assert not config.has_write_capability()


def test_init_with_wallet(chain_name, web3_mainnet):
    """Test initialization with wallet address."""
    wallet = "0x1234567890123456789012345678901234567890"
    config = GMXConfig(web3_mainnet, chain=chain_name, user_wallet_address=wallet)

    assert config._user_wallet_address == wallet
    assert config._base_config_dict["user_wallet_address"] == wallet


def test_init_with_private_key(chain_name, web3_mainnet):
    """Test initialization with private key."""
    private_key = "0x1234567890123456789012345678901234567890123456789012345678901234"
    config = GMXConfig(web3_mainnet, chain=chain_name, private_key=private_key)

    assert config._private_key == private_key
    assert config._write_config is not None
    assert config.has_write_capability()

    # Ensure private key is NOT in the base config dict
    assert "private_key" not in config._base_config_dict


def test_init_with_private_key_and_wallet(chain_name, web3_mainnet):
    """Test initialization with both private key and wallet address."""
    private_key = "0x1234567890123456789012345678901234567890123456789012345678901234"
    wallet = "0x1234567890123456789012345678901234567890"

    config = GMXConfig(web3_mainnet, chain=chain_name, private_key=private_key, user_wallet_address=wallet)

    assert config._private_key == private_key
    assert config._user_wallet_address == wallet
    assert config._write_config is not None

    # Check that write config dict has private key but base config doesn't
    assert "private_key" not in config._base_config_dict


def test_get_read_config(chain_name, web3_mainnet):
    """Test get_read_config method returns a ConfigManager without private key."""
    config = GMXConfig(web3_mainnet, chain=chain_name)
    read_config = config.get_read_config()

    assert read_config.chain == chain_name
    assert read_config.private_key is None


def test_get_write_config_with_private_key(chain_name, web3_mainnet):
    """Test get_write_config method with private key."""
    private_key = "0x1234567890123456789012345678901234567890123456789012345678901234"
    config = GMXConfig(web3_mainnet, chain=chain_name, private_key=private_key)

    write_config = config.get_write_config()
    assert write_config.chain == chain_name
    assert write_config.private_key == private_key


def test_get_write_config_without_private_key(chain_name, web3_mainnet):
    """Test get_write_config method without private key should raise ValueError."""
    config = GMXConfig(web3_mainnet, chain=chain_name)

    with pytest.raises(ValueError) as excinfo:
        config.get_write_config()

    assert "No private key provided" in str(excinfo.value)


def test_has_write_capability(chain_name, web3_mainnet):
    """Test has_write_capability method."""
    # Without private key
    config = GMXConfig(web3_mainnet, chain=chain_name)
    assert not config.has_write_capability()

    # With private key
    private_key = "0x1234567890123456789012345678901234567890123456789012345678901234"
    config_with_key = GMXConfig(web3_mainnet, chain=chain_name, private_key=private_key)
    assert config_with_key.has_write_capability()


def test_get_chain(chain_name, web3_mainnet):
    """Test get_chain method."""
    config = GMXConfig(web3_mainnet, chain=chain_name)
    assert config.get_chain() == chain_name


def test_get_wallet_address(chain_name, web3_mainnet):
    """Test get_wallet_address method."""
    # Without wallet address
    config = GMXConfig(web3_mainnet, chain=chain_name)
    assert config.get_wallet_address() is None

    # With wallet address
    wallet = "0x1234567890123456789012345678901234567890"
    config_with_wallet = GMXConfig(web3_mainnet, chain=chain_name, user_wallet_address=wallet)
    assert config_with_wallet.get_wallet_address() == wallet


def test_get_network_info(chain_name, web3_mainnet):
    """Test get_network_info method."""
    config = GMXConfig(web3_mainnet, chain=chain_name)

    network_info = config.get_network_info()
    assert network_info["chain"] == chain_name
    assert isinstance(network_info["rpc_url"], str)
    assert "chain_id" in network_info

    assert network_info["chain_id"] == CHAIN_CONFIG[chain_name]["chain_id"]
