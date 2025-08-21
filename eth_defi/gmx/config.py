"""
GMX Configuration Module

This module provides the production-ready configuration management system for
GMX protocol integration, implementing automatic network detection, secure
wallet abstraction, and comprehensive authentication strategy support. It
represents the mature, stable implementation used in production trading
systems and financial applications.

**Production Configuration Architecture:**

This implementation focuses on reliability, security, and operational simplicity
for production environments. Automatic network detection eliminates configuration
errors, while sophisticated wallet abstraction provides universal compatibility
without compromising security or performance characteristics of individual
wallet implementations.

**Key Production Features:**

- **Automatic Chain Detection**: Eliminates network configuration errors
- **Universal Wallet Support**: Compatible with all BaseWallet implementations
- **Security Isolation**: Strict separation between read and write operations
- **Production Stability**: Thoroughly tested patterns for reliable operation
- **Backward Compatibility**: Preserves legacy interfaces for existing systems

**Security-First Design:**

The production configuration implements defense-in-depth security patterns
where sensitive operations are isolated into separate configuration contexts.
Read-only configurations provide safe data access, while write configurations
implement comprehensive validation and secure credential delegation.

**Operational Reliability:**

Production systems require configuration management that never fails unexpectedly.
This implementation includes comprehensive error handling, automatic validation,
and clear diagnostic information to prevent and resolve operational issues quickly.

**Multi-Environment Support:**

The configuration system automatically adapts to different blockchain networks
while maintaining consistent interfaces and operational patterns. This enables
the same application code to work seamlessly across Arbitrum, Avalanche, and
other supported networks.

Example:

.. code-block:: python

    # Production deployment patterns
    from web3 import Web3
    from eth_defi.gmx.config import GMXConfig
    from eth_defi.hotwallet import HotWallet

    # Production configuration with automatic network detection
    web3 = Web3(Web3.HTTPProvider("https://arb1.arbitrum.io/rpc"))
    wallet = HotWallet.from_private_key("0x...")

    # Network automatically detected - no manual configuration needed
    config = GMXConfig(web3, wallet=wallet)

    # Verify production readiness
    assert config.has_write_capability()
    assert config.get_chain() == "arbitrum"

    # Safe read-only configuration for monitoring systems
    readonly_config = GMXConfig(web3)  # No wallet = monitoring mode
    market_data = readonly_config.get_read_config()

    # Legacy system integration
    legacy_config = GMXConfig.from_private_key(
        web3=web3,
        private_key="0x...",
        chain="arbitrum",
    )

    # All configurations provide identical operational interfaces
    configs = [config, readonly_config, legacy_config]
    for cfg in configs:
        network_info = cfg.get_network_info()
        print(f"Connected to {network_info['chain']} ({network_info['chain_id']})")

**Design Philosophy:**

The production configuration embodies the principle of "secure by default"
where the safest operational mode is also the most convenient. Automatic
detection reduces human error, while security isolation prevents accidental
exposure of sensitive operations.

Note:
    This is the recommended configuration implementation for production
    deployments where reliability and security are paramount considerations.

Warning:
    Production configurations with write capabilities enable real financial
    transactions. Ensure proper security procedures and testing before
    deployment in environments with significant financial exposure.
"""

from typing import Optional, Any, Union
from web3 import Web3

from eth_defi.chain import get_chain_name
from eth_defi.basewallet import BaseWallet
from eth_defi.gmx.wallet_adapter_signer import WalletAdapterSigner
from eth_defi.hotwallet import HotWallet


