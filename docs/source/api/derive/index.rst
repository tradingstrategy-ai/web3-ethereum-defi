Derive API
----------

Derive.xyz decentralised perpetuals and options exchange integration with session key authentication.

Preface
~~~~~~~

Derive.xyz (formerly Lyra) is a self-custodial, high-performance trading platform for perpetuals and options, built on Derive Chain (OP Stack L2).

**Supported products:**

- Perpetual futures
- Options trading
- Spot trading (coming soon)

**Supported collateral:**

- USDC
- wETH (Wrapped Ethereum)
- wstETH (Wrapped Liquid Staked Ethereum)
- wBTC (Wrapped Bitcoin)

Authentication
~~~~~~~~~~~~~~

Derive uses a three-tier wallet system:

1. **Owner EOA** - Your original Ethereum wallet
2. **Derive Wallet** - Smart contract wallet on Derive Chain (not your EOA)
3. **Session Keys** - Temporary wallets for API access

Session keys must be registered by the owner and support three permission levels:

- ``read_only`` - View account data only
- ``account`` - Manage orders and settings
- ``admin`` - Full access including trading and withdrawals

To find your Derive Wallet address: https://testnet.derive.xyz/ → Home → Developers → "Derive Wallet"

Getting started
~~~~~~~~~~~~~~~

Example workflow:

.. code-block:: python

    from eth_account import Account
    from eth_defi.derive.authentication import DeriveApiClient, SessionKeyScope
    from eth_defi.derive.account import fetch_account_summary

    # Initialise with owner account
    owner = Account.from_key("0x...")

    client = DeriveApiClient(
        owner_account=owner,
        derive_wallet_address="0x...",  # From Derive.xyz interface
        is_testnet=True,
    )

    # Register session key
    session_info = client.register_session_key(
        scope=SessionKeyScope.read_only,
        expiry_hours=24,
    )
    client.session_key_private = session_info["session_key_private"]

    # Fetch account data
    summary = fetch_account_summary(client)
    print(f"Total value: ${summary.total_value_usd}")
    for col in summary.collaterals:
        print(f"{col.token}: {col.available}")

Environment variables
~~~~~~~~~~~~~~~~~~~~~

For testing, set these environment variables:

.. code-block:: bash

    # Owner wallet private key (your main Ethereum wallet)
    DERIVE_OWNER_PRIVATE_KEY=0x...

    # Derive wallet address (find in Derive.xyz interface)
    DERIVE_WALLET_ADDRESS=0x...

    # Session key private key (generated after registration)
    DERIVE_SESSION_KEY_PRIVATE=0x...

    # Enable real API calls for testing
    SEND_REAL_REQUESTS=true

Creating test accounts
~~~~~~~~~~~~~~~~~~~~~~

To create a fresh test account on Derive testnet:

1. Generate a new wallet: ``Account.create()``
2. Fund it with testnet ETH (Sepolia faucet)
3. Use Derive testnet interface to mint USDC
4. Visit https://testnet.derive.xyz/ to create your Derive Wallet
5. Find your Derive Wallet address in the interface (Developers section)
6. Register session key for API access

Links
~~~~~

- `Derive.xyz Platform <https://www.derive.xyz/>`__
- `Testnet Interface <https://testnet.derive.xyz/>`__
- `API Documentation <https://docs.derive.xyz/>`__
- `API Reference <https://docs.derive.xyz/reference/overview>`__
- `Session Keys Guide <https://docs.derive.xyz/reference/session-keys>`__
- `Python Signing SDK <https://github.com/derivexyz/v2-action-signing-python>`__
- `Derive Chain Explorer <https://explorer.derive.xyz/>`__
- `Circle USDC Testnet Faucet <https://faucet.circle.com/>`__

API modules
~~~~~~~~~~~

.. autosummary::
   :toctree: _autosummary_derive
   :recursive:

   eth_defi.derive.session
   eth_defi.derive.authentication
   eth_defi.derive.account
   eth_defi.derive.constants
