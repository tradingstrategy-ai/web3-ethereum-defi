"""Frax vault deployment constants."""

from eth_typing import HexAddress

#: Reviewed FraxlendPairDeployer contracts recovered from ``LogDeploy`` events.
#:
#: ``FraxlendPair.DEPLOYER_ADDRESS()`` must resolve to one of these addresses
#: before a vault is attributed to Frax. This excludes third-party Fraxlend
#: forks while retaining a single ABI probe per candidate vault.
FRAXLEND_DEPLOYERS_BY_CHAIN: dict[int, frozenset[HexAddress]] = {
    1: frozenset(
        {
            HexAddress("0x7ab788d0483551428f2291232477f1818952998c"),
            HexAddress("0xaa913c26dd7723fcae9dbd2036d28171a56c6251"),
            HexAddress("0xeb8816baeb70690660ce6c0eda2b07a21493e664"),
            HexAddress("0xf767a82a188305461b6f01a7706f7bc0ba941fff"),
        }
    ),
    42161: frozenset({HexAddress("0xc70cc721d19dc7e627b81feacb6a357fb11200af")}),
}

#: Reviewed Frax staking vault deployments by chain.
#:
#: These contracts share generic linear-reward vault implementations that are
#: not unique to Frax, so they are deliberately classified by address.
#:
#: - sFRAX documentation: https://docs.frax.finance/frax-v3-100-cr-and-more/sfrax
#: - sfrxUSD deployments: https://docs.frax.com/frxusd/stake-and-unstake-supported-networks
FRAX_STAKING_VAULTS_BY_CHAIN: dict[int, frozenset[HexAddress]] = {
    1: frozenset(
        {
            HexAddress("0x03cb4438d015b9646d666316b617a694410c216d"),
            HexAddress("0xa663b02cf0a4b149d2ad41910cb81e23e1c41c32"),
            HexAddress("0xcf62f905562626cfcdd2261162a51fd02fc9c5b6"),
        }
    ),
}

#: All reviewed Frax staking vault addresses, for legacy address-only lookups.
FRAX_STAKING_VAULT_ADDRESSES = frozenset(address for addresses in FRAX_STAKING_VAULTS_BY_CHAIN.values() for address in addresses)
