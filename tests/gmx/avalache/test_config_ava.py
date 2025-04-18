from eth_defi.gmx.config import GMXConfig


def test_avalanche_connection_basic(web3_avalanche):
    """Test basic Avalanche connection parameters."""
    config = GMXConfig(web3_avalanche, chain="avalanche")

    # Check that the chain is set correctly
    assert config.chain == "avalanche"

    # Check that the network info shows avalanche
    network_info = config.get_network_info()
    assert network_info["chain"] == "avalanche"

    # Check that the read config is initialized with avalanche
    read_config = config.get_read_config()
    assert read_config.chain == "avalanche"

    # The chain_id should match the actual Avalanche chain ID
    assert network_info["chain_id"] == 43114


def test_avalanche_connection_with_private_key(web3_avalanche):
    """Test Avalanche connection with transaction capabilities."""
    private_key = "0x1234567890123456789012345678901234567890123456789012345678901234"
    wallet = "0x1234567890123456789012345678901234567890"

    config = GMXConfig(web3_avalanche, chain="avalanche", private_key=private_key, user_wallet_address=wallet)

    # Check that write capability is properly set up for Avalanche
    assert config.has_write_capability()

    # Get the write config and verify it's set for Avalanche
    write_config = config.get_write_config()
    assert write_config.chain == "avalanche"
    assert write_config.private_key == private_key

    # Verify the correct chain is configured
    network_info = config.get_network_info()
    assert network_info["chain"] == "avalanche"
    assert network_info["chain_id"] == 43114
