"""
GMX Protocol Contract Infrastructure

This module provides contract addresses, ABIs, and utility functions for interacting
with GMX protocol contracts across supported networks.
"""

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from eth_typing import HexAddress
from eth_utils import to_checksum_address
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.gmx.constants import GMX_API_URLS, GMX_API_URLS_BACKUP, GMX_CONTRACTS_JSON_URL, GMX_CONTRACTS_JSON_URL_UPDATES
from eth_defi.gmx.api import GMXAPI

logger = logging.getLogger(__name__)


# Subsquid GraphQL endpoints by chain (primary)
# Official :prod aliases per GMX docs. If :prod becomes unresponsive, the versioned hash backup is used.
GMX_SUBSQUID_ENDPOINTS = {
    "arbitrum": "https://gmx.squids.live/gmx-synthetics-arbitrum:prod/api/graphql",
    "avalanche": "https://gmx.squids.live/gmx-synthetics-avalanche:prod/api/graphql",
    "arbitrum_sepolia": "https://gmx.squids.live/gmx-synthetics-arb-sepolia:prod/api/graphql",
}

# Subsquid GraphQL backup endpoints by chain.
# Versioned hashes (e.g. @5acc9d, @cc00ce) have been removed — they return 404
# once Subsquid rotates its deployments.  The :prod alias is stable and is used
# for both primary and backup to benefit from automatic fail-over without
# hard-coding a hash that will eventually go stale.
GMX_SUBSQUID_ENDPOINTS_BACKUP = {
    "arbitrum": "https://gmx.squids.live/gmx-synthetics-arbitrum:prod/api/graphql",
    "avalanche": "https://gmx.squids.live/gmx-synthetics-avalanche:prod/api/graphql",
    "arbitrum_sepolia": "https://gmx.squids.live/gmx-synthetics-arb-sepolia:prod/api/graphql",
}


# Helper function to extract actual API URLs (filtering out docstring keys)
def _get_clean_api_urls() -> dict[str, str]:
    """Extract actual API URLs, filtering out docstring keys."""
    clean_urls = {}
    for key, value in GMX_API_URLS.items():
        if isinstance(value, str) and value.startswith("https://"):
            # Handle case where chain name is embedded at end of docstring key
            if key.endswith("arbitrum"):
                clean_urls["arbitrum"] = value
            elif key.endswith("avalanche"):
                clean_urls["avalanche"] = value
            else:
                # Regular key
                clean_urls[key] = value
    return clean_urls


def _get_clean_backup_urls() -> dict[str, str]:
    """Extract actual backup API URLs, filtering out docstring keys."""
    clean_urls = {}
    for key, value in GMX_API_URLS_BACKUP.items():
        if isinstance(value, str) and value.startswith("https://"):
            # Handle case where chain name is embedded at end of docstring key
            if key.endswith("arbitrum"):
                clean_urls["arbitrum"] = value
            elif key.endswith("avalanche"):
                clean_urls["avalanche"] = value
            else:
                # Regular key
                clean_urls[key] = value
    return clean_urls


@dataclass(slots=True)
class ContractAddresses:
    """GMX contract addresses for a specific network."""

    #: DataStore contract address for on-chain data storage
    datastore: HexAddress
    #: EventEmitter contract address for protocol event logging
    eventemitter: HexAddress
    #: ExchangeRouter contract address for trading operations
    exchangerouter: HexAddress
    #: DepositVault contract address for deposit operations
    depositvault: HexAddress
    #: WithdrawalVault contract address for withdrawal operations
    withdrawalvault: HexAddress
    #: OrderVault contract address for order management
    ordervault: HexAddress
    #: SyntheticsReader contract address for efficient data queries
    syntheticsreader: HexAddress
    #: SyntheticsRouter contract address for synthetic asset routing
    syntheticsrouter: HexAddress
    #: GLVReader contract address for GLV token queries
    glvreader: HexAddress
    #: ChainlinkPriceFeedProvider contract address (optional)
    chainlinkpricefeedprovider: Optional[HexAddress] = None
    #: ChainlinkDataStreamProvider contract address (optional)
    chainlinkdatastreamprovider: Optional[HexAddress] = None
    #: GMOracleProvider contract address (optional)
    gmoracleprovider: Optional[HexAddress] = None
    #: OrderHandler contract address (optional)
    orderhandler: Optional[HexAddress] = None
    #: Oracle contract address (optional)
    oracle: Optional[HexAddress] = None


