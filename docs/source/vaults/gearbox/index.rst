Gearbox API
-----------

`Gearbox Protocol <https://gearbox.finance/>`__ integration.

Gearbox Protocol is a composable leverage protocol that allows users to take
leverage in one place and use it across various DeFi protocols. The protocol
has two sides: passive liquidity providers who earn yield by depositing assets,
and active traders/farmers who can borrow assets for leveraged positions.

The `PoolV3` contracts are ERC-4626 compatible lending pools that manage liquidity
from passive lenders. When users deposit stablecoins or other assets, they receive
pool shares (dTokens) representing their deposit. The underlying deposits are
lent to credit accounts that pay interest on borrowed funds.

Key features:

- Composable leverage: Use borrowed funds across integrated DeFi protocols
- Passive yield: Earn interest from leveraged borrowers
- ERC-4626 compatible: Standard vault interface for easy integration
- Multiple audits: ChainSecurity audits for V3 core contracts

Fee structure:

- Withdrawal fee: 0% for passive lenders
- APY spread: ~50% between borrower rate and lender rate goes to protocol
- For passive lenders, fees are internalised in the share price

Example vault contracts:

- `Hyperithm USDT0 Pool on Plasma <https://plasmascan.to/address/0xb74760fd26400030620027dd29d19d74d514700e>`__
- `GHO v3 Pool on Ethereum <https://etherscan.io/address/0x4d56c9cba373ad39df69eb18f076b7348000ae09>`__

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/gearbox>`__
- `Homepage <https://gearbox.finance/>`__
- `App <https://app.gearbox.fi/>`__
- `Documentation <https://docs.gearbox.finance/>`__
- `Protocol fees <https://docs.gearbox.finance/overview/protocol-fees>`__
- `GitHub <https://github.com/Gearbox-protocol/core-v3>`__
- `Twitter <https://x.com/GearboxProtocol>`__
- `Audits <https://docs.gearbox.finance/risk-and-security/audits-bug-bounty>`__


.. autosummary::
   :toctree: _autosummary_gearbox
   :recursive:

   eth_defi.erc_4626.vault_protocol.gearbox.vault
