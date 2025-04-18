from web3 import Web3
from typing import Optional, Any
from gmx_python_sdk.scripts.v2.gmx_utils import ConfigManager


class GMXConfig:
    """Secure configuration adapter for GMX integration."""

    def __init__(self, web3: Web3, chain: str = "arbitrum", private_key: Optional[str] = None, user_wallet_address: Optional[str] = None):
        """
        Initialize GMX configuration.

        Args:
            web3: Web3 instance
            chain: Chain to use (arbitrum or avalanche)
            private_key: Private key for transactions (optional)
            user_wallet_address: User wallet address (optional)
        """
        self.web3 = web3
        self.chain = chain
        self._private_key = private_key
        self._user_wallet_address = user_wallet_address

        # Extract RPC URL from web3 provider
        self._rpc_url = None
        if hasattr(web3.provider, "endpoint_uri"):
            self._rpc_url = web3.provider.endpoint_uri

        # Create config dictionary - without private key for base config
        self._base_config_dict = {
            "rpcs": {chain: self._rpc_url},
            "chain_ids": {chain: web3.eth.chain_id},
            "user_wallet_address": user_wallet_address,
        }

        # Initialize a read-only ConfigManager instance
        self._read_config = ConfigManager(chain=chain, rpc=self._rpc_url, chain_id=web3.eth.chain_id, user_wallet_address=user_wallet_address, config=self._base_config_dict)

        # Only initialize a write config if we have a private key
        self._write_config = None
        if private_key:
            # Create a separate config dict with private key for write operations
            write_config_dict = self._base_config_dict.copy()
            write_config_dict["private_key"] = private_key

            self._write_config = ConfigManager(chain=chain, rpc=self._rpc_url, chain_id=web3.eth.chain_id, user_wallet_address=user_wallet_address, private_key=private_key, config=write_config_dict)

    def get_read_config(self) -> ConfigManager:
        """
        Return a read-only configuration for query operations.
        This config does NOT contain private keys.
        """
        return self._read_config

    def get_write_config(self) -> Optional[ConfigManager]:
        """
        Return a configuration for transaction signing.
        This config contains private keys for signing transactions.

        Returns:
            ConfigManager with private key if available, None otherwise

        Raises:
            ValueError: If no private key was provided during initialization
        """
        if not self._write_config:
            raise ValueError("No private key provided. Cannot perform write operations.")
        return self._write_config

    def has_write_capability(self) -> bool:
        """Check if this config has transaction signing capability."""
        return self._write_config is not None

    def get_chain(self) -> str:
        """Get the configured chain."""
        return self.chain

    def get_wallet_address(self) -> Optional[str]:
        """Get the configured wallet address."""
        return self._user_wallet_address

    def get_network_info(self) -> dict[str, Any]:
        """Get network information (chain, RPC, chain ID)."""
        return {"chain": self.chain, "rpc_url": self._rpc_url, "chain_id": self.web3.eth.chain_id}
