# Ruff: repository source boundaries use naive UTC.
# ruff: noqa: DTZ001
"""Reviewed Infinit identities and source-quality boundaries.

Deployment relationships are verified against the Arbitrum manager, and terms
come from https://docs.infinit.tech/infinit-ai-managed-trading-strategy-marketplace/vault-terms.
Do not assign a future manager vault to this product's trading account.
"""

import datetime
from dataclasses import dataclass

from eth_typing import HexAddress

from eth_defi.types import Percent

INFINIT_CHAIN_ID = 42161
INFINIT_MANAGER = HexAddress("0xafe480f375ebd13df703ef50b429357d29d162ee")
INFINIT_SAFE = HexAddress("0x79e78a5a07f09c0f59f21d4b9dcc18626a0f164d")
INFINIT_USDC = HexAddress("0xaf88d065e77c8cc2239327c5edb3a432268e5831")
INFINIT_APP = "https://app.infinit.tech"
INFINIT_MANAGER_DEPLOYMENT_BLOCK = 472_227_568

#: Published commercial terms; fee treatment in the API price is unverified.
INFINIT_MANAGEMENT_FEE: Percent = 0.02
INFINIT_PERFORMANCE_FEE: Percent = 0.20

#: One publication interval of lag beyond the normal fifteen-minute cadence.
INFINIT_MAX_PRICE_AGE = datetime.timedelta(minutes=30)


@dataclass(slots=True, frozen=True)
class InfinitProduct:
    """One independently reviewed contract, share token and application entry."""

    #: Actual vault identity, distinct from its ERC-20 receipt token.
    address: HexAddress
    #: ERC-20 receipt token.
    share_token: HexAddress
    #: Contract manager index.
    vault_id: int
    #: Public application slug.
    slug: str
    #: Application database identity, validated in every API response.
    application_id: str
    #: Manager contract whose vault index resolves this product.
    manager: HexAddress
    #: Independently reviewed custody multisig, separate from trading.
    custody_safe: HexAddress
    #: Hyperliquid account address, also the Arbitrum funding EOA.
    trading_account: HexAddress
    #: Contract deployment block, updated from explorer creation receipt.
    deployment_block: int
    #: Naive UTC deployment timestamp.
    deployed_at: datetime.datetime


APEX_PRODUCT = InfinitProduct(
    address=HexAddress("0xe64655e24b66073c73300aa924cd6df5c716638f"),
    share_token=HexAddress("0xb5fb43efd9b607c6ca863a56d22c491c82275c08"),
    vault_id=1,
    slug="apex-multistrategy",
    application_id="019f5a8e-62cf-752c-9e2f-8640768c7d3d",
    manager=INFINIT_MANAGER,
    custody_safe=INFINIT_SAFE,
    trading_account=HexAddress("0xf2460f28c9bacb35ac6fef048b661d33ad070aaf"),
    deployment_block=483_358_787,
    deployed_at=datetime.datetime.fromtimestamp(1783930721, datetime.UTC).replace(tzinfo=None),
)
INFINIT_PRODUCTS = {APEX_PRODUCT.address: APEX_PRODUCT}
INFINIT_HARDCODED_LEADS = tuple((INFINIT_CHAIN_ID, p.address, p.deployment_block, p.deployed_at) for p in INFINIT_PRODUCTS.values())

#: Reviewed on 2026-10-26: application placeholders while custody changed.
#: The public ledger shows capital remained present; these are not wipe-outs.
#: Boundaries are inclusive, in naive UTC. Raw source prices remain retained.
INFINIT_PRICE_GAPS = (
    (datetime.datetime(2026, 7, 30, 10), datetime.datetime(2026, 8, 4, 1, 45)),
    (datetime.datetime(2026, 8, 6, 9, 30), datetime.datetime(2026, 8, 27, 10, 30)),
)


def is_quarantined(timestamp: datetime.datetime) -> bool:
    """Check the reviewed Apex source exclusions.

    Exclusions apply only to this product and require review before removal.

    :param timestamp: Naive UTC source timestamp.
    :return: Whether the source lies inside a reviewed custody-transition gap.
    """
    return any(start <= timestamp <= end for start, end in INFINIT_PRICE_GAPS)
