.. meta::
   :description: Tutorial for Lagoon vaults and GMX perpetuals trading

.. _lagoon-gmx:

Lagoon and GMX perpetuals integration
=====================================

Here is a Python example how to trade GMX V2 perpetuals from a Lagoon vault.

- You need ~0.01 ETH and ~$5 USDC on Arbitrum to run this tutorial script.
- This script deploys a new Lagoon vault with GMX integration enabled.
- The deployed vault has `TradingStrategyModuleV0 <https://github.com/tradingstrategy-ai/web3-ethereum-defi/tree/master/contracts/safe-integration>`__
  configured for allowing automated whitelisted trades by an asset manager. This is a Zodiac module which extends the underlying
  Gnosis Safe functionality used as the core of Lagoon vaults.
- In this example, the deployer account, asset manager and Gnosis co-signers are all the same account for simplicity.
- After deploying the vault, the script deposits USDC collateral into the vault.
- The deposit must be settled to the vault per `ERC-7540 deposit and settlement cycle <https://tradingstrategy.ai/glossary/erc-7540>`__.
- When the vault has USDC in the Safe, we open a leveraged ETH long position via GMX.
- The script then closes the position and withdraws collateral.

Architecture overview
---------------------

The Lagoon vault uses a Gnosis Safe multisig to hold assets securely.
Trading is performed through the TradingStrategyModuleV0, which wraps
all transactions via ``performCall()``. This allows the asset manager's
hot wallet to execute trades while the Safe retains custody of funds.

::

    Asset Manager (Hot Wallet)
        │
        ▼
    TradingStrategyModuleV0.performCall()
        │
        ▼
    Gnosis Safe (Holds assets)
        │
        ▼
    GMX ExchangeRouter.multicall([sendWnt, sendTokens, createOrder])
        │
        ▼
    GMX Keeper (Executes order on-chain)

The Guard contract validates all GMX calls to ensure:

- Funds can only be sent to the GMX OrderVault (not arbitrary addresses)
- Order receivers are whitelisted (Safe address only)
- Only approved markets and collateral tokens can be used

For security details, see the `README-GMX-Lagoon.md <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/README-GMX-Lagoon.md>`__ file.

Prerequisites
-------------

You need:

- An Arbitrum wallet funded with ETH and USDC (see amounts below)
- Environment variables configured (see below)

Required funds
--------------

Your wallet must have the following minimum balances on Arbitrum:

**ETH** (~0.01 ETH recommended)
    Used for gas fees. The tutorial performs multiple transactions:

    - Vault deployment: ~0.003-0.005 ETH
    - Guard configuration: ~0.001 ETH
    - Deposit and settlement: ~0.001 ETH
    - GMX order transactions: ~0.001 ETH each (includes execution fees)
    - Withdrawal: ~0.001 ETH

    Total gas costs vary with network congestion. Having 0.01 ETH provides
    a comfortable buffer.

**USDC** (~$5 minimum)
    Used as trading collateral. The tutorial deposits $5 USDC into the vault
    and opens a $2 position with 2x leverage ($1 collateral).

    You can modify ``deposit_amount`` and ``position_size`` in the script
    to trade larger amounts.

.. note::

    GMX has minimum position sizes. Very small positions may be rejected
    by the GMX keeper. The default $2 position size is near the minimum.

Environment variables
---------------------

The following environment variables must be configured before running the tutorial script:

``JSON_RPC_ARBITRUM`` (required)
    Arbitrum mainnet JSON-RPC endpoint URL. You can use a public RPC or a private
    node provider like Alchemy, Infura, or QuickNode.

    Example values:

    - ``https://arb1.arbitrum.io/rpc`` (public, rate-limited)
    - ``https://arb-mainnet.g.alchemy.com/v2/YOUR_API_KEY`` (Alchemy)
    - ``https://arbitrum-mainnet.infura.io/v3/YOUR_PROJECT_ID`` (Infura)

    The RPC must be an archive node if you want to query historical state.

``PRIVATE_KEY_SWAP_TEST`` (required)
    Private key of the Arbitrum wallet that will deploy and interact with the vault.
    This wallet must have sufficient ETH and USDC (see "Required funds" above).

    The private key should be in hexadecimal format, with or without the ``0x`` prefix.
    See `how to export your private key from MetaMask <https://ethereum.stackexchange.com/a/125699/620>`__.

    .. warning::

        Never commit your private key to version control. Use environment variables
        or a secrets manager in production.

