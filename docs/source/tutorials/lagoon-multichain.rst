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
    CREATE2 produces the same Safe address when the factory, implementation,
    initial owner configuration and salt nonce match across chains. The same
    address can then hold the vault's assets on each participating chain.

**Source vs satellite chains**
    The source chain (first in the ``CHAINS`` list) deploys the full
    `Lagoon protocol <https://lagoon.finance/>`__ — vault contract, Safe,
    and guard. Users deposit and redeem here. Satellite chains deploy only
    a Safe and guard for executing trades with bridged capital.

**CCTP V2 bridging**
    `Circle's Cross-Chain Transfer Protocol <https://www.circle.com/cross-chain-transfer-protocol>`__
    transfers native USDC between chains using Circle's signed attestations. The source vault burns
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
- At least 2 native USDC on the source chain for vault funding and bridge testing
- At least 1 native USDC in the deployer wallet on every satellite chain
- RPC URLs for all selected chains
- Refer to the script source below for the full list of environment variables

Running the script
------------------

Load the selected RPCs and deployment key from your private environment first.
Do not paste a private key into shell history. The examples use routine defaults.

Simulate on Anvil forks (no real funds needed):

.. code-block:: shell

    SIMULATE=true poetry run python scripts/lagoon/deploy-lagoon-multichain.py

Mainnet deployment (real funds):

.. code-block:: shell

    poetry run python scripts/lagoon/deploy-lagoon-multichain.py

Deploy on a subset of chains only:

.. code-block:: shell

    CHAINS=arbitrum,base SIMULATE=true poetry run python scripts/lagoon/deploy-lagoon-multichain.py

Script workflow
---------------

The script follows these steps:

1. **Set up chain connections** — creates Anvil forks (simulate mode) or connects to live RPCs
2. **Create deployer wallet** — generates a random account (simulate) or loads from ``LAGOON_MULTCHAIN_TEST_PRIVATE_KEY``
3. **Check deployer balances** — verifies native gas on all chains, the hub subscription and satellite USDC reserves (live mode only)
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

Manual Lighter satellite deployment
-----------------------------------

``scripts/lagoon/deploy-lagoon-lighter-multichain.py`` extends this deployment
pattern with an Ethereum satellite Safe for Lighter Core. It defaults to a
HyperEVM hub with Hypercore HLP and Felix USDC ERC-4626 permissions. All
contracts are deployed before activation. The deployer remains the sole owner
of each test Safe.

Load ``LAGOON_MULTCHAIN_TEST_PRIVATE_KEY`` and the selected RPC variables from
your private environment. Prefund the deployer with **1 native USDC on Ethereum**
and native gas on all selected chains. This deployment-only script requires no
USDC on the hub or other satellites. Use :ref:`lifi-feed-crosschain` and its
optional ``ETHEREUM_USDC_AMOUNT`` transfer to prepare the Ethereum wallet.

.. code-block:: shell

    PREFLIGHT_ONLY=true poetry run python scripts/lagoon/deploy-lagoon-lighter-multichain.py
    poetry run python scripts/lagoon/deploy-lagoon-lighter-multichain.py

The second command sends real mainnet transactions. After all contracts exist,
it transfers exactly 1 USDC from the deployer's Ethereum wallet to the satellite
Safe, deposits through the Lighter guard, then registers and verifies the API key.
There is no hub subscription, CCTP fee setting, burn, attestation or relay during
activation. CCTP permissions remain configured for subsequent strategy transfers.

The activation amount is a deployer contribution, with no Lagoon shares issued.
Its resulting collateral must be included once in initial multichain NAV before
settling future subscriptions. The protected report distinguishes the contribution
from subscription capital and records its funding transaction.

The mode-0600 JSONL report also contains the generated Lighter API private key,
saved before registration, and progress for manual recovery. Re-running starts
a fresh deployment; reconcile any incomplete operation first. This checks
deployment and activation only. Pool investment, withdrawal and multichain NAV
conservation require a separate lifecycle test. See the script's module
docstring for all environment variables and recovery limitations.

.. _lagoon-multichain-core-roundtrip:

Manual Core investment and delayed unwind
-----------------------------------------

``scripts/lagoon/lagoon-multichain-core-roundtrip.py`` deploys the main Lagoon
vault on Arbitrum and satellite Safes on Ethereum and HyperEVM. It subscribes
accounted capital, bridges native USDC, and invests in one open Hypercore user
vault and one Lighter public pool. The deployer is the sole Safe owner and
manager for this manual test.

Load ``JSON_RPC_ARBITRUM``, ``JSON_RPC_ETHEREUM``, ``JSON_RPC_HYPERLIQUID`` and,
for live transactions, ``LAGOON_MULTCHAIN_TEST_PRIVATE_KEY`` from a private
environment. Install the optional Lighter SDK using the pinned instructions in
``scripts/lagoon/lagoon-lighter-trade-example.py``. Live deployment needs ETH on
Arbitrum/Ethereum, HYPE on HyperEVM, at least 1 native USDC on each satellite,
and the following Arbitrum USDC budget:

=============================== ===========
Allocation                      USDC
=============================== ===========
Hypercore position + activation 12.00
Lighter position + activation   11.00
Outbound bridge fee ceilings    0.02
Initial funding floor           23.02
Default subscription            25.00
=============================== ===========

