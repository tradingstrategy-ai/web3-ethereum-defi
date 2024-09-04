"""TokenSniffer API.

- Python wrapper for TokenSniffer API

- API documentation https://tokensniffer.readme.io/reference/introduction

- TokenSniffer API is $99/month, 500 requests a day
"""
import logging
import json
from pathlib import Path
from typing import TypedDict

import requests
from eth_typing import HexAddress
from requests import Session

from eth_defi.token_analysis.sqlite_cache import PersistentKeyValueStore


logger  = logging.getLogger(__name__)


class TokenSnifferError(Exception):
    """Wrap bad API replies from TokenSniffer"""


class TokenSnifferReply(TypedDict):
    """TokenSniffer JSON payload.

    - Some of the fields annotated (not all)

    - Described here https://tokensniffer.readme.io/reference/response
    """

    #: Added to the response if it was locally cached
    cached: bool

    #: OK if success
    message: str

    #:
    status: str

    #: 0-100 d
    score: int


class TokenSniffer:
    """TokenSniffer API."""

    def __init__(self, api_key: str, session: Session = None):
        assert api_key
        self.api_key = api_key

        if session is None:
            session = requests.Session()

        self.session = session

    def fetch_token_info(self, chain_id: int, address: str | HexAddress) -> TokenSnifferReply:
        """Get TokenSniffer token data.

        This is a synchronous method and may block long time if TokenSniffer does not have cached results.

        https://tokensniffer.com/api/v2/tokens/{chain_id}/{address}

        :param chain_id:
            Integer. Example for Ethereum os 1

        :param address:
            ERC-20 smart contract address.

        :return:
            Raw TokenSniffer JSON reply.

        """
        assert type(chain_id) == int
        assert address.startswith("0x")

        parameters = {
            "apikey": self.api_key,
            "include_metrics": True,
            "include_tests": True,
            "include_similar": True,
            "block_until_ready": True,
        }

        logger.info("Fetching TokenSniffer data %d: %s", chain_id, address)

        url = f"https://tokensniffer.com/api/v2/tokens/{chain_id}/{address}"
        resp = self.session.get(url, params=parameters)
        if resp.status_code != 200:
            raise TokenSnifferError(f"TokeSniffer replied: {resp}: {resp.text}")

        data = resp.json()

        if data["message"] != "OK":
            raise TokenSnifferError(f"Bad TokenSniffer reply: {data}")

        return data


class CachedTokenSniffer(TokenSniffer):
    """Add file-system based cache for TokenSniffer API.

    - Use SQLite DB as a key-value cache backend

    - No cache expiration

    - No support for multithreading/etc. fancy stuff
    """

    def __init__(
            self,
            cache_file: Path,
            api_key: str,
            session: Session = None
    ):
        assert isinstance(cache_file, Path)
        super().__init__(api_key, session)
        self.cache = PersistentKeyValueStore(cache_file)

    def fetch_token_info(self, chain_id: int, address: str | HexAddress) -> TokenSnifferReply:
        """Get TokenSniffer info.

        Use local file cache if available.
        """
        cache_key = f"{chain_id}-{address}"
        cached = self.cache.get(cache_key)
        if not cached:
            decoded = super().fetch_token_info(chain_id, address)
            self.cache[cache_key] = json.dumps(decoded)
            decoded["cached"] = False
        else:
            decoded = json.loads(cached)
            decoded["cached"] = True

        return decoded