``ETHERSCAN_API_KEY`` (optional but recommended)
    Arbiscan API key for contract verification. When provided, the deployed contracts
    will be verified on Arbiscan, making them easier to inspect and interact with.

    Get a free API key at `arbiscan.io/apis <https://arbiscan.io/apis>`__.

    If not provided, contracts will still deploy but won't be verified.

Running the script
------------------

.. code-block:: shell

    # Arbitrum JSON-RPC endpoint (required)
    # Use a reliable RPC provider for production
    export JSON_RPC_ARBITRUM="https://arb1.arbitrum.io/rpc"

    # Private key of wallet with ETH + USDC (required)
    # Must have ~0.01 ETH for gas and ~$5 USDC for trading
    export PRIVATE_KEY_SWAP_TEST="0x..."

    # Arbiscan API key for contract verification (optional)
    # Get one at https://arbiscan.io/apis
    export ETHERSCAN_API_KEY="..."

    # Run the tutorial script
    poetry run python scripts/lagoon/lagoon-gmx-example.py

The script will:

1. Deploy a Lagoon vault with GMX integration
2. Whitelist GMX contracts and the ETH/USDC market
3. Deposit USDC collateral into the vault
4. Approve tokens for GMX SyntheticsRouter
5. Open a leveraged ETH long position
6. Wait for GMX keeper execution
7. Close the position
8. Withdraw collateral from the vault
9. Print a summary of all transactions and costs

Listing available GMX markets
-----------------------------

To see all available GMX markets that can be whitelisted for trading, use the
``list-gmx-markets.py`` script:

.. code-block:: shell

    export JSON_RPC_ARBITRUM="https://arb1.arbitrum.io/rpc"

    # Table output with all markets
    poetry run python scripts/gmx/list-gmx-markets.py

    # Python code for copy-pasting into your deployment
    poetry run python scripts/gmx/list-gmx-markets.py --python

    # JSON output for programmatic use
    poetry run python scripts/gmx/list-gmx-markets.py --json

    # Plain addresses only (for scripting)
    poetry run python scripts/gmx/list-gmx-markets.py --addresses

Configuring GMX whitelisting
----------------------------

When deploying a Lagoon vault with GMX support, use the :py:class:`~eth_defi.gmx.whitelist.GMXDeployment`
dataclass to configure which GMX contracts and markets to whitelist:

.. code-block:: python

    from eth_defi.gmx.whitelist import GMXDeployment

    # Configure GMX integration
    gmx_deployment = GMXDeployment(
        # GMX contract addresses (Arbitrum mainnet)
        exchange_router="0x7C68C7866A64FA2160F78EEaE12217FFbf871fa8",
        synthetics_router="0x7452c558d45f8afC8c83dAe62C3f8A5BE19c71f6",
        order_vault="0x31eF83a530Fde1B38EE9A18093A333D8Bbbc40D5",
        # Markets to whitelist for trading
        markets=[
            "0x70d95587d40A2caf56bd97485aB3Eec10Bee6336",  # ETH/USD
            "0x47c031236e19d024b42f8AE6780E44A573170703",  # BTC/USD
        ],
        # Optional: collateral tokens to whitelist
        tokens=[
            "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",  # USDC
            "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",  # WETH
        ],
    )

    # Pass to deployment function
    deployment = deploy_automated_lagoon_vault(
        ...
        gmx_deployment=gmx_deployment,
    )

For convenience, you can use the factory method for Arbitrum:

.. code-block:: python

    from eth_defi.gmx.whitelist import GMXDeployment

    # Create with Arbitrum defaults
    gmx_deployment = GMXDeployment.create_arbitrum(
        markets=["0x70d95587d40A2caf56bd97485aB3Eec10Bee6336"],  # ETH/USD
    )

API documentation
-----------------

- GMX whitelisting: :py:mod:`eth_defi.gmx.whitelist`
- GMX CCXT adapter: :py:mod:`eth_defi.gmx.ccxt`
- LagoonWallet: :py:mod:`eth_defi.gmx.lagoon.wallet`
- LagoonVault: :py:mod:`eth_defi.erc_4626.vault_protocol.lagoon.vault`
- Vault deployment: :py:func:`eth_defi.erc_4626.vault_protocol.lagoon.deployment.deploy_automated_lagoon_vault`
- Guard contract: `GuardV0Base.sol <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/contracts/guard/src/GuardV0Base.sol>`__

Source code
-----------

.. literalinclude:: ../../../scripts/lagoon/lagoon-gmx-example.py
   :language: python