# Keep only networks that won't be fetched dynamically
NETWORK_CONTRACTS = {
    "arbitrum_sepolia": ContractAddresses(
        datastore=to_checksum_address("0xCF4c2C4c53157BcC01A596e3788fFF69cBBCD201"),
        eventemitter=to_checksum_address("0xa973c2692C1556E1a3d478e745e9a75624AEDc73"),
        exchangerouter=to_checksum_address("0x657F9215FA1e839FbA15cF44B1C00D95cF71ed10"),
        depositvault=to_checksum_address("0x809Ea82C394beB993c2b6B0d73b8FD07ab92DE5A"),
        withdrawalvault=to_checksum_address("0x7601c9dBbDCf1f5ED1E7Adba4EFd9f2cADa037A5"),
        ordervault=to_checksum_address("0x1b8AC606de71686fd2a1AEDEcb6E0EFba28909a2"),
        syntheticsreader=to_checksum_address("0x37a0A165389B2f959a04685aC8fc126739e86926"),
        syntheticsrouter=to_checksum_address("0x72F13a44C8ba16a678CAD549F17bc9e06d2B8bD2"),
        glvreader=to_checksum_address("0x4843D570c726cFb44574c1769f721a49c7e9c350"),
        chainlinkpricefeedprovider=to_checksum_address("0xa76BF7f977E80ac0bff49BDC98a27b7b070a937d"),
        chainlinkdatastreamprovider=to_checksum_address("0x13d6133F9ceE27B6C9A4559849553F10A45Bd9a4"),
        gmoracleprovider=to_checksum_address("0xFcE6f3D7a312C16ddA64dB049610f3fa4a477627"),
        orderhandler=to_checksum_address("0x96332063e9dAACF93A7379CCa13BC2C8Ff5809cb"),
        oracle=to_checksum_address("0x0dC4e24C63C24fE898Dda574C962Ba7Fbb146964"),
    ),
}


#: Default GMX contract release used to resolve addresses.
#:
#: Address resolution is **pinned** rather than fetched live. GMX publishes new
#: deployments to the ``updates`` branch of ``gmx-io/gmx-synthetics``; resolving
#: against that branch on every call means the ExchangeRouter a bot trades through
#: can change because of an upstream ``git push``, or flip back to the previous
#: deployment when GitHub returns HTTP 429. Both happened in production and caused
#: every order to revert with ``Target not allowed`` against a Lagoon vault guard
#: whose allowlist still held the older router.
GMX_DEFAULT_CONTRACT_RELEASE = "v2.2c"

#: Environment variable overriding :data:`GMX_DEFAULT_CONTRACT_RELEASE`.
#:
#: This is the documented escape hatch. Set it to another key of
#: :data:`PINNED_CONTRACTS` (e.g. ``v2.2b``) to roll back without a code change, or
#: to :data:`GMX_CONTRACT_RELEASE_REMOTE` to restore the legacy dynamic-fetch
#: behaviour.
GMX_CONTRACT_RELEASE_ENV_VAR = "GMX_CONTRACT_RELEASE"

#: Sentinel release restoring live resolution from ``gmx-io/gmx-synthetics``.
#:
#: Opt-in only. Results are still cached for
#: :data:`GMX_REMOTE_CACHE_TTL_SECONDS` so the order hot path does not perform an
#: HTTP round trip per order.
GMX_CONTRACT_RELEASE_REMOTE = "remote"

#: How long a dynamically fetched address set stays cached, in seconds.
GMX_REMOTE_CACHE_TTL_SECONDS = 3600.0

