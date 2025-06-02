"""
GMX Constants Module

This module serves as the central registry for all GMX protocol infrastructure
constants, including contract addresses, API endpoints, ABIs, and event signatures.
It provides a single source of truth for network-specific configuration data
that enables the GMX library to operate across multiple blockchain networks.

The constants are organized into several categories:

**API Endpoints**: Primary and backup URLs for GMX's REST API services, which
provide market data, price feeds, and other off-chain information.

**Contract Addresses**: On-chain smart contract addresses for core GMX protocol
components, organized by blockchain network (Arbitrum and Avalanche).

**ABIs (Application Binary Interfaces)**: Contract interface definitions that
enable Python code to interact with deployed smart contracts.

**Event Signatures**: Cryptographic signatures for important contract events
that allow efficient filtering and monitoring of on-chain activity.

This architectural approach allows the same codebase to work across multiple
networks by simply selecting the appropriate constants for the target blockchain.
The constants are loaded at module import time to ensure consistent configuration
throughout the application lifecycle.

Example:

.. code-block:: python

    # Access API endpoints for different networks
    arbitrum_api = GMX_API_URLS["arbitrum"]
    avalanche_api = GMX_API_URLS["avalanche"]

    # Get contract addresses for specific operations
    reader_address = GMX_READER_ADDRESS["arbitrum"]
    exchange_router = GMX_EXCHANGE_ROUTER_ADDRESS["avalanche"]

    # Use event signatures for blockchain monitoring
    position_event = EVENT_SIGNATURES["IncreasePosition"]

    # Load ABI for contract interaction
    event_emitter_abi = GMX_EVENT_EMITTER_ABI

Note:
    GMX maintains the API endpoints and official documentation can be found at:
    https://gmx-docs.io/docs/api/rest-v2
"""

from pathlib import Path
import json

# Define the base path relative to this script
base_dir = Path(__file__).resolve().parent

# GMX Is maintaining these APIs and the official documentation can be found here: https://gmx-docs.io/docs/api/rest-v2
GMX_API_URLS: dict = {
    """
    Primary API endpoint URLs for GMX protocol services by blockchain network.
    
    These endpoints provide access to GMX's REST API services, including market
    data, price feeds, position information, and other off-chain data. Each
    network maintains its own API infrastructure to ensure optimal performance
    and reliability for network-specific operations.
    
    The APIs follow `REST`ful conventions and return JSON responses. They support
    public endpoints (market data, prices).
    
    :type: dict[str, str]
    :var arbitrum: Primary API endpoint for Arbitrum network operations
    :var avalanche: Primary API endpoint for Avalanche network operations
    """
    "arbitrum": "https://arbitrum-api.gmxinfra.io",
    "avalanche": "https://avalanche-api.gmxinfra.io",
}

GMX_API_URLS_BACKUP: dict = {
    """
    Backup API endpoint URLs for GMX protocol services by blockchain network.
    
    These backup endpoints provide redundancy and failover capability when
    primary API endpoints are unavailable. The backup infrastructure mirrors
    the functionality of the primary endpoints, ensuring continuous service
    availability for critical trading and market data operations.
    
    The GMX client libraries automatically attempt backup endpoints when
    primary endpoints fail, providing transparent failover without requiring
    application-level retry logic.
    
    :type: dict[str, str]
    :var arbitrum: Backup API endpoint for Arbitrum network operations
    :var avalanche: Backup API endpoint for Avalanche network operations
    """
    "arbitrum": "https://arbitrum-api.gmxinfra2.io",
    "avalanche": "https://avalanche-api.gmxinfra2.io",
}

# Contract addresses by chain
GMX_EVENT_EMITTER_ADDRESS = {
    """
    Smart contract addresses for GMX Event Emitter contracts by network.
    
    The Event Emitter contract serves as a central logging system for the GMX
    protocol, recording important events such as position updates, liquidations,
    funding rate changes, and other critical protocol activities. This contract
    provides a standardized interface for monitoring and indexing GMX protocol
    events across different blockchain networks.
    
    Event emitters are essential for building real-time monitoring systems,
    analytics dashboards, and automated trading strategies that need to react
    to protocol state changes. They emit structured events that can be efficiently
    filtered and processed by off-chain systems.
    
    :type: dict[str, str]
    :var arbitrum: Event Emitter contract address on Arbitrum network
    :var avalanche: Event Emitter contract address on Avalanche network
    """
    "arbitrum": "0xC8ee91A54287DB53897056e12D9819156D3822Fb",
    "avalanche": "0xDb17B211c34240B014ab6d61d4A31FA0C0e20c26",
}

GMX_DATASTORE_ADDRESS = {
    """
    Smart contract addresses for GMX DataStore contracts by network.
    
    The DataStore contract acts as the primary data repository for the GMX
    protocol, storing critical information such as market configurations,
    position data, pricing parameters, and protocol settings. It serves as
    the authoritative source for protocol state that other contracts query
    to make trading and liquidation decisions.
    
    This contract implements a key-value storage pattern that allows efficient
    storage and retrieval of complex protocol data. It's designed for high
    read frequency with controlled write access, ensuring data integrity
    while supporting the performance requirements of a high-frequency trading
    protocol.
    
    :type: dict[str, str]
    :var arbitrum: DataStore contract address on Arbitrum network
    :var avalanche: DataStore contract address on Avalanche network
    """
    "arbitrum": "0xFD70de6b91282D8017aA4E741e9Ae325CAb992d8",
    "avalanche": "0x2F0b22339414ADeD7D5F06f9D604c7fF5b2fe3f6",
}

