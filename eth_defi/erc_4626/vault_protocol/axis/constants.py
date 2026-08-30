"""Reviewed Axis StakedUSDx deployment constants."""

import datetime

from eth_typing import HexAddress

#: Ethereum mainnet chain id.
AXIS_ETHEREUM_CHAIN_ID = 1

#: Plasma mainnet chain id.
AXIS_PLASMA_CHAIN_ID = 9745

#: Axis's reviewed StakedUSDx V1 rewards vault on Plasma.
#:
#: https://plasmaexplorer.com/address/0x13A099765B34b3aAFedb8698CF7fd418E7730012
AXIS_PLASMA_STAKED_USDX_VAULT = HexAddress("0x13a099765b34b3aafedb8698cf7fd418e7730012")

#: Axis's reviewed StakedUSDx V2 rewards vault on Ethereum.
#:
#: https://docs.axis.to/reference/staking-contracts
#: https://etherscan.io/address/0xEB892628D1E58BC475A6dCB7F5dBC4F591632AA4
AXIS_ETHEREUM_STAKED_USDX_VAULT = HexAddress("0xeb892628d1e58bc475a6dcb7f5dbc4f591632aa4")

#: Reviewed V2 implementation currently used by the Ethereum proxy.
#:
#: The proxy is upgradeable, so live integration coverage asserts that this
#: address has not changed before relying on the reviewed ERC-4626 ABI.
#: https://etherscan.io/address/0x1D8191c20c06c5628f1a977bc6D6aFe7dD541cf2#code
AXIS_ETHEREUM_STAKED_USDX_IMPLEMENTATION = HexAddress("0x1d8191c20c06c5628f1a977bc6d6afe7dd541cf2")

#: Reviewed Axis StakedUSDx deployments keyed by EVM chain ID.
AXIS_STAKED_USDX_BY_CHAIN: dict[int, HexAddress] = {
    AXIS_ETHEREUM_CHAIN_ID: AXIS_ETHEREUM_STAKED_USDX_VAULT,
    AXIS_PLASMA_CHAIN_ID: AXIS_PLASMA_STAKED_USDX_VAULT,
}

#: Backwards-compatible Plasma chain alias.
AXIS_CHAIN_ID = AXIS_PLASMA_CHAIN_ID

#: Backwards-compatible Plasma vault alias.
AXIS_STAKED_USDX_VAULT = AXIS_PLASMA_STAKED_USDX_VAULT

#: One-line description used by the vault list.
AXIS_SHORT_DESCRIPTION = "USDx rewards vault whose redemption flow depends on the deployment."

#: Ethereum V2 redemption note shown in vault metadata.
AXIS_ETHEREUM_NOTES = """Axis StakedUSDx V2 accepts USDx and issues sUSDx shares whose USDx value grows as rewards vest.

V2 redemptions are asynchronous: request redemption, wait for the contract's account policy cooldown, then claim after Axis services the request. The default cooldown is configurable and servicing has no contract-enforced maximum delay, so this vault must not be treated as immediately redeemable ERC-4626 liquidity.

- [Axis StakedUSDx documentation](https://docs.axis.to/susdx-the-rewards-vault/susdx)
- [Axis staking and unstaking guide](https://docs.axis.to/susdx-the-rewards-vault/stake-and-unstake)"""

#: Plasma V1 redemption note shown in vault metadata.
AXIS_PLASMA_NOTES = """Axis StakedUSDx V1 accepts USDx and issues sUSDx shares whose USDx value grows as rewards vest.

V1 uses a governance-configurable cooldown rather than ERC-7540. When ``cooldownDuration()`` is zero, direct ERC-4626 redemption is enabled; when it is non-zero, holders use the contract's cooldown and unstake flow. Callers must read the current contract setting before choosing a redemption path.

- [Axis StakedUSDx documentation](https://docs.axis.to/susdx-the-rewards-vault/susdx)
- [Plasma V1 contract](https://plasmaexplorer.com/address/0x13A099765B34b3aAFedb8698CF7fd418E7730012)"""

#: Deployment-specific notes keyed by ``(chain_id, address)``.
AXIS_NOTES_BY_CHAIN: dict[tuple[int, HexAddress], str] = {
    (AXIS_ETHEREUM_CHAIN_ID, AXIS_ETHEREUM_STAKED_USDX_VAULT): AXIS_ETHEREUM_NOTES,
    (AXIS_PLASMA_CHAIN_ID, AXIS_PLASMA_STAKED_USDX_VAULT): AXIS_PLASMA_NOTES,
}

#: Backwards-compatible generic Axis note.
AXIS_NOTES = """Axis StakedUSDx accepts USDx and issues sUSDx shares whose USDx value grows as rewards vest.

Redemption mechanics differ between Ethereum V2 and Plasma V1. Callers must use the deployment-specific contract flow and current cooldown setting.

- [Axis staking contract reference](https://docs.axis.to/reference/staking-contracts)"""

#: First block at which the production scanner observed the Plasma V1 vault.
AXIS_PLASMA_STAKED_USDX_FIRST_SEEN_AT_BLOCK = 18_204_445

#: Timestamp of :data:`AXIS_PLASMA_STAKED_USDX_FIRST_SEEN_AT_BLOCK`, stored as naive UTC.
AXIS_PLASMA_STAKED_USDX_FIRST_SEEN_AT = datetime.datetime(2026, 4, 2, 13, 25, 8)  # noqa: DTZ001 - vault pipeline timestamps are naive UTC

#: Ethereum V2 proxy deployment block reported by Etherscan.
#:
#: Creation transaction:
#: https://etherscan.io/tx/0xdf82a52bbd1c7082c9c0523c25e47b9c4bd02bebcc1110bbb31ddeebe4de9791
AXIS_ETHEREUM_STAKED_USDX_FIRST_SEEN_AT_BLOCK = 25_597_991

#: Ethereum V2 proxy deployment timestamp, stored as naive UTC.
AXIS_ETHEREUM_STAKED_USDX_FIRST_SEEN_AT = datetime.datetime(2026, 7, 23, 21, 3, 23)  # noqa: DTZ001 - vault pipeline timestamps are naive UTC

#: Backwards-compatible Plasma first-seen block alias.
AXIS_STAKED_USDX_FIRST_SEEN_AT_BLOCK = AXIS_PLASMA_STAKED_USDX_FIRST_SEEN_AT_BLOCK

#: Backwards-compatible Plasma first-seen timestamp alias.
AXIS_STAKED_USDX_FIRST_SEEN_AT = AXIS_PLASMA_STAKED_USDX_FIRST_SEEN_AT

#: Hardcoded discovery leads for all reviewed Axis production vaults.
AXIS_HARDCODED_LEADS = (
    (
        AXIS_ETHEREUM_CHAIN_ID,
        AXIS_ETHEREUM_STAKED_USDX_VAULT,
        AXIS_ETHEREUM_STAKED_USDX_FIRST_SEEN_AT_BLOCK,
        AXIS_ETHEREUM_STAKED_USDX_FIRST_SEEN_AT,
    ),
    (
        AXIS_PLASMA_CHAIN_ID,
        AXIS_PLASMA_STAKED_USDX_VAULT,
        AXIS_PLASMA_STAKED_USDX_FIRST_SEEN_AT_BLOCK,
        AXIS_PLASMA_STAKED_USDX_FIRST_SEEN_AT,
    ),
)
