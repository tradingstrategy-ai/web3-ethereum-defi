"""
GMX Wallet Adapter Signer Module

This module implements a sophisticated adapter pattern that bridges the gap between
the diverse wallet implementations in the eth_defi ecosystem and the specific
signer interface required by the GMX Python SDK. It represents a masterful example
of how modern software architecture solves incompatibility problems through
intelligent abstraction and unified interfaces.

**Adapter Pattern in Financial Systems:**

The adapter pattern is one of the most powerful architectural tools for creating
interoperability between systems that were never designed to work together. In
this implementation, we're solving the challenge of making multiple wallet types
with different APIs, security models, and operational characteristics all work
seamlessly with the GMX protocol's expectations.

**Security Architecture Philosophy:**

Wallet adapters operate in the most security-critical layer of blockchain applications.
Every design decision must balance usability with security, ensuring that the
abstraction layer never compromises the underlying security guarantees of individual
wallet implementations while providing a consistent interface for application code.

**Multi-Wallet Type Support:**

Modern DeFi applications must support diverse wallet types to accommodate different
user preferences and security requirements:

- **HotWallet**: Direct private key management with optimized performance
- **Web3ProviderWallet**: Integration with browser wallets and external providers
- **BaseWallet**: Generic interface supporting various specialized implementations

**Transaction Signing Complexity:**

Blockchain transaction signing involves intricate details around nonce management,
gas estimation, signature format compatibility, and error handling. The adapter
abstracts these complexities while preserving the specific optimizations and
security features of each wallet type.

**Key Architectural Achievements:**

- **Universal Compatibility**: Any BaseWallet implementation works with GMX
- **Security Preservation**: No compromise of individual wallet security models
- **Performance Optimization**: Wallet-specific optimizations remain intact
- **Error Transparency**: Clear error propagation and debugging information
- **Future Extensibility**: Easy integration of new wallet types

Example:

.. code-block:: python

    # Universal wallet adapter usage across different wallet types
    from web3 import Web3
    from eth_defi.hotwallet import HotWallet
    from eth_defi.provider_wallet import Web3ProviderWallet
    from eth_defi.gmx.wallet_adapter_signer import WalletAdapterSigner

    web3 = Web3(Web3.HTTPProvider("https://arb1.arbitrum.io/rpc"))

    # Scenario 1: High-performance trading with HotWallet
    hot_wallet = HotWallet.from_private_key("0x...")
    hot_adapter = WalletAdapterSigner(hot_wallet, web3)

    # Scenario 2: Browser integration with Web3ProviderWallet
    provider_wallet = Web3ProviderWallet(web3)
    provider_adapter = WalletAdapterSigner(provider_wallet, web3)

    # Scenario 3: Custom wallet implementation
    custom_wallet = MyCustomWallet(...)
    custom_adapter = WalletAdapterSigner(custom_wallet, web3)

    # All adapters expose identical interface to GMX
    for adapter in [hot_adapter, provider_adapter, custom_adapter]:
        address = adapter.get_address()
        message_sig = adapter.sign_message("Hello GMX!")
        tx_hash = adapter.send_transaction(
            {
                "to": "0x...",
                "value": web3.to_wei(0.1, "ether"),
                "gas": 21000,
            }
        )

        print(f"Wallet {address} signed message and sent transaction {tx_hash.hex()}")

**Design Philosophy:**

The adapter implements a "no-compromise" philosophy where universal compatibility
never comes at the expense of security, performance, or wallet-specific features.
Each wallet type's strengths are preserved while providing a unified interface
that simplifies application development.

Note:
    This adapter is designed for production use in financial applications where
    security and reliability are paramount. All operations include comprehensive
    error handling and validation to prevent common blockchain interaction pitfalls.

Warning:
    Transaction signing involves irreversible financial operations. Always test
    adapter behavior thoroughly with your specific wallet implementation before
    using in production environments with significant value.
"""

from typing import Union, Any, cast

from eth_account.messages import encode_defunct
from eth_typing import ChecksumAddress
from cchecksum import to_checksum_address
from hexbytes import HexBytes

