USDD API
--------

`Decentralized USD (USDD) <https://usdd.io/>`__ integration.

USDD is a decentralised stablecoin protocol that provides the USDD stablecoin
and allows users to earn yield through staking USDD in sUSDD savings vaults.

The sUSDD vault is an ERC-4626 compliant tokenised vault that allows users to
stake USDD and earn yield. USDD is deployed on multiple chains including
Ethereum and BNB Chain.

Key features:

- No deposit/withdrawal fees at the smart contract level
- Instant deposits and withdrawals
- Cross-chain deployment on Ethereum and BNB Chain

- `Homepage <https://usdd.io/>`__
- `Documentation <https://docs.usdd.io/>`__
- `Twitter <https://x.com/usaborning>`__
- `sUSDD Contract on Ethereum <https://etherscan.io/address/0xC5d6A7B61d18AfA11435a889557b068BB9f29930>`__
- `sUSDD Contract on BNB Chain <https://bscscan.com/address/0x8bA9dA757d1D66c58b1ae7e2ED6c04087348A82d>`__


.. autosummary::
   :toctree: _autosummary_usdd
   :recursive:

   eth_defi.erc_4626.vault_protocol.usdd.vault
