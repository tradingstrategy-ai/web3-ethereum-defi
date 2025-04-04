from web3 import Web3
from typing import Optional, Dict, Any
from gmx_python_sdk.scripts.v2.gmx_utils import ConfigManager


class GMXConfig:
    """Configuration adapter for GMX integration."""

    def __init__(
            self,
            web3: Web3,
            chain: str = "arbitrum",
            private_key: Optional[str] = None,
            user_wallet_address: Optional[str] = None
    ):
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

        # Extract RPC URL from web3 provider
        rpc_url = None
        if hasattr(web3.provider, "endpoint_uri"):
            rpc_url = web3.provider.endpoint_uri

        # Create config dictionary
        config_dict = {
            'rpcs': {chain: rpc_url},
            'chain_ids': {chain: web3.eth.chain_id},
            'private_key': private_key,
            'user_wallet_address': user_wallet_address
        }

        # Initialize the ConfigManager with our parameters
        self.config = ConfigManager(
            chain=chain,
            rpc=rpc_url,
            chain_id=web3.eth.chain_id,
            user_wallet_address=user_wallet_address,
            private_key=private_key,
            config=config_dict
        )

        # Set the configuration
        self.config.set_config_from_dict(config_dict)

    def get_config(self) -> ConfigManager:
        """Return the configured ConfigManager instance."""
        return self.config