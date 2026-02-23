.. meta::
   :description: Tutorial for deploying Lagoon vaults on HyperEVM with Hypercore vault deposits

.. _lagoon-hyperliquid:

Lagoon vault on HyperEVM with Hypercore deposits
=================================================

Here is a Python example how to deploy a `Lagoon vault <https://lagoon.finance/>`__
on `HyperEVM <https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/hyperevm>`__
and deposit USDC into a `Hypercore vault <https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults>`__.
This is a low-level code example that shows every step in the deployment and
deposit/withdrawal process.

- You need ~0.1 HYPE and ~$7 USDC on HyperEVM to run this tutorial script.
- This script deploys a new Lagoon vault with Hypercore integration on HyperEVM.
- The deployed vault has `TradingStrategyModuleV0 <https://github.com/tradingstrategy-ai/web3-ethereum-defi/tree/master/contracts/safe-integration>`__
  configured for allowing automated whitelisted trades by an asset manager.
- In this example, the deployer account, asset manager and Safe co-signer are all the same account for simplicity.
- After deploying the vault, the script deposits USDC into a Hypercore vault via ``CoreWriter``.
- The deposit bridges USDC from HyperEVM to HyperCore, then moves it through spot and perp accounts into the target vault.
- The script can also withdraw from the vault after the lock-up period expires.
- A simulation mode (Anvil fork) is available for testing without real funds.

Architecture overview
---------------------

The Lagoon vault uses a Gnosis Safe multisig to hold assets on HyperEVM.
The asset manager's hot wallet sends transactions through ``TradingStrategyModuleV0.performCall()``,
which wraps all calls via the Safe. Hypercore deposits require bridging USDC from
HyperEVM to HyperCore, then executing ``CoreWriter`` actions to move funds
through spot and perp accounts into the target vault.

::

    Asset Manager (Hot Wallet)
        │
        ▼
    TradingStrategyModuleV0.performCall()
        │
        ▼
    Gnosis Safe (Holds EVM USDC)
        │
        ▼
    CoreDepositWallet.deposit()         ← Bridge USDC from EVM to HyperCore
        │
        ▼
    [EVM escrow clears]                 ← Wait for HyperCore to process bridge
        │
        ▼
    CoreWriter.sendRawAction()          ← Multicall: transferUsdClass + vaultTransfer
        │
        ▼
    HyperCore Vault (Holds position)

The Guard contract validates all calls to ensure:

- Only whitelisted ``CoreWriter`` actions can be executed
- Funds can only be sent to ``CoreDepositWallet`` (not arbitrary addresses)
- Only approved Hypercore vault addresses can receive deposits

Two-phase deposit flow
----------------------

Deposits from HyperEVM to a Hypercore vault are split into two phases because
the ``CoreDepositWallet`` bridge is asynchronous — USDC sits in an EVM escrow
until HyperCore processes it (typically 2–10 seconds).

::

    Phase 1: Bridge                     Phase 2: Vault deposit
    ────────────────                    ──────────────────────
    Safe                                HyperCore Spot
      │                                   │
      │── approve USDC ──▶ CDW            │── transferUsdClass ──▶ Perp
      │── CDW.deposit() ──▶ EVM escrow    │── vaultTransfer ──────▶ Vault
      │                                   │
      ▼                                   ▼
    [Wait for escrow to clear]          [CoreWriter actions batched
     via spotClearinghouseState]          in a single EVM block]

Phase 2 batches ``transferUsdClass`` and ``vaultTransfer`` into a single
``multicall`` transaction. Because both CoreWriter actions land in the same
EVM block, HyperCore processes them sequentially — the spot-to-perp transfer
completes before the vault deposit runs.

HyperEVM dual-block architecture
---------------------------------

HyperEVM produces two types of blocks:

- **Small blocks** (~2–3M gas, every ~1 second): normal transactions
- **Large blocks** (30M gas, every ~1 minute): contract deployments