#: Pinned GMX contract addresses, keyed by release then by chain.
#:
#: Sourced from ``gmx-io/gmx-synthetics``: ``v2.2c`` from the ``updates`` branch
#: and ``v2.2b`` from ``main``, both read from ``docs/contracts.json``.
#:
#: Seven addresses rotated between v2.2b and v2.2c, identically on both chains:
#: ``ExchangeRouter``, ``Reader``, ``GlvReader``, ``ChainlinkPriceFeedProvider``,
#: ``ChainlinkDataStreamProvider``, ``OrderHandler`` and ``Oracle``. Of these only
#: the first three matter to this integration — ``ExchangeRouter`` is the trading
#: target a vault guard allowlists, and the two readers are decoded with vendored
#: ABIs. Nothing here reads the other four.
#:
#: The rest are unchanged: ``Router`` (SyntheticsRouter), ``OrderVault``,
#: ``DataStore``, ``EventEmitter``, ``DepositVault``, ``WithdrawalVault`` and
#: ``GmOracleProvider``. That is the load-bearing property — an existing ERC-20
#: approval targets the SyntheticsRouter and a guard maps each ExchangeRouter to
#: an OrderVault, so both survive the upgrade and the migration is a single
#: whitelist entry rather than a re-approval plus a guard remap.
#:
#: ``test_release_rotation_set_is_documented`` pins this list, so a future release
#: that rotates something else fails rather than silently invalidating the claim.
PINNED_CONTRACTS: dict[str, dict[str, ContractAddresses]] = {
    "v2.2c": {
        "arbitrum": ContractAddresses(
            datastore=to_checksum_address("0xFD70de6b91282D8017aA4E741e9Ae325CAb992d8"),
            eventemitter=to_checksum_address("0xC8ee91A54287DB53897056e12D9819156D3822Fb"),
            exchangerouter=to_checksum_address("0x7dE39FF2e232A2203196788d37e234cF8F1b83f1"),
            depositvault=to_checksum_address("0xF89e77e8Dc11691C9e8757e84aaFbCD8A67d7A55"),
            withdrawalvault=to_checksum_address("0x0628D46b5D145f183AdB6Ef1f2c97eD1C4701C55"),
            ordervault=to_checksum_address("0x31eF83a530Fde1B38EE9A18093A333D8Bbbc40D5"),
            syntheticsreader=to_checksum_address("0xfA26cBb46e2614609406de08CA1Dc7f70a684184"),
            syntheticsrouter=to_checksum_address("0x7452c558d45f8afC8c83dAe62C3f8A5BE19c71f6"),
            glvreader=to_checksum_address("0x85fcBD684D08053f1efAB302dCb04F22E20E65B1"),
            chainlinkpricefeedprovider=to_checksum_address("0x90218fbb064b1475E4382b041Cc7ccF08AF718B0"),
            chainlinkdatastreamprovider=to_checksum_address("0x7BA7Ae61887F1aca28E0FE5aB1434ce85b6606aa"),
            gmoracleprovider=to_checksum_address("0x5d6B84086DA6d4B0b6C0dF7E02f8a6A039226530"),
            orderhandler=to_checksum_address("0xa5D2d45228ee2E3A18AB122B2cE84997d008f4Eb"),
            oracle=to_checksum_address("0x26C02F221e8dB5A821e12347C7eA8a6b6E10842f"),
        ),
        "avalanche": ContractAddresses(
            datastore=to_checksum_address("0x2F0b22339414ADeD7D5F06f9D604c7fF5b2fe3f6"),
            eventemitter=to_checksum_address("0xDb17B211c34240B014ab6d61d4A31FA0C0e20c26"),
            exchangerouter=to_checksum_address("0xc002Db96E682FFF6675966F959677285a0C45Efa"),
            depositvault=to_checksum_address("0x90c670825d0C62ede1c5ee9571d6d9a17A722DFF"),
            withdrawalvault=to_checksum_address("0xf5F30B10141E1F63FC11eD772931A8294a591996"),
            ordervault=to_checksum_address("0xD3D60D22d415aD43b7e64b510D86A30f19B1B12C"),
            syntheticsreader=to_checksum_address("0xa34320a507493C71Fe35E982e496F7C5d1a7fa02"),
            syntheticsrouter=to_checksum_address("0x820F5FfC5b525cD4d88Cd91aCf2c28F16530Cc68"),
            glvreader=to_checksum_address("0x321EB66dD95ad33715ee615AAb8dAC6394E7b3F9"),
            chainlinkpricefeedprovider=to_checksum_address("0x86E284921273E1442f32ebd1b567Ce988Cf50dfE"),
            chainlinkdatastreamprovider=to_checksum_address("0xc581aF9d20b1d95e456e4Bb9E3039aa0d48F9891"),
            gmoracleprovider=to_checksum_address("0x9Dc4f12Eb2d8405b499FB5B8AF79a5f64aB8a457"),
            orderhandler=to_checksum_address("0xC993eF170859DAE0241a3c12B8186e456Fa1c1B0"),
            oracle=to_checksum_address("0x29220fA3b24279279C211701DE4a7b035122B911"),
        ),
    },
    "v2.2b": {
        "arbitrum": ContractAddresses(
            datastore=to_checksum_address("0xFD70de6b91282D8017aA4E741e9Ae325CAb992d8"),
            eventemitter=to_checksum_address("0xC8ee91A54287DB53897056e12D9819156D3822Fb"),
            exchangerouter=to_checksum_address("0x1C3fa76e6E1088bCE750f23a5BFcffa1efEF6A41"),
            depositvault=to_checksum_address("0xF89e77e8Dc11691C9e8757e84aaFbCD8A67d7A55"),
            withdrawalvault=to_checksum_address("0x0628D46b5D145f183AdB6Ef1f2c97eD1C4701C55"),
            ordervault=to_checksum_address("0x31eF83a530Fde1B38EE9A18093A333D8Bbbc40D5"),
            syntheticsreader=to_checksum_address("0x470fbC46bcC0f16532691Df360A07d8Bf5ee0789"),
            syntheticsrouter=to_checksum_address("0x7452c558d45f8afC8c83dAe62C3f8A5BE19c71f6"),
            glvreader=to_checksum_address("0x2C670A23f1E798184647288072e84054938B5497"),
            chainlinkpricefeedprovider=to_checksum_address("0x38B8dB61b724b51e42A88Cb8eC564CD685a0f53B"),
            chainlinkdatastreamprovider=to_checksum_address("0xE1d5a068c5b75E0c7Ea1A9Fe8EA056f9356C6fFD"),
            gmoracleprovider=to_checksum_address("0x5d6B84086DA6d4B0b6C0dF7E02f8a6A039226530"),
            orderhandler=to_checksum_address("0x63492B775e30a9E6b4b4761c12605EB9d071d5e9"),
            oracle=to_checksum_address("0x7F01614cA5198Ec979B1aAd1DAF0DE7e0a215BDF"),
        ),
        "avalanche": ContractAddresses(
            datastore=to_checksum_address("0x2F0b22339414ADeD7D5F06f9D604c7fF5b2fe3f6"),
            eventemitter=to_checksum_address("0xDb17B211c34240B014ab6d61d4A31FA0C0e20c26"),
            exchangerouter=to_checksum_address("0x8f550E53DFe96C055D5Bdb267c21F268fCAF63B2"),
            depositvault=to_checksum_address("0x90c670825d0C62ede1c5ee9571d6d9a17A722DFF"),
            withdrawalvault=to_checksum_address("0xf5F30B10141E1F63FC11eD772931A8294a591996"),
            ordervault=to_checksum_address("0xD3D60D22d415aD43b7e64b510D86A30f19B1B12C"),
            syntheticsreader=to_checksum_address("0x62Cb8740E6986B29dC671B2EB596676f60590A5B"),
            syntheticsrouter=to_checksum_address("0x820F5FfC5b525cD4d88Cd91aCf2c28F16530Cc68"),
            glvreader=to_checksum_address("0x5C6905A3002f989E1625910ba1793d40a031f947"),
            chainlinkpricefeedprovider=to_checksum_address("0x05d97cee050bfb81FB3EaD4A9368584F8e72C88e"),
            chainlinkdatastreamprovider=to_checksum_address("0xC181eB022F33b8ba808AD96348B03e8A753A859b"),
            gmoracleprovider=to_checksum_address("0x9Dc4f12Eb2d8405b499FB5B8AF79a5f64aB8a457"),
            orderhandler=to_checksum_address("0x823b558B4bC0a2C4974a0d8D7885AA1102D15dEC"),
            oracle=to_checksum_address("0xE1d5a068c5b75E0c7Ea1A9Fe8EA056f9356C6fFD"),
        ),
    },
}