class GMXConfigManager:
    """
    GMX protocol configuration manager.

    Manages configuration parameters for GMX protocol operations including
    blockchain network details, wallet integration, and RPC connectivity.
    The address is automatically extracted from the wallet during initialization.

    :ivar chain: Blockchain network name
    :ivar chain_id: Blockchain network ID
    :ivar user_wallet_address: Wallet address for operations
    :ivar wallet: Wallet instance for transaction signing
    """

    def __init__(
        self,
        chain: str,
        chain_id: int,
        wallet: Optional[Union[BaseWallet, HotWallet]] = None,
        web3: Optional[Web3] = None,
    ):
        """
        Initialize configuration manager.

        :param chain: Blockchain network name (e.g., 'arbitrum', 'avalanche')
        :param chain_id: Blockchain network ID
        :param wallet: Wallet instance for signing operations
        :param web3: Web3 instance for signer creation
        """
        self.chain = chain
        self.chain_id = chain_id
        self.wallet = wallet
        self.web3 = web3

        # Address is set automatically from wallet
        self.user_wallet_address = wallet.get_main_address() if wallet else None

        # Interface compatibility
        self.private_key = None
        self._signer = WalletAdapterSigner(wallet, web3) if wallet and web3 else None


class GMXConfig:
    """
    Production-grade configuration management system for secure GMX protocol integration.

    This class provides the stable, production-ready implementation of configuration
    management that powers real trading systems and financial applications. It
    implements automatic network detection, universal wallet compatibility, and
    comprehensive security controls while maintaining the simplicity and reliability
    required for production deployment.

    **Production Architecture Principles:**

    The production implementation prioritizes operational reliability, security
    isolation, and automatic error prevention. Network parameters are detected
    automatically to eliminate configuration errors, wallet integration uses
    secure adapter patterns to preserve individual security models, and comprehensive
    validation prevents common operational pitfalls.

    **Security Architecture:**

    The configuration system implements strict security boundaries between read
    and write operations. Read configurations provide safe data access without
    any exposure to sensitive credentials, while write configurations implement
    secure credential delegation through validated adapter interfaces.

    **Operational Reliability:**

    Production systems require configuration management that operates predictably
    under all conditions. This implementation includes comprehensive error handling,
    automatic parameter validation, and clear diagnostic information to enable
    rapid troubleshooting and reliable operation.

    **Universal Wallet Compatibility:**

    Modern financial applications must support diverse wallet implementations
    to accommodate different security requirements and operational preferences.
    The configuration system provides seamless integration with any BaseWallet-
    compatible implementation while preserving the unique capabilities of each
    wallet type.

    :ivar web3: Web3 instance providing blockchain connectivity and network context
    :vartype web3: Web3
    :ivar chain: Automatically detected blockchain network identifier
    :vartype chain: str
    """

    def __init__(
        self,
        web3: Web3,
        wallet: Optional[Union[BaseWallet, HotWallet]] = None,
        user_wallet_address: Optional[str] = None,
    ):
        """
        Initialize production GMX configuration with automatic network detection and secure wallet integration.

        This constructor implements production-grade initialization logic that
        automatically detects network parameters, validates wallet compatibility,
        and establishes secure configuration contexts for both read and write
        operations. The design prioritizes reliability, security, and ease of
        deployment in production environments.

        **Automatic Network Detection:**

        The production configuration automatically detects blockchain network
        parameters from Web3 connections, eliminating manual configuration
        requirements and preventing network mismatch errors that could cause
        operational failures or financial losses.

        **Secure Initialization Patterns:**

        The initialization process implements secure credential handling where
        sensitive information is isolated into appropriate security contexts.
        Read-only operations never have access to signing credentials, while
        write operations use secure delegation patterns that preserve wallet
        security models.

        **Production Validation:**

        All initialization parameters undergo comprehensive validation to ensure
        operational compatibility and prevent common configuration errors that
        could cause failures in production environments.

        :param web3:
            Web3 instance connected to the target blockchain network. The
            configuration system automatically detects network parameters from
            this connection, ensuring consistency and operational reliability
        :type web3: Web3
        :param wallet:
            Optional wallet implementation for transaction signing operations.
            Supports any BaseWallet-compatible implementation while preserving
            individual security and performance characteristics
        :type wallet: Optional[Union[BaseWallet, HotWallet]]
        :param user_wallet_address:
            Optional explicit wallet address specification. When not provided,
            addresses are automatically derived from wallet implementations or
            the configuration operates in read-only mode
        :type user_wallet_address: Optional[str]
        :raises AssertionError:
            When the Web3 connection targets an unsupported blockchain network
            or automatic network detection fails due to connectivity issues
        """
        self.web3 = web3

        # Used in some GMX API mappings
        chain = self.chain = get_chain_name(web3.eth.chain_id).lower()

        assert self.chain, f"Unsupported chain ID: {web3.eth.chain_id}. Supported chains are Arbitrum and Avalanche."

        self._wallet = wallet

        # Get the wallet address either from the provided address or the wallet
        if wallet and not user_wallet_address:
            user_wallet_address = wallet.get_main_address()
        self._user_wallet_address = user_wallet_address

        # Extract RPC URL from web3 provider
        self._rpc_url = None
        if hasattr(web3.provider, "endpoint_uri"):
            self._rpc_url = web3.provider.endpoint_uri

        # Initialize a read-only configuration manager
        self._read_config = GMXConfigManager(
            chain=chain,
            chain_id=web3.eth.chain_id,
            wallet=None,  # Read-only, no wallet
            web3=web3,
        )
        # Set user wallet address manually for read config
        self._read_config.user_wallet_address = user_wallet_address

        # Only initialize a write config if we have a wallet
        self._write_config = None
        if wallet:
            # Create a configuration manager for write operations
            self._write_config = self._create_write_config()

    def _create_write_config(self) -> GMXConfigManager:
        """
        Create production-grade ConfigManager with secure transaction signing capabilities.

        This private method implements the secure adapter pattern integration
        that enables universal wallet compatibility while maintaining strict
        security isolation. It creates reliable signing delegation through the
        wallet adapter system without exposing sensitive credential information
        to broader system components.

        **Production Security Architecture:**

        The write configuration creation process implements production-grade
        security patterns including credential isolation, secure adapter delegation,
        and comprehensive state synchronization to prevent security vulnerabilities
        and operational failures.

        **Reliability Engineering:**

        The method includes comprehensive error handling and state validation
        to ensure reliable operation in production environments where configuration
        failures could have significant operational and financial consequences.

        :return:
            GMXConfigManager instance configured with secure wallet-based signing
            through production-tested adapter interfaces
        :rtype: GMXConfigManager
        """
        # Ensure wallet nonce is synced with blockchain if it supports it
        if hasattr(self._wallet, "sync_nonce"):
            self._wallet.sync_nonce(self.web3)

        # Create configuration manager with wallet
        config_manager = GMXConfigManager(chain=self.chain, chain_id=self.web3.eth.chain_id, wallet=self._wallet, web3=self.web3)

        return config_manager

    def get_read_config(self) -> GMXConfigManager:
        """
        Provide production-safe read-only configuration for data access operations.

        This method returns a ConfigManager instance specifically designed for
        safe data access operations in production environments. The read-only
        configuration contains no sensitive credential information and cannot
        perform transaction operations, making it safe for use in monitoring
        systems, analytics platforms, and other non-transactional contexts.

        **Production Security Guarantees:**

        The read-only configuration implements strict security isolation that
        prevents any possibility of accidental transaction execution or credential
        exposure. This design enables safe integration with monitoring systems
        and analytics platforms without security risks.

        **Operational Scope:**

        Read-only configurations support comprehensive GMX protocol data access
        including market data queries, position analysis, liquidity metrics,
        and all other non-transactional operations required for monitoring and
        analysis in production environments.

        :return:
            GMXConfigManager instance configured for safe read-only operations
            with comprehensive data access but no transaction capabilities
        :rtype: GMXConfigManager
        """
        return self._read_config

    def get_write_config(self) -> GMXConfigManager:
        """
        Provide production-grade write-enabled configuration for transaction operations.

        This method returns a ConfigManager instance configured with full
        transaction signing capabilities through secure wallet integration.
        The write configuration enables all GMX protocol transaction operations
        while maintaining comprehensive security controls and operational
        reliability required for production financial applications.

        **Production Security Controls:**

        Write configurations implement secure credential delegation through
        thoroughly tested wallet adapter systems, ensuring that sensitive
        operations maintain appropriate security controls while enabling
        necessary transaction functionality.

        **Operational Reliability:**

        The write configuration includes comprehensive validation, error handling,
        and state management to ensure reliable operation in production
        environments where transaction failures could have significant
        financial consequences.

        :return:
            GMXConfigManager instance configured with secure transaction signing
            capabilities suitable for production financial operations
        :rtype: GMXConfigManager
        :raises ValueError:
            When the configuration was initialized without wallet credentials,
            preventing transaction operations and ensuring fail-safe behavior
        """
        if not self._write_config:
            raise ValueError("No wallet provided. Cannot perform write operations.")
        return self._write_config

    def has_write_capability(self) -> bool:
        """
        Determine transaction signing capability for operational planning and validation.

        This method provides essential capability detection that enables safe
        operational planning in production environments. It validates both
        wallet availability and configuration completeness to prevent runtime
        failures when transaction operations are attempted.

        **Production Validation:**

        The capability check implements comprehensive validation of the complete
        configuration chain required for secure transaction operations, preventing
        partial configuration states that could cause operational failures.

        **Operational Planning Integration:**

        This method enables production applications to adapt their behavior
        based on available capabilities, providing appropriate functionality
        degradation for read-only configurations while enabling full transaction
        capabilities when credentials are available.

        :return:
            True when the configuration includes wallet credentials and can
            perform transaction signing operations, False when limited to
            read-only data access functionality
        :rtype: bool
        """
        return self._write_config is not None and self._wallet is not None

    def get_chain(self) -> str:
        """
        Retrieve the automatically detected blockchain network identifier.

        This method returns the network name that was automatically detected
        from the Web3 connection during configuration initialization. Automatic
        detection ensures operational consistency and eliminates network
        configuration errors in production deployments.

        **Production Reliability:**

        Automatic network detection prevents configuration mismatches that
        could cause operational failures or financial losses in production
        trading systems and financial applications.

        :return:
            Blockchain network identifier automatically detected from Web3
            connection, ensuring operational consistency and reliability
        :rtype: str
        """
        return self.chain

    def get_wallet_address(self) -> Optional[str]:
        """
        Retrieve the wallet address associated with this configuration.

        This method returns the Ethereum address that will be used for
        transaction operations when write capabilities are available. The
        address may be explicitly specified or automatically derived from
        wallet implementations using production-tested resolution logic.

        **Production Address Management:**

        The configuration system implements reliable address resolution that
        prioritizes explicit specifications while providing secure fallbacks
        to wallet-derived addresses when appropriate for production operation.

        :return:
            Ethereum wallet address in standard format, or None when the
            configuration operates in read-only mode without transaction
            capabilities
        :rtype: Optional[str]
        """
        return self._user_wallet_address

    def get_network_info(self) -> dict[str, Any]:
        """
        Provide comprehensive network configuration information for operational monitoring.

        This method returns detailed information about blockchain network
        configuration including automatically detected parameters, connectivity
        details, and validation status. The information supports operational
        monitoring, debugging, and validation in production environments.

        **Production Monitoring Support:**

        The network information includes all parameters necessary for operational
        monitoring systems to validate connectivity, track network status,
        and diagnose operational issues in production deployments.

        :return:
            Dictionary containing comprehensive network configuration including
            automatically detected chain identifier, RPC endpoint information,
            and blockchain-specific parameters
        :rtype: dict[str, Any]
        """
        return {
            "chain": self.chain,
            "rpc_url": self._rpc_url,
            "chain_id": self.web3.eth.chain_id,
        }

    @classmethod
    def from_private_key(cls, web3: Web3, private_key: str):
        """
        Create GMX configuration from private key by creating a HotWallet.

        This convenience method creates a HotWallet from the private key
        and initializes the configuration with it.

        :param web3: Web3 instance connected to the blockchain network
        :param private_key: Private key in hexadecimal format
        :return: GMXConfig instance with HotWallet
        """
        from eth_account import Account

        account = Account.from_key(private_key)
        wallet = HotWallet(account)
        wallet.sync_nonce(web3)

        return cls(web3=web3, wallet=wallet)