``TradingStrategyModuleV0`` requires ~5.4M gas to deploy, exceeding the small
block gas limit. The deployment script automatically toggles ``usingBigBlocks``
via the ``evmUserModify`` exchange API action. This routes the deployer's
transactions to the large block mempool for the duration of each contract
deployment, then switches back to small blocks for fast confirmations.

See `dual-block architecture docs <https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/hyperevm/dual-block-architecture>`__
for details.

Prerequisites
-------------

You need:

- A HyperEVM wallet funded with HYPE (gas) and USDC (see amounts below)
- Environment variables configured (see below)

Required funds
--------------

Your wallet must have the following minimum balances on HyperEVM:

**HYPE** (~0.1 HYPE recommended)
    Used for gas fees. The tutorial performs multiple transactions:

    - Vault deployment (big blocks): ~0.02–0.05 HYPE
    - Guard configuration: ~0.005 HYPE
    - USDC transfer to Safe: ~0.001 HYPE
    - Account activation: ~0.001 HYPE
    - Deposit phases: ~0.002 HYPE
    - Withdrawal: ~0.002 HYPE

    Total gas costs vary with network congestion. Having 0.1 HYPE provides
    a comfortable buffer. Big block transactions take ~1 minute to confirm.

**USDC** (~$7 minimum for deposit)
    Broken down as:

    - $5 USDC for the vault deposit (minimum Hypercore vault deposit)
    - $2 USDC for HyperCore account activation ($1 creation fee + $1 reaches spot)

    You can modify ``USDC_AMOUNT`` to deposit larger amounts.

.. note::

    Hypercore silently rejects vault deposits below $5 USDC. The script
    enforces this minimum with an assertion. The $2 activation overhead
    is only charged once per Safe address — subsequent deposits do not
    incur the activation cost.

Account funding for HyperEVM testnet
-------------------------------------

Funding a HyperEVM testnet account requires several steps because there is no
direct faucet:

1. Create a new private key and set ``HYPERCORE_WRITER_TEST_PRIVATE_KEY``
2. Move ~$2 worth of ETH on Arbitrum to that address
3. Move ~$5 worth of USDC on Arbitrum to that address
4. Sign in to `app.hyperliquid.xyz <https://app.hyperliquid.xyz>`__ with the new account
5. Deposit $5 USDC (minimum)
6. Now you have an account on Hyperliquid mainnet
7. Visit `app.hyperliquid-testnet.xyz/drip <https://app.hyperliquid-testnet.xyz/drip>`__ and claim
8. Now you have 1,000 USDC on the Hypercore testnet
9. Buy 1 HYPE with the mock USDC (set max slippage to 99%, testnet orderbook is illiquid)
10. Visit `Testnet portfolio <https://app.hyperliquid-testnet.xyz/portfolio>`__ — click EVM <-> CORE
11. Move 100 USDC to HyperEVM testnet
12. Move 0.01 HYPE to HyperEVM testnet
13. Check HyperEVM testnet balance on EVM <-> CORE dialog
    (there is no working HyperEVM testnet explorer)

.. tip::

    We recommend using **HyperEVM mainnet** for testing. It's cheaper and
    less hassle than funding a testnet account.

Environment variables
---------------------

The following environment variables must be configured before running the script:

``HYPERCORE_WRITER_TEST_PRIVATE_KEY`` (required on live network)
    Private key of the HyperEVM wallet that will deploy and interact with the vault.
    This wallet must have sufficient HYPE and USDC (see "Required funds" above).

    The private key should be in hexadecimal format, with or without the ``0x`` prefix.

    In ``SIMULATE`` mode, defaults to the Anvil account #0 private key.

    .. warning::

        Never commit your private key to version control. Use environment variables
        or a secrets manager in production.

``NETWORK`` (optional, default: ``testnet``)
    Network to deploy on: ``mainnet`` or ``testnet``.

    - ``mainnet``: HyperEVM chain 999. Requires ``JSON_RPC_HYPERLIQUID``.
    - ``testnet``: HyperEVM chain 998. Uses public RPC by default.

