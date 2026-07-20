"""NaraUSD+ production deployment metadata."""

import datetime

from eth_typing import HexAddress

#: Ethereum mainnet chain id.
NARA_CHAIN_ID = 1

#: NaraUSD+ is Nara's sole production staking vault on Ethereum.
#:
#: https://etherscan.io/address/0x1aa23CDFC941f6b54251C72012A9Bfa4bF5394D6
NARAUSD_PLUS_VAULT = HexAddress("0x1aa23cdfc941f6b54251c72012a9bfa4bf5394d6")

#: First Ethereum block containing NaraUSD+ runtime bytecode.
NARAUSD_PLUS_FIRST_SEEN_AT_BLOCK = 24_983_816

#: Timestamp of :data:`NARAUSD_PLUS_FIRST_SEEN_AT_BLOCK`, stored as naive UTC.
NARAUSD_PLUS_FIRST_SEEN_AT = datetime.datetime(2026, 4, 29, 6, 1, 47, tzinfo=datetime.UTC).replace(tzinfo=None)

#: Hardcoded discovery lead for Nara's only production vault.
NARAUSD_PLUS_HARDCODED_LEADS = (
    (
        NARA_CHAIN_ID,
        NARAUSD_PLUS_VAULT,
        NARAUSD_PLUS_FIRST_SEEN_AT_BLOCK,
        NARAUSD_PLUS_FIRST_SEEN_AT,
    ),
)
