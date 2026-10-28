"""Reviewed Ethereum Frankencoin Shares deployment.

The official deployment catalogue separates FCS equity shares from svZCHF
savings: https://github.com/Frankencoin-ZCHF/Frankencoin/blob/main/exports/address.config.ts
"""

import datetime

from eth_typing import HexAddress

#: Ethereum mainnet, the reviewed FCS deployment chain.
FRANKENCOIN_SHARES_CHAIN_ID = 1

#: ERC-4626 equity wrapper and FCS share token.
FRANKENCOIN_SHARES_ADDRESS = HexAddress("0xdb861830d9ae2d1fcf99fa0cfd3973de382b0b5b")

#: Underlying Equity contract issuing FPS and holding the reserve.
FRANKENCOIN_EQUITY_ADDRESS = HexAddress("0x1ba26788dfde592fec8bcb0eaff472a42be341b2")

#: ZCHF, the denomination token used by the FCS ERC-4626 interface.
FRANKENCOIN_ZCHF_ADDRESS = HexAddress("0xb58e61c3098d85632df34eecfb899a1ed80921cb")

#: Deployment receipt: https://etherscan.io/tx/0x5b7df19db201535b1a10f3e30a9d343cf2f78fcc645d059b6f5f3b84aeee70ec
FRANKENCOIN_SHARES_DEPLOYMENT_BLOCK = 25_852_506

#: Naive UTC timestamp of the verified deployment block.
FRANKENCOIN_SHARES_DEPLOYMENT_TIME = datetime.datetime(2026, 8, 28, 8, 21, 11)  # noqa: DTZ001 - repository timestamps are naive UTC.
