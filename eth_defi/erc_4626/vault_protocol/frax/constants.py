"""Frax vault deployment constants and product metadata."""

from dataclasses import dataclass

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


@dataclass(slots=True, frozen=True)
class FraxStakingVaultMetadata:
    """Human-readable metadata for a reviewed Frax staking vault.

    Frax staking contracts use a generic linear-rewards ERC-4626 design, so
    their product identity and lifecycle status cannot be inferred safely from
    the ABI alone. Keep this copy beside the address allowlist that establishes
    their Frax provenance.

    :param short_description:
        One-line product summary for vault listings.
    :param notes:
        Longer Markdown explanation for the vault detail page.
    """

    #: One-line product summary for vault listings.
    short_description: str

    #: Longer Markdown explanation for the vault detail page.
    notes: str


#: Address-specific descriptions for reviewed Ethereum Frax staking vaults.
#:
#: Sources:
#:
#: - https://docs.frax.finance/frax-v3-100-cr-and-more/sfrax
#: - https://docs.frax.com/protocol/assets/frxusd/sfrxusd
#: - https://docs.frax.com/frxusd/stake-and-unstake-overview
FRAX_STAKING_VAULT_METADATA_BY_CHAIN: dict[int, dict[HexAddress, FraxStakingVaultMetadata]] = {
    1: {
        HexAddress("0x03cb4438d015b9646d666316b617a694410c216d"): FraxStakingVaultMetadata(
            short_description="Legacy sFRAX vault that distributed Frax protocol yield to staked FRAX.",
            notes="""**Legacy sFRAX deployment.** This is an earlier Staked Frax ERC-4626 contract. Frax's current [sFRAX documentation](https://docs.frax.finance/frax-v3-100-cr-and-more/sfrax) identifies `0xA663B02CF0a4b149d2aD41910CB81e23e1c41c32` as the canonical sFRAX deployment. Treat this address as a legacy product when comparing active Frax staking vaults.""",
        ),
        HexAddress("0xa663b02cf0a4b149d2ad41910cb81e23e1c41c32"): FraxStakingVaultMetadata(
            short_description="Stake FRAX to receive weekly Frax protocol yield through sFRAX.",
            notes="""sFRAX is a non-rebasing ERC-4626 vault whose redeemable FRAX per share rises as Frax distributes protocol earnings in weekly reward cycles. Frax states that shares remain withdrawable for their pro-rata FRAX at all times. The vault targets the US Federal Reserve IORB benchmark rate, but this target is not a guaranteed return. See the [Frax sFRAX documentation](https://docs.frax.finance/frax-v3-100-cr-and-more/sfrax).""",
        ),
        HexAddress("0xcf62f905562626cfcdd2261162a51fd02fc9c5b6"): FraxStakingVaultMetadata(
            short_description="Stake frxUSD in Frax's benchmark-strategy savings vault to earn automatically compounded yield.",
            notes="""sfrxUSD is a non-rebasing ERC-4626 savings vault whose redeemable frxUSD per share rises as yield accrues. Frax's Benchmark Yield Strategy can allocate across carry trades, DeFi AMOs and Treasury or other real-world-asset sources as market conditions change. Frax documents no lock-up and no staking or unstaking fee. See the [sfrxUSD staking overview](https://docs.frax.com/frxusd/stake-and-unstake-overview).""",
        ),
    },
}