#: Cached dynamically fetched address sets: chain -> (fetched_at, addresses).
_remote_address_cache: dict[str, tuple[float, ContractAddresses]] = {}

#: Guards :data:`_remote_address_cache` against concurrent refresh.
_remote_address_cache_lock = threading.Lock()


def get_pinned_contract_release() -> str:
    """Resolve which GMX contract release address lookups should use.

    Reads :data:`GMX_CONTRACT_RELEASE_ENV_VAR` at call time so tests and operators
    can override the pin without reimporting the module.

    :return: Release key into :data:`PINNED_CONTRACTS`, or
        :data:`GMX_CONTRACT_RELEASE_REMOTE` for live resolution.
    """
    return os.environ.get(GMX_CONTRACT_RELEASE_ENV_VAR, GMX_DEFAULT_CONTRACT_RELEASE).strip()


def clear_contract_address_cache() -> None:
    """Drop cached dynamically fetched addresses.

    Only affects the :data:`GMX_CONTRACT_RELEASE_REMOTE` code path; pinned lookups
    are not cached because they perform no I/O.
    """
    with _remote_address_cache_lock:
        _remote_address_cache.clear()


def _get_remote_contract_addresses(chain: str) -> ContractAddresses:
    """Fetch and cache contract addresses from ``gmx-io/gmx-synthetics``.

    :param chain: Chain name, ``arbitrum`` or ``avalanche``.
    :return: Freshly fetched addresses, or a cached set younger than
        :data:`GMX_REMOTE_CACHE_TTL_SECONDS`.
    :raises ValueError: If the addresses could not be fetched and nothing is cached.
    """
    now = time.time()

    with _remote_address_cache_lock:
        cached = _remote_address_cache.get(chain)
        if cached is not None and (now - cached[0]) < GMX_REMOTE_CACHE_TTL_SECONDS:
            return cached[1]

    try:
        addresses = _fetch_contract_addresses_from_url(chain)
    except (requests.RequestException, json.JSONDecodeError) as exc:
        # _fetch_contract_addresses_from_url() re-raises rather than returning
        # None when the final URL exhausts its retries, which is what a plain
        # connection failure looks like — the most likely outage of all. Treat it
        # exactly like a None so the stale-serving path below still applies.
        #
        # Deliberately narrow: these are the two families that function re-raises
        # for a transport or payload failure. A TypeError from, say, constructing
        # ContractAddresses with a renamed field is a bug in this library, not an
        # outage, and must surface as itself rather than as a misleading
        # "failed to fetch" warning that quietly serves stale addresses.
        logger.warning("Error fetching GMX contract addresses for %s: %s", chain, exc)
        addresses = None

    if addresses is None:
        # Serve a stale entry rather than failing an order: a known-slightly-old
        # address set beats no address set when GitHub rate limits us.
        with _remote_address_cache_lock:
            stale = _remote_address_cache.get(chain)
        if stale is not None:
            logger.warning(
                "Failed to refresh GMX contract addresses for %s, serving cached set fetched %.0fs ago",
                chain,
                now - stale[0],
            )
            return stale[1]
        raise ValueError(
            f"Failed to fetch contract addresses for {chain} from GMX ({GMX_CONTRACTS_JSON_URL}). Set {GMX_CONTRACT_RELEASE_ENV_VAR} to a pinned release ({', '.join(sorted(PINNED_CONTRACTS))}) to avoid the network dependency.",
        )

    _warn_if_superseded_release(chain, addresses)

    with _remote_address_cache_lock:
        _remote_address_cache[chain] = (now, addresses)

    return addresses