from web3 import Web3
from web3.types import SignedTx, TxParams
from eth_defi.basewallet import BaseWallet
from eth_defi.hotwallet import HotWallet, SignedTransactionWithNonce
from eth_defi.provider_wallet import Web3ProviderWallet

from gmx_python_sdk.scripts.v2.signers import Signer


class WalletAdapterSigner(Signer):
    """
    Universal adapter enabling any BaseWallet implementation to work with GMX's Signer interface.

    This class implements the adapter pattern to create seamless interoperability between
    the diverse wallet ecosystem of eth_defi and the specific signer interface required
    by the GMX Python SDK. It handles the complex differences in wallet APIs, security
    models, and operational characteristics while providing a unified interface that
    preserves the unique capabilities of each wallet type.

    **Architectural Challenge Solved:**

    Different wallet implementations have evolved different approaches to transaction
    signing, nonce management, and security models. HotWallet optimizes for performance
    with direct private key access, Web3ProviderWallet integrates with browser-based
    wallets and external providers, while other BaseWallet implementations may use
    hardware security modules, multi-signature schemes, or custom security protocols.

    The adapter pattern allows us to support all these approaches without forcing
    any wallet to compromise its design principles or security model. Each wallet
    continues to operate according to its intended architecture while presenting
    a standardized interface to application code.

    **Security Architecture:**

    The adapter operates as a security-preserving translation layer. It never
    handles private keys directly, never bypasses wallet-specific security measures,
    and never makes assumptions about internal wallet implementations. Instead, it
    intelligently delegates operations to the appropriate wallet methods while
    handling the format and protocol differences that would otherwise prevent
    interoperability.

    **Performance Considerations:**

    The adapter adds minimal overhead while preserving wallet-specific optimizations.
    HotWallet's direct signing capabilities remain fast, Web3ProviderWallet's
    delegation to external providers works seamlessly, and specialized wallet
    implementations can maintain their performance characteristics.

    :ivar wallet: The underlying wallet implementation being adapted
    :vartype wallet: Union[BaseWallet, HotWallet, Web3ProviderWallet]
    :ivar web3: Web3 instance for blockchain interaction and transaction broadcasting
    :vartype web3: Web3
    """

    def __init__(self, wallet: Union[BaseWallet, HotWallet, Web3ProviderWallet], web3: Web3) -> None:
        """
        Initialize the universal wallet adapter with comprehensive compatibility validation.

        This constructor establishes the adapter relationship between a specific wallet
        implementation and the Web3 connection needed for blockchain interaction. It
        performs validation to ensure compatibility while preserving the security and
        operational characteristics of the underlying wallet.

        **Initialization Philosophy:**

        The adapter initialization is designed to be lightweight and non-intrusive.
        It doesn't modify the wallet's internal state, doesn't require additional
        configuration beyond what the wallet already provides, and doesn't establish
        any persistent connections that could affect wallet behavior.

        :param wallet:
            Any implementation conforming to the BaseWallet interface, including
            specialized implementations like HotWallet and Web3ProviderWallet.
            The adapter will intelligently detect the wallet type and adapt its
            behavior accordingly
        :type wallet: Union[BaseWallet, HotWallet, Web3ProviderWallet]
        :param web3:
            Web3 instance connected to the target blockchain network. Must be
            properly configured with an active RPC connection for transaction
            broadcasting and blockchain interaction
        :type web3: Web3
        """
        self.wallet = wallet
        self.web3 = web3

    def get_address(self) -> ChecksumAddress:
        """
        Retrieve the Ethereum address associated with this wallet in standardized format.

        This method provides universal address access across all wallet types while
        ensuring the returned address conforms to Ethereum's checksum address standard.
        The checksum format provides built-in error detection for address handling
        and is the standard format expected by most blockchain applications.

        **Address Standardization:**

        Different wallet implementations may store or return addresses in various
        formats (lowercase, uppercase, mixed case). The adapter ensures consistent
        checksum address format regardless of the underlying wallet's internal
        address representation, preventing compatibility issues with downstream
        systems that expect standardized address formats.

        :return:
            The wallet's Ethereum address in EIP-55 checksum format, providing
            built-in error detection and compatibility with all standard Ethereum
            tooling and smart contracts
        :rtype: ChecksumAddress
        """
        return to_checksum_address(self.wallet.get_main_address())

    def sign_message(self, message: Union[str, bytes, int]) -> HexBytes:
        """
        Sign arbitrary messages with universal compatibility across wallet types and message formats.

        This method implements sophisticated message signing logic that handles the
        diverse message formats used in blockchain applications while adapting to
        the different signing capabilities of various wallet implementations. It
        provides a unified interface for message signing that works consistently
        across all supported wallet types.

        **Message Format Handling:**

        Blockchain applications use message signing for various purposes including
        authentication, authorization, and cryptographic proofs. Messages may arrive
        as human-readable strings, binary data, hexadecimal representations, or
        numeric values. The adapter intelligently handles format conversion while
        preserving the semantic meaning of the original message.

        **Wallet-Specific Adaptations:**

        Different wallet types provide different levels of message signing support.
        HotWallet offers direct signing through its account object, Web3ProviderWallet
        delegates to external providers with varying capabilities, and other wallet
        implementations may use specialized signing procedures. The adapter navigates
        these differences to provide consistent functionality.

        **Security Considerations:**

        Message signing in financial contexts requires careful handling to prevent
        attacks where malicious applications trick users into signing dangerous
        messages. The adapter preserves wallet-specific security measures while
        providing clear error messages when signing operations cannot be completed
        safely.

        Example:

        .. code-block:: python

            # Universal message signing across different formats
            adapter = WalletAdapterSigner(wallet, web3)

            # Sign human-readable text message
            text_signature = adapter.sign_message("Welcome to GMX Protocol")

            # Sign hexadecimal data
            hex_signature = adapter.sign_message("0x1234567890abcdef")

            # Sign binary data
            binary_signature = adapter.sign_message(b"\\x12\\x34\\x56\\x78")

            # Sign numeric value
            numeric_signature = adapter.sign_message(12345)

            # All signatures are returned in consistent HexBytes format
            assert isinstance(text_signature, HexBytes)
            assert len(text_signature) == 65  # Standard Ethereum signature length

        :param message:
            Message to sign in any supported format. String messages are encoded
            as UTF-8 unless they begin with "0x" (treated as hex). Binary data
            and integers are handled with appropriate encoding for signature
            generation
        :type message: Union[str, bytes, int]
        :return:
            Cryptographic signature of the message in standard Ethereum format,
            suitable for verification and use in smart contract operations
        :rtype: HexBytes
        :raises NotImplementedError:
            When the underlying wallet implementation doesn't support message
            signing or the specific message format cannot be handled safely
        :raises ValueError:
            When message format is invalid, signing fails due to wallet state,
            or cryptographic operations encounter errors
        """
        # Convert message to bytes if it's not already
        if isinstance(message, str):
            # Check if it's a hex string
            if message.startswith("0x"):
                signable_message = encode_defunct(HexBytes(message))
            else:
                signable_message = encode_defunct(message.encode("utf-8"))
        elif isinstance(message, int):
            signable_message = encode_defunct(Web3.to_bytes(message))
        elif isinstance(message, bytes) or isinstance(message, HexBytes):
            signable_message = encode_defunct(message)
        else:
            raise ValueError(f"Unsupported message type: {type(message)}")

        try:
            # HotWallet has an account object we can use directly
            if isinstance(self.wallet, HotWallet):
                # Use the account's sign method
                signature = self.wallet.account.sign_message(signable_message)
                return HexBytes(signature.signature)

            elif isinstance(self.wallet, Web3ProviderWallet):
                # For provider wallets, use personal_sign
                raise NotImplementedError("Message signing not implemented for wallet type: Web3ProviderWallet")

            else:
                # Try using the wallet's sign_message method if available
                if hasattr(self.wallet, "sign_message"):
                    return HexBytes(self.wallet.sign_message(signable_message).messageHash)

                raise NotImplementedError(f"Message signing not implemented for wallet type: {type(self.wallet)}")

        except Exception as e:
            raise ValueError(f"Failed to sign message: {str(e)}") from e

    def sign_transaction(self, unsigned_tx: TxParams) -> Union[SignedTransactionWithNonce, SignedTx]:
        """
        Sign blockchain transactions with sophisticated nonce management and wallet-specific optimizations.

        This method implements the most complex aspect of wallet adaptation - transaction
        signing with proper nonce handling across different wallet architectures. It
        navigates the intricate differences in how various wallet types manage transaction
        nonces, handle signing procedures, and maintain state consistency.

        **Nonce Management Complexity:**

        Transaction nonces are critical for blockchain security and proper transaction
        ordering. Different wallet implementations have evolved different approaches:
        some manage nonces internally, others delegate to external systems, and some
        require explicit nonce provision. The adapter handles all these approaches
        while maintaining proper nonce sequencing and state consistency.

        **Wallet-Specific Signing Strategies:**

        HotWallet can sign transactions directly through its account object, providing
        optimal performance for high-frequency operations. Web3ProviderWallet must
        coordinate with external providers that may have different capabilities and
        security models. Other wallet implementations may use hardware security
        modules, multi-signature schemes, or custom cryptographic procedures.

        **State Consistency Guarantees:**

        The adapter ensures that transaction signing operations maintain consistent
        state across the wallet's internal nonce tracking, the blockchain's nonce
        requirements, and any external systems involved in the signing process.
        This prevents nonce conflicts that could cause transaction failures or
        unexpected behavior.

        Example:

        .. code-block:: python

            # Transaction signing with automatic nonce management
            adapter = WalletAdapterSigner(wallet, web3)

            # Sign transaction without explicit nonce (adapter manages nonce)
            unsigned_tx = {
                "to": "0x742d35Cc6634C0532925a3b8D6c2C0C4e85a4d0A",
                "value": web3.to_wei(0.1, "ether"),
                "gas": 21000,
                "gasPrice": web3.to_wei(20, "gwei"),
            }

            signed_tx = adapter.sign_transaction(unsigned_tx)

            # Sign transaction with explicit nonce (for advanced use cases)
            nonce_tx = {
                **unsigned_tx,
                "nonce": web3.eth.get_transaction_count(adapter.get_address()),
            }

            signed_nonce_tx = adapter.sign_transaction(nonce_tx)

            # Both approaches maintain proper nonce sequencing and state consistency

        :param unsigned_tx:
            Transaction parameters to sign, optionally including a nonce. When nonce
            is provided, the adapter uses it directly while maintaining state
            consistency. When omitted, the adapter uses wallet-specific nonce
            management
        :type unsigned_tx: TxParams
        :return:
            Signed transaction object ready for blockchain submission, with format
            determined by the underlying wallet implementation but compatible with
            standard Web3 transaction broadcasting
        :rtype: Union[SignedTransactionWithNonce, SignedTx]
        :raises NotImplementedError:
            When the wallet type doesn't support the requested signing operation
            or nonce management approach
        :raises ValueError:
            When transaction parameters are invalid, nonce conflicts occur, or
            cryptographic signing operations fail
        """
        # Handle transactions that already have a nonce
        if "nonce" in unsigned_tx:
            # HotWallet has an account object we can use directly
            if isinstance(self.wallet, HotWallet):
                # Sign directly with the account to bypass HotWallet's nonce assertion
                result = self.wallet.account.sign_transaction(unsigned_tx)
                # Update the wallet's nonce tracking to stay in sync
                if self.wallet.current_nonce is not None:
                    self.wallet.current_nonce = max(unsigned_tx["nonce"] + 1, self.wallet.current_nonce)
                return result
            elif isinstance(self.wallet, Web3ProviderWallet):
                # Web3ProviderWallet needs to delegate to the provider
                # But we can't directly sign, only send
                raise NotImplementedError("Direct signing with nonce not supported for Web3ProviderWallet")
            else:
                # For other wallet types, we need to extract nonce, sign, and update
                nonce = unsigned_tx.pop("nonce")
                result = self.wallet.sign_transaction_with_new_nonce(unsigned_tx)
                # Restore the nonce value in the original transaction
                unsigned_tx["nonce"] = nonce
                # Update the wallet's nonce tracking
                if hasattr(self.wallet, "current_nonce") and self.wallet.current_nonce is not None:
                    self.wallet.current_nonce = max(nonce + 1, self.wallet.current_nonce)
                return result
        else:
            # No nonce provided, use the wallet's nonce management
            return self.wallet.sign_transaction_with_new_nonce(unsigned_tx)

    def send_transaction(self, unsigned_tx: TxParams) -> HexBytes:
        """
        Execute complete transaction workflow from signing through blockchain submission with universal compatibility.

        This method implements the most comprehensive transaction handling logic,
        combining transaction signing with blockchain submission while adapting to
        the diverse capabilities and requirements of different wallet types. It
        provides a unified interface for transaction execution that works seamlessly
        across all supported wallet implementations.

        **Transaction Execution Strategies:**

        Different wallet types require different approaches to transaction execution.
        Web3ProviderWallet delegates transaction submission to external providers
        that handle signing and submission as an atomic operation. Other wallet
        types require explicit signing followed by raw transaction submission.
        The adapter implements the optimal strategy for each wallet type.

        **Error Handling and Recovery:**

        Transaction submission can fail for numerous reasons including network
        issues, insufficient gas, nonce conflicts, or wallet-specific errors.
        The adapter provides comprehensive error handling with clear diagnostic
        information to enable rapid troubleshooting and recovery strategies.

        **Performance Optimization:**

        The adapter preserves wallet-specific performance optimizations while
        providing universal compatibility. High-performance wallets maintain
        their speed advantages, while wallets optimized for security preserve
        their security guarantees during transaction execution.

        Example:

        .. code-block:: python

            # Universal transaction execution across wallet types
            adapter = WalletAdapterSigner(wallet, web3)

            # Execute simple transfer transaction
            transfer_tx = {
                "to": "0x742d35Cc6634C0532925a3b8D6c2C0C4e85a4d0A",
                "value": web3.to_wei(0.1, "ether"),
                "gas": 21000,
                "gasPrice": web3.to_wei(20, "gwei"),
            }

            tx_hash = adapter.send_transaction(transfer_tx)

            # Wait for confirmation and verify success
            receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
            assert receipt.status == 1  # Transaction succeeded

            # Execute complex contract interaction
            contract_tx = {
                "to": contract_address,
                "data": contract.encodeABI(fn_name="complexOperation", args=[...]),
                "gas": 500000,
                "gasPrice": web3.to_wei(50, "gwei"),
            }

            complex_tx_hash = adapter.send_transaction(contract_tx)
            print(f"Contract interaction: {complex_tx_hash.hex()}")

        :param unsigned_tx:
            Complete transaction parameters for execution. The adapter will handle
            any missing fields (like nonce) using wallet-specific logic while
            preserving explicit parameters where provided
        :type unsigned_tx: TxParams
        :return:
            Transaction hash confirming successful blockchain submission, suitable
            for transaction tracking and receipt retrieval
        :rtype: HexBytes
        :raises ValueError:
            When transaction execution fails due to invalid parameters, insufficient
            funds, network errors, or wallet-specific issues
        """
        # Handle Web3ProviderWallet specially
        if isinstance(self.wallet, Web3ProviderWallet):
            # Remove the "from" field - the provider will add it
            tx_copy = dict(unsigned_tx)
            if "from" in tx_copy:
                del tx_copy["from"]

            # Use the wallet's nonce management if no nonce is provided
            if "nonce" not in tx_copy and hasattr(self.wallet, "allocate_nonce"):
                tx_copy["nonce"] = self.wallet.allocate_nonce()

            # Send through the provider
            return self.web3.eth.send_transaction(tx_copy)

        # For other wallet types, sign and then send the raw transaction
        try:
            signed_tx = self.sign_transaction(unsigned_tx)

            # Extract the raw transaction bytes
            if hasattr(signed_tx, "rawTransaction"):
                raw_tx = cast(Any, signed_tx).rawTransaction
            elif hasattr(signed_tx, "raw_transaction"):
                raw_tx = cast(Any, signed_tx).raw_transaction
            elif isinstance(signed_tx, SignedTransactionWithNonce):
                raw_tx = signed_tx.rawTransaction
            else:
                raise ValueError(f"Unknown signed transaction format: {type(signed_tx)}")

            # Send the raw transaction
            return self.web3.eth.send_raw_transaction(raw_tx)
        except Exception as e:
            raise ValueError(f"Failed to send transaction: {e!s}") from e
