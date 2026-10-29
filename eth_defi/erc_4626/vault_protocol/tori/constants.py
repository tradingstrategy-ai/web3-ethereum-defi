"""Reviewed Tori Finance deployment identity.

Only Ethereum's native staking vault implements the reviewed ERC-4626 product.
Other chains host bridged ERC-20 representations, documented at
https://docs.tori.finance/resources/contracts.
"""

from eth_typing import HexAddress

#: Ethereum mainnet, the canonical staking chain.
TORI_CHAIN_ID = 1

#: Native strUSD staking vault; distinct from Upshift's etrUSD ecosystem vault.
TORI_VAULT_ADDRESS = HexAddress("0x280839980a7ed0d7717f64125fe241012e5f5815")

#: Tori's trUSD denomination token, not Reservoir's similarly named product.
TORI_ASSET_ADDRESS = HexAddress("0xd0580192e98ea6ceb9c7b6191ed2e27560911697")