GMX_READER_ADDRESS = {
    """
    Smart contract addresses for GMX Reader contracts by network.
    
    The Reader contract provides optimized read-only access to protocol data,
    offering batch queries and computed values that would be expensive to
    calculate on-demand. It acts as a view layer that aggregates information
    from multiple protocol contracts, providing convenient interfaces for
    common data access patterns.
    
    Reader contracts are particularly important for user interfaces and
    analytics systems that need to efficiently query large amounts of protocol
    data. They implement gas-optimized functions that can return complex data
    structures in single calls, reducing the number of RPC requests needed
    for comprehensive protocol state queries.
    
    :type: dict[str, str]
    :var arbitrum: Reader contract address on Arbitrum network
    :var avalanche: Reader contract address on Avalanche network
    """
    "arbitrum": "0x5Ca84c34a381434786738735265b9f3FD814b824",
    "avalanche": "0xBAD04dDcc5CC284A86493aFA75D2BEb970C72216",
}

GMX_EXCHANGE_ROUTER_ADDRESS = {
    """
    Smart contract addresses for GMX Exchange Router contracts by network.
    
    The Exchange Router contract serves as the main entry point for trading
    operations on the GMX protocol. It handles position opening, closing,
    order placement, and other trading-related transactions. The router
    implements safety checks, fee calculations, and coordinates with other
    protocol contracts to execute trades securely and efficiently.
    
    This contract is where users submit trading transactions, making it one
    of the most critical components of the GMX protocol infrastructure. It
    implements sophisticated validation logic to ensure trades comply with
    protocol rules, risk parameters, and market conditions before execution.
    
    :type: dict[str, str]
    :var arbitrum: Exchange Router contract address on Arbitrum network
    :var avalanche: Exchange Router contract address on Avalanche network
    """
    "arbitrum": "0x900173A66dbD345006C51fA35fA3aB760FcD843b",
    "avalanche": "0x2b76df209E1343da5698AF0f8757f6170162e78b",
}

# Define the paths to ABI files
eventemitter_path = base_dir / "../" / "abi" / "gmx" / "eventemitter.json"

# Read and parse the JSON ABI file
GMX_EVENT_EMITTER_ABI = json.loads(eventemitter_path.read_text())
"""
Application Binary Interface (ABI) for the GMX Event Emitter contract.

The ABI defines the contract's interface, including function signatures, event
definitions, and data types. This allows Python code to properly encode function
calls and decode contract responses when interacting with the deployed Event
Emitter contracts on different blockchain networks.

The Event Emitter ABI includes definitions for all events that the contract
can emit, such as position updates, liquidations, and funding rate changes.
This information is essential for parsing event logs and building event
monitoring systems that react to protocol state changes.

:type: list[dict[str, Any]]
"""


# Event signatures for GMX contracts
EVENT_SIGNATURES = {
    """
    Cryptographic event signatures for important GMX protocol events.
    
    These hexadecimal signatures represent the keccak256 hash of event
    signatures, which are used for efficient event filtering when monitoring
    blockchain logs. Each signature corresponds to a specific event type
    emitted by GMX protocol contracts, allowing applications to selectively
    listen for events of interest.
    
    Event signatures are calculated as keccak256(event_signature) where
    event_signature follows the format "EventName(type1,type2,...)". These
    signatures are used in eth_getLogs RPC calls to filter for specific
    event types without downloading all contract events.
    
    Understanding these signatures is crucial for building real-time monitoring
    systems, automated trading strategies, and analytics platforms that need
    to react to specific protocol events as they occur on-chain.
    
    :type: dict[str, str]
    :var UpdateFundingRate: Signature for funding rate update events, emitted
        when the protocol adjusts funding rates for perpetual positions
    :var IncreasePosition: Signature for position increase events, emitted
        when users open new positions or add size to existing positions
    :var DecreasePosition: Signature for position decrease events, emitted
        when users close positions or reduce position size
    :var LiquidatePosition: Signature for liquidation events, emitted when
        positions are automatically closed due to insufficient margin
    :var SetPrice: Signature for price update events, emitted when oracle
        prices are updated for protocol calculations
    """
    "UpdateFundingRate": "0xaa58a1c124fe8c67db114d6a19c3ef5b564f4ef3bd820f71e94473e846e3bb12",
    "IncreasePosition": "0x2fe68525253654c21998f35787a8d0f361bd444120e6c65920e8f7e9e4c26930",
    "DecreasePosition": "0xca28a6b76a3f6dc9124d60540e577c6adbd1e3ba0b52e013908b9ad5f15a4464",
    "LiquidatePosition": "0x2e1f85a5194ea85aa10539a6e819c82b7244e0a61ab25bd09627a29e2f7b996b",
    "SetPrice": "0x42b65f4eb3437d54b4e320a5863c8a1c28e539af1226161b7602ef73f567da5c",
}
