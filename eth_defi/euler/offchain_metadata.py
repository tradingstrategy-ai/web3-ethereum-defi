"""Euler vault labelling

- Euler has put vault names offchain in Github, because of course Solidity programmers would do something like this
- ``name()`` accessor in Euler vault returns just a running counter
"""

import datetime
import json
from json import JSONDecodeError
from pathlib import Path
from typing import TypedDict
import logging
from urllib.error import HTTPError

import requests

from web3 import Web3
from eth_typing import HexAddress
from eth_defi.compat import native_datetime_utc_now, native_datetime_utc_fromtimestamp

#: Where we copy files from Euelr Github repo
DEFAULT_CACHE_PATH = Path.home() / ".cache" / "euler"


logger = logging.getLogger(__name__)


class EulerVaultMetadata(TypedDict):
    """Metadata about an Euler vault from offchain source.

    https://raw.githubusercontent.com/euler-xyz/euler-labels/refs/heads/master/130/vaults.json
    """

    name: str
    entity: str
    description: str


def fetch_euler_vaults_file_for_chain(
    chain_id: int,
    cache_path=DEFAULT_CACHE_PATH,
    github_base_url="https://raw.githubusercontent.com/euler-xyz/euler-labels/refs/heads/master",
    now_=None,
    max_cache_duration=datetime.timedelta(days=2),
) -> dict:
    """Fetch and cache Euler offchain vault metadata for a given chain.

    - One JSON file per chain
    """

    assert type(chain_id) is int, "chain_id must be integer"
    assert isinstance(cache_path, Path), "cache_path must be Path instance"

    cache_path.mkdir(parents=True, exist_ok=True)
    file = cache_path / f"euler_vaults_chain_{chain_id}.json"

    if not now_:
        now_ = native_datetime_utc_now()

    if not file.exists() or (now_ - native_datetime_utc_fromtimestamp(file.stat().st_mtime)) > max_cache_duration:
        logger.info(f"Re-fetching cached Euler vaults file for chain {chain_id} from {github_base_url}")
        with file.open("wt") as f:
            url = f"{github_base_url}/{chain_id}/vaults.json"

            # Fetch and save the file
            response = requests.get(url)

            logger.info(f"Got response code {response.status_code} for Euler vaults file for chain {chain_id} from {url}")

            try:
                response.raise_for_status()  # Raises exception for HTTP errors

                # Check Github file looks valuew
                logger.info("Fetched Euler vaults file for chain %d from %s, size %d bytes", chain_id, url, len(response.text))
                content = json.loads(response.text)  # Validate
                f.write(response.text)

                logger.info(f"Wrote {file.resolve()}")

            except (HTTPError, JSONDecodeError) as e:
                logger.warning(
                    "Euler vault file missing for chain %d is empty, writing empty JSON object, url %s, error %s, content %s",
                    chain_id,
                    url,
                    e,
                    response.text,
                )
                f.write("{}")
                content = {}

        # Strange Things happening here
        assert file.stat().st_size > 0, f"File {file} is empty after writing"
        return content

    else:
        timestamp = datetime.datetime.fromtimestamp(file.stat().st_mtime, tz=None)
        ago = now_ - timestamp
        logger.info(f"Using cached Euler vaults file for chain {chain_id} from {file}, last fetched at {timestamp.isoformat()}. ago {ago}")
        try:
            return json.load(open(file, "rt"))
        except JSONDecodeError as e:
            content = open(file, "rt").read()
            raise RuntimeError(f"Could not parse Euler vaults file for chain {chain_id} at {file}, length {len(content)} content starts with {content[:100]!r}") from e


def fetch_euler_vault_metadata(web3: Web3, vault_address: HexAddress) -> EulerVaultMetadata | None:
    """Fetch vault metadata from offchain source."""
    chain_id = web3.eth.chain_id
    vaults = fetch_euler_vaults_file_for_chain(chain_id)
    if vaults:
        vault_address = web3.to_checksum_address(vault_address)
        return vaults.get(vault_address)
    return None
