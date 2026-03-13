.. meta::
   :description: Tutorial for running Freqtrade strategies on GMX using a Lagoon vault

.. _gmx-freqtrade-lagoon:

GMX perpetuals trading with Freqtrade and Lagoon vaults
========================================================

This tutorial explains how to deploy `Freqtrade <https://tradingstrategy.ai/glossary/freqtrade>`__
trading strategies on `GMX V2 <https://tradingstrategy.ai/glossary/gmx>`__
perpetual futures by using a `Lagoon <https://lagoon.finance>`__ ERC-4626 vault.

- Funds stay in a non-custodial `Gnosis Safe <https://safe.global>`__ multisig — the bot never holds user capital directly.
- Standard Freqtrade strategies work unchanged against GMX's on-chain order book.

For the plain GMX + Freqtrade setup *without* a vault, see the
`gmx-ccxt-freqtrade repository <https://github.com/tradingstrategy-ai/gmx-ccxt-freqtrade>`__.

Architecture
------------

In Lagoon mode every GMX order is routed through the vault's
``TradingStrategyModuleV0.performCall()`` Zodiac module.  The asset manager's hot wallet
signs transactions, but the Gnosis Safe is the account that actually sends tokens to GMX
and receives positions.

.. code-block:: text

   ┌─────────────────────────────────────────────────────────────────┐
   │                       Freqtrade bot                             │
   │  strategy.confirm_trade_entry() / confirm_trade_exit()          │
   └────────────────────────┬────────────────────────────────────────┘
                            │  create_order() / cancel_order()
                            ▼
   ┌─────────────────────────────────────────────────────────────────┐
   │              GMXExchange  (freqtrade/gmx_exchange.py)           │
   └────────────────────────┬────────────────────────────────────────┘
                            │
                            ▼
   ┌─────────────────────────────────────────────────────────────────┐
   │              GMX CCXT adapter  (ccxt/exchange.py)               │
   └────────────────────────┬────────────────────────────────────────┘
                            │  sign_transaction_with_new_nonce()
                            ▼
   ┌─────────────────────────────────────────────────────────────────┐
   │          LagoonGMXTradingWallet  (gmx/lagoon/wallet.py)         │
   │  • Wraps tx in performCall()   • Asset manager signs            │
   └──────────────┬──────────────────────────────────────────────────┘
                  │  eth_sendRawTransaction
                  ▼
   ┌─────────────────────────────────────────────────────────────────┐
   │       TradingStrategyModuleV0.performCall(to, data, value)      │
   │                    (Zodiac module on Safe)                       │
   └──────────────┬──────────────────────────────────────────────────┘
                  │
                  ▼
   ┌─────────────────────────────────────────────────────────────────┐
   │                Gnosis Safe  (holds USDC + ETH)                  │
   └──────────────┬──────────────────────────────────────────────────┘
                  │  GMX ExchangeRouter.multicall([sendWnt, sendTokens, createOrder])
                  ▼
   ┌─────────────────────────────────────────────────────────────────┐
   │               GMX V2 on Arbitrum                                │
   └──────────────┬──────────────────────────────────────────────────┘
                  │  keeper execution (~30 s)
                  ▼
             Position opened / closed on-chain

**Lagoon vault** — An ERC-4626 vault whose underlying assets are held in a Gnosis Safe; investors
deposit USDC and receive shares, while the Safe retains custody at all times.

**TradingStrategyModuleV0** — A Zodiac module on the Safe that lets the asset manager's hot wallet
submit trades; it validates every call against a whitelist before the Safe executes it, so a
compromised key cannot drain funds to arbitrary addresses.

**Asset manager** — A hot wallet (EOA) that signs transactions and, when ``lagoon_forward_eth``
is enabled, forwards ETH with each ``performCall`` so the Safe is never required to hold ETH
for keeper fees.

