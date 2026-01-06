Silo Finance API
----------------

`Silo Finance <https://silo.finance/>`__ integration.

The Silo Protocol is a non-custodial lending primitive that creates programmable risk-isolated markets
known as silos. Any user with a wallet can lend or borrow in a silo in a non-custodial manner. Silo
markets use the peer-to-pool, overcollateralised model, where the value of a borrower's collateral
always exceeds the value of their loan.

Unlike traditional lending protocols where all assets share risk in a common pool, Silo creates
separate, isolated lending pools for each supported asset. Each Silo is composed of the unique
asset for which it was created and bridge assets (ETH, USDC). Silo V2 supports ERC-4626 integration
for seamless compatibility with third-party DeFi applications.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/silo-finance>`__
- `Homepage <https://silo.finance/>`__
- `App <https://app.silo.finance/>`__
- `Documentation <https://docs.silo.finance/>`__
- `GitHub <https://github.com/silo-finance>`__
- `Twitter <https://x.com/SiloFinance>`__
- `DefiLlama <https://defillama.com/protocol/silo-finance>`__

.. autosummary::
   :toctree: _autosummary_d2
   :recursive:

   eth_defi.erc_4626.vault_protocol.silo.vault
