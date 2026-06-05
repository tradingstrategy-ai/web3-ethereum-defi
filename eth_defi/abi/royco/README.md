Royco tranche ABIs
==================

These ABIs were downloaded from verified Ethereum contracts:

- Junior tranche example: https://etherscan.io/address/0x059bc7aa5000a26aae2601cfbf060653adf8fd91
- Senior tranche example: https://etherscan.io/address/0x1ba515a409dd702105415cdaae439059aa0b402a

``RoycoSeniorTranche.json`` and ``RoycoJuniorTranche.json`` expose the same
runtime ABI surface used by the vault reader. The only known difference in the
stored ABI JSON is the constructor parameter name, ``_stParams`` versus
``_jtParams``.

The reader loads ``RoycoSeniorTranche.json`` as the canonical runtime ABI for
both senior and junior tranche vaults. ``RoycoJuniorTranche.json`` is kept for
provenance and for future diffing against verified junior tranche deployments.