def _warn_if_superseded_release(chain: str, addresses: ContractAddresses) -> None:
    """Log loudly when live resolution returned a release older than the default.

    :func:`_fetch_contract_addresses_from_url` tries the ``updates`` branch and
    falls back to ``main``. That fallback does not fail — it *succeeds*, returning
    a valid but older address set. So a transient GitHub 429 can still swap the
    resolved ExchangeRouter out from under a running bot, which is the failure
    this module exists to prevent; caching then holds the wrong set for up to
    :data:`GMX_REMOTE_CACHE_TTL_SECONDS`.

    Remote resolution is opt-in, so this is reported rather than rejected. The
    fix for anyone seeing it is to pin — that is what the default does.

    :param chain: Chain the addresses were resolved for.
    :param addresses: The freshly resolved address set.
    """
    default_chains = PINNED_CONTRACTS.get(GMX_DEFAULT_CONTRACT_RELEASE, {})
    expected = default_chains.get(chain)
    if expected is None or addresses.exchangerouter == expected.exchangerouter:
        return

    for release, chains in PINNED_CONTRACTS.items():
        known = chains.get(chain)
        if known is not None and known.exchangerouter == addresses.exchangerouter:
            logger.warning(
                "GMX live resolution for %s returned the %s ExchangeRouter (%s), not the current %s (%s). The updates branch was probably unavailable and the fetch fell back to main. Set %s to pin a release instead of resolving live.",
                chain,
                release,
                addresses.exchangerouter,
                GMX_DEFAULT_CONTRACT_RELEASE,
                expected.exchangerouter,
                GMX_CONTRACT_RELEASE_ENV_VAR,
            )
            return

    logger.warning(
        "GMX live resolution for %s returned an unrecognised ExchangeRouter %s (expected the %s address %s). If GMX has published a new release, verify it is whitelisted on any vault guard before pinning to it.",
        chain,
        addresses.exchangerouter,
        GMX_DEFAULT_CONTRACT_RELEASE,
        expected.exchangerouter,
    )


def _fetch_contract_addresses_from_url(
    chain: str,
    timeout: float = 10.0,
    max_retries: int = 2,
    retry_delay: float = 0.1,
) -> Optional[ContractAddresses]:
    """Fetch contract addresses for a chain from the GMX contracts.json URL with retry logic.

    Tries the updates branch first (which has the latest addresses), then falls back
    to main branch if updates returns 404 (branch merged/deleted). Each URL is retried
    with exponential backoff on failure.

    :param chain: Chain name (arbitrum, avalanche, etc.)
    :param timeout: HTTP request timeout in seconds (default: 10.0)
    :param max_retries: Maximum number of retry attempts per URL (default: 2)
    :param retry_delay: Initial delay between retries with exponential backoff (default: 0.1s)
    :return: ContractAddresses object or None if fetching failed
    """
    # Try updates branch first (has latest Reader and other contract addresses)
    urls_to_try = [
        (GMX_CONTRACTS_JSON_URL_UPDATES, "updates branch"),
        (GMX_CONTRACTS_JSON_URL, "main branch"),
    ]

    contracts_data = None
    last_error = None

    for url, branch_name in urls_to_try:
        for attempt in range(max_retries):
            try:
                response = requests.get(url, timeout=timeout)
                response.raise_for_status()
                contracts_data = response.json()
                logger.debug("Successfully fetched contracts.json from %s", branch_name)
                break  # Success! Stop retrying this URL
            except requests.HTTPError as e:
                if e.response.status_code == 404 and branch_name == "updates branch":
                    # Updates branch not found (merged to main), try main branch
                    logger.debug("Updates branch not found (404), trying main branch")
                    break  # Break inner retry loop, continue to next URL
                else:
                    # Other HTTP error
                    last_error = e
                    if attempt < max_retries - 1:
                        delay = retry_delay * (2**attempt)
                        logger.warning(
                            "Attempt %d/%d failed for %s: %s. Retrying in %.1f seconds...",
                            attempt + 1,
                            max_retries,
                            branch_name,
                            e,
                            delay,
                        )
                        time.sleep(delay)
                    else:
                        logger.warning("All %d attempts failed for %s", max_retries, branch_name)
            except (requests.RequestException, json.JSONDecodeError) as e:
                # Network or JSON error
                last_error = e
                if attempt < max_retries - 1:
                    delay = retry_delay * (2**attempt)
                    logger.warning(
                        "Attempt %d/%d failed for %s: %s. Retrying in %.1f seconds...",
                        attempt + 1,
                        max_retries,
                        branch_name,
                        e,
                        delay,
                    )
                    time.sleep(delay)
                else:
                    logger.warning("All %d attempts failed for %s", max_retries, branch_name)
                    if branch_name == "main branch":
                        # Last URL failed after all retries, re-raise
                        raise

        # If we got data from this URL, stop trying other URLs
        if contracts_data is not None:
            break

    if contracts_data is None:
        return None

    try:
        # Get contracts for the specified chain
        if chain not in contracts_data:
            return None

        contracts_list = contracts_data[chain]

        # Map contract names to addresses
        contract_map = {}
        for contract_info in contracts_list:
            name = contract_info.get("contractName", "")
            address = contract_info.get("contractAddress", "")
            if name and address:
                contract_map[name.lower()] = to_checksum_address(address)

        # Create ContractAddresses instance based on available contracts
        # Map the exact contract names to field names in ContractAddresses
        field_mappings = {
            "datastore": "datastore",
            "eventemitter": "eventemitter",
            "exchangerouter": "exchangerouter",
            "depositvault": "depositvault",
            "withdrawalvault": "withdrawalvault",
            "ordervault": "ordervault",
            "syntheticsreader": "reader",  # The synthetics reader is called "Reader" in the JSON
            "syntheticsrouter": "router",  # The synthetics router is called "Router" in the JSON
            "glvreader": "glvreader",
        }

        # Additional optional fields
        optional_field_mappings = {
            "chainlinkpricefeedprovider": "chainlinkpricefeedprovider",
            "chainlinkdatastreamprovider": "chainlinkdatastreamprovider",
            "gmoracleprovider": "gmoracleprovider",
            "orderhandler": "orderhandler",
            "oracle": "oracle",
        }

        # Build the contract addresses dict
        addresses_dict = {}

        # Map the required fields
        for field_name, contract_name in field_mappings.items():
            if contract_name.lower() in contract_map:
                addresses_dict[field_name] = contract_map[contract_name.lower()]
            else:
                # If we can't find the specific contract, we can't return a valid ContractAddresses
                return None

        # Map the optional fields
        for field_name, contract_name in optional_field_mappings.items():
            if contract_name.lower() in contract_map:
                addresses_dict[field_name] = contract_map[contract_name.lower()]

        # Create the ContractAddresses object
        contract_addresses = ContractAddresses(**addresses_dict)

        # Note: We now fetch from the 'updates' branch first which has the latest contract addresses.
        # When GMX merges updates to main, this will automatically switch to main branch.
        return contract_addresses
    except (requests.RequestException, json.JSONDecodeError, KeyError, ValueError) as e:
        # Return None if there's an error fetching or parsing
        return None


