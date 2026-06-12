Aera
----

`Aera <https://www.aera.finance/>`__ integration.

Aera powers onchain vaults with offchain intelligence. Vault owners configure
assets, protocol allowlists, guardians, and hooks so strategy execution can be
optimised offchain while remaining constrained and enforced onchain.

The protocol's V3 documentation describes a common ``BaseVault`` security model
for both single-depositor treasury vaults and multi-depositor yield vaults. This
initial integration identifies known Aera vaults by hardcoded vault addresses;
a generic contract probe can be added later once a stable protocol-specific
signature has been selected.

For currently discovered Ethereum ERC-4626 AeraStrategy wrappers, fee data is
read from the deployed contracts. The strategy wrapper exposes a Yearn
TokenizedStrategy ``performanceFee()`` in basis points. The wrapper's
``vaultAera()`` points to an Aera V2 vault whose ``fee()`` is a per-second
18-decimal fixed-point TVL fee. This TVL fee is annualised and reported as the
vault management fee.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/aera>`__
- `Homepage <https://www.aera.finance/>`__
- `App <https://app.aera.finance/>`__
- `Documentation <https://docs.aera.finance/>`__
- `GitHub <https://github.com/aera-finance/aera-contracts-public>`__
- `Protocol overview <https://docs.aera.finance/aera-protocol-in-one-page>`__