``JSON_RPC_HYPERLIQUID`` (required for mainnet)
    HyperEVM mainnet JSON-RPC endpoint URL. Required when ``NETWORK=mainnet``.

``JSON_RPC_HYPERLIQUID_TESTNET`` (optional)
    HyperEVM testnet JSON-RPC endpoint URL.
    Defaults to ``https://rpc.hyperliquid-testnet.xyz/evm``.

``SIMULATE`` (optional)
    Set to any value to fork the network via Anvil. No real funds needed.
    Mock CoreWriter/CoreDepositWallet contracts are deployed automatically.

``ACTION`` (optional, default: ``both``)
    Which operation to perform:

    - ``deposit``: Deploy vault and deposit USDC
    - ``withdraw``: Withdraw from an existing vault (requires ``LAGOON_VAULT``)
    - ``both``: Deploy, deposit, then withdraw

    On testnet, vault deposits have a 1-day lock-up period, so you may need
    to run ``deposit`` first and ``withdraw`` the next day.

``HYPERCORE_VAULT`` (optional)
    Hypercore vault address to deposit into. Defaults to the
    `HLP vault <https://app.hyperliquid.xyz/vaults/0xdfc24b077bc1425ad1dea75bcb6f8158e10df303>`__
    for the selected network.

``USDC_AMOUNT`` (optional, default: ``5``)
    USDC amount in human units for the vault deposit.
    On live networks, an additional 2 USDC is automatically added for
    HyperCore account activation.

``DEPOSIT_MODE`` (optional, default: ``two_phase``)
    Deposit strategy:

    - ``two_phase`` (recommended): Bridges USDC in phase 1, waits for EVM escrow
      to clear, then batches spot-to-perp and vault deposit in phase 2.
    - ``batched``: Single multicall with all steps. Only available in ``SIMULATE``
      mode — disabled on live networks because CoreWriter actions can fail
      silently if the bridge hasn't cleared.

``LAGOON_VAULT`` (optional)
    Existing Lagoon vault address. When set, skips deployment and whitelisting.
    Requires ``TRADING_STRATEGY_MODULE``.

``TRADING_STRATEGY_MODULE`` (optional)
    Existing TradingStrategyModuleV0 address. Required when ``LAGOON_VAULT`` is set.

``LOG_LEVEL`` (optional, default: ``info``)
    Python logging level.

Running the script
------------------

Simulate on Anvil fork (no real funds needed):

.. code-block:: shell

    # Simulate — deploys mock contracts on Anvil fork
    poetry run python scripts/hyperliquid/deploy-lagoon-hyperliquid-vault.py

    # Explicit simulate flag
    SIMULATE=true poetry run python scripts/hyperliquid/deploy-lagoon-hyperliquid-vault.py

Mainnet deployment (recommended for testing):

.. code-block:: shell

    # Set your private key (must have HYPE + USDC on HyperEVM)
    export HYPERCORE_WRITER_TEST_PRIVATE_KEY="0x..."
    export JSON_RPC_HYPERLIQUID="https://rpc.hyperliquid.xyz/evm"

    # Deploy vault and deposit 5 USDC into HLP vault
    NETWORK=mainnet ACTION=deposit \
        poetry run python scripts/hyperliquid/deploy-lagoon-hyperliquid-vault.py

Testnet deployment:

.. code-block:: shell

    export HYPERCORE_WRITER_TEST_PRIVATE_KEY="0x..."

    # Deploy vault and deposit 5 USDC (testnet)
    NETWORK=testnet ACTION=deposit \
        poetry run python scripts/hyperliquid/deploy-lagoon-hyperliquid-vault.py

    # Next day: withdraw from the same vault after lock-up expires
    NETWORK=testnet ACTION=withdraw USDC_AMOUNT=5 \
        LAGOON_VAULT=0x... TRADING_STRATEGY_MODULE=0x... \
        poetry run python scripts/hyperliquid/deploy-lagoon-hyperliquid-vault.py