**Safe** - Where the funds are held and GMX positions are opened. The Safe is configured with a custom guard contract that restricts outgoing transactions to only valid GMX interactions. You can add the safe address as a read-only wallet in your preferred wallet interface (e.g., Gnosis Safe mobile app, MetaMask) to monitor positions and balances from GMX web UI.

The guard enforces that:

- Tokens can only be sent to the GMX ``OrderVault`` (never arbitrary addresses)
- Order receivers are the Safe address only
- Only whitelisted markets and collateral tokens are permitted

Deploying the vault
-------------------

First, clone the repository with submodules and install all dependencies.
The guard contracts (``contracts/safe-integration/``) use git submodules and
the Lagoon vault contracts (``contracts/lagoon-v0/``) use Soldeer:

.. code-block:: shell

    git clone --recurse-submodules https://github.com/tradingstrategy-ai/web3-ethereum-defi.git
    cd web3-ethereum-defi

    # Python dependencies
    poetry install -E ccxt -E data

    # Solidity dependencies for Lagoon vault contracts
    cd contracts/lagoon-v0 && forge soldeer install && cd ../..

You will also need `Foundry <https://book.getfoundry.sh/getting-started/installation>`__ installed
(``forge`` is used to compile and deploy the contracts on the fly).

Then deploy with the standalone script:

.. code-block:: shell

    export JSON_RPC_ARBITRUM="https://arb1.arbitrum.io/rpc"
    export GMX_PRIVATE_KEY="0x..."

    # Deploy a vault whitelisted for ETH, BTC, and SOL markets
    poetry run python scripts/lagoon/deploy-lagoon-gmx-market.py -t ETH BTC SOL

The script handles the full flow — vault deployment, guard configuration,
GMX contract whitelisting, and collateral approval.  When complete it prints
a guard configuration report and a deployment summary:

.. code-block:: text

    Guard configuration for Safe 0x05c7BADC6248dDd8E43223e31C5b3d17B5fE684f
    │
    └── Arbitrum (chain 42161)
        Safe:   0x05c7BADC6248dDd8E43223e31C5b3d17B5fE684f
        Module: 0xd12B2000B59D60a59D6473C9F0d07f8be0F5dA00
        │
        ├── Senders (trade executors)
        │   └── 0x6DC51f9C50735658Cc6a003e07B0b92dF9c98473
        ├── Receivers
        │   └── <our multisig> (0x05c7BADC6248dDd8E43223e31C5b3d17B5fE684f)
        ├── Whitelisted assets
        │   ├── USDC (0x...)
        │   └── WETH (0x...)
        ├── GMX markets
        │   ├── ETH/USD (0x70d95587d40A2caf56bd97485aB3Eec10Bee6336)
        │   ├── BTC/USD (0x47c031236e19d024b42f8AE6780E44A573170703)
        │   └── SOL/USD (0x09400D9DB990D5ed3f35D7be61DfAEB900Af03C9)
        └── Call sites: 9 whitelisted

    ================================================================================
    DEPLOYMENT COMPLETE
    ================================================================================

      Markets:
        ETH/USD  0x70d95587d40A2caf56bd97485aB3Eec10Bee6336
        BTC/USD  0x47c031236e19d024b42f8AE6780E44A573170703
        SOL/USD  0x09400D9DB990D5ed3f35D7be61DfAEB900Af03C9
      Vault address:    0xF662aB04945cF91e21B5a3dD5229784d2166e2A2
      Safe address:     0x05c7BADC6248dDd8E43223e31C5b3d17B5fE684f
      Trading module:   0xd12B2000B59D60a59D6473C9F0d07f8be0F5dA00
      Asset manager:    0x6DC51f9C50735658Cc6a003e07B0b92dF9c98473
      Forward ETH:      True

      Vault on explorer:  https://arbiscan.io/address/0xF662aB04945cF91e21B5a3dD5229784d2166e2A2
      Safe on explorer:   https://arbiscan.io/address/0x05c7BADC6248dDd8E43223e31C5b3d17B5fE684f

    To trade ETH/USD, BTC/USD, SOL/USD through this vault, configure your
    Freqtrade bot with vault_address=0xF662aB04945cF91e21B5a3dD5229784d2166e2A2