def _fetch_tokens_from_gmx_api(chain: str) -> Optional[dict[str, dict]]:
    """Fetch token data for a chain from GMX API.

    Returns address -> {symbol, decimals, synthetic} mapping to avoid
    expensive contract calls for each token.
    """
    try:
        # Use the updated GMXAPI constructor that accepts chain directly
        api = GMXAPI(chain=chain)

        # Fetch tokens from GMX API
        token_data = api.get_tokens()
        token_infos = token_data.get("tokens", [])

        # Convert to address -> metadata mapping (includes decimals from API)
        tokens_dict = {}
        for token_info in token_infos:
            symbol = token_info.get("symbol", "")
            address = token_info.get("address", "")
            decimals = token_info.get("decimals")
            synthetic = token_info.get("synthetic", False)

            if symbol and address:
                if decimals is None:
                    raise ValueError(f"GMX API did not return decimals for token {symbol} ({address}). Cannot safely convert prices.")
                checksum_address = to_checksum_address(address)
                tokens_dict[checksum_address] = {
                    "symbol": symbol,
                    "decimals": decimals,
                    "synthetic": synthetic,
                }

        return tokens_dict

    except Exception as e:
        # No fallback - raise error with helpful message
        raise ValueError(f"Failed to fetch token data for {chain} from GMX API. Error: {str(e)}. Please check your internet connection and try again.")


# ABI loading function
def _load_abi(filename: str) -> list:
    """Load ABI from JSON file in the eth_defi/abi/gmx directory."""
    current_dir = Path(__file__).parent.parent
    abi_path = current_dir / "abi" / "gmx" / filename
    with open(abi_path, "r") as f:
        return json.load(f)


# Token addresses by network - fallback values when API calls fail
NETWORK_TOKENS = {
    "arbitrum": {
        "WETH": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
        "ETH": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",  # ETH and WETH are treated the same for GMX
        "WBTC": "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f",
        "USDC": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        "USDT": "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
        "ARB": "0x912CE59144191C1204E64559FE8253a0e49E6548",
        "LINK": "0xf97f4df75117a78c1A5a0DBb814Af92458539FB4",
        "wstETH": "0x5979D7b546E38E414F7E9822514be443A4800529",
    },
    "avalanche": {
        "WAVAX": "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7",
        "AVAX": "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7",  # AVAX and WAVAX are treated the same for GMX
        "WETH": "0x49D5c2BdFfac6CE2BFdB6640F4F80f226bc10bAB",
        "WBTC": "0x50b7545627a5162F82A992c33b87aDc75187B218",
        "USDC": "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E",
        "USDT": "0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7",
    },
    "arbitrum_sepolia": {
        "WETH": "0x980B62Da83eFf3D4576C647993b0c1D7faf17c73",
        "ETH": "0x980B62Da83eFf3D4576C647993b0c1D7faf17c73",  # ETH and WETH are treated the same for GMX
        "BTC": "0xF79cE1Cf38A09D572b021B4C5548b75A14082F12",
        "USDC": "0x3321Fd36aEaB0d5CdfD26f4A3A93E2D2aAcCB99f",
        "USDC.SG": "0x3253a335E7bFfB4790Aa4C25C4250d206E9b9773",
        "CRV": "0xD5DdAED48B09fa1D7944bd662CB05265FCD7077C",
    },
}


# Token metadata by network including symbol, decimals, and synthetic flag
NETWORK_TOKENS_METADATA = {
    "arbitrum_sepolia": {
        "0x980B62Da83eFf3D4576C647993b0c1D7faf17c73": {
            "symbol": "WETH",
            "decimals": 18,
            "synthetic": False,
        },  # Also represents ETH
        "0x3321Fd36aEaB0d5CdfD26f4A3A93E2D2aAcCB99f": {
            "symbol": "USDC",
            "decimals": 6,
            "synthetic": False,
        },
        "0xF79cE1Cf38A09D572b021B4C5548b75A14082F12": {
            "symbol": "BTC",
            "decimals": 8,
            "synthetic": False,
        },
        "0x3253a335E7bFfB4790Aa4C25C4250d206E9b9773": {
            "symbol": "USDC.SG",
            "decimals": 6,
            "synthetic": False,
        },
        "0xD5DdAED48B09fa1D7944bd662CB05265FCD7077C": {
            "symbol": "CRV",
            "decimals": 18,
            "synthetic": True,
        },
    },
}


