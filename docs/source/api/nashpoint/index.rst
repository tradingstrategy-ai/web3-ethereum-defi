NashPoint API
-------------

NashPoint is a smart contract protocol that enables asset managers (Owners) to independently create and manage diversified investment products onchain. Each investment product, or Node, encapsulates a customized strategy defined by selecting underlying assets (components), setting management fees, determining reserve ratios, establishing rebalancing frequency, and specifying acceptable tracking error. Users easily deposit funds using the standardized ERC-4626 interface and withdraw asynchronously via ERC-7540, with all assets securely held within Node contracts.

By bridging traditional Real-World Assets (RWAs) and crypto-native DeFi protocols, NashPoint offers comprehensive, diversified investment portfolios accessible through a unified interface. Nodes integrate effortlessly with prominent protocols such as Aave, Morpho, Ethena, Centrifuge, and Ondo. As ERC-20 compliant tokens, Nodes can be freely transferred, traded, or used as collateral across the broader DeFi ecosystem.

NashPoint's offchain balance sheet management service handles the execution of investment strategies, ensuring efficient investment of user deposits according to Node parameters and managing liquidity effectively during withdrawals. The protocol's secure, modular, and compliance-focused infrastructure supports sophisticated strategies and is designed to accelerate institutional adoption in decentralized finance.

NashPoint vaults are known as *Node*.


- `Twitter <https://x.com/NashPointFi>`__

.. autosummary::
   :toctree: _autosummary_d2
   :recursive:

   eth_defi.nashpoint.vault
