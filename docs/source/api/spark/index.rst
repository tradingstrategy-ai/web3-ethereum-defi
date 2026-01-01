Spark API
---------

`Spark <https://spark.fi/>`__ integration.

Spark is a decentralised non-custodial liquidity protocol built on top of the MakerDAO/Sky
infrastructure. It allows users to participate as suppliers or borrowers, and provides
yield-generating stablecoin products through the Sky Savings Rate (SSR).

The sUSDC vault is an ERC-4626 compliant tokenised vault that allows users to deposit USDC
and earn the Sky Savings Rate. The vault handles USDC deposits by converting USDC to USDS
using the dss-lite-psm (Peg Stability Module) and then depositing into sUSDS to earn yield.

Key features:

- No deposit/withdrawal fees at the smart contract level
- Yield accrues through the Sky Savings Rate (SSR)
- Instant deposits and withdrawals (subject to PSM liquidity)
- Fully backed by sUSDS (savings USDS)

- `Homepage <https://spark.fi/>`__
- `Savings page <https://app.spark.fi/savings/mainnet/spusdc>`__
- `Documentation <https://docs.spark.fi/>`__
- `GitHub <https://github.com/sparkdotfi/spark-vaults>`__
- `Twitter <https://x.com/sparkdotfi>`__
- `Contract on Etherscan <https://etherscan.io/address/0xbc65ad17c5c0a2a4d159fa5a503f4992c7b545fe>`__


.. autosummary::
   :toctree: _autosummary_spark
   :recursive:

   eth_defi.spark.vault
