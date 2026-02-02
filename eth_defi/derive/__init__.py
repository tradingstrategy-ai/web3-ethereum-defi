"""Derive.xyz protocol integration.

Derive.xyz (formerly Lyra) is a decentralised perpetuals and options exchange
built on Derive Chain (OP Stack L2).

This module provides:

- Session key registration for existing accounts
- Session key authentication with EIP-191 personal-sign
- LightAccount wallet address resolution
- Balance and collateral reading
- HTTP session with thread-safe rate limiting

.. important::

    Account creation requires the Derive web interface.
    See :py:mod:`eth_defi.derive.onboarding` for details.

Key components:

- :py:mod:`eth_defi.derive.onboarding` - Session key registration and wallet resolution
- :py:mod:`eth_defi.derive.authentication` - API client with session key auth
- :py:mod:`eth_defi.derive.account` - Balance and collateral functions
- :py:mod:`eth_defi.derive.session` - HTTP session management
- :py:mod:`eth_defi.derive.constants` - API URLs, contract addresses, and enums

Example workflow::

    from eth_account import Account
    from eth_defi.derive.authentication import DeriveApiClient
    from eth_defi.derive.account import fetch_account_collaterals

    # Use credentials from the Derive web interface developer page
    client = DeriveApiClient(
        owner_account=Account.from_key("0x..."),  # Owner wallet private key
        derive_wallet_address="0x...",  # From developer page
        session_key_private="0x...",  # From developer page
        is_testnet=True,
    )

    collaterals = fetch_account_collaterals(client)

Authentication:
    Derive uses a three-tier wallet system:

    1. **Owner EOA** - Your original Ethereum wallet
    2. **Derive Wallet** - LightAccount smart contract wallet on Derive Chain
    3. **Session Keys** - Temporary wallets for API access

    Create your account at: https://testnet.derive.xyz/ (testnet)
    or https://derive.xyz/ (mainnet)

Environment variables for testing:
    - ``DERIVE_OWNER_PRIVATE_KEY`` - Owner wallet private key
    - ``DERIVE_SESSION_PRIVATE_KEY`` - Session key private key (from developer page)
    - ``DERIVE_WALLET_ADDRESS`` - Derive smart contract wallet (from developer page)
"""
