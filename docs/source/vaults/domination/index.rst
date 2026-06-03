Domination Finance API
----------------------

`Domination Finance <https://domination.finance/>`__ vault integration.

Domination Finance is a perpetual futures exchange for dominance indices: the
relative market share of major crypto assets such as BTCDOM, ETHDOM, USDTDOM,
BNBDOM and SOLDOM. Traders can use leverage on these proprietary
oracle-backed markets.

The dfUSDC vault is an ERC-4626 vault on Base where users deposit native USDC
and receive dfUSDC shares. Vault capital acts as counterparty liquidity to
leveraged trading activity on the exchange.

Domination Finance vaults are implemented as a Gains/Ostium-family vault
adapter in this package. The known dfUSDC vault address is hardcoded for
protocol detection, because the onchain feature surface is shared with other
Gains-like deployments.

Key features:

- ERC-4626 standard vault on Base
- Native USDC underlying asset
- Real-yield model driven by exchange trading fees and counterparty PnL
- No protocol management fee or performance fee for dfUSDC depositors

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/domination-finance>`__
- `Homepage <https://domination.finance/>`__
- `Vault app <https://app.domination.finance/vault>`__
- `Documentation <https://docs.domination.finance/>`__
- `Developer addresses <https://docs.domination.finance/docs/developers/addresses/>`__
- `Audits <https://docs.domination.finance/docs/security/audits/>`__
- `Twitter <https://x.com/dominationfi>`__
- `dfUSDC vault on Basescan <https://basescan.org/address/0xA194082Aabb75Dd1Ca9Dc1BA573A5528BeB8c2Fb>`__

For the shared Gains-family implementation, see
:py:class:`~eth_defi.erc_4626.vault_protocol.gains.vault.DominationFinanceVault`.

.. autosummary::
   :toctree: _autosummary_domination
   :recursive:

   eth_defi.erc_4626.vault_protocol.gains.vault.DominationFinanceVault
