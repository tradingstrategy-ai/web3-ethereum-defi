.. meta::
   :description: Tutorial for deploying Lagoon vaults across multiple chains with CCTP V2 bridging

.. _lagoon-multichain:

Lagoon multichain vault deployment
===================================

This tutorial deploys a `Lagoon <https://tradingstrategy.ai/glossary/lagoon>`__
`vault <https://tradingstrategy.ai/glossary/vault>`__ across multiple EVM chains,
sharing the same deterministic `Gnosis Safe <https://safe.global/>`__ address
on every chain. The source vault on Arbitrum serves as the single deposit/redeem
entry point, while satellite chains hold only a Safe and
`TradingStrategyModuleV0 <https://github.com/tradingstrategy-ai/web3-ethereum-defi/tree/master/contracts/safe-integration>`__
guard for executing whitelisted trades. After deployment, the script bridges
`USDC <https://tradingstrategy.ai/glossary/usdc>`__ to each satellite chain
via Circle's `CCTP V2 <https://www.circle.com/cross-chain-transfer-protocol>`__
protocol to verify cross-chain connectivity.

Architecture overview
---------------------

The multichain vault follows a hub-and-spoke model. The source chain
(Arbitrum) runs the full Lagoon protocol — vault contract, Safe, and guard.
Satellite chains run only a Safe and guard, receiving USDC via CCTP V2 burns
and mints.

::

    Arbitrum (SOURCE — full Lagoon protocol)
    ┌───────────────────────────────────────────────┐
    │  Lagoon Vault (ERC-7540)                      │
    │  └── Safe 0xABC... (deterministic CREATE2)    │
    │      └── TradingStrategyModuleV0               │
    │          └── GuardV0 (ERC-4626 vaults)        │
    │  └── CCTP TokenMessenger (burn USDC)          │
    └────────────┬──────────┬──────────┬────────────┘
                 │          │          │
              CCTP V2    CCTP V2    CCTP V2
              burn →     burn →     burn →
              attest     attest     attest
              mint ↓     mint ↓     mint ↓
                 │          │          │
    ┌────────────▼──┐ ┌─────▼───────┐ ┌▼─────────────┐
    │  Ethereum     │ │  Base       │ │  HyperEVM    │
    │  SATELLITE    │ │  SATELLITE  │ │  SATELLITE   │
    │               │ │             │ │              │
    │  Safe 0xABC.. │ │  Safe ..    │ │  Safe ..     │  ... Monad
    │  Guard:       │ │  Guard:     │ │  Guard:      │
    │  - ERC-4626   │ │  - ERC-4626 │ │  - ERC-4626  │
    │  - CowSwap    │ │             │ │  - Hypercore │
    └───────────────┘ └─────────────┘ └──────────────┘

All Safes share the same Ethereum address, computed from the same CREATE2
salt nonce during deployment. The asset manager's hot wallet can execute
trades on any chain through the chain-specific guard, which enforces
per-chain whitelisting rules.

Key concepts
------------

**Deterministic Safe addresses**
    A CREATE2 deployment with a fixed salt nonce produces the same Safe
    address on every EVM chain. This simplifies bookkeeping — the same
    address holds assets everywhere.

**Source vs satellite chains**
    The source chain (first in the ``CHAINS`` list) deploys the full
    `Lagoon protocol <https://lagoon.finance/>`__ — vault contract, Safe,
    and guard. Users deposit and redeem here. Satellite chains deploy only
    a Safe and guard for executing trades with bridged capital.

**CCTP V2 bridging**
    `Circle's Cross-Chain Transfer Protocol <https://www.circle.com/cross-chain-transfer-protocol>`__
    enables trustless USDC transfers between chains. The source vault burns
    USDC on Arbitrum, Circle's attestation service signs the burn message,
    and a receiver mints fresh USDC on the destination chain. The script
    bridges to all destinations in parallel.

**Per-chain whitelisting**
    Each chain's guard is configured with chain-specific whitelist rules.
    A chain may whitelist ERC-4626 vaults, CowSwap, Hypercore, or CCTP
    depending on what protocols are available on that chain.

Supported chains
----------------

**Mainnet** (5 chains):

.. list-table::
   :header-rows: 1
   :widths: 15 15 70

   * - Chain
     - Role
     - Whitelisted protocols
   * - Arbitrum
     - Source
     - ERC-4626 vaults (Silo, Euler, USDai), CCTP V2
   * - Ethereum
     - Satellite
     - ERC-4626 vaults (Centrifuge, Euler, USDD), CowSwap, CCTP V2
   * - Base
     - Satellite
     - ERC-4626 vaults (Morpho, Avantis), CCTP V2
   * - HyperEVM
     - Satellite
     - ERC-4626 vaults (Morpho Felix), Hypercore vaults (HLP)
   * - Monad
     - Satellite
     - ERC-4626 vaults (Accountable, Gearbox, Curvance)

**Testnet** (2 chains):

.. list-table::
   :header-rows: 1
   :widths: 20 15 65

   * - Chain
     - Role
     - Whitelisted protocols
   * - Arbitrum Sepolia
     - Source
     - Uniswap V3, CCTP V2
   * - Base Sepolia
     - Satellite
     - Uniswap V3, CCTP V2