Take note of the ``Vault address`` — this goes into ``ccxt_config.options.vaultAddress`` in your
Freqtrade config (see `Freqtrade config reference`_ below).

For a simulation run against a local Anvil fork (no real funds needed):

.. code-block:: shell

    SIMULATE=true JSON_RPC_ARBITRUM="https://arb1.arbitrum.io/rpc" \
        poetry run python scripts/lagoon/deploy-lagoon-gmx-market.py -t ETH

.. note::

    Before running the bot you must have a Lagoon vault deployed and configured
    with GMX whitelisting (see above).  For a full walkthrough of vault mechanics
    and deployment steps, see the
    `Lagoon + GMX tutorial <https://web3-ethereum-defi.tradingstrategy.ai/tutorials/lagoon-gmx.html>`__.

Prerequisites
-------------

You need:

- An Arbitrum Lagoon vault deployed and configured with GMX whitelisting
  (see `Deploying the vault`_ above).
- An **asset manager** hot wallet (EOA) with:
  - The asset manager role on the vault
  - ~0.01 ETH for gas (replenished from the Safe automatically when ``lagoon_forward_eth`` is enabled)
- The `gmx-ccxt-freqtrade <https://github.com/tradingstrategy-ai/gmx-ccxt-freqtrade>`__
  repository cloned and its dependencies installed.


Freqtrade config reference
--------------------------

Below is an annotated version of a production Lagoon config.  The key sections are
``exchange.ccxt_config`` (GMX CCXT adapter settings) and the Lagoon-specific top-level
exchange keys.

.. code-block:: json

    {
        "trading_mode": "futures",
        "margin_mode": "isolated",
        "stake_currency": "USDC",
        "stake_amount": 10,
       
        [...]

        "exchange": {
            "name": "gmx",

            "ccxt_config": {
                "enableRateLimit": true,
                "rateLimit": 500,

                "executionBuffer": 2.2,
                "defaultSlippage": 0.004,
                "referralCode": "tradingStrategy",
                // just need to pass the vault address here to enable Lagoon mode — the rest is auto-handled
                "options": {
                    "vaultAddress": "0xE3D5595707b2b75B3F25fBCc9A212A547d6E29ca"
                }
            },

            "ccxt_async_config": {
                "enableRateLimit": true,
                "rateLimit": 500
            },
            // Some live tested defaults for Lagoon mode — adjust as needed
            // Need this to enabled so the asset manager can pay the gas fees on behalf of the Vault
            "lagoon_forward_eth": true,
            "lagoon_gas_buffer": 500000,
            "lagoon_auto_approve": true
        }
        [...]
    }

Config parameters explained
----------------------------

``ccxt_config.options.vaultAddress`` (**required** — enables Lagoon mode)
    Address of the Lagoon ERC-4626 vault contract (not the Gnosis Safe address).
    When present, the exchange initialises a ``LagoonGMXTradingWallet`` and routes all
    orders through ``performCall()``.  The Safe address and module address are
    discovered automatically from the vault.

``ccxt_config.executionBuffer`` (default: ``2.2``)
    Multiplier applied to the GMX execution fee estimate to ensure orders are not
    rejected by the keeper for insufficient fee.  Raise this value if orders are
    consistently failing at execution time.

``ccxt_config.defaultSlippage`` (default: ``0.003`` = 0.3%)
    Acceptable price deviation between order submission and keeper execution.
    Higher values reduce the chance of price-impact rejections in volatile markets;
    lower values give tighter fill prices.  Typical values: ``0.003``–``0.010``.

``ccxt_config.referralCode`` (optional)
    Human-readable GMX referral code (max 32 chars).  Encoded as ``bytes32`` and
    embedded in every ``createOrder`` call for protocol fee discounts.
    Register a code at `app.gmx.io/#/referrals <https://app.gmx.io/#/referrals>`__.