# Testnet token to mainnet oracle address mapping
# Testnets don't have their own oracles, so we map testnet token addresses to mainnet
# token addresses for oracle price lookups
TESTNET_TO_MAINNET_ORACLE_TOKENS = {
    # Arbitrum Sepolia testnet → Arbitrum mainnet oracle addresses
    "0xD5DdAED48B09fa1D7944bd662CB05265FCD7077C": "0xe5f01aeAcc8288E9838A60016AB00d7b6675900b",  # CRV
    "0x980B62Da83eFf3D4576C647993b0c1D7faf17c73": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",  # WETH
    "0xF79cE1Cf38A09D572b021B4C5548b75A14082F12": "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f",  # BTC/WBTC
    "0x3253a335E7bFfB4790Aa4C25C4250d206E9b9773": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",  # USDC
    "0x3321Fd36aEaB0d5CdfD26f4A3A93E2D2aAcCB99f": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",  # USDC.SG
}


def get_contract_addresses(chain: str, release: Optional[str] = None) -> ContractAddresses:
    """
    Get GMX contract addresses for a specific network.

    Addresses are resolved from :data:`PINNED_CONTRACTS` by default. Pinning is
    deliberate: a trading target must not change because GMX pushed to the
    ``updates`` branch of ``gmx-io/gmx-synthetics``, and must not flip back to the
    previous deployment because GitHub returned HTTP 429. Both occur in practice,
    and against an on-chain allowlist (such as a Lagoon vault guard) a silently
    rotated ExchangeRouter turns every order into a reverted transaction.

    To follow GMX's published addresses live instead, pass
    ``release=GMX_CONTRACT_RELEASE_REMOTE`` or set the
    :data:`GMX_CONTRACT_RELEASE_ENV_VAR` environment variable. Remote results are
    cached for :data:`GMX_REMOTE_CACHE_TTL_SECONDS`, so this no longer costs an
    HTTP round trip per order.

    Example — pin to the previous release without a code change:

    .. code-block:: shell

        export GMX_CONTRACT_RELEASE=v2.2b

    :param chain: Network name ("arbitrum", "avalanche", or "arbitrum_sepolia")
    :param release: Override the release for this call. Defaults to
        :func:`get_pinned_contract_release`.
    :return: Contract addresses for the network
    :raises ValueError: If chain or release is not supported
    """
    # Handle the docstring keys in NETWORK_CONTRACTS
    clean_contracts = {}
    for key, value in NETWORK_CONTRACTS.items():
        if isinstance(value, ContractAddresses):
            # Handle case where chain name is embedded at end of docstring key
            if key.endswith("arbitrum"):
                clean_contracts["arbitrum"] = value
            elif key.endswith("avalanche"):
                clean_contracts["avalanche"] = value
            else:
                # Regular key (like arbitrum_sepolia)
                clean_contracts[key] = value

    if release is None:
        release = get_pinned_contract_release()

    # Networks with a static deployment (testnets) never take part in release
    # pinning — they have a single known address set.
    if chain in clean_contracts:
        return clean_contracts[chain]

    if release == GMX_CONTRACT_RELEASE_REMOTE:
        if chain not in ("arbitrum", "avalanche"):
            raise ValueError(
                f"Unsupported chain: {chain}. Supported: {list(clean_contracts.keys()) + ['arbitrum', 'avalanche']}",
            )
        return _get_remote_contract_addresses(chain)

    pinned_chains = PINNED_CONTRACTS.get(release)
    if pinned_chains is None:
        raise ValueError(
            f"Unknown GMX contract release: {release!r}. Supported: {sorted(PINNED_CONTRACTS)} or {GMX_CONTRACT_RELEASE_REMOTE!r}. Set via the {GMX_CONTRACT_RELEASE_ENV_VAR} environment variable.",
        )

    addresses = pinned_chains.get(chain)
    if addresses is None:
        raise ValueError(
            f"Unsupported chain: {chain}. Supported: {list(clean_contracts.keys()) + sorted(pinned_chains)}",
        )

    return addresses


def get_reader_contract(web3: Web3, chain: str) -> Contract:
    """
    Get SyntheticsReader contract instance for a specific network.

    :param web3: Web3 connection instance
    :param chain: Network name
    :return: Web3 contract instance for SyntheticsReader
    """
    addresses = get_contract_addresses(chain)
    return get_deployed_contract(web3, "gmx/Reader.json", addresses.syntheticsreader)


def get_datastore_contract(web3: Web3, chain: str) -> Contract:
    """
    Get DataStore contract instance for a specific network.

    :param web3: Web3 connection instance
    :param chain: Network name
    :return: Web3 contract instance for DataStore
    """
    addresses = get_contract_addresses(chain)
    return get_deployed_contract(web3, "gmx/DataStore.json", addresses.datastore)