The deployer must be prefunded on **every chain before starting**. At the defaults,
this means 27 USDC total: 25 on Arbitrum, 1 on Ethereum and 1 on HyperEVM.
Satellite reserves remain in the deployer wallet and are excluded from vault NAV,
subscription proceeds and final redemption. Investment capital still reaches the
Safes through the accounted hub subscription and CCTP transfers.

These amounts assume the deployer is already an activated Hypercore user.
HyperEVM gas funding alone does not activate that account, which is required
to enable large blocks for contract deployment. The tutorial checks this before
deploying any chain. Check with
:py:func:`eth_defi.hyperliquid.evm_escrow.is_account_activated`; the existing
:py:func:`eth_defi.hyperliquid.evm_escrow.activate_account_sponsored` helper can
activate the deployer's own address using an additional 2 HyperEVM USDC.
Keep the 1-USDC reserve afterwards. Alternatively, use the documented
`native HYPE transfer to Core <https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/hyperevm/hypercore-less-than-greater-than-hyperevm-transfers>`_.
This separate deployer activation is outside Lagoon accounting.

Use the :ref:`gas distribution script <lifi-feed-crosschain>` before deployment.
Its optional ``ETHEREUM_USDC_AMOUNT=5`` adds an Ethereum USDC transfer;
``DRY_RUN=true`` shows quotes without spending. Prefund HyperEVM USDC separately.
Check that the remaining Arbitrum balance still covers the subscription after
funding transfers and fees. Non-zero gas checks do not guarantee enough gas for
deployment and tomorrow's unwind; keep a buffer on each chain.

Return fees come from recovered funds. Activation deposits include recoverable
collateral; they are not entirely spent fees. A 5-USDC allocation per protocol
can use a 15-USDC subscription plus the two 1-USDC satellite reserves
(17 USDC total), subject to current pool constraints.

.. code-block:: shell

    # Day one: deploy, invest, save state and exit.
    poetry run python scripts/lagoon/lagoon-multichain-core-roundtrip.py

    # Read-only diagnostics, including actual locks and pending operations.
    ACTION=status poetry run python scripts/lagoon/lagoon-multichain-core-roundtrip.py

    # Run manually after unlock; repeat if settlement/liquidity remains pending.
    ACTION=unwind poetry run python scripts/lagoon/lagoon-multichain-core-roundtrip.py

    # Entire local fork workflow with explicit Core mocks and no lock delay.
    SIMULATE=true poetry run python scripts/lagoon/lagoon-multichain-core-roundtrip.py

Hypercore user-vault locks and current withdrawal liquidity determine when exit
is possible. HLP and LLP are excluded from automatic selection. The Lighter
position remains open until manual unwind; redemption, secure withdrawal and
Ethereum claim are separate stages. The script does not wait overnight or
schedule future transactions. Exit code 2 means a known pending delay; code 1
means failure or uncertain execution. Code 0 means the requested command
completed; read the outcome to distinguish ``invested`` from final redemption.

State defaults to
``~/.tradingstrategy/lagoon-multichain-core-roundtrip/mainnet/state.json``.
It stores the original deployment, targets, signed-operation identities and
withdrawal/bridge evidence. The generated Lighter key is backed up in an adjacent
0600 file. ``STATE_PATH`` optionally selects a new run location; deployment never
overwrites a prior run. Status requires no EOA key. Unwind loads current holdings,
reconciles original submissions, returns both satellites' capital and redeems
through Lagoon. A terminal unwind is safe to repeat. Ambiguous Core execution
requires inspection rather than automatically submitting another action.

``USDC_AMOUNT``, ``POSITION_USDC``, ``CCTP_MAX_FEE_USDC``,
``MAX_RESIDUAL_USDC`` and target overrides are optional; see the module docstring
for defaults. The default aggregate residual budget is 0.01 USDC, only for
measured non-recoverable cash after positions/receivables are resolved. Such a
run ends as ``redeemed_with_dust``, with its amount/location disclosed. A zero
residual budget prevents final redemption if any cash remains; it does not
remove the Core return fee margin. After inspecting measured residual cash, an
unwind may explicitly override ``MAX_RESIDUAL_USDC`` up to 1 USDC. Remaining
vault positions and unresolved transfers are never treated as cash dust.

Simulation uses canonical-block Anvil forks and real Lagoon/Safe/guard/CCTP and
Lighter custody calls. Only Core sequencing, API signatures, prices and proof
settlement are mocked. It reloads state between investment and exit, models a
small gain, checks cash conservation and repeats the terminal unwind to check
idempotence. Simulation uses a separate state directory and needs no real key
or funds. Its ephemeral forks cannot be resumed after shutdown. A simulation
pass does not establish live sequencer or funded lifecycle readiness.

================================ ================================================
Observation                      Next action
================================ ================================================
Position locked or pool illiquid  Use status; repeat unwind after the reported delay
CCTP burn awaiting settlement    Reconcile/relay the saved message; do not burn again
Core receipt but no investment   Inspect spot/perp/escrow and original action evidence
Lighter withdrawal unindexed     Continue the saved hash/history match; no new request
Lagoon redemption already queued Continue its saved settlement/claim stage
================================ ================================================

The manual script-writing reference is
``.claude/docs/manual-tutorial-script.md``. The API workflow is documented at
:py:mod:`eth_defi.erc_4626.vault_protocol.lagoon.roundtrip`, and the explicit
simulation boundary at :py:mod:`eth_defi.testing.lagoon_roundtrip`.

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
