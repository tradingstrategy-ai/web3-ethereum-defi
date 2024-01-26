.. meta::
   :title: Programmatically deploy Enzyme vault
   :description: Tutorial for deploying Enzyme vaults in Python

.. _enzyme-deploy:

Enzyme vault deployment
=======================

- This example deploys an Enzyme vault with custom policies and adapters.
  This is a different what you would be able eto deploy through Enzyme user interface.

- The adapter is configured to use the `GuardedGenericAdapter <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/contracts/in-house/src/GuardedGenericAdapter.sol>`__ for trading from eth_defi package, allowing pass through any trades satisfying the `GuardV0 rules <https://github.com/tradingstrategy-ai/web3-ethereum-defi/tree/master/contracts/guard>`__.

- Because we want multiple deployed smart contracts to be verified on Etherscan,
  this deployed uses a Forge-based toolchain and thus the script
  can be only run from the git checkout where submodules are included.

- The custom deposit and terms of service contracts are bound to the vault.

- Reads input from environment variables, so this can be used with scripting.

- The script can launch Anvil to simulate the deployment

The following Enzyme policies and activated to enable trading only via the generic adapter:

- `cumulative_slippage_tolerance_policy` (10% week)

- `allowed_adapters_policy` (only generic adapter)

- `only_remove_dust_external_position_policy`

- `only_untrack_dust_or_priceless_assets_policy`

- `allowed_external_position_types_policy`

Guard configuration:

- Guard ownership is *not* transferred from the deployer
  to the owner at the end of the script, as you likely need to configure


Example how to run this script to deploy a vault on Polygon:

.. code-block:: shell

    export SIMULATE=true
    export FUND_NAME="TradingStrategy.ai ETH Breakpoint I"
    export FUND_SYMBOL=TS1
    export TERMS_OF_SERVICE=0xDCD7C644a6AA72eb2f86781175b18ADc30Aa4f4d
    export ASSET_MANAGER_ADDRESS=0xe747721f8C79A98d7A8dcE0dbd9f26B99E188137
    export OWNER_ADDRESS=0x238B0435F69355e623d99363d58F7ba49C408491
    # Whitelisted tokens for Polygon: WETH, WMATIC
    export WHITELISTED_TOKENS=0x7ceb23fd6bc0add59e62ac25578270cff1b9f619 0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270
    export PRIVATE_KEY=
    export JSON_RPC_URL=

    python scripts/enzyme/deploy-vault.py

.. literalinclude:: ../../../scripts/enzyme/deploy-vault.py
   :language: python