The script will:

1. Deploy a Lagoon vault with Hypercore guard integration
2. Whitelist the target Hypercore vault and CoreWriter contracts
3. Fund the Safe with USDC (deposit amount + activation overhead)
4. Activate the Safe's HyperCore account via ``depositFor``
5. Bridge USDC from HyperEVM to HyperCore spot (phase 1)
6. Wait for EVM escrow to clear
7. Move USDC from spot to perp and deposit into vault (phase 2)
8. Verify the deposit landed on HyperCore
9. Withdraw from the vault (if ``ACTION=both`` or ``ACTION=withdraw``)
10. Print a summary of all transactions and gas costs

Troubleshooting
---------------

If a deposit lands on HyperEVM but the vault position is missing, use the
``check-hypercore-user.py`` diagnostic script to inspect the Safe's HyperCore state:

.. code-block:: shell

    ADDRESS=<safe_address> NETWORK=mainnet \
        poetry run python scripts/hyperliquid/check-hypercore-user.py

This shows the Safe's spot balances, EVM escrows, perp account and vault positions
on HyperCore, helping diagnose where the USDC ended up.

Common issues:

**USDC stuck in EVM escrow**
    The bridge step succeeded but HyperCore has not yet processed the deposit.
    Wait and re-check — escrow typically clears within 2–10 seconds.

**USDC in spot but no vault position**
    ``transferUsdClass`` or ``vaultTransfer`` failed silently on HyperCore.
    This usually means the CoreWriter actions landed before the bridge cleared.
    Re-submit phase 2.

**USDC in perp but no vault position**
    ``vaultTransfer`` failed — possibly due to vault lock-up, wrong vault address,
    or deposit amount below the 5 USDC minimum.

**"exceeds block gas limit" during deployment**
    The big blocks toggle did not take effect. The script handles this automatically
    by always enabling big blocks before each contract deployment.

Configuring Hypercore vault whitelisting
----------------------------------------

When deploying a Lagoon vault with Hypercore support, pass the target vault
addresses via the ``hypercore_vaults`` parameter:

.. code-block:: python

    from eth_defi.erc_4626.vault_protocol.lagoon.deployment import (
        LagoonConfig,
        LagoonDeploymentParameters,
        deploy_automated_lagoon_vault,
    )

    config = LagoonConfig(
        parameters=LagoonDeploymentParameters(
            underlying=usdc_address,
            name="My Hypercore Strategy",
            symbol="MHCS",
        ),
        asset_manager=deployer.address,
        safe_owners=[deployer.address],
        safe_threshold=1,
        any_asset=False,
        # Whitelist these Hypercore vaults for deposits
        hypercore_vaults=[
            "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303",  # HLP vault (mainnet)
        ],
    )

    deploy_info = deploy_automated_lagoon_vault(
        web3=web3,
        deployer=deployer,
        config=config,
    )

The guard automatically whitelists ``CoreWriter`` and ``CoreDepositWallet``
contracts when ``hypercore_vaults`` is provided.

API documentation
-----------------

- CoreWriter actions: :py:mod:`eth_defi.hyperliquid.core_writer`
- EVM escrow management: :py:mod:`eth_defi.hyperliquid.evm_escrow`
- Hyperliquid API: :py:mod:`eth_defi.hyperliquid.api`
- Session management: :py:mod:`eth_defi.hyperliquid.session`
- Big block helpers: :py:mod:`eth_defi.hyperliquid.block`
- LagoonVault: :py:mod:`eth_defi.erc_4626.vault_protocol.lagoon.vault`
- Vault deployment: :py:func:`eth_defi.erc_4626.vault_protocol.lagoon.deployment.deploy_automated_lagoon_vault`
- Guard contract: `GuardV0Base.sol <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/contracts/guard/src/GuardV0Base.sol>`__

Source code
-----------

.. literalinclude:: ../../../scripts/hyperliquid/deploy-lagoon-hyperliquid-vault.py
   :language: python