def get_tokens_metadata_dict(chain: str) -> dict[str, dict]:
    """
    Get full token metadata for a specific network from GMX API.

    Returns address -> {symbol, decimals, synthetic} mapping.
    This avoids expensive contract calls for each token.

    :param chain: Network name
    :return: Dictionary mapping token addresses to metadata
    :raises ValueError: If chain is not supported or API request fails
    """
    tokens_metadata = _fetch_tokens_from_gmx_api(chain)
    if tokens_metadata is not None:
        return tokens_metadata
    else:
        raise ValueError(f"Failed to fetch token metadata for {chain} from GMX API. Please check your internet connection and try again.")


def get_tokens_address_dict(chain: str) -> dict[str, str]:
    """
    Get token address mapping for a specific network from GMX API.

    Returns symbol -> address mapping for backward compatibility.

    :param chain: Network name
    :return: Dictionary mapping token symbols to addresses
    :raises ValueError: If chain is not supported or API request fails
    """
    # Get full metadata
    tokens_metadata = get_tokens_metadata_dict(chain)

    # Convert to symbol -> address mapping for backward compatibility
    symbol_to_address = {}
    for address, metadata in tokens_metadata.items():
        symbol = metadata["symbol"].upper()
        symbol_to_address[symbol] = address

    return symbol_to_address


def get_token_address(chain: str, symbol: str, web3: Optional[Web3] = None) -> Optional[str]:
    """
    Get address for a specific token on a network.

    :param chain: Network name
    :param symbol: Token symbol
    :param web3: Web3 connection instance (optional, not required for API calls)
    :return: Token address or None if not found
    """
    return get_token_address_normalized(chain, symbol, web3)


def get_exchange_router_contract(web3: Web3, chain: str) -> Contract:
    """
    Get ExchangeRouter contract instance for a specific network.

    :param web3: Web3 connection instance
    :param chain: Network name
    :return: Web3 contract instance for ExchangeRouter
    """
    addresses = get_contract_addresses(chain)
    return get_deployed_contract(web3, "gmx/ExchangeRouter.json", addresses.exchangerouter)


def get_oracle_contract(web3: Web3, chain: str) -> Optional[Contract]:
    """
    Get Oracle contract instance for a specific network.

    :param web3: Web3 connection instance
    :param chain: Network name
    :return: Web3 contract instance for Oracle, or None if not available for the chain
    """
    addresses = get_contract_addresses(chain)
    if addresses.oracle:
        return get_deployed_contract(web3, "gmx/Oracle.json", addresses.oracle)
    return None


def get_glv_reader_contract(web3: Web3, chain: str) -> Contract:
    """
    Get GLV Reader contract instance for a specific network.

    :param web3: Web3 connection instance
    :param chain: Network name
    :return: Web3 contract instance for GLV Reader
    """
    addresses = get_contract_addresses(chain)
    return get_deployed_contract(web3, "gmx/GlvReader.json", addresses.glvreader)


def get_token_balance_contract(web3: Web3, contract_address: HexAddress) -> Contract:
    return get_deployed_contract(web3, "gmx/balance.json", contract_address)


def get_token_metadata(chain: str, address: str) -> Optional[dict]:
    """
    Get metadata for a specific token on a network.

    :param chain: Network name
    :param address: Token address
    :return: Token metadata dictionary or None if not found
    """
    tokens_metadata = get_tokens_metadata_dict(chain)
    return tokens_metadata.get(address)


def normalize_gmx_token_symbol(chain: str, token_symbol: str) -> str:
    """Normalize token symbol to the canonical form used by GMX API for a given chain.

    On GMX, ETH and WETH are treated as the same token, as are AVAX and WAVAX.
    This function returns the canonical symbol that GMX API uses.

    :param chain: Network name
    :param token_symbol: Original token symbol (e.g., "ETH", "WETH")
    :return: Canonical token symbol (e.g., always "ETH" for ETH/WETH on Arbitrum)
    """
    token_symbol_upper = token_symbol.upper()

    if chain in ["arbitrum", "arbitrum_sepolia"] and token_symbol_upper in ["ETH", "WETH"]:
        return "ETH"  # GMX API uses "ETH" for wrapped ETH on Arbitrum
    elif chain in ["avalanche", "avalanche_fuji"] and token_symbol_upper in ["AVAX", "WAVAX"]:
        return "AVAX"  # GMX API uses "AVAX" for wrapped AVAX on Avalanche
    else:
        return token_symbol_upper


def get_token_address_normalized(chain: str, symbol: str, web3: Optional[Web3] = None) -> Optional[str]:
    """Get address for a specific token on a network, with proper normalization for GMX.

    This function handles the special case where ETH and WETH are treated as the same
    token on GMX protocol, as well as AVAX and WAVAX on Avalanche.

    :param chain: Network name
    :param symbol: Token symbol (ETH/WETH will be normalized)
    :param web3: Web3 connection instance (optional, not required for API calls)
    :return: Token address or None if not found
    """
    normalized_symbol = normalize_gmx_token_symbol(chain, symbol)
    tokens = get_tokens_address_dict(chain)
    return tokens.get(normalized_symbol)
