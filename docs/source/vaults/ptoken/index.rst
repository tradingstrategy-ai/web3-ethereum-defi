pToken
======

Currently not yet identified. The reviewed `BTC (3x Long) <https://robinhoodchain.blockscout.com/address/0x4472C69d299382F8847ebCE4FC6Ed8e295510E3e>`__ and `HOOD (3x Long) <https://robinhoodchain.blockscout.com/address/0xe24CABDf76DD1c2576049167eB1755C84b985C36>`__ pTokens are USDG-denominated asynchronous vaults on Robinhood Chain. The two contracts share a factory, upgradeable beacon and unlabelled manager address. Their issuer, source repository, product terms and public deployment registry have not been identified.

Their deployment transactions contain Arcus-branded product strings. The pTokens use Arcus's `published Paxos USDG deposit proxy <https://github.com/arcus-xyz/rootchain-contracts-abis/blob/main/deployments.json>`__; that shared funding integration does not by itself identify the issuer. The reader consequently provides generic ERC-4626 reads and no deposit/redeem manager or verified management or performance fee. It assigns this protocol only to the two reviewed addresses.

.. autosummary::
   :toctree: _autosummary_ptoken
   :recursive:

   eth_defi.erc_4626.vault_protocol.ptoken.constants
   eth_defi.erc_4626.vault_protocol.ptoken.vault
