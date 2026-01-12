Morpho API
----------

`Morpho protocol <https://morpho.org/>`__ integration.

Morpho is a decentralised lending protocol that enables peer-to-peer lending
with optimised interest rates. The protocol offers two major versions:

**Morpho V1 (MetaMorpho)**: The original Morpho vault architecture that directly
integrates with Morpho markets. Vaults expose the ``MORPHO()`` function for identification.

**Morpho V2**: A newer adapter-based architecture that enables flexible allocation
across multiple yield sources through smart contract adapters. V2 vaults use
``adaptersLength()`` for identification and support:

- Multi-protocol yield allocation via adapters
- Granular ID & Cap system for risk management
- Performance fees (up to 50%) and management fees (up to 5%)
- Timelocked governance with curator/allocator roles
- Non-custodial exits via ``forceDeallocate``

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/morpho>`__
- `Homepage <https://morpho.org/>`__
- `App <https://app.morpho.org/>`__
- `Documentation <https://docs.morpho.org/>`__
- `Morpho V2 docs <https://docs.morpho.org/learn/concepts/vault-v2/>`__
- `GitHub (V2) <https://github.com/morpho-org/vault-v2>`__
- `Twitter <https://x.com/MorphoLabs>`__
- `DefiLlama <https://defillama.com/protocol/morpho>`__

Audits
~~~~~~

- `Morpho audits <https://docs.morpho.org/security/audits/>`__

Morpho V1 API
~~~~~~~~~~~~~

.. autosummary::
   :toctree: _autosummary_morpho
   :recursive:

   eth_defi.morpho.vault

Morpho V2 API
~~~~~~~~~~~~~

.. autosummary::
   :toctree: _autosummary_morpho_v2
   :recursive:

   eth_defi.erc_4626.vault_protocol.morpho_v2.vault
