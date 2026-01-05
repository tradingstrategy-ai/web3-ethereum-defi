NashPoint API
-------------

`NashPoint <https://www.nashpoint.fi/>`__ integration.

NashPoint is a smart contract protocol that enables asset managers (Owners) to independently create
and manage diversified investment products onchain. Each investment product, or Node, encapsulates
a customised strategy defined by selecting underlying assets (components), setting management fees,
determining reserve ratios, establishing rebalancing frequency, and specifying acceptable tracking
error. Users deposit funds using the standardised ERC-4626 interface and withdraw asynchronously
via ERC-7540, with all assets securely held within Node contracts.

By bridging traditional Real-World Assets (RWAs) and crypto-native DeFi protocols, NashPoint offers
comprehensive, diversified investment portfolios accessible through a unified interface. Nodes
integrate effortlessly with prominent protocols such as Aave, Morpho, Ethena, Centrifuge, and Ondo.
As ERC-20 compliant tokens, Nodes can be freely transferred, traded, or used as collateral across
the broader DeFi ecosystem.

NashPoint vaults are known as *Node*.

Links
~~~~~

- `Homepage <https://www.nashpoint.fi/>`__
- `Documentation <https://nashpoint.gitbook.io/nashpoint>`__
- `Twitter <https://x.com/NashPointFi>`__

.. autosummary::
   :toctree: _autosummary_d2
   :recursive:

   eth_defi.erc_4626.vault_protocol.nashpoint.vault
