"""Address-scoped public metadata for Bulla Factoring pools.

Bulla's public Banker application exposes pool names and owner addresses through
its Goldsky indexer, but it does not publish a general, unauthenticated API for
curator names or editorial pool descriptions. Its richer custodian and
agreement endpoints require an authenticated participant session, so they must
not be queried by the public vault scanner.

This module therefore contains only source-linked metadata that Bulla publishes
on public pages and that can be tied confidently to a particular pool. It does
not perform HTTP requests. New Bulla pools return ``None`` until there is an
address-specific public source for their metadata.
"""

from dataclasses import dataclass

from eth_typing import HexAddress


@dataclass(slots=True, frozen=True)
class BullaVaultMetadata:
    """Public, address-specific Bulla pool metadata.

    The values are deliberately editorial rather than financial projections.
    They describe the financing purpose, eligibility and redemption constraints
    published by Bulla, while the contract adapter remains the source for live
    onchain values such as assets and fees.
    """

    #: One-line pool summary suitable for a vault list.
    short_description: str

    #: Longer plain-language description of the selected pool.
    description: str

    #: Publicly named organisations managing the pool, if published by Bulla.
    manager_name: str | None


#: TCS Settlement Pool Token V2.1 on Arbitrum.
#:
#: Bulla identifies this offering as the Bulla TCS Settlement Pool and says it
#: finances short-term freight receivables. The public page describes it as an
#: accredited-investor offering with a 30-day average and 40-day maximum
#: redemption period. Bulla's liquidity-pools page attributes management to
#: the Bulla and TCS Blockchain in-house finance team.
#:
#: Sources:
#: - https://www.bulla.network/bullatcspools
#: - https://www.bulla.network/bulla-finance-liquidity-pools
_BULLA_VAULT_METADATA: dict[tuple[int, str], BullaVaultMetadata] = {
    (
        42161,
        "0xc099773267308d8e9e805f47eabf9ab13bbc9e37",
    ): BullaVaultMetadata(
        short_description="Permissioned stablecoin pool financing short-term freight receivables through TCS Blockchain.",
        description=("The TCS Settlement Pool provides stablecoin liquidity for short-term freight receivables originated through TCS Blockchain. Bulla presents the pool as an offering for accredited investors, with exposure to 30-60 day invoices. Withdrawals depend on available liquidity; Bulla states an average 30-day redemption period and a maximum of 40 days. Returns depend on the repayment and collection of the financed invoices, so payment delays or impairments can affect both liquidity and returns."),
        manager_name="Bulla and TCS Blockchain",
    ),
}


def get_bulla_vault_metadata(chain_id: int, vault_address: HexAddress | str) -> BullaVaultMetadata | None:
    """Return public metadata for a Bulla pool when it is address-scoped.

    The lookup is intentionally exact. A pool name such as ``tcs`` is not a
    sufficiently stable identifier to apply the TCS description to another
    Bulla deployment, and unlisted pools must not inherit this pool's curator,
    liquidity terms or receivables focus.

    :param chain_id: EVM chain identifier of the Bulla pool.
    :param vault_address: Bulla pool share-token address.
    :return: Public pool metadata, or ``None`` when no address-specific source
        has been reviewed.
    """

    return _BULLA_VAULT_METADATA.get((chain_id, str(vault_address).lower()))
