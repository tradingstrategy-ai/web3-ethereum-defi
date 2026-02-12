.. meta::
   :description: Tutorial for Lagoon vaults and GMX perpetuals trading

.. _lagoon-gmx:

Lagoon vault, GMX and CCXT integration
======================================

Here is a Python example how to trade GMX V2 perpetuals from a Lagoon vault.
This is a low level code example that shows every step in the process. For setting up a full trading vault with a bot for GMX,
see `trade-executor <https://github.com/tradingstrategy-ai/trade-executor/>`__ Python project.
For trading GMX perpetuals using Freqtrade and CCXT, see `gmx-ccxt-freqtrade <https://github.com/tradingstrategy-ai/gmx-ccxt-freqtrade>`__.

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

ERC-7540 deposit/redeem flow
-----------------------------

Lagoon vaults implement `ERC-7540 <https://tradingstrategy.ai/glossary/erc-7540>`__ (async redemption extension to ERC-4626).
Deposits and redemptions are asynchronous with a silo holding assets/shares
between request and finalisation.

Deposit flow::

    User                    Vault                   Silo                    Safe
      │                       │                       │                       │
      │── requestDeposit() ──▶│                       │                       │
      │   (transfer USDC)     │── hold USDC ─────────▶│                       │
      │                       │                       │                       │
      │                       │◀── settleDeposit() ───│                       │
      │                       │   (asset manager)     │── transfer USDC ─────▶│
      │                       │                       │                       │
      │                       │── mint shares ───────▶│                       │
      │                       │                       │                       │
      │◀── finaliseDeposit() ─│◀── transfer shares ───│                       │
      │   (claim shares)      │                       │                       │
      │                       │                       │                       │

Redeem flow::

    User                    Vault                   Silo                    Safe
      │                       │                       │                       │
      │── requestRedeem() ───▶│                       │                       │
      │   (transfer shares)   │── hold shares ───────▶│                       │
      │                       │                       │                       │
      │                       │◀── settleRedeem() ────│◀── transfer USDC ─────│
      │                       │   (asset manager)     │   (burn shares)       │
      │                       │                       │                       │
      │◀── finaliseRedeem() ──│◀── transfer USDC ─────│                       │
      │   (claim USDC)        │                       │                       │
      │                       │                       │                       │

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
    and opens a $5 position with 1.1x leverage (~$4.55 collateral).

    You can modify ``deposit_amount`` and ``position_size`` in the script
    to trade larger amounts.

.. note::

    GMX requires >$2 of collateral per position. The default $5 position
    size with 1.1x leverage provides ~$4.55 collateral, safely above the
    minimum.

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

``GMX_PRIVATE_KEY`` (required)
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
    export GMX_PRIVATE_KEY="0x..."

    # Arbiscan API key for contract verification (optional)
    # Get one at https://arbiscan.io/apis
    export ETHERSCAN_API_KEY="..."

    # Run the tutorial script
    poetry run python scripts/lagoon/lagoon-gmx-example.py

The script will:

1. Deploy a Lagoon vault with GMX integration
2. Whitelist GMX contracts and the ETH/USDC market
3. Deposit USDC collateral into the vault
4. Fund the vault's Safe with ETH for GMX execution fees
5. Approve tokens for GMX SyntheticsRouter
6. Open a leveraged ETH long position
7. Wait for GMX keeper execution
8. Close the position
9. Recover remaining ETH from the Safe back to the hot wallet
10. Withdraw collateral from the vault
11. Print a summary of all transactions and costs

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

    # create_arbitrum() dynamically fetches the latest GMX contract
    # addresses from the GMX contracts registry on GitHub
    gmx_deployment = GMXDeployment.create_arbitrum(
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
- LagoonGMXTradingWallet: :py:mod:`eth_defi.gmx.lagoon.wallet`
- LagoonVault: :py:mod:`eth_defi.erc_4626.vault_protocol.lagoon.vault`
- Vault deployment: :py:func:`eth_defi.erc_4626.vault_protocol.lagoon.deployment.deploy_automated_lagoon_vault`
- Guard contract: `GuardV0Base.sol <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/contracts/guard/src/GuardV0Base.sol>`__

Source code
-----------

.. literalinclude:: ../../../scripts/lagoon/lagoon-gmx-example.py
   :language: python

Example output
--------------

.. code-block:: plain

    ================================================================================
    LAGOON-GMX TRADING TUTORIAL
    ================================================================================

    Connected to Arbitrum (chain ID: 42161)
    Latest block: 431,223,855

    Wallet: 0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6
    ETH balance: 0.025746700731346354 ETH
    USDC balance: 41.725675

    Current ETH price: $1969.93

    ================================================================================
    STEP 1: Deploy Lagoon vault with GMX integration
    ================================================================================

    Deploying Lagoon vault with GMX integration...
    Deployer/Asset Manager: 0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6
    Base asset: USDC (0xaf88d065e77c8cC2239327C5EDb3A432268e5831)
    GMX ExchangeRouter: 0x1C3fa76e6E1088bCE750f23a5BFcffa1efEF6A41
    GMX Market: 0x70d95587d40A2caf56bd97485aB3Eec10Bee6336

    Vault deployed successfully!
    Vault address: 0xc1121b5808068815bc7D330Af6F1e5BE50b8a253
    Safe address: 0x6ed3C2fe7deDe9c0Da94bd1D1ed04718689a91fC
    Trading module: 0x9e7Bc7E5f60f9dE92aB76F42a948b48176A8E30c
    GMX integration configured!

    ================================================================================
    STEP 2: Deposit USDC collateral to vault
    ================================================================================

    Depositing 5 USDC to vault...

    Broadcasting tx #1: Approve USDC for vault deposit
    TX hash: 8f521397f87893d5d8dc1bcfd1306fee2c9a230d0618ee606794b0a774f5c084
    Gas used: 55,527 @ 0.02 gwei = 0.000001 ETH

    Broadcasting tx #2: Request USDC deposit to vault
    TX hash: 2c995b5083685c2662b70aa1a103005402c61df6292f04baa219f44fe28682fc
    Gas used: 152,211 @ 0.02 gwei = 0.000003 ETH

    Broadcasting tx #3: Post vault valuation
    TX hash: c72a1a367ff59f885296bcdf90736b1838ae79b95d5a18ab3709e805df530fff
    Gas used: 129,483 @ 0.02 gwei = 0.000003 ETH

    Broadcasting tx #4: Settle vault deposits
    TX hash: a5f3c37f919af9b470c9b1e89c717d0bcfc4aa50cef2030b513da9e75361b969
    Gas used: 318,665 @ 0.02 gwei = 0.000006 ETH

    Broadcasting tx #5: Finalise deposit (claim shares)
    TX hash: b69faff61b19e91064adfac2a3dbbe0985dcaccc34ad3bce36615328a32ecf34
    Gas used: 71,732 @ 0.02 gwei = 0.000001 ETH

    Deposit complete!
    Safe USDC balance: 5
    Depositor share balance: 5

    --------------------------------------------------------------------------------
    STEP 2b: Fund Safe with ETH for GMX execution fees
    --------------------------------------------------------------------------------

    Funding Safe with 0.001 ETH for GMX execution fees...
    Safe address: 0x6ed3C2fe7deDe9c0Da94bd1D1ed04718689a91fC
    Safe ETH balance: 0.001 ETH

    ================================================================================
    STEP 3: Setup GMX trading
    ================================================================================

    Setting up GMX trading...

    Broadcasting tx #7: Approve USDC for GMX SyntheticsRouter
    TX hash: 1d74747f76131d2ef7961cec7d56dbcdac579701f49dd95012c83a1f29bac13e
    Gas used: 92,350 @ 0.02 gwei = 0.000002 ETH
    GMX trading ready!

    ================================================================================
    STEP 4: Open leveraged ETH long position
    ================================================================================

    Opening LONG ETH position...
    Size: $5
    Leverage: 1.1x
    Collateral: $4.55 USDC

    Order submitted!
    TX hash: 0xe184bb5e1005d99f236d98cc81af03530d41203d841d7ebcd87e60eb0a26acce
    Status: open

    Waiting for GMX keeper execution...

    ================================================================================
    STEP 5: Close the position
    ================================================================================

    Closing LONG ETH position...

    Close order submitted!
    TX hash: 0x1b0817309cd94fe80bd5d6dfe4776534bab147cd5b8f4b1225990854ef4d1911
    Status: open

    Waiting for GMX keeper execution...

    --------------------------------------------------------------------------------
    STEP 5b: Recover ETH from Safe
    --------------------------------------------------------------------------------

    Recovering 0.000766 ETH from Safe to hot wallet...
    Recovered 0.000766 ETH

    ================================================================================
    STEP 6: Withdraw collateral from vault
    ================================================================================

    Withdrawing from vault...
    Shares to redeem: 5

    Broadcasting tx #9: Request vault redemption
    TX hash: f4c1edf95dd3f23879e24b83ce9699a3e3e6d674846c8356743197832cdd239b
    Gas used: 127,582 @ 0.02 gwei = 0.000003 ETH

    Broadcasting tx #10: Post vault valuation for withdrawal
    TX hash: d180907f38b51a868b0b055e8953913ac2325383045d32d32800d6fb4c9d50e8
    Gas used: 114,495 @ 0.02 gwei = 0.000002 ETH

    Broadcasting tx #11: Settle vault for withdrawal
    TX hash: ff0df4eaba37286b9aae48a3421ca1bfbdc9d3099d4524b673bf37aa85fcc71e
    Gas used: 302,482 @ 0.02 gwei = 0.000006 ETH

    Broadcasting tx #12: Finalise redemption (claim USDC)
    TX hash: 49c84e6a928dcbcfbeac1acd761ea57df3832a4c2fd92af04e89966e1a697ebd
    Gas used: 75,264 @ 0.02 gwei = 0.000002 ETH

    Withdrawal complete! USDC balance: 41.720259

    ================================================================================
    STEP 7: Trading summary
    ================================================================================

    ================================================================================
    TRADING SUMMARY
    ================================================================================

    Transactions:
    --------------------------------------------------------------------------------
    1. Approve USDC for vault deposit
        TX: 8f521397f87893d5d8dc1bcfd1306fee2c9a230d0618ee606794b0a774f5c084
        Gas: 55,527 @ 0.02 gwei = 0.000001 ETH ($0.00)

    2. Request USDC deposit to vault
        TX: 2c995b5083685c2662b70aa1a103005402c61df6292f04baa219f44fe28682fc
        Gas: 152,211 @ 0.02 gwei = 0.000003 ETH ($0.01)

    3. Post vault valuation
        TX: c72a1a367ff59f885296bcdf90736b1838ae79b95d5a18ab3709e805df530fff
        Gas: 129,483 @ 0.02 gwei = 0.000003 ETH ($0.01)

    4. Settle vault deposits
        TX: a5f3c37f919af9b470c9b1e89c717d0bcfc4aa50cef2030b513da9e75361b969
        Gas: 318,665 @ 0.02 gwei = 0.000006 ETH ($0.01)

    5. Finalise deposit (claim shares)
        TX: b69faff61b19e91064adfac2a3dbbe0985dcaccc34ad3bce36615328a32ecf34
        Gas: 71,732 @ 0.02 gwei = 0.000001 ETH ($0.00)

    6. Fund Safe with ETH for GMX execution fees
        TX: e46b64b6cd843e1e435ebd9e561499f053e294290e65fe5a0d76a1d17f4769be
        Gas: 27,359 @ 0.02 gwei = 0.000001 ETH ($0.00)

    7. Approve USDC for GMX SyntheticsRouter
        TX: 1d74747f76131d2ef7961cec7d56dbcdac579701f49dd95012c83a1f29bac13e
        Gas: 92,350 @ 0.02 gwei = 0.000002 ETH ($0.00)

    8. Recover ETH from Safe to hot wallet
        TX: 0e80889ae34b4462d85f29b36a989e157d71afe8a0e2e13a55279e788b898b2a
        Gas: 63,984 @ 0.02 gwei = 0.000001 ETH ($0.00)

    9. Request vault redemption
        TX: f4c1edf95dd3f23879e24b83ce9699a3e3e6d674846c8356743197832cdd239b
        Gas: 127,582 @ 0.02 gwei = 0.000003 ETH ($0.01)

    10. Post vault valuation for withdrawal
        TX: d180907f38b51a868b0b055e8953913ac2325383045d32d32800d6fb4c9d50e8
        Gas: 114,495 @ 0.02 gwei = 0.000002 ETH ($0.00)

    11. Settle vault for withdrawal
        TX: ff0df4eaba37286b9aae48a3421ca1bfbdc9d3099d4524b673bf37aa85fcc71e
        Gas: 302,482 @ 0.02 gwei = 0.000006 ETH ($0.01)

    12. Finalise redemption (claim USDC)
        TX: 49c84e6a928dcbcfbeac1acd761ea57df3832a4c2fd92af04e89966e1a697ebd
        Gas: 75,264 @ 0.02 gwei = 0.000002 ETH ($0.00)

    --------------------------------------------------------------------------------
    Position details:
    Size:        $5.00
    Entry price: $1968.62
    Exit price:  $1968.60
    Realised PnL: $36.72

    Costs:
    Total gas:           0.000031 ETH ($0.06)
    GMX execution fees:  0.000301 ETH
    Total costs:         0.000332 ETH

    Net result:
    PnL after costs: $36.66
    ================================================================================

    Tutorial complete!