Prerequisites
-------------

You need:

- A deployer wallet with native gas tokens on all target chains
  (ETH on Arbitrum/Ethereum/Base, HYPE on HyperEVM, MON on Monad)
- At least 2 USDC on the source chain for vault funding and bridge testing
- RPC URLs for all chains (archive nodes recommended)
- Refer to the script source below for the full list of environment variables

Running the script
------------------

Simulate on Anvil forks (no real funds needed):

.. code-block:: shell

    SIMULATE=true \
    JSON_RPC_ARBITRUM="https://..." \
    JSON_RPC_ETHEREUM="https://..." \
    JSON_RPC_BASE="https://..." \
    JSON_RPC_HYPERLIQUID="https://..." \
    JSON_RPC_MONAD="https://..." \
    poetry run python scripts/lagoon/deploy-lagoon-multichain.py

Mainnet deployment (real funds):

.. code-block:: shell

    export LAGOON_MULTCHAIN_TEST_PRIVATE_KEY="0x..."
    export JSON_RPC_ARBITRUM="https://..."
    export JSON_RPC_ETHEREUM="https://..."
    export JSON_RPC_BASE="https://..."
    export JSON_RPC_HYPERLIQUID="https://..."
    export JSON_RPC_MONAD="https://..."

    poetry run python scripts/lagoon/deploy-lagoon-multichain.py

Deploy on a subset of chains only:

.. code-block:: shell

    CHAINS=arbitrum,base \
    SIMULATE=true \
    JSON_RPC_ARBITRUM="https://..." \
    JSON_RPC_BASE="https://..." \
    poetry run python scripts/lagoon/deploy-lagoon-multichain.py

Script workflow
---------------

The script follows these steps:

1. **Set up chain connections** — creates Anvil forks (simulate mode) or connects to live RPCs
2. **Create deployer wallet** — generates a random account (simulate) or loads from ``LAGOON_MULTCHAIN_TEST_PRIVATE_KEY``
3. **Check deployer balances** — verifies gas token balance on all chains (live mode only)
4. **Build per-chain configurations** — resolves ERC-4626 vaults, configures CCTP, CowSwap, and Hypercore per chain
5. **Deploy across all chains in parallel** — deploys Safe, guard, and vault (source) or Safe + guard (satellites)
6. **Print deployment summary** — shows vault, Safe, and module addresses per chain
7. **Fund source vault** — deposits USDC into the Arbitrum vault via the ERC-7540 flow
8. **Bridge USDC** — burns USDC on source, waits for Circle attestation, mints on each destination
9. **Swap on satellites** — (testnet only) swaps bridged USDC to WETH via Uniswap V3
10. **Print vault status** — shows USDC balances and whitelisted items across all chains

Testnet deployment
------------------

Testnet mode (``NETWORK=testnet``) uses Arbitrum Sepolia and Base Sepolia.
Key differences from mainnet:

- No factory contracts on testnets — deploys Lagoon protocol from scratch using Forge
- No ERC-4626 vault whitelisting — only CCTP and Uniswap V3
- After bridging, swaps USDC to WETH on satellite chains to prove the guard works
- Testnet simulation (``NETWORK=testnet SIMULATE=true``) is **not supported**
  because Lagoon factory contracts are not deployed on Sepolia chains

For testnet funding:

- `thirdweb faucet <https://thirdweb.com/base-sepolia-testnet>`__ for Base Sepolia ETH
- `LearnWeb3 faucet <https://learnweb3.io/faucets/arbitrum_sepolia/>`__ for Arbitrum Sepolia ETH
- `Circle faucet <https://faucet.circle.com/>`__ for testnet USDC

API documentation
-----------------

- Multichain deployment: :py:func:`eth_defi.erc_4626.vault_protocol.lagoon.deployment.deploy_multichain_lagoon_vault`
- Deployment parameters: :py:class:`eth_defi.erc_4626.vault_protocol.lagoon.deployment.LagoonDeploymentParameters`
- Per-chain configuration: :py:class:`eth_defi.erc_4626.vault_protocol.lagoon.deployment.LagoonConfig`
- Deployment result: :py:class:`eth_defi.erc_4626.vault_protocol.lagoon.deployment.LagoonMultichainDeployment`
- CCTP bridging: :py:func:`eth_defi.cctp.bridge.bridge_usdc_cctp_parallel`
- CCTP bridge destination: :py:class:`eth_defi.cctp.bridge.CCTPBridgeDestination`
- CCTP chain constants: :py:mod:`eth_defi.cctp.constants`
- CCTP whitelist configuration: :py:class:`eth_defi.cctp.whitelist.CCTPDeployment`
- Vault deployment: :py:func:`eth_defi.erc_4626.vault_protocol.lagoon.deployment.deploy_automated_lagoon_vault`
- Guard contract: `GuardV0Base.sol <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/contracts/guard/src/GuardV0Base.sol>`__

Source code
-----------

.. literalinclude:: ../../../scripts/lagoon/deploy-lagoon-multichain.py
   :language: python
