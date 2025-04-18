# tests/test_gmx_config.py
# To pass these tests and most of the GMX tests ARBITRUM_JSON_RPC_URL & AVALANCHE_JSON_RPC_URL environment variables are set
import pytest

from eth_defi.gmx.config import GMXConfig
from gmx_python_sdk.scripts.v2.gmx_utils import ConfigManager
import os

mainnet_rpc = os.environ.get("ARBITRUM_JSON_RPC_URL")

pytestmark = pytest.mark.skipif(not mainnet_rpc, reason="No ARBITRUM_JSON_RPC_URL environment variable")


def test_init_basic(web3_arbitrum):
    """Test basic initialization without private key."""
    config = GMXConfig(web3_arbitrum, chain="arbitrum")

    # Check attributes
    assert config.chain == "arbitrum"
    assert config._private_key is None
    assert config._user_wallet_address is None
    assert config._rpc_url is not None

    # Check configs
    assert config._read_config is not None
    assert config._write_config is None
    assert not config.has_write_capability()


def test_init_with_wallet(web3_arbitrum):
    """Test initialization with wallet address."""
    wallet = "0x1234567890123456789012345678901234567890"
    config = GMXConfig(web3_arbitrum, chain="arbitrum", user_wallet_address=wallet)

    assert config._user_wallet_address == wallet
    assert config._base_config_dict["user_wallet_address"] == wallet


def test_init_with_private_key(web3_arbitrum):
    """Test initialization with private key."""
    private_key = "0x1234567890123456789012345678901234567890123456789012345678901234"
    config = GMXConfig(web3_arbitrum, chain="arbitrum", private_key=private_key)

    assert config._private_key == private_key
    assert config._write_config is not None
    assert config.has_write_capability()

    # Ensure private key is NOT in the base config dict
    assert "private_key" not in config._base_config_dict


def test_init_with_private_key_and_wallet(web3_arbitrum):
    """Test initialization with both private key and wallet address."""
    private_key = "0x1234567890123456789012345678901234567890123456789012345678901234"
    wallet = "0x1234567890123456789012345678901234567890"

    config = GMXConfig(web3_arbitrum, chain="arbitrum", private_key=private_key, user_wallet_address=wallet)

    assert config._private_key == private_key
    assert config._user_wallet_address == wallet
    assert config._write_config is not None

    # Check that write config dict has private key but base config doesn't
    assert "private_key" not in config._base_config_dict


def test_get_read_config(web3_arbitrum):
    """Test get_read_config method returns a ConfigManager without private key."""
    config = GMXConfig(web3_arbitrum, chain="arbitrum")
    read_config = config.get_read_config()

    assert isinstance(read_config, ConfigManager)
    assert read_config.chain == "arbitrum"
    assert read_config.private_key is None


def test_get_write_config_with_private_key(web3_arbitrum):
    """Test get_write_config method with private key."""
    private_key = "0x1234567890123456789012345678901234567890123456789012345678901234"
    config = GMXConfig(web3_arbitrum, chain="arbitrum", private_key=private_key)

    write_config = config.get_write_config()
    assert isinstance(write_config, ConfigManager)
    assert write_config.chain == "arbitrum"
    assert write_config.private_key == private_key


def test_get_write_config_without_private_key(web3_arbitrum):
    """Test get_write_config method without private key should raise ValueError."""
    config = GMXConfig(web3_arbitrum, chain="arbitrum")

    with pytest.raises(ValueError) as excinfo:
        config.get_write_config()

    assert "No private key provided" in str(excinfo.value)


def test_has_write_capability(web3_arbitrum):
    """Test has_write_capability method."""
    # Without private key
    config = GMXConfig(web3_arbitrum)
    assert not config.has_write_capability()

    # With private key
    private_key = "0x1234567890123456789012345678901234567890123456789012345678901234"
    config_with_key = GMXConfig(web3_arbitrum, private_key=private_key)
    assert config_with_key.has_write_capability()


def test_get_chain(web3_arbitrum):
    """Test get_chain method."""
    config = GMXConfig(web3_arbitrum, chain="arbitrum")
    assert config.get_chain() == "arbitrum"

    config_avax = GMXConfig(web3_arbitrum, chain="avalanche")
    assert config_avax.get_chain() == "avalanche"


def test_get_wallet_address(web3_arbitrum):
    """Test get_wallet_address method."""
    # Without wallet address
    config = GMXConfig(web3_arbitrum)
    assert config.get_wallet_address() is None

    # With wallet address
    wallet = "0x1234567890123456789012345678901234567890"
    config_with_wallet = GMXConfig(web3_arbitrum, user_wallet_address=wallet)
    assert config_with_wallet.get_wallet_address() == wallet


def test_get_network_info(web3_arbitrum):
    """Test get_network_info method."""
    config = GMXConfig(web3_arbitrum, chain="arbitrum")

    network_info = config.get_network_info()
    assert network_info["chain"] == "arbitrum"
    assert isinstance(network_info["rpc_url"], str)
    assert "chain_id" in network_info
