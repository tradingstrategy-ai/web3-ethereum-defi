"""Derive.xyz protocol integration.

Derive.xyz (formerly Lyra) is a decentralised perpetuals and options exchange
built on Derive Chain (OP Stack L2).

This module provides:

- Session key authentication with EIP-712 signing
- Account creation and management
- Balance and collateral reading
- HTTP session with thread-safe rate limiting

Key components:

- :py:mod:`eth_defi.derive.authentication` - API client with session key auth
- :py:mod:`eth_defi.derive.account` - Balance and collateral functions
- :py:mod:`eth_defi.derive.session` - HTTP session management
- :py:mod:`eth_defi.derive.constants` - API URLs and enums

Example workflow::

    from eth_account import Account
    from eth_defi.derive.authentication import DeriveApiClient, SessionKeyScope
    from eth_defi.derive.account import fetch_account_summary

    # Initialize client
    owner = Account.from_key("0x...")
    client = DeriveApiClient(
        owner_account=owner,
        derive_wallet_address="0x...",  # From Derive.xyz interface
        is_testnet=True,
    )

    # Register session key
    session = client.register_session_key(
        scope=SessionKeyScope.read_only,
        expiry_hours=24,
    )
    client.session_key_private = session["session_key_private"]

    # Fetch balances
    summary = fetch_account_summary(client)
    print(f"Total: ${summary.total_value_usd}")

Authentication:
    Derive uses a three-tier wallet system:

    1. **Owner EOA** - Your original Ethereum wallet
    2. **Derive Wallet** - Smart contract wallet on Derive Chain
    3. **Session Keys** - Temporary wallets for API access

    Find your Derive Wallet at: https://testnet.derive.xyz/ → Developers

Environment variables for testing:
    - ``DERIVE_OWNER_PRIVATE_KEY`` - Owner wallet private key
    - ``DERIVE_WALLET_ADDRESS`` - Derive smart contract wallet
    - ``DERIVE_SESSION_KEY_PRIVATE`` - Session key (after registration)
    - ``SEND_REAL_REQUESTS`` - Enable live API calls
"""
