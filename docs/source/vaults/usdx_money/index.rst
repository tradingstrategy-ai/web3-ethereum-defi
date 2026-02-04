USDX Money API
--------------

`USDX Money <https://usdx.money/>`__ integration.

USDX Money is a synthetic USD stablecoin protocol that provides stability without
relying on traditional banking infrastructure. USDX is backed by delta-neutral
positions across multiple exchanges, seamlessly bridging DeFi, CeFi, and TradFi.

sUSDX (Staked USDX) is a reward-bearing token where users stake USDX to receive
a proportionate share of protocol-generated yield. The value of sUSDX appreciates
over time rather than its quantity increasing (similar to cbETH or rETH for ETH).
The staked USDX is not rehypothecated or used independently to generate sUSDX value
accrual.

Key features:

- Reward-bearing staking token (value appreciation, not quantity increase)
- No explicit management or performance fees at the smart contract level
- Yield generated from protocol activities
- Deployed on multiple chains with the same contract address
- 8-hour vesting period for reward distributions
- Configurable cooldown mechanism for unstaking (up to 90 days)

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/usdx-money>`__
- `Homepage <https://usdx.money/>`__
- `Documentation <https://docs.usdx.money/>`__
- `GitHub <https://github.com/X-Financial-Technologies/usdx>`__
- `Twitter <https://x.com/StablesLabs>`__
- `DefiLlama <https://defillama.com/protocol/stables-labs-usdx>`__
- `Contract on BSC <https://bscscan.com/address/0x7788a3538c5fc7f9c7c8a74eac4c898fc8d87d92>`__
- `Contract on Ethereum <https://etherscan.io/address/0x7788a3538c5fc7f9c7c8a74eac4c898fc8d87d92>`__


.. autosummary::
   :toctree: _autosummary_usdx_money
   :recursive:

   eth_defi.erc_4626.vault_protocol.usdx_money.vault