``ccxt_config.options.gasMonitorEnabled`` (default: ``true``)
    Enable per-order gas cost estimation and logging.  Gas warnings are forwarded
    to Telegram when the estimated cost exceeds ``gasWarningThresholdUsd`` ($1.00).

``lagoon_forward_eth`` (default: ``true``)
    When ``true``, the asset manager forwards ETH with every ``performCall``
    transaction so the Safe receives GMX keeper execution fees automatically.
    This means the asset manager wallet only needs to hold gas ETH; the Safe is
    refunded from the forwarded value.

``lagoon_gas_buffer`` (default: ``500000``)
    Additional gas units added to each order transaction to cover the
    ``performCall`` wrapper overhead on top of the inner GMX transaction gas.

``lagoon_auto_approve`` (default: ``true``)
    When ``true``, the bot automatically approves common collateral tokens (USDC,
    WETH) for the GMX ``SyntheticsRouter`` contract via ``performCall`` on startup.
    Disable this if approvals are managed externally.


Running the bot
---------------

.. code-block:: shell

    git clone  --recurse-submodules  https://github.com/tradingstrategy-ai/gmx-ccxt-freqtrade.git
    cd gmx-ccxt-freqtrade
    git submodule update --remote --merge

    ./freqtrade-gmx trade \
        --config configs/adxmomentum_gmx_lagoon.json \
        --strategy ADXMomentumGMX \
        --logfile freqtrade.log

Pair whitelist
--------------

GMX markets are specified as ``BASE/USDC:USDC`` pairs in the ``pair_whitelist``.
All perpetual markets available on GMX V2 Arbitrum can be used; see the full list
with:

.. code-block:: shell

    export JSON_RPC_ARBITRUM=$ARBITRUM_CHAIN_JSON_RPC
    poetry run python scripts/gmx/list-gmx-markets.py

Operational scripts
-------------------

Several utility scripts are available in ``scripts/gmx/`` and ``scripts/lagoon/``:

``scripts/gmx/gmx_lagoon_close_all_positions.py``
    Force-close all open GMX positions held by the vault Safe.  Useful for
    emergency shutdown or manual position management.

    .. code-block:: shell

        export JSON_RPC_ARBITRUM=$ARBITRUM_CHAIN_JSON_RPC
        export GMX_PRIVATE_KEY="0x..."
        export LAGOON_VAULT_ADDRESS="0xE3D5595707b2b75B3F25fBCc9A212A547d6E29ca"
        poetry run python scripts/gmx/gmx_lagoon_close_all_positions.py

``scripts/lagoon/recover-safe-funds.py``
    Transfer tokens (USDC, WBTC, ARB, etc.) or ETH from the Safe back to the
    asset manager wallet.

    .. code-block:: shell

        # Recover USDC (denomination token, default)
        poetry run python scripts/lagoon/recover-safe-funds.py \
            --vault 0xE3D5595707b2b75B3F25fBCc9A212A547d6E29ca \
            --module 0x10BaB635acDBD6938B0177a053CfBABeEcd8F3BD

        # Recover a specific ERC-20 (e.g., WBTC)
        poetry run python scripts/lagoon/recover-safe-funds.py \
            --vault 0x... --module 0x... \
            --token 0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f

        # Dry-run: show balances without transacting
        poetry run python scripts/lagoon/recover-safe-funds.py \
            --vault 0x... --module 0x... --dry-run

API documentation
-----------------

- GMX CCXT adapter: ``eth_defi.gmx.ccxt``
- Freqtrade exchange wrapper: ``eth_defi.gmx.freqtrade.gmx_exchange``
- Lagoon wallet: ``eth_defi.gmx.lagoon.wallet``
- Lagoon vault: ``eth_defi.erc_4626.vault_protocol.lagoon.vault``
- Open positions: ``eth_defi.gmx.core.open_positions``
- Vault deployment: ``eth_defi.erc_4626.vault_protocol.lagoon.deployment``
