from typing import Optional, Any, Union
from web3 import Web3

from eth_defi.chain import get_chain_name
from gmx_python_sdk.scripts.v2.gmx_utils import ConfigManager
from eth_defi.basewallet import BaseWallet
from eth_defi.gmx.wallet_adapter_signer import WalletAdapterSigner
from eth_defi.hotwallet import HotWallet


class GMXConfig:
    """Secure configuration adapter for GMX integration.

    This class adapts the GMX Python SDK's ConfigManager to work with
    the eth_defi wallet system, allowing different wallet implementations
    to be used interchangeably.
    """

    def __init__(
        self,
        web3: Web3,
        wallet: Optional[Union[BaseWallet, HotWallet]] = None,
        user_wallet_address: Optional[str] = None,
        private_key: Optional[str] = None,
    ):  # For backward compatibility
        """
        Initialize GMX configuration.

        Args:
            web3: Web3 instance connected to the appropriate network
            chain: Chain to use (arbitrum or avalanche)
            wallet: A BaseWallet implementation (HotWallet, Web3ProviderWallet, etc.)
            user_wallet_address: User wallet address (optional)
            private_key: Private key (optional, for backward compatibility)
        """
        self.web3 = web3

        # Used in some GMX API mappings
        chain = self.chain = get_chain_name(web3.eth.chain_id).lower()

        assert self.chain, f"Unsupported chain ID: {web3.eth.chain_id}. Supported chains are Arbitrum and Avalanche."

        self._wallet = wallet

        # For backward compatibility
        self._private_key = private_key

        # Create a HotWallet if private_key is provided but no wallet
        if private_key and not wallet:
            from eth_account import Account

            account = Account.from_key(private_key)
            self._wallet = HotWallet(account)
            self._wallet.sync_nonce(web3)

            # If no user_wallet_address is provided, derive it from the created wallet
            if not user_wallet_address:
                user_wallet_address = self._wallet.get_main_address()

        # Get the wallet address either from the provided address or the wallet
        if wallet and not user_wallet_address:
            user_wallet_address = wallet.get_main_address()
        self._user_wallet_address = user_wallet_address

        # Extract RPC URL from web3 provider
        self._rpc_url = None
        if hasattr(web3.provider, "endpoint_uri"):
            self._rpc_url = web3.provider.endpoint_uri

        # Create base config dictionary without private key
        self._base_config_dict = {
            # "rpcs": {chain: self._rpc_url},
            "chain_ids": {chain: web3.eth.chain_id},
            "user_wallet_address": user_wallet_address,
        }

        # Initialize a read-only ConfigManager instance (no private key)
        self._read_config = ConfigManager(
            chain=chain,
            chain_id=web3.eth.chain_id,
            user_wallet_address=user_wallet_address,
            config=self._base_config_dict,
            web3=web3,
        )

        # Only initialize a write config if we have a wallet
        self._write_config = None
        if wallet:
            # Create a ConfigManager for write operations
            # We do NOT pass private key directly - instead we'll create a custom signer
            self._write_config = self._create_write_config()
        elif private_key:
            # For backward compatibility
            write_config_dict = self._base_config_dict.copy()
            write_config_dict["private_key"] = private_key
            self._write_config = ConfigManager(
                chain=chain,
                chain_id=web3.eth.chain_id,
                user_wallet_address=user_wallet_address,
                private_key=private_key,
                config=write_config_dict,
                web3=self.web3,
            )

    def _create_write_config(self) -> ConfigManager:
        """Create a ConfigManager with transaction signing capability."""
        # Create a copy of the base config
        write_config_dict = self._base_config_dict.copy()

        # Create the unified adapter for any wallet type
        adapter_signer = WalletAdapterSigner(self._wallet, self.web3)

        # Ensure wallet nonce is synced with blockchain if it supports it
        if hasattr(self._wallet, "sync_nonce"):
            self._wallet.sync_nonce(self.web3)

        # Create ConfigManager with the adapter signer
        config_manager = ConfigManager(
            chain=self.chain,
            chain_id=self.web3.eth.chain_id,
            user_wallet_address=self._user_wallet_address,
            config=write_config_dict,
            signer=adapter_signer,
            web3=self.web3
        )

        return config_manager

    def get_read_config(self) -> ConfigManager:
        """
        Return a read-only configuration for query operations.
        This config does NOT contain private keys or signers.
        """
        return self._read_config

    def get_write_config(self) -> ConfigManager:
        """
        Return a configuration for transaction signing.

        Returns:
            ConfigManager with wallet-based signer if available

        Raises:
            ValueError: If no wallet was provided during initialization
        """
        if not self._write_config:
            raise ValueError("No wallet provided. Cannot perform write operations.")
        return self._write_config

    def has_write_capability(self) -> bool:
        """Check if this config has transaction signing capability."""
        return self._write_config is not None and (self._wallet is not None or self._private_key is not None)

    def get_chain(self) -> str:
        """Get the configured chain."""
        return self.chain

    def get_wallet_address(self) -> Optional[str]:
        """Get the configured wallet address."""
        return self._user_wallet_address

    def get_network_info(self) -> dict[str, Any]:
        """Get network information (chain, RPC, chain ID)."""
        return {
            "chain": self.chain,
            "rpc_url": self._rpc_url,
            "chain_id": self.web3.eth.chain_id,
        }

    @classmethod
    def from_private_key(cls, web3: Web3, private_key: str, chain: str = "arbitrum"):
        """Create a GMXConfig using a private key.

        This is a convenience method that creates a HotWallet and passes it to GMXConfig.

        Args:
            web3: Web3 instance
            private_key: Private key (with 0x prefix)
            chain: Chain to use (arbitrum or avalanche)

        Returns:
            GMXConfig instance with a HotWallet
        """
        from eth_account import Account

        account = Account.from_key(private_key)
        wallet = HotWallet(account)
        wallet.sync_nonce(web3)

        return cls(web3=web3, chain=chain, wallet=wallet)
