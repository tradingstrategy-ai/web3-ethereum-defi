"""Hardcoded Asseto IP Vault deployment on Soneium.

Identity comes from https://ipusd.gitbook.io/ipusd-docs/technical-reference/contracts-roles-and-disclosures.
Creation evidence: https://soneium.blockscout.com/tx/0xb55e179e522ed5542e8180a5cee082b1a08d708785decb698ffa1b13ba536456.
The product remains under verification; these constants do not certify its
governance, valuation or transaction flows.
"""

import datetime

from eth_typing import HexAddress

#: Soneium mainnet only; other chains must not inherit this classification.
ROYALTY_CHAIN_ID = 1868
#: IP Vault proxy and sIPUSD share token. Never classify the implementation as a vault.
ROYALTY_VAULT_ADDRESS = HexAddress("0x2ba3fc5bdbbe7be2630fb71e759739625246bcd9")
#: ipUSD denomination token, distinct from the vault's investment shares.
ROYALTY_ASSET_ADDRESS = HexAddress("0x9db3ddb02159e57b5078317a2391bc3c94ef5cd0")
#: Verified proxy creation, used without fabricating deposit events.
ROYALTY_DEPLOYMENT_BLOCK = 29_828_010
ROYALTY_DEPLOYED_AT = datetime.datetime(2026, 10, 23, 21, 26, 11)  # noqa: DTZ001
ROYALTY_HARDCODED_LEADS = ((ROYALTY_CHAIN_ID, ROYALTY_VAULT_ADDRESS, ROYALTY_DEPLOYMENT_BLOCK, ROYALTY_DEPLOYED_AT),)
