"""Read the public Pear vault catalogue and archived NAV observations.

The endpoints used by the `Pear dashboard <https://vaults.pear.garden/>`__
are useful for a one-off backfill because the HyperCore state that contributes
to NAV cannot be read at an old EVM block. The scheduled scanner still values
Pear through ERC-4626 at the HyperEVM head.
"""

import datetime
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal

import requests
from eth_typing import HexAddress

from eth_defi.erc_4626.vault_protocol.pear.constants import PEAR_CHAIN_ID, PEAR_CURATORS, PEAR_FIRST_DEPOSIT_BLOCKS
from eth_defi.vault.base import RawVaultPriceRow, normalise_vault_description, normalise_vault_short_description
from eth_defi.vault.handwritten_metadata import get_handwritten_vault_metadata

logger = logging.getLogger(__name__)

PEAR_API_URL = "https://pro-gateway.pearprotocol.io/vaults"

#: A raw range at this size may have been truncated by the gateway.
RAW_NAV_POINT_LIMIT = 5000


@dataclass(slots=True, frozen=True)
class PearVaultMetadata:
    """Pear catalogue entry with the reviewed, address-scoped display metadata."""

    #: Name used by the dashboard.
    name: str
    #: Curator slug, when the vault operator has been verified.
    curator_slug: str | None
    #: One-sentence listing summary.
    short_description: str | None
    #: Markdown description of the strategy.
    description: str | None


def fetch_pear_vault_metadata(session: requests.Session) -> dict[HexAddress, PearVaultMetadata]:
    """Read the public vault catalogue keyed by lower-case HyperEVM address.

    Prefer the reviewed copy for known vaults. For other listings, expose the
    catalogue's strategy text. An owner or agent wallet does not establish the
    identity of an unreviewed curator.

    :param session: Caller-owned HTTP session.
    :return: Vault metadata from the Pear listing and the reviewed address mappings.
    """
    response = session.get(PEAR_API_URL, timeout=20)
    response.raise_for_status()
    metadata = {}
    for raw in response.json()["vaults"]:
        address = HexAddress(raw["vaultAddress"].lower())
        reviewed = get_handwritten_vault_metadata(PEAR_CHAIN_ID, address)
        about = raw.get("about")
        metadata[address] = PearVaultMetadata(
            name=raw["name"],
            curator_slug=PEAR_CURATORS.get(address),
            short_description=normalise_vault_short_description(reviewed.short_description if reviewed else about),
            description=normalise_vault_description(reviewed.description if reviewed else about),
        )
    return metadata


def fetch_pear_hourly_nav(
    session: requests.Session,
    address: HexAddress,
    start: datetime.datetime,
    end: datetime.datetime,
    *,
    end_block: int | None = None,
) -> Iterator[RawVaultPriceRow]:
    """Yield the last funded Pear snapshot in each UTC hour of a reviewed range.

    The API's hourly resolution returns the time label of the bucket alongside
    the block of its last snapshot. Request raw snapshots here to keep the actual
    observation time paired with its block. Two-day windows fit below the API's
    observed 5,000-point cap at its roughly one-minute collection cadence.
    Stopping at the first saved block also handles small clock differences
    between Pear's observation time and the EVM's block timestamp.

    :param session: Caller-owned HTTP session.
    :param address: One of the reviewed HyperEVM vaults with six-decimal assets and shares.
    :param start: Inclusive naive UTC hour that begins the import range.
    :param end: Exclusive naive UTC time. A partial last hour is allowed.
    :param end_block: Optional exclusive source block from an existing price row.
    :return: Canonical raw price rows with display-unit prices, assets and supply.
    """
    assert start.tzinfo is None and end.tzinfo is None, "Pear NAV ranges use naive UTC"
    address = HexAddress(address.lower())
    first_block = PEAR_FIRST_DEPOSIT_BLOCKS[address]
    assert start == start.replace(minute=0, second=0, microsecond=0)
    while start < end:
        stop = min(start + datetime.timedelta(days=2), end)
        logger.info("Reading Pear NAV for %s from %s to %s UTC", address, start, stop)
        response = session.get(f"{PEAR_API_URL}/{address}/nav", params={"from": start.isoformat() + "Z", "to": stop.isoformat() + "Z"}, timeout=30)
        response.raise_for_status()
        points = response.json()["points"]
        if len(points) >= RAW_NAV_POINT_LIMIT:
            raise ValueError(f"Pear returned the raw point limit for the NAV window for {address} at {start}")
        hourly = {}
        # Both ends of the API query are inclusive. Half-open windows keep an
        # observation on the boundary in its own hour and prevent duplicates.
        for point in points:
            source_time = datetime.datetime.fromisoformat(point["bucket"].replace("Z", "+00:00"))
            if source_time.tzinfo is None:
                raise ValueError(f"Pear observation has no time zone for {address} at {point['bucket']}")
            observed_at = source_time.astimezone(datetime.timezone.utc).replace(tzinfo=None)
            block = int(point["blockNumber"])
            if not start <= observed_at < stop or block < first_block or (end_block is not None and block >= end_block):
                continue
            supply = int(point["totalSupply"])
            if supply == 0:
                continue
            share_price = Decimal(point["sharePrice"]).scaleb(-18)
            total_assets = Decimal(point["totalAssets"]).scaleb(-6)
            if not share_price.is_finite() or not total_assets.is_finite() or min(supply, share_price, total_assets) < 0:
                raise ValueError(f"Invalid Pear valuation for {address} at block {block}")
            hour = observed_at.replace(minute=0, second=0, microsecond=0)
            previous = hourly.get(hour)
            if previous and (observed_at, block) <= (previous["timestamp"], previous["block_number"]):
                continue
            hourly[hour] = RawVaultPriceRow(
                chain=PEAR_CHAIN_ID,
                address=address,
                block_number=block,
                timestamp=observed_at,
                share_price=float(share_price),
                total_assets=float(total_assets),
                total_supply=float(Decimal(supply).scaleb(-6)),
                vault_poll_frequency="pear_api",
                errors="",
            )
        yield from (hourly[hour] for hour in sorted(hourly))
        if end_block is not None and any(int(point["blockNumber"]) >= end_block for point in points):
            return
        start = stop
