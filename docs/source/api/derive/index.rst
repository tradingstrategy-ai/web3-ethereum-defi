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

Bridging
~~~~~~~~

Derive Chain is an OP Stack L2. To trade on Derive, you need to bridge collateral from other networks
to Derive Chain. Derive uses a custom bridge built on `Socket <https://www.socket.tech/>`__ smart contracts
and L1-L2 messaging infrastructure.

**Supported source networks:**

- Ethereum
- Arbitrum
- Optimism
- Base
- HyperEVM

**Bridge timing:**

- From Layer 2 networks (Arbitrum, Optimism, Base): 2-5 minutes
- From Ethereum mainnet: 5-10 minutes

Delays result from cross-chain message relaying required to securely confirm deposits on Derive Chain.

**Fast withdrawals:**

Derive supports fast withdrawals that bypass the standard 7-day OP Stack challenge period.
Global daily limits apply to fast withdrawals to maintain self-custody protections
inherent to the fraud proof system.

For more details, see the `Derive bridging documentation <https://help.derive.xyz/en/articles/9086191-what-bridge-does-derive-use>`__
and `supported networks <https://help.derive.xyz/en/articles/9085623-what-networks-are-supported>`__.

Authentication
~~~~~~~~~~~~~~

Derive uses a three-tier wallet system:

1. **Owner EOA** - Your original Ethereum wallet
2. **Derive Wallet** - LightAccount smart contract wallet on Derive Chain (ERC-4337)
3. **Session Keys** - Temporary wallets for API access

Session keys must be registered by the owner and support three permission levels:

- ``read_only`` - View account data only
- ``account`` - Manage orders and settings
- ``admin`` - Full access including trading and withdrawals

Authentication headers use EIP-191 personal-sign (``encode_defunct``) with uppercase header names
(``X-LYRAWALLET``, ``X-LYRATIMESTAMP``, ``X-LYRASIGNATURE``).

Account creation
~~~~~~~~~~~~~~~~

.. important::

    Account creation requires the Derive web interface. The web UI deploys an
    ERC-4337 LightAccount via a gas-sponsored UserOperation. This step cannot be
    automated due to bot detection on the SIWE endpoint and paymaster access controls.

1. Visit https://testnet.derive.xyz/ (testnet) or https://derive.xyz/ (mainnet)
2. Connect your Ethereum wallet (MetaMask, etc.)
3. The interface will deploy your LightAccount and register the initial session key
4. Find your Derive Wallet address: Home → Developers → "Derive Wallet"

Getting started
~~~~~~~~~~~~~~~

Once your account exists via the web interface, use the session key from the
developer page to authenticate API requests:

.. code-block:: python

    from eth_account import Account
    from eth_defi.derive.authentication import DeriveApiClient
    from eth_defi.derive.account import fetch_account_collaterals

    # Use credentials from the Derive web interface developer page
    client = DeriveApiClient(
        owner_account=Account.from_key("0x..."),        # Owner wallet private key
        derive_wallet_address="0x...",                    # From developer page
        session_key_private="0x...",                      # From developer page
        is_testnet=True,
    )

    # Fetch account data
    collaterals = fetch_account_collaterals(client)
    for col in collaterals:
        print(f"{col.token}: {col.available}")

Environment variables
~~~~~~~~~~~~~~~~~~~~~

For testing, set these environment variables:

.. code-block:: bash

    # Owner wallet private key (from web UI wallet)
    DERIVE_OWNER_PRIVATE_KEY=0x...

    # Session key private key (from testnet developer page)
    DERIVE_SESSION_PRIVATE_KEY=0x...

    # Derive wallet address (from testnet developer page)
    DERIVE_WALLET_ADDRESS=0x...

Finding your private key
^^^^^^^^^^^^^^^^^^^^^^^^

``DERIVE_OWNER_PRIVATE_KEY`` is the private key of the Ethereum wallet you used to
connect to the Derive web interface. To export it:

- **MetaMask**: Account menu → "Account details" → "Show private key" → enter password
- **Rabby**: Account address → three-dot menu → "Export Private Key" → enter password
- **Other wallets**: Look for "Export private key" in account/security settings

``DERIVE_SESSION_PRIVATE_KEY`` and ``DERIVE_WALLET_ADDRESS`` are found on the Derive
web interface under Home → Developers.

Links
~~~~~

- `Derive.xyz Platform <https://www.derive.xyz/>`__
- `Testnet Interface <https://testnet.derive.xyz/>`__
- `API Documentation <https://docs.derive.xyz/>`__
- `API Reference <https://docs.derive.xyz/reference/overview>`__
- `Manual Onboarding Guide <https://docs.derive.xyz/reference/onboard-manually>`__
- `Python Signing SDK <https://github.com/derivexyz/v2-action-signing-python>`__
- `Derive Chain Explorer <https://explorer.derive.xyz/>`__

API modules
~~~~~~~~~~~

.. autosummary::
   :toctree: _autosummary_derive
   :recursive:

   eth_defi.derive.onboarding
   eth_defi.derive.session
   eth_defi.derive.authentication
   eth_defi.derive.account
   eth_defi.derive.constants
